import os
import hashlib
import logging
import sys
from datetime import datetime
from splitwise import Splitwise
from splitwise.expense import Expense
from splitwise.user import ExpenseUser
import mysql.connector
from config import Config

# SETUP LOGGING
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
c_handler = logging.StreamHandler(sys.stdout)
log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
c_handler.setFormatter(log_format)
logger.addHandler(c_handler)

# Splitwise User IDs (Cached from API check)
GUS_SW_ID = 53594144
GIULIA_SW_ID = 51571446
HOUSEHOLD_GROUP_ID = 71205816 # Kebab Gs group

def get_db_connection():
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
    """Mirror hashing from importer.py for deduplication consistency."""
    combined = f"{row['Date']}|{row['Description']}|{row['Cost']}|{row['Category']}"
    return hashlib.sha256(combined.encode()).hexdigest()

def get_metadata(cursor):
    cursor.execute("SELECT user_id, name FROM users")
    users = {row['name']: row['user_id'] for row in cursor.fetchall()}
    cursor.execute("SELECT id, name FROM categories")
    categories = {row['name']: row['id'] for row in cursor.fetchall()}
    return users, categories

def push_expense_to_splitwise(description, cost, date_str=None):
    """Creates a 50/50 split expense on Splitwise within the Kebab Gs Group."""
    if not Config.SPLITWISE_API_KEY:
        return False, "API Key missing"

    s_obj = Splitwise(Config.SPLITWISE_CONSUMER_KEY, Config.SPLITWISE_CONSUMER_SECRET, api_key=Config.SPLITWISE_API_KEY)
    
    expense = Expense()
    expense.setCost(str(cost))
    expense.setDescription(description)
    expense.setGroupId(HOUSEHOLD_GROUP_ID)
    if date_str:
        expense.setDate(date_str)
    
    # User 1: Gus (Payer)
    user1 = ExpenseUser()
    user1.setId(GUS_SW_ID)
    user1.setPaidShare(str(cost)) # Gus pays full amount
    user1.setOwedShare(str(float(cost) / 2)) # Gus owes half
    
    # User 2: Giulia
    user2 = ExpenseUser()
    user2.setId(GIULIA_SW_ID)
    user2.setPaidShare('0.00')
    user2.setOwedShare(str(float(cost) / 2)) # Giulia owes half
    
    expense.addUser(user1)
    expense.addUser(user2)
    
    try:
        created_expense, errors = s_obj.createExpense(expense)
        if errors:
            return False, errors
        return True, created_expense.getId()
    except Exception as e:
        logger.error(f"Failed to push to Splitwise: {e}")
        return False, str(e)

def run_splitwise_sync():
    logger.info("--- Starting Splitwise API Sync ---")
    
    if not Config.SPLITWISE_API_KEY:
        logger.error("SPLITWISE_API_KEY not found in config.")
        return False

    s_obj = Splitwise(Config.SPLITWISE_CONSUMER_KEY, Config.SPLITWISE_CONSUMER_SECRET, api_key=Config.SPLITWISE_API_KEY)
    
    try:
        # Get current user to identify who 'we' are
        sw_current_user = s_obj.getCurrentUser()
        logger.info(f"Connected as: {sw_current_user.getFirstName()} {sw_current_user.getLastName()}")
        
        # Fetch recent expenses (limit 50 for now)
        expenses = s_obj.getExpenses(limit=50)
    except Exception as e:
        logger.error(f"Splitwise API Error: {e}")
        return False

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        user_map, cat_map = get_metadata(cursor)
        
        # Map Gus and Joules IDs (assumed 0 and 1 from schema)
        gus_id = 0
        joules_id = 1
        
        insert_sql = """
            INSERT IGNORE INTO transactions 
            (date, description, total_amount, user_id, category_id, payer_id, Gus_share, Joules_share, is_split, transaction_hash) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        import_count, skip_count = 0, 0
        
        for exp in expenses:
            if exp.getDeletedAt(): continue
            
            # Extract basic info
            description = exp.getDescription()
            cost = float(exp.getCost())
            # Date format: 2026-02-22T14:30:00Z -> 2026-02-22
            raw_date = exp.getDate()
            clean_date = raw_date.split('T')[0]
            category_name = exp.getCategory().getName()
            cat_id = cat_map.get(category_name, cat_map.get('General', 39))
            
            # Handle shares
            gus_share = 0
            joules_share = 0
            payer_id = gus_id # Default
            
            users = exp.getUsers()
            for u in users:
                u_first_name = u.getFirstName() or ""
                u_last_name = u.getLastName() or ""
                u_full_name = f"{u_first_name} {u_last_name}".lower().strip()
                u_share = float(u.getOwedShare())
                
                if 'gus' in u_full_name:
                    gus_share = u_share
                elif 'joules' in u_full_name or 'giulia' in u_full_name or 'sautto' in u_full_name:
                    joules_share = u_share
                
                # Determine who paid
                if float(u.getPaidShare()) > 0:
                    if 'gus' in u_full_name:
                        payer_id = gus_id
                    elif 'joules' in u_full_name or 'giulia' in u_full_name or 'sautto' in u_full_name:
                        payer_id = joules_id

            is_split = 1 if (gus_share > 0 and joules_share > 0) else 0
            
            # Create a mock row for hash generation consistent with CSV importer
            mock_row = {
                'Date': clean_date,
                'Description': description,
                'Cost': cost,
                'Category': category_name
            }
            t_hash = generate_transaction_hash(mock_row)

            try:
                cursor.execute(insert_sql, (
                    clean_date, description, cost, gus_id, cat_id, 
                    payer_id, gus_share, joules_share, is_split, t_hash
                ))
                
                if cursor.rowcount > 0:
                    import_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                logger.warning(f"Failed to insert expense {description}: {e}")

        conn.commit()
        logger.info(f"Sync Summary: {import_count} New, {skip_count} Duplicates/Existing Ignored.")
        return True

    except Exception as e:
        logger.exception(f"Fatal Sync Error: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    run_splitwise_sync()
