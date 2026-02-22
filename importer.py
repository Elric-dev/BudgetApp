import pandas as pd
import mysql.connector
import os
import hashlib
import logging
import sys
import glob
from datetime import datetime
from dotenv import load_dotenv

from config import Config

# 1. SETUP LOGGING
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
c_handler = logging.StreamHandler(sys.stdout)
log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
c_handler.setFormatter(log_format)
logger.addHandler(c_handler)

def get_db_connection():
    """Establishes connection to MySQL/MariaDB."""
    try:
        return mysql.connector.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            database=Config.DB_NAME
        )
    except mysql.connector.Error as err:
        logger.error(f"MySQL Connection Error: {err}")
        raise

def generate_transaction_hash(row):
    """Creates a unique fingerprint to prevent duplicate spending records."""
    # Standard hash without salt for production deduplication
    combined = f"{row['Date']}|{row['Description']}|{row['Cost']}|{row['Category']}"
    return hashlib.sha256(combined.encode()).hexdigest()

def get_metadata(cursor):
    """Fetches user and category mappings from DB."""
    cursor.execute("SELECT user_id, name FROM users")
    users = {row['name']: row['user_id'] for row in cursor.fetchall()}
    cursor.execute("SELECT id, name FROM categories")
    categories = {row['name']: row['id'] for row in cursor.fetchall()}
    return users, categories

def run_import(csv_file_path):
    """Processes Splitwise CSVs into the transactions table."""
    print(f"--- Scanning: {csv_file_path} ---")
    
    try:
        # fillna(0) ensures numeric safety for solo baseline periods
        df = pd.read_csv(csv_file_path).fillna(0)
    except Exception as e:
        logger.error(f"CSV Read Error: {e}")
        return

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        user_map, cat_map = get_metadata(cursor)
        
        # User ID Mapping
        gus_id, joules_id = 0, 1
        gus_col = [name for name, u_id in user_map.items() if u_id == gus_id][0]
        joules_col = [name for name, u_id in user_map.items() if u_id == joules_id][0]

        # Using exact column names: Gus_share and Joules_share
        insert_sql = """
            INSERT IGNORE INTO transactions 
            (date, description, total_amount, user_id, category_id, payer_id, Gus_share, Joules_share, is_split, transaction_hash) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        import_count, skip_count = 0, 0
        for index, row in df.iterrows():
            # Filter out internal payments and footer totals
            if row['Category'] == 'Payment' or str(row['Description']).strip() == 'Total balance':
                continue

            try:
                cost = float(row['Cost'])
                clean_date = pd.to_datetime(row['Date']).strftime('%Y-%m-%d')
                cat_id = cat_map.get(row['Category'], cat_map.get('General', 39))

                # Extract liability directly from user columns
                gus_val = abs(float(row.get(gus_col, 0)))
                joules_val = abs(float(row.get(joules_col, 0)))
                
                # Determine payer based on Splitwise balance column
                payer_id = gus_id if float(row.get('Gus', 0)) > 0 else joules_id
                is_split = 1 if (gus_val > 0 and joules_val > 0) else 0
                
                t_hash = generate_transaction_hash(row)

                cursor.execute(insert_sql, (
                    clean_date, row['Description'], cost, gus_id, cat_id, 
                    payer_id, gus_val, joules_val, is_split, t_hash
                ))
                
                if cursor.rowcount > 0:
                    import_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                logger.warning(f"Row {index} skipped: {e}")

        # Commit changes for MySQL persistence
        conn.commit()
        print(f"Import Summary: {import_count} New, {skip_count} Duplicates Ignored.")

    except Exception as e:
        logger.exception(f"Fatal Importer Error: {e}")
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    for csv_file in glob.glob('data/*.csv'):
        run_import(csv_file)