from flask import Flask, jsonify, render_template, request
import mysql.connector
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from datetime import datetime
from importer import generate_transaction_hash

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# --- DATABASE CONNECTION ---
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        database=os.getenv('DB_NAME')
    )

# --- PAGE ROUTES ---
@app.route('/')
def index():
    """Main Dashboard Page"""
    return render_template('index.html')

@app.route('/input')
def input_page():
    """Manual Input and CSV Upload Page"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM categories ORDER BY name ASC")
    cats = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('input.html', categories=cats)

@app.route('/cleanup')
def cleanup_page():
    """Data Hygiene / Categorization Page"""
    return render_template('cleanup.html')

# --- API ENDPOINTS ---

@app.route('/api/spending')
def api_spending():
    # Get filters from the URL
    user_id = request.args.get('user_id', 'all')
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Base query using UPPERCASE columns
    # We use a 1=1 trick to make appending 'AND' conditions easier
    query = """
        SELECT 
            c.parent_name, 
            SUM(t.total_amount) as total_household,
            SUM(t.Gus_share) as total_gus,
            SUM(t.Joules_share) as total_joules
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE 1=1
    """
    params = []

    # Only apply date filter if BOTH dates are provided
    if start_date and end_date:
        query += " AND t.date BETWEEN %s AND %s"
        params.extend([start_date, end_date])
    # If no dates are provided, we don't add any date constraint -> All Time Expenses

    query += " GROUP BY c.parent_name ORDER BY total_household DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(results)

@app.route('/api/food_drink_breakdown')
def food_drink_breakdown():
    start_date = request.args.get('start')
    end_date = request.args.get('end')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Query using your exact column names: Gus_share and Joules_share
    query = """
        SELECT 
            c.name as subcategory, 
            SUM(t.total_amount) as total_household,
            SUM(t.Gus_share) as total_gus,
            SUM(t.Joules_share) as total_joules
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE LOWER(c.parent_name) LIKE 'food % drink'
    """
    params = []
    
    if start_date and end_date:
        query += " AND t.date BETWEEN %s AND %s"
        params.extend([start_date, end_date])

    query += " GROUP BY c.name ORDER BY total_household DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify(results)
    

@app.route('/api/categories')
def get_all_categories():
    """Returns all available categories for dropdowns"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM categories ORDER BY name ASC")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(results)

@app.route('/api/uncategorized')
def get_uncategorized():
    """Returns transactions labeled as General/Uncategorized for the Cleanup tool"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT t.id, t.date, t.description, t.total_amount, c.name as current_category
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE c.name = 'General' OR c.parent_name = 'Uncategorized'
        ORDER BY t.date DESC
    """
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(results)

@app.route('/api/update_category', methods=['POST'])
def update_category():
    """Updates a transaction's category from the Cleanup UI"""
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "UPDATE transactions SET category_id = %s WHERE id = %s"
    cursor.execute(query, (data['category_id'], data['transaction_id']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})


from importer import generate_transaction_hash

@app.route('/api/expense/manual', methods=['POST'])
def save_manual_expense():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Create a mock row that matches EXACTLY what importer.py expects
        mock_row = {
            'Date': data.get('date'),
            'Description': data.get('description'),
            'Cost': data.get('amount'),         # importer.py uses 'Cost'
            'Category': data.get('category_name') # importer.get uses 'Category'
        }
        
        # This will now work without changing importer.py
        t_hash = generate_transaction_hash(mock_row)

        query = """
            INSERT INTO transactions 
            (date, description, total_amount, user_id, category_id, payer_id, 
             Gus_share, Joules_share, is_split, transaction_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        total = float(data['amount'])
        g_share = total * (float(data['split_gus']) / 100)
        j_share = total * (float(data['split_joules']) / 100)
        
        values = (
            data['date'], data['description'], total,
            0, # user_id Gus
            int(data['category_id']),
            0, # payer_id Gus
            g_share, j_share,
            1 if (g_share > 0 and j_share > 0) else 0,
            t_hash
        )
        
        cursor.execute(query, values)
        conn.commit()
        return jsonify({"status": "success"}), 201
    except Exception as e:
        print(f"!!! CRASH LOG: {e}") # This will show the KeyError in your terminal
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/upload_csv', methods=['POST'])
def upload_csv():
    """Handles CSV upload and triggers the processing logic"""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join('/tmp', filename)
    file.save(filepath)
    
    # Tip: You can import your 'run_import' function from your importer script here
    # from importer import run_import
    # run_import(filepath)
    
    return jsonify({"status": f"Successfully uploaded {filename}. Now run your importer script to process it."})

@app.route('/transactions')
def transactions_page():
    return render_template('transactions.html')

@app.route('/api/transactions')
def get_transactions_paginated():
    page = int(request.args.get('page', 1))
    offset = (page - 1) * 20
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Get total count
    cursor.execute("SELECT COUNT(*) as count FROM transactions")
    total_count = cursor.fetchone()['count']
    
    # Query: Added t.category_id to the SELECT statement
    query = """
        SELECT 
            t.id, 
            DATE_FORMAT(t.date, '%Y-%m-%d') as clean_date, 
            t.description, 
            t.total_amount, 
            t.Gus_share, 
            t.Joules_share, 
            t.category_id,
            c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        ORDER BY t.date DESC 
        LIMIT 20 OFFSET %s
    """
    cursor.execute(query, (offset,))
    rows = cursor.fetchall()
    
    cursor.close()
    conn.close()
    return jsonify({"transactions": rows, "total": total_count, "page": page})

@app.route('/api/transactions/update', methods=['POST'])
def update_transaction():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Aligned with your specific column names: Gus_share, Joules_share, total_amount
        query = """
            UPDATE transactions 
            SET category_id = %s, 
                description = %s, 
                total_amount = %s, 
                Gus_share = %s, 
                Joules_share = %s 
            WHERE id = %s
        """
        values = (
            int(data['category_id']),
            data['description'],
            float(data['total_amount']),
            float(data['Gus_share']),
            float(data['Joules_share']),
            int(data['id'])
        )
        
        cursor.execute(query, values)
        conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"!!! UPDATE ERROR: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

###### ------- Budget vs Income API Endpoint ------- ######
@app.route('/api/budget/save', methods=['POST'])
def save_budget():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Using ON DUPLICATE KEY UPDATE so users can just change the value
    query = """
        INSERT INTO budgets (user_id, category_name, target_amount)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE target_amount = VALUES(target_amount)
    """
    cursor.execute(query, (data['user_id'], data['category'], data['amount']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/budget/report')
def budget_report():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. Calculate Income (as before)
    cursor.execute("""
        SELECT SUM(monthly_gross * (1 - tax_rate/100)) as total_net 
        FROM income_streams WHERE user_id = %s
    """, (user_id,))
    net_income = float(cursor.fetchone()['total_net'] or 0.0)

    # 2. Get Categories and Averages
    # We use subqueries to get "Last Month" and "All Time Avg"
    query = """
        SELECT 
            c.parent_name as category,
            MAX(COALESCE(b.target_percent, 0)) as target_pct,
            
            -- Last Calendar Month
            (SELECT SUM(CASE WHEN %s = 0 THEN Gus_share ELSE Joules_share END)
             FROM transactions t2 JOIN categories c2 ON t2.category_id = c2.id
             WHERE c2.parent_name = c.parent_name 
             AND t2.date >= LAST_DAY(CURRENT_DATE - INTERVAL 2 MONTH) + INTERVAL 1 DAY
             AND t2.date <= LAST_DAY(CURRENT_DATE - INTERVAL 1 MONTH)) as last_month_actual,
             
            -- Historical Monthly Average
            (SELECT SUM(CASE WHEN %s = 0 THEN Gus_share ELSE Joules_share END) / 
                    NULLIF(TIMESTAMPDIFF(MONTH, MIN(t3.date), CURRENT_DATE) + 1, 0)
             FROM transactions t3 JOIN categories c3 ON t3.category_id = c3.id
             WHERE c3.parent_name = c.parent_name) as hist_avg
             
        FROM categories c
        LEFT JOIN budgets b ON c.parent_name = b.category_name AND b.user_id = %s
        GROUP BY c.parent_name
        ORDER BY c.parent_name ASC
    """
    cursor.execute(query, (user_id, user_id, user_id))
    rows = cursor.fetchall()
    
    report_data = []
    for row in rows:
        hist_avg = float(row['hist_avg'] or 0)
        report_data.append({
            "category": row['category'],
            "target_pct": float(row['target_pct']),
            "last_month": float(row['last_month_actual'] or 0),
            "hist_avg": hist_avg,
            "avg_salary_pct": (hist_avg / net_income * 100) if net_income > 0 else 0
        })

    cursor.close()
    conn.close()
    return jsonify({"report": report_data, "net_income": net_income})

@app.route('/budget')
def budget_page():
    return render_template('budget.html')

@app.route('/api/budget/get')
def get_budget():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT savings_pct, expenses_pct FROM budgets WHERE user_id = %s", (user_id,))
    budget = cursor.fetchone() or {"savings_pct": 0, "expenses_pct": 0}
    cursor.close()
    conn.close()
    return jsonify(budget)

@app.route('/api/income', methods=['GET', 'POST'])
def handle_income():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        data = request.json
        # Insert or Update income stream
        query = """
            INSERT INTO income_streams (user_id, source_name, monthly_gross, tax_rate)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                monthly_gross = VALUES(monthly_gross), 
                tax_rate = VALUES(tax_rate)
        """
        cursor.execute(query, (user_id, data['source'], data['gross'], data['tax']))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"status": "success"})

    # GET request: return all streams for the user
    cursor.execute("SELECT * FROM income_streams WHERE user_id = %s", (user_id,))
    streams = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(streams)

@app.route('/api/categories', methods=['GET'])
def get_categories():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True) # Return as dict for JSON
    
    try:
        # Alphabetical order by parent then name for a clean UI
        cursor.execute("SELECT id, name, parent_name FROM categories ORDER BY parent_name, name")
        categories = cursor.fetchall()
        return jsonify(categories)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()



@app.route('/api/budget/list')
def list_budget_categories():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Crucial: Join on name string, not ID
        query = """
            SELECT 
                c.id, 
                c.name, 
                COALESCE(b.target_amount, 0) as amount
            FROM categories c
            LEFT JOIN budgets b ON c.name = b.category_name AND b.user_id = %s
            ORDER BY c.name ASC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        return jsonify(rows)
    except Exception as e:
        print(f"Fetch Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/budget/save_items', methods=['POST'])
def save_budget_items():
    data = request.json
    user_id = data.get('user_id')
    items = data.get('items', [])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for item in items:
            # We use item['name'] because that is what JS is sending
            query = """
                INSERT INTO budgets (user_id, category_name, target_amount) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    target_amount = VALUES(target_amount)
            """
            cursor.execute(query, (user_id, item['name'], item['amount']))
        
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        conn.rollback()
        print(f"SQL SAVE ERROR: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/income/delete', methods=['POST'])
def delete_income():
    id = request.json.get('id')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM income_streams WHERE id = %s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

### Networth API Endpoint

@app.route('/networth')
def networth_page():
    return render_template('networth.html')

@app.route('/api/networth')
def get_networth():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM assets WHERE user_id = %s ORDER BY current_value DESC", (user_id,))
    assets = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(assets)

@app.route('/api/networth/update', methods=['POST'])
def update_asset():
    data = request.json
    # Debug print to see what is arriving in your terminal
    print(f"Incoming Asset Data: {data}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if data.get('id'):
            # UPDATE existing
            query = "UPDATE assets SET current_value = %s, asset_name = %s WHERE id = %s"
            cursor.execute(query, (data['value'], data['name'], data['id']))
        else:
            # INSERT new - ensure user_id is 0 or 1
            query = """
                INSERT INTO assets (user_id, asset_name, asset_type, current_value) 
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(query, (data['user_id'], data['name'], data['type'], data['value']))
        
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error saving asset: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/finance/snapshot', methods=['POST'])
def save_snapshot():
    data = request.json
    user_id = data.get('user_id', 0)
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Check if calculation logic is sound
    cursor.execute("SELECT SUM(current_value) as total FROM assets WHERE user_id = %s", (user_id,))
    nw = cursor.fetchone()['total'] or 0
    
    cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as total_net FROM income_streams WHERE user_id = %s", (user_id,))
    inc = cursor.fetchone()['total_net'] or 0
    
    # The 'ON DUPLICATE KEY UPDATE' ensures you only have 1 entry per day
    cursor.execute("""
        INSERT INTO net_worth_history (user_id, total_value, snapshot_date) 
        VALUES (%s, %s, CURDATE()) 
        ON DUPLICATE KEY UPDATE total_value = VALUES(total_value)
    """, (user_id, nw))
    
    cursor.execute("""
        INSERT INTO income_history (user_id, total_net_income, snapshot_date) 
        VALUES (%s, %s, CURDATE()) 
        ON DUPLICATE KEY UPDATE total_net_income = VALUES(total_net_income)
    """, (user_id, inc))
    
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/finance/snapshot', methods=['POST'])
def take_snapshot():
    data = request.json
    user_id = int(data.get('user_id', 0))
    other_user_id = 1 if user_id == 0 else 0
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # 1. CALCULATE CURRENT VALUES FOR ACTIVE USER
        # Get Net Worth (sum of assets)
        cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
        current_nw = cursor.fetchone()['nw'] or 0
        
        # Get Net Income (sum of income streams)
        cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
        current_inc = cursor.fetchone()['inc'] or 0

        # 2. SAVE ACTIVE USER DATA (Current Date)
        # Net Worth History
        cursor.execute("""
            INSERT INTO net_worth_history (user_id, total_value, snapshot_date) 
            VALUES (%s, %s, CURDATE()) 
            ON DUPLICATE KEY UPDATE total_value = VALUES(total_value)
        """, (user_id, current_nw))
        
        # Income History
        cursor.execute("""
            INSERT INTO income_history (user_id, total_net_income, snapshot_date) 
            VALUES (%s, %s, CURDATE()) 
            ON DUPLICATE KEY UPDATE total_net_income = VALUES(total_net_income)
        """, (user_id, current_inc))

        # 3. SYNC TRIGGER: "GHOST" THE OTHER USER'S DATA
        # This ensures the Household view (User 2) always has values for both parties on this date.
        
        # Sync Net Worth: Grab other user's latest value and copy it to TODAY
        cursor.execute(f"""
            INSERT INTO net_worth_history (user_id, total_value, snapshot_date)
            SELECT user_id, total_value, CURDATE()
            FROM net_worth_history 
            WHERE user_id = {other_user_id} 
            ORDER BY snapshot_date DESC LIMIT 1
            ON DUPLICATE KEY UPDATE total_value = total_value
        """)
        
        # Sync Income: Grab other user's latest value and copy it to TODAY
        cursor.execute(f"""
            INSERT INTO income_history (user_id, total_net_income, snapshot_date)
            SELECT user_id, total_net_income, CURDATE()
            FROM income_history 
            WHERE user_id = {other_user_id} 
            ORDER BY snapshot_date DESC LIMIT 1
            ON DUPLICATE KEY UPDATE total_net_income = total_net_income
        """)

        conn.commit()
        return jsonify({"status": "success", "message": "Snapshot synchronized for household."})

    except Exception as e:
        conn.rollback()
        print(f"Snapshot Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/finance/history')
def finance_history():
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if user_id == 2:
        # HOUSEHOLD NORMALIZATION
        # We create a unique list of dates first, then sum everyone's data for that date
        query = """
            WITH DateRange AS (
                SELECT DISTINCT snapshot_date FROM net_worth_history
                UNION
                SELECT DISTINCT snapshot_date FROM income_history
            )
            SELECT 
                d.snapshot_date,
                (SELECT SUM(total_value) FROM net_worth_history WHERE snapshot_date = d.snapshot_date) as nw_total,
                (SELECT SUM(total_net_income) FROM income_history WHERE snapshot_date = d.snapshot_date) as inc_total
            FROM DateRange d
            ORDER BY d.snapshot_date ASC
        """
        cursor.execute(query)
    else:
        # INDIVIDUAL: Standard filtering
        query = """
            SELECT n.snapshot_date, n.total_value as nw_total, 
                   COALESCE(i.total_net_income, 0) as inc_total
            FROM net_worth_history n
            LEFT JOIN income_history i ON n.snapshot_date = i.snapshot_date 
                                       AND n.user_id = i.user_id
            WHERE n.user_id = %s
            ORDER BY n.snapshot_date ASC
        """
        cursor.execute(query, (user_id,))
    
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify({
        "dates": [r['snapshot_date'].strftime('%d %b') for r in rows],
        "nw_values": [float(r['nw_total'] or 0) for r in rows],
        "inc_values": [float(r['inc_total'] or 0) for r in rows]
    })

@app.route('/api/networth/delete', methods=['POST'])
def delete_asset():
    data = request.json
    asset_id = data.get('id')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM assets WHERE id = %s", (asset_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "deleted"})

@app.route('/api/networth/edit-name', methods=['POST'])
def edit_asset_name():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE assets SET asset_name = %s WHERE id = %s", (data['name'], data['id']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "updated"})


###### ------- Final Dashboard Route ------- ######
@app.route('/api/dashboard/summary')
def dashboard_summary():
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        if user_id == 2:
            # HOUSEHOLD: No WHERE clause on user_id
            cursor.execute("SELECT SUM(current_value) as nw FROM assets")
            nw = cursor.fetchone()['nw'] or 0
            
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams")
            inc = cursor.fetchone()['inc'] or 0
            
            cursor.execute("SELECT SUM(Gus_share + Joules_share) as spent FROM transactions WHERE MONTH(date) = MONTH(CURDATE())")
            spent = cursor.fetchone()['spent'] or 0
        else:
            # INDIVIDUAL: Filter by user_id
            cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
            nw = cursor.fetchone()['nw'] or 0
            
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
            inc = cursor.fetchone()['inc'] or 0
            
            share_col = "Gus_share" if user_id == 0 else "Joules_share"
            cursor.execute(f"SELECT SUM({share_col}) as spent FROM transactions WHERE MONTH(date) = MONTH(CURDATE())")
            spent = cursor.fetchone()['spent'] or 0

        return jsonify({
            "net_worth": float(nw),
            "income": float(inc),
            "spent": float(spent),
            "savings": float(inc - spent)
        })
    except Exception as e:
        print(f"Summary Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/spending/category')
def spending_by_category():
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Define what we are summing
    if user_id == 2:
        share_calc = "(t.Gus_share + t.Joules_share)"
    else:
        share_calc = "t.Gus_share" if user_id == 0 else "t.Joules_share"
    
    try:
        query = f"""
            SELECT c.name as category_name, SUM({share_calc}) as total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE t.date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY c.name
            HAVING total > 0
            ORDER BY total DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return jsonify({
            "labels": [r['category_name'] for r in rows],
            "values": [float(r['total']) for r in rows]
        })
    except Exception as e:
        print(f"Category Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()





##### RUNNING SCRRIPT #####

if __name__ == '__main__':
    # Running on 5001 to avoid macOS AirPlay conflict on 5000
    app.run(debug=True, host='0.0.0.0', port=5001)