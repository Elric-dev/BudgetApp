from flask import Flask, jsonify, render_template, request
import mysql.connector
import os
import hashlib
import time
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from datetime import datetime
from importer import generate_transaction_hash

# Load environment variables
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

# --- HELPER UTILITIES ---
def get_date_filter(period):
    """Returns SQL WHERE clause fragment and params for time frames."""
    if period == 'last_month':
        return "AND t.date >= DATE_SUB(DATE_FORMAT(NOW(), '%Y-%m-01'), INTERVAL 1 MONTH) AND t.date < DATE_FORMAT(NOW(), '%Y-%m-01')", []
    elif period == 'last_3':
        return "AND t.date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)", []
    elif period == 'lifetime':
        return "", []
    else:
        # Default: Current Month (Feb 2026)
        return "AND MONTH(t.date) = MONTH(CURRENT_DATE()) AND YEAR(t.date) = YEAR(CURRENT_DATE())", []

# ==========================================
# 1. DASHBOARD PAGE & APIs
# ==========================================

@app.route('/')
def index():
    """Main Executive Dashboard View"""
    return render_template('index.html')

@app.route('/api/dashboard/summary')
def dashboard_summary():
    """Top-level KPIs for Net Worth, Income, Spending, and Savings"""
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        if user_id == 2: # Household
            cursor.execute("SELECT SUM(current_value) as nw FROM assets")
            nw = cursor.fetchone()['nw'] or 0
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams")
            inc = cursor.fetchone()['inc'] or 0
            cursor.execute("SELECT SUM(Gus_share + Joules_share) as spent FROM transactions WHERE MONTH(date) = MONTH(CURDATE())")
            spent = cursor.fetchone()['spent'] or 0
        else: # Gus (0) or Joules (1)
            cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
            nw = cursor.fetchone()['nw'] or 0
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
            inc = cursor.fetchone()['inc'] or 0
            share_col = "Gus_share" if user_id == 0 else "Joules_share"
            cursor.execute(f"SELECT SUM({share_col}) as spent FROM transactions WHERE MONTH(date) = MONTH(CURDATE())")
            spent = cursor.fetchone()['spent'] or 0

        return jsonify({
            "net_worth": float(nw), "income": float(inc),
            "spent": float(spent), "savings": float(inc - spent)
        })
    finally:
        cursor.close()
        conn.close()

@app.route('/api/spending/parent-categories', methods=['GET'])
def get_parent_spending():
    user_id = int(request.args.get('user_id', 0))
    period = request.args.get('period', 'current')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    date_clause, _ = get_date_filter(period)
    
    # REVISED LOGIC: Filter by where the person actually has a financial share
    if user_id == 2: # Household
        share_calc = "SUM(t.Gus_share + t.Joules_share)"
        user_filter = "(t.Gus_share > 0 OR t.Joules_share > 0)"
    elif user_id == 0: # Gus
        share_calc = "SUM(t.Gus_share)"
        user_filter = "t.Gus_share > 0"
    else: # Joules (user_id 1)
        share_calc = "SUM(t.Joules_share)"
        user_filter = "t.Joules_share > 0"
    
    try:
        query = f"""
            SELECT COALESCE(NULLIF(c.parent_name, ''), 'Other') as parent_class, 
                   {share_calc} as total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE {user_filter} {date_clause}
            GROUP BY parent_class ORDER BY total DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return jsonify({"labels": [r['parent_class'] for r in rows], "values": [float(r['total']) for r in rows]})
    finally:
        cursor.close()
        conn.close()

@app.route('/api/spending/sub-categories', methods=['GET'])
def get_sub_spending():
    user_id = int(request.args.get('user_id', 0))
    parent_name = request.args.get('parent_name')
    period = request.args.get('period', 'current')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    date_clause, _ = get_date_filter(period)
    
    # DYNAMIC COLUMN SELECTION
    if user_id == 2:
        share_calc = "SUM(t.Gus_share + t.Joules_share)"
        user_filter = "t.user_id IN (0, 1)"
    elif user_id == 0:
        share_calc = "SUM(t.Gus_share)"
        user_filter = "t.user_id = 0"
    else:
        share_calc = "SUM(t.Joules_share)"
        user_filter = "t.user_id = 1"

    try:
        query = f"""
            SELECT c.name as sub_category, {share_calc} as total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE {user_filter} AND c.parent_name = %s {date_clause}
            GROUP BY c.name ORDER BY total DESC
        """
        cursor.execute(query, (parent_name,))
        rows = cursor.fetchall()
        return jsonify({"labels": [r['sub_category'] for r in rows], "values": [float(r['total']) for r in rows]})
    finally:
        cursor.close()
        conn.close()

# ==========================================
# 2. TRANSACTIONS & CLEANUP PAGES
# ==========================================

@app.route('/transactions')
def transactions_page():
    return render_template('transactions.html')

@app.route('/api/transactions')
def get_transactions_paginated():
    page = int(request.args.get('page', 1))
    offset = (page - 1) * 20
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as count FROM transactions")
    total_count = cursor.fetchone()['count']
    query = """
        SELECT t.id, DATE_FORMAT(t.date, '%Y-%m-%d') as clean_date, t.description, 
               t.total_amount, t.Gus_share, t.Joules_share, t.category_id, c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        ORDER BY t.date DESC LIMIT 20 OFFSET %s
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
        query = """
            UPDATE transactions SET category_id = %s, description = %s, total_amount = %s, 
            Gus_share = %s, Joules_share = %s WHERE id = %s
        """
        cursor.execute(query, (int(data['category_id']), data['description'], float(data['total_amount']),
                               float(data['Gus_share']), float(data['Joules_share']), int(data['id'])))
        conn.commit()
        return jsonify({"status": "success"})
    finally:
        cursor.close()
        conn.close()

@app.route('/cleanup')
def cleanup_page():
    return render_template('cleanup.html')

@app.route('/api/uncategorized')
def get_uncategorized():
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

# ==========================================
# 3. INPUT (MANUAL & CSV) PAGES
# ==========================================

@app.route('/input')
def input_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM categories ORDER BY name ASC")
    cats = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('input.html', categories=cats)

@app.route('/api/expense/manual', methods=['POST'])
def save_manual_expense():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        mock_row = {'Date': data.get('date'), 'Description': data.get('description'),
                    'Cost': data.get('amount'), 'Category': data.get('category_name')}
        t_hash = generate_transaction_hash(mock_row)
        query = """
            INSERT INTO transactions (date, description, total_amount, user_id, category_id, payer_id, 
            Gus_share, Joules_share, is_split, transaction_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        total = float(data['amount'])
        g_share = total * (float(data['split_gus']) / 100)
        j_share = total * (float(data['split_joules']) / 100)
        cursor.execute(query, (data['date'], data['description'], total, 0, int(data['category_id']), 
                               0, g_share, j_share, 1 if (g_share > 0 and j_share > 0) else 0, t_hash))
        conn.commit()
        return jsonify({"status": "success"}), 201
    finally:
        cursor.close()
        conn.close()

@app.route('/api/upload_csv', methods=['POST'])
def upload_csv():
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    filename = secure_filename(file.filename)
    filepath = os.path.join('/tmp', filename)
    file.save(filepath)
    return jsonify({"status": f"Uploaded {filename}. Run importer script manually to process."})

# ==========================================
# 4. BUDGET & INCOME PAGES
# ==========================================

@app.route('/budget')
def budget_page():
    return render_template('budget.html')

@app.route('/api/budget/list')
def list_budget_categories():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = """
        SELECT c.id, c.name, COALESCE(b.target_amount, 0) as amount
        FROM categories c
        LEFT JOIN budgets b ON c.name = b.category_name AND b.user_id = %s
        ORDER BY c.name ASC
    """
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(rows)

@app.route('/api/budget/save_items', methods=['POST'])
def save_budget_items():
    data, conn = request.json, get_db_connection()
    cursor = conn.cursor()
    try:
        for item in data.get('items', []):
            query = """
                INSERT INTO budgets (user_id, category_name, target_amount) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE target_amount = VALUES(target_amount)
            """
            cursor.execute(query, (data['user_id'], item['name'], item['amount']))
        conn.commit()
        return jsonify({"status": "success"})
    finally:
        cursor.close()
        conn.close()

@app.route('/api/income', methods=['GET', 'POST'])
def handle_income():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        data = request.json
        query = """
            INSERT INTO income_streams (user_id, source_name, monthly_gross, tax_rate) VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE monthly_gross = VALUES(monthly_gross), tax_rate = VALUES(tax_rate)
        """
        cursor.execute(query, (user_id, data['source'], data['gross'], data['tax']))
        conn.commit()
        return jsonify({"status": "success"})
    cursor.execute("SELECT * FROM income_streams WHERE user_id = %s", (user_id,))
    streams = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(streams)

# ==========================================
# 5. NET WORTH & ASSETS PAGES
# ==========================================

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
    data, conn = request.json, get_db_connection()
    cursor = conn.cursor()
    try:
        if data.get('id'):
            cursor.execute("UPDATE assets SET current_value = %s, asset_name = %s WHERE id = %s", (data['value'], data['name'], data['id']))
        else:
            cursor.execute("INSERT INTO assets (user_id, asset_name, asset_type, current_value) VALUES (%s, %s, %s, %s)",
                           (data['user_id'], data['name'], data['type'], data['value']))
        conn.commit()
        return jsonify({"status": "success"})
    finally:
        cursor.close()
        conn.close()

@app.route('/api/finance/history')
def finance_history():
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if user_id == 2:
        query = """
            WITH DateRange AS (SELECT DISTINCT snapshot_date FROM net_worth_history UNION SELECT DISTINCT snapshot_date FROM income_history)
            SELECT d.snapshot_date,
                   (SELECT SUM(total_value) FROM net_worth_history WHERE snapshot_date = d.snapshot_date) as nw_total,
                   (SELECT SUM(total_net_income) FROM income_history WHERE snapshot_date = d.snapshot_date) as inc_total
            FROM DateRange d ORDER BY d.snapshot_date ASC
        """
        cursor.execute(query)
    else:
        query = """
            SELECT n.snapshot_date, n.total_value as nw_total, COALESCE(i.total_net_income, 0) as inc_total
            FROM net_worth_history n LEFT JOIN income_history i ON n.snapshot_date = i.snapshot_date AND n.user_id = i.user_id
            WHERE n.user_id = %s ORDER BY n.snapshot_date ASC
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

# ==========================================
# 6. SHARED CORE APIS (Categories)
# ==========================================

@app.route('/api/categories')
def get_categories():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, parent_name FROM categories ORDER BY parent_name, name")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)