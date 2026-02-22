import os
import hashlib
import time
import traceback
import logging
from datetime import datetime
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import mysql.connector

from config import Config
from importer import run_import, generate_transaction_hash

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

# --- LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id, name):
        self.id = id
        self.name = name

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT user_id, name FROM users WHERE user_id = %s", (user_id,))
    user_data = cursor.fetchone()
    cursor.close()
    conn.close()
    if user_data:
        return User(user_data['user_id'], user_data['name'])
    return None

# --- DATABASE CONNECTION ---
def get_db_connection():
    return mysql.connector.connect(
        host=app.config['DB_HOST'],
        user=app.config['DB_USER'],
        password=app.config['DB_PASS'],
        database=app.config['DB_NAME']
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
        # Default: Current Month
        return "AND MONTH(t.date) = MONTH(CURRENT_DATE()) AND YEAR(t.date) = YEAR(CURRENT_DATE())", []

# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE name = %s", (username,))
        user_data = cursor.fetchone()
        cursor.close()
        conn.close()
        
        # Simple auth: check if user exists and password matches
        # Note: In a real system, you'd use check_password_hash
        # Since this is for personal use, we might want to set up the first user manually
        if user_data and user_data['password_hash']:
            if check_password_hash(user_data['password_hash'], password):
                user_obj = User(user_data['user_id'], user_data['name'])
                login_user(user_obj)
                return redirect(url_for('index'))
        elif user_data and not user_data['password_hash']:
            # IF no password set yet, allow first login to SET password
            # SECURE THIS IN REAL PROD: This is a "first run" convenience
            pw_hash = generate_password_hash(password)
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (pw_hash, user_data['user_id']))
            conn.commit()
            cursor.close()
            conn.close()
            user_obj = User(user_data['user_id'], user_data['name'])
            login_user(user_obj)
            return redirect(url_for('index'))
            
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==========================================
# DASHBOARD PAGE & APIs
# ==========================================

@app.route('/')
@login_required
def index():
    """Main Executive Dashboard View"""
    return render_template('index.html')

@app.route('/api/dashboard/summary')
@login_required
def dashboard_summary():
    """Top-level KPIs for Net Worth, Income, Spending, and Savings"""
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        if user_id == 2: # Household
            cursor.execute("SELECT SUM(current_value) as nw FROM assets")
            res = cursor.fetchone()
            nw = res['nw'] if res and res['nw'] else 0
            
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams")
            res = cursor.fetchone()
            inc = res['inc'] if res and res['inc'] else 0
            
            cursor.execute("""
                SELECT SUM(Gus_share + Joules_share) as spent 
                FROM transactions 
                WHERE MONTH(date) = MONTH(CURDATE()) AND YEAR(date) = YEAR(CURDATE())
            """)
            res = cursor.fetchone()
            spent = res['spent'] if res and res['spent'] else 0
        else: # Gus (0) or Joules (1)
            cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
            res = cursor.fetchone()
            nw = res['nw'] if res and res['nw'] else 0
            
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
            res = cursor.fetchone()
            inc = res['inc'] if res and res['inc'] else 0
            
            share_col = "Gus_share" if user_id == 0 else "Joules_share"
            cursor.execute(f"""
                SELECT SUM({share_col}) as spent 
                FROM transactions 
                WHERE MONTH(date) = MONTH(CURDATE()) AND YEAR(date) = YEAR(CURDATE())
            """)
            res = cursor.fetchone()
            spent = res['spent'] if res and res['spent'] else 0

        return jsonify({
            "net_worth": float(nw), "income": float(inc),
            "spent": float(spent), "savings": float(inc - spent)
        })
    finally:
        cursor.close()
        conn.close()

@app.route('/api/spending/parent-categories', methods=['GET'])
@login_required
def get_parent_spending():
    user_id = int(request.args.get('user_id', 0))
    period = request.args.get('period', 'current')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    date_clause, _ = get_date_filter(period)
    
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
@login_required
def get_sub_spending():
    user_id = int(request.args.get('user_id', 0))
    parent_name = request.args.get('parent_name')
    period = request.args.get('period', 'current')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    date_clause, _ = get_date_filter(period)
    
    if user_id == 2:
        share_calc = "SUM(t.Gus_share + t.Joules_share)"
        user_filter = "t.user_id IN (0, 1, 2)" # Adjusted for safety
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
# TRANSACTIONS & CLEANUP PAGES
# ==========================================

@app.route('/transactions')
@login_required
def transactions_page():
    return render_template('transactions.html')

@app.route('/api/transactions')
@login_required
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
@login_required
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
    except Exception as e:
        logger.error(f"Error updating transaction: {e}")
        return jsonify({"error": "Failed to update transaction"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/transactions/delete', methods=['POST'])
@login_required
def delete_transaction():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = "DELETE FROM transactions WHERE id = %s"
        cursor.execute(query, (int(data['id']),))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting transaction: {e}")
        return jsonify({"error": "Failed to delete transaction"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/cleanup')
@login_required
def cleanup_page():
    return render_template('cleanup.html')

@app.route('/api/uncategorized')
@login_required
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
# INPUT (MANUAL & CSV) PAGES
# ==========================================

@app.route('/input')
@login_required
def input_page():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM categories ORDER BY name ASC")
    cats = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('input.html', categories=cats)

@app.route('/api/expense/manual', methods=['POST'])
@login_required
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
    except Exception as e:
        logger.error(f"Error saving manual expense: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/upload_csv', methods=['POST'])
@login_required
def upload_csv():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    try:
        logger.info(f"Starting Import for: {filepath}")
        run_import(filepath)
        logger.info("Import Successful")
        return jsonify({"status": "Database updated successfully."})
    except Exception as e:
        logger.error(f"IMPORT CRASHED: {e}")
        logger.error(traceback.format_exc()) 
        return jsonify({"error": "Check server logs for database/importer crash."}), 500

# ==========================================
# BUDGET & INCOME PAGES
# ==========================================

@app.route('/budget')
@login_required
def budget_page():
    return render_template('budget.html')

@app.route('/api/budget/list')
@login_required
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
@login_required
def save_budget_items():
    data = request.json
    conn = get_db_connection()
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
    except Exception as e:
        logger.error(f"Error saving budget items: {e}")
        return jsonify({"error": "Failed to save budget"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/income', methods=['GET', 'POST'])
@login_required
def handle_income():
    user_id = request.args.get('user_id', 0)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if request.method == 'POST':
        data = request.json
        try:
            query = """
                INSERT INTO income_streams (user_id, source_name, monthly_gross, tax_rate) VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE monthly_gross = VALUES(monthly_gross), tax_rate = VALUES(tax_rate)
            """
            cursor.execute(query, (user_id, data['source'], data['gross'], data['tax']))
            conn.commit()
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"Error saving income: {e}")
            return jsonify({"error": "Failed to save income"}), 500
        finally:
            cursor.close()
            conn.close()
            
    cursor.execute("SELECT * FROM income_streams WHERE user_id = %s", (user_id,))
    streams = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(streams)

@app.route('/api/budget/progress')
@login_required
def budget_progress():
    user_id = int(request.args.get('user_id', 0))
    parent_name = request.args.get('parent_name')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if user_id == 2:
        share_calc = "t.Gus_share + t.Joules_share"
        user_filter = "(t.Gus_share > 0 OR t.Joules_share > 0)"
    else:
        share_calc = "t.Gus_share" if user_id == 0 else "t.Joules_share"
        user_filter = f"t.{'Gus' if user_id == 0 else 'Joules'}_share > 0"

    try:
        if parent_name:
            query = f"""
                SELECT 
                    c.name as label,
                    COALESCE(b.target_amount, 0) as budget,
                    COALESCE(SUM({share_calc}), 0) as actual
                FROM categories c
                LEFT JOIN budgets b ON c.name = b.category_name AND b.user_id = %s
                LEFT JOIN transactions t ON c.id = t.category_id 
                    AND MONTH(t.date) = MONTH(CURRENT_DATE())
                    AND YEAR(t.date) = YEAR(CURRENT_DATE())
                WHERE c.parent_name = %s
                GROUP BY c.name, b.target_amount
            """
            cursor.execute(query, (user_id, parent_name))
        else:
            query = f"""
                SELECT 
                    COALESCE(c.parent_name, 'Other') as label,
                    SUM(DISTINCT b.target_amount) as budget,
                    COALESCE(SUM({share_calc}), 0) as actual
                FROM categories c
                LEFT JOIN (
                    SELECT category_name, SUM(target_amount) as target_amount 
                    FROM budgets WHERE user_id = %s GROUP BY category_name
                ) b ON c.name = b.category_name
                LEFT JOIN transactions t ON c.id = t.category_id 
                    AND MONTH(t.date) = MONTH(CURRENT_DATE())
                    AND YEAR(t.date) = YEAR(CURRENT_DATE())
                WHERE {user_filter}
                GROUP BY c.parent_name
            """
            cursor.execute(query, (user_id,))
            
        rows = cursor.fetchall()
        return jsonify(rows)
    finally:
        cursor.close()
        conn.close()

@app.route('/api/finance/burn-rate')
@login_required
def calculate_burn_rate():
    user_id = int(request.args.get('user_id', 0))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if user_id == 2:
        share_calc = "SUM(t.Gus_share + t.Joules_share)"
        user_filter = "(t.Gus_share > 0 OR t.Joules_share > 0)"
    else:
        share_calc = f"SUM(t.{'Gus' if user_id == 0 else 'Joules'}_share)"
        user_filter = f"t.{'Gus' if user_id == 0 else 'Joules'}_share > 0"

    periods = {
        "30d": "INTERVAL 30 DAY",
        "3m": "INTERVAL 3 MONTH",
        "1y": "INTERVAL 1 YEAR",
        "lifetime": None
    }
    
    results = {}
    try:
        for key, interval in periods.items():
            date_condition = f"AND t.date >= DATE_SUB(CURDATE(), {interval})" if interval else ""
            query = f"""
                SELECT {share_calc} as total, 
                       TIMESTAMPDIFF(MONTH, MIN(t.date), CURDATE()) + 1 as months
                FROM transactions t
                WHERE {user_filter} {date_condition}
            """
            cursor.execute(query)
            row = cursor.fetchone()
            total_spend = float(row['total'] or 0)
            num_months = 1 if key == "30d" else (row['months'] or 1)
            avg_burn = total_spend / num_months
            results[key] = {
                "actual": avg_burn,
                "cushioned": avg_burn * 1.15
            }
        return jsonify(results)
    finally:
        cursor.close()
        conn.close()

# ==========================================
# NET WORTH & ASSETS PAGES
# ==========================================

@app.route('/networth')
@login_required
def networth_page():
    return render_template('networth.html')

@app.route('/api/networth')
@login_required
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
@login_required
def update_asset():
    data = request.json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if data.get('id'):
            cursor.execute("UPDATE assets SET current_value = %s, asset_name = %s WHERE id = %s", (data['value'], data['name'], data['id']))
        else:
            cursor.execute("INSERT INTO assets (user_id, asset_name, asset_type, current_value) VALUES (%s, %s, %s, %s)",
                           (data['user_id'], data['name'], data['type'], data['value']))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating asset: {e}")
        return jsonify({"error": "Failed to update asset"}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/finance/history')
@login_required
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
        "dates": [r['snapshot_date'].strftime('%%d %%b') for r in rows],
        "nw_values": [float(r['nw_total'] or 0) for r in rows],
        "inc_values": [float(r['inc_total'] or 0) for r in rows]
    })

# ==========================================
# SHARED CORE APIS (Categories)
# ==========================================

@app.route('/api/categories')
@login_required
def get_categories():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, parent_name FROM categories ORDER BY parent_name, name")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(results)

if __name__ == '__main__':
    # For production, use gunicorn
    app.run(debug=app.config['DEBUG'], host='0.0.0.0', port=5001)
