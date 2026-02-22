import mysql.connector
import hashlib
from config import Config
from datetime import date

def generate_hash(date_str, description, amount, category_id):
    combined = f"{date_str}|{description}|{amount}|{category_id}"
    return hashlib.sha256(combined.encode()).hexdigest()

def backfill_history():
    # Category mapping from DB
    categories = {
        'Rent': {'id': 13, 'amount': 600.00},
        'Electricity': {'id': 2, 'amount': 60.00},
        'Gas': {'id': 4, 'amount': 60.00},
        'TV/Phone/Internet': {'id': 6, 'amount': 20.00},
        'Bus/train': {'id': 19, 'amount': 35.00}
    }
    
    user_id = 0 # Gus
    
    conn = mysql.connector.connect(
        host=Config.DB_HOST,
        user=Config.DB_USER,
        password=Config.DB_PASS,
        database=Config.DB_NAME
    )
    cursor = conn.cursor()
    
    # Generate months for 2024 and 2025
    months = []
    for year in [2024, 2025]:
        for month in range(1, 13):
            months.append(date(year, month, 1))
            
    insert_sql = """
        INSERT IGNORE INTO transactions 
        (date, description, total_amount, user_id, category_id, payer_id, Gus_share, Joules_share, is_split, transaction_hash) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    total_inserted = 0
    for d in months:
        date_str = d.strftime('%Y-%m-%d')
        for name, info in categories.items():
            desc = f"Historical Baseline - {name}"
            amount = info['amount']
            cat_id = info['id']
            t_hash = generate_hash(date_str, desc, amount, cat_id)
            
            cursor.execute(insert_sql, (
                date_str, desc, amount, user_id, cat_id, 
                user_id, amount, 0.00, 0, t_hash
            ))
            if cursor.rowcount > 0:
                total_inserted += 1
                
    conn.commit()
    print(f"Backfill Complete: Inserted {total_inserted} historical records.")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    backfill_history()
