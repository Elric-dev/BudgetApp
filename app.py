from flask import Flask, jsonify, render_template, request
import mysql.connector
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

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

@app.route('/api/add_expense', methods=['POST'])
def add_expense():
    """Saves a manually entered expense"""
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Simple manual logic: assume 50/50 split for quick entry
    amount = float(data['amount'])
    share = amount / 2
    
    sql = """INSERT INTO transactions 
             (date, description, total_amount, user_id, category_id, payer_id, gus_share, gf_share, is_split) 
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    
    # Hardcoding user/payer as 0 (Gus) for simple manual entries
    cursor.execute(sql, (data['date'], data['description'], amount, 0, data['category_id'], 0, share, share, True))
    
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

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
    
    # Query: Added Category Name and Date Formatting
    query = """
        SELECT 
            t.id, 
            DATE_FORMAT(t.date, '%Y-%m-%d') as clean_date, 
            t.description, 
            t.total_amount, 
            t.Gus_share, 
            t.Joules_share, 
            c.name as category_name
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
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
    # Using your specific capitalization for Gus_share and Joules_share
    query = """
        UPDATE transactions 
        SET description = %s, total_amount = %s, Gus_share = %s, Joules_share = %s 
        WHERE id = %s
    """
    cursor.execute(query, (data['description'], data['amount'], data['gus'], data['joules'], data['id']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "success"})

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
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Calculate Income Breakdown (Gross, Tax, Net)
        cursor.execute("""
            SELECT 
                SUM(monthly_gross) as total_gross,
                SUM(monthly_gross * (tax_rate/100)) as total_tax,
                SUM(monthly_gross * (1 - tax_rate/100)) as total_net 
            FROM income_streams 
            WHERE user_id = %s
        """, (user_id,))
        
        income_data = cursor.fetchone()
        gross = float(income_data['total_gross'] or 0.0)
        tax = float(income_data['total_tax'] or 0.0)
        net = float(income_data['total_net'] or 0.0)

        # 2. Get All Categories + Budget % + Current Month Actuals
        # We use a LEFT JOIN on transactions for the CURRENT MONTH only
        query = """
            SELECT 
                c.parent_name as category,
                MAX(COALESCE(b.target_percent, 0)) as target_pct,
                SUM(CASE 
                    WHEN %s = 0 THEN COALESCE(t.Gus_share, 0) 
                    ELSE COALESCE(t.Joules_share, 0) 
                END) as actual
            FROM categories c
            LEFT JOIN budgets b ON c.parent_name = b.category_name AND b.user_id = %s
            LEFT JOIN transactions t ON c.id = t.category_id 
                 AND t.date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
            GROUP BY c.parent_name
            ORDER BY c.parent_name ASC
        """
        cursor.execute(query, (user_id, user_id))
        rows = cursor.fetchall()
        
        report_data = []
        for row in rows:
            t_pct = float(row['target_pct'] or 0)
            actual = float(row['actual'] or 0)
            # Calculate the Euro target based on the combined Net Income
            t_euro = (t_pct / 100) * net
            
            report_data.append({
                "category": row['category'],
                "target_pct": t_pct,
                "target_euro": t_euro,
                "actual": actual
            })

        cursor.close()
        conn.close()
        
        return jsonify({
            "report": report_data,
            "net_income": net,
            "gross_income": gross,
            "tax_amount": tax
        })
        
    except Exception as e:
        print(f"SQL Error in budget_report: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/budget')
def budget_page():
    return render_template('budget.html')

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







if __name__ == '__main__':
    # Running on 5001 to avoid macOS AirPlay conflict on 5000
    app.run(debug=True, host='0.0.0.0', port=5001)