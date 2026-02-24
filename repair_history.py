import mysql.connector
import sys
import logging
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def repair_data():
    logger.info("Starting historical data repair (LOF - Last Observation Carried Forward)...")
    
    try:
        conn = mysql.connector.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            database=Config.DB_NAME
        )
        cursor = conn.cursor(dictionary=True, buffered=True)
    except mysql.connector.Error as err:
        logger.error(f"Error connecting to Database: {err}")
        sys.exit(1)

    try:
        # 1. Get all unique snapshot dates across both tables
        cursor.execute("""
            SELECT DISTINCT snapshot_date FROM (
                SELECT snapshot_date FROM net_worth_history
                UNION
                SELECT snapshot_date FROM income_history
            ) AS all_dates ORDER BY snapshot_date ASC
        """)
        all_dates = [row['snapshot_date'] for row in cursor.fetchall()]
        
        if not all_dates:
            logger.info("No history data found to repair.")
            return

        # 2. For each user and each date, ensure a record exists
        users = [0, 1] # Gus and Joules
        
        for user_id in users:
            logger.info(f"Repairing data for User {user_id}...")
            
            # Net Worth
            last_nw = 0
            for d in all_dates:
                cursor.execute("SELECT total_value FROM net_worth_history WHERE user_id = %s AND snapshot_date = %s", (user_id, d))
                row = cursor.fetchone()
                if row:
                    last_nw = row['total_value']
                else:
                    if last_nw > 0:
                        logger.info(f"  [NW] Filling missing {d} for User {user_id} with {last_nw}")
                        cursor.execute("INSERT INTO net_worth_history (user_id, snapshot_date, total_value) VALUES (%s, %s, %s)", (user_id, d, last_nw))
            
            # Income
            last_inc = 0
            for d in all_dates:
                cursor.execute("SELECT total_net_income FROM income_history WHERE user_id = %s AND snapshot_date = %s", (user_id, d))
                row = cursor.fetchone()
                if row:
                    last_inc = row['total_net_income']
                else:
                    if last_inc > 0:
                        logger.info(f"  [INC] Filling missing {d} for User {user_id} with {last_inc}")
                        cursor.execute("INSERT INTO income_history (user_id, snapshot_date, total_net_income) VALUES (%s, %s, %s)", (user_id, d, last_inc))
        
        conn.commit()
        logger.info("Data repair complete!")
        
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    repair_data()
