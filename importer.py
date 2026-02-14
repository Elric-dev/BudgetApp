import pandas as pd
import mysql.connector
import os
import hashlib
import logging
import sys
from datetime import datetime
from dotenv import load_dotenv
import glob
import datetime



# 1. SETUP LOGGING
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Create handlers: Console for basic info, File for detailed errors
c_handler = logging.StreamHandler(sys.stdout)
f_handler = logging.FileHandler('logs/budget_importer.log')
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.DEBUG)

# Create formatters and add to handlers
log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
c_handler.setFormatter(log_format)
f_handler.setFormatter(log_format)

# Add handlers to the logger
logger.addHandler(c_handler)
logger.addHandler(f_handler)

load_dotenv()

def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASS'),
            database=os.getenv('DB_NAME')
        )
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Failed to connect to MySQL: {err}") #
        raise

def generate_transaction_hash(row):
    """Creates a unique fingerprint to prevent duplicate imports."""
    combined = f"{row['Date']}|{row['Description']}|{row['Cost']}|{row['Category']}"
    return hashlib.sha256(combined.encode()).hexdigest()

def get_metadata(cursor):
    """Fetches users and categories from DB."""
    cursor.execute("SELECT user_id, name FROM users")
    users = {row['name']: row['user_id'] for row in cursor.fetchall()}
    cursor.execute("SELECT id, name FROM categories")
    categories = {row['name']: row['id'] for row in cursor.fetchall()}
    return users, categories

def run_import(csv_file_path):
    """Imports transactions from a CSV file from Splitwise into the database."""
    print("Starting CSV import process...")
    tik = datetime.datetime.now()
    if not os.path.exists(csv_file_path):
        logger.error(f"CSV file not found: {csv_file_path}")
        return

    try:
        df = pd.read_csv(csv_file_path).dropna(how='all')
        logger.info(f"Loaded CSV: {csv_file_path} with {len(df)} rows.") #
    except Exception:
        logger.exception("Fatal error reading CSV file") # Captures stack trace
        return

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        user_map, cat_map = get_metadata(cursor)
        
        # Mapping IDs (Gus=0, Joules=1)
        gus_id, joules_id = 0, 1
        gus_col = [name for name, u_id in user_map.items() if u_id == gus_id][0]
        joules_col = [name for name, u_id in user_map.items() if u_id == joules_id][0]

        insert_sql = """
            INSERT IGNORE INTO transactions 
            (date, description, total_amount, user_id, category_id, payer_id, Gus_share, Joules_share, is_split, transaction_hash) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        import_count, skip_count = 0, 0

        for index, row in df.iterrows():
            # SKIP LOGIC: Payments and empty/footer rows
            if row['Category'] == 'Payment' or str(row['Description']).strip() == 'Total balance':
                logger.debug(f"Skipping row {index}: {row['Description']} (Category: {row['Category']})")
                continue

            try:
                # Data Validation
                cost = float(row['Cost'])
                clean_date = pd.to_datetime(row['Date']).strftime('%Y-%m-%d')
                cat_id = cat_map.get(row['Category'], cat_map.get('General', 39))

                # Share Calculation
                gus_val, joules_val = float(row[gus_col]), float(row[joules_col])
                payer_id = gus_id if gus_val > 0 else joules_id
                
                paid_by_gus = cost if payer_id == gus_id else 0
                paid_by_joules = cost if payer_id == joules_id else 0
                
                gus_share = abs(paid_by_gus - gus_val)
                joules_share = abs(paid_by_joules - joules_val)
                is_split = (gus_share > 0 and joules_share > 0)
                
                t_hash = generate_transaction_hash(row)

                cursor.execute(insert_sql, (
                    clean_date, row['Description'], cost, gus_id, cat_id, 
                    payer_id, gus_share, joules_share, is_split, t_hash
                ))
                
                if cursor.rowcount > 0:
                    import_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                logger.warning(f"Failed to process row {index} ({row['Description']}): {e}")

        conn.commit()
        logger.info(f"Import Finished. New: {import_count}, Skipped: {skip_count}")

    except Exception:
        logger.exception("A critical error occurred during the database operation")
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
            logger.info("Database connection closed.")

    # Log total execution time           
    tok = datetime.datetime.now()
    elapsed = (tok - tik).total_seconds()
    logger.info(f"Total execution time: {elapsed} seconds.")
    print(f"Total execution time: {elapsed} seconds, imported {import_count} transactions, skipped {skip_count}.")

if __name__ == "__main__":
    for csv_file in glob.glob('data/*.csv'):
        run_import(csv_file)