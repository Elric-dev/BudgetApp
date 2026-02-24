import mysql.connector
import hashlib
import sys
from config import Config
from datetime import date

def generate_hash(date_str, description, amount, category_id):
    combined = f"{date_str}|{description}|{amount}|{category_id}"
    return hashlib.sha256(combined.encode()).hexdigest()

def backfill_history():
    print("🚀 Initializing BudgetArchitect Historical Data...")
    
    # Category mapping from DB (Matched to expanded schema.sql)
    categories = {
        'Rent': {'id': 13, 'amount': 600.00},
        'Electricity': {'id': 2, 'amount': 65.00},
        'Gas': {'id': 4, 'amount': 45.00},
        'TV/Phone/Internet': {'id': 6, 'amount': 25.00},
        'Bus/train': {'id': 19, 'amount': 40.00}
    }
    
    try:
        conn = mysql.connector.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            database=Config.DB_NAME
        )
        cursor = conn.cursor()
    except mysql.connector.Error as err:
        print(f"❌ Error connecting to Database: {err}")
        print("Tip: Check your .env file and ensure MySQL is running.")
        sys.exit(1)

    # 1. SETUP INITIAL INCOME STREAMS
    print("📈 Setting up default income streams...")
    income_sql = """
        INSERT IGNORE INTO income_streams (user_id, source_name, monthly_gross, tax_rate) 
        VALUES (%s, %s, %s, %s)
    """
    income_data = [
        (0, 'Main Salary', 3500.00, 20.0),
        (1, 'Secondary Income', 2800.00, 15.0)
    ]
    cursor.executemany(income_sql, income_data)

    # 2. SETUP INITIAL ASSETS
    print("💎 Setting up starting assets...")
    assets_sql = """
        INSERT IGNORE INTO assets (user_id, asset_name, asset_type, current_value) 
        VALUES (%s, %s, %s, %s)
    """
    asset_data = [
        (0, 'Savings Account', 'Savings', 12000.00),
        (0, 'Brokerage', 'Investment', 5000.00),
        (1, 'Personal Savings', 'Savings', 8500.00),
        (1, 'Crypto Wallet', 'Investment', 1200.00)
    ]
    cursor.executemany(assets_sql, asset_data)

    # 3. BACKFILL HISTORICAL DATA (2024 - Present)
    print("🕰️  Generating historical transaction logs and snapshots (2024-2025)...")
    months = []
    for year in [2024, 2025]:
        for month in range(1, 13):
            months.append(date(year, month, 1))
            
    insert_trans_sql = """
        INSERT IGNORE INTO transactions 
        (date, description, total_amount, user_id, category_id, payer_id, Gus_share, Joules_share, is_split, transaction_hash) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    nw_hist_sql = """
        INSERT INTO net_worth_history (user_id, snapshot_date, total_value) 
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE total_value = VALUES(total_value)
    """
    inc_hist_sql = """
        INSERT INTO income_history (user_id, snapshot_date, total_net_income) 
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE total_net_income = VALUES(total_net_income)
    """
    
    total_inserted = 0
    gus_nw = 10000.00
    joules_nw = 6000.00
    gus_inc = 2800.00 
    joules_inc = 2380.00
    
    for idx, d in enumerate(months):
        date_str = d.strftime('%Y-%m-%d')
        
        # Transactions for both users to create immediate chart data
        for u_id in [0, 1]:
            for name, info in categories.items():
                desc = f"Historical - {name}"
                amount = info['amount']
                cat_id = info['id']
                t_hash = generate_hash(date_str, desc, amount, cat_id + u_id)
                
                cursor.execute(insert_trans_sql, (
                    date_str, desc, amount, u_id, cat_id, 
                    u_id, amount, 0.00, 0, t_hash
                ))
                if cursor.rowcount > 0:
                    total_inserted += 1
        
        # Snapshots: Simulate steady growth for the wealth trend charts
        cursor.execute(nw_hist_sql, (0, date_str, gus_nw + (idx * 450)))
        cursor.execute(nw_hist_sql, (1, date_str, joules_nw + (idx * 320)))
        
        cursor.execute(inc_hist_sql, (0, date_str, gus_inc))
        cursor.execute(inc_hist_sql, (1, date_str, joules_inc))
                
    conn.commit()
    print(f"✨ Setup Complete!")
    print(f"✅ Initialized Assets & Income Streams.")
    print(f"✅ Backfilled {total_inserted} transactions.")
    print(f"✅ Generated {len(months)*2} growth snapshots.")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    backfill_history()
