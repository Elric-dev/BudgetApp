import os
import hashlib
import time
import traceback
import logging
from datetime import datetime
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, g
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
    cursor = get_db().cursor(dictionary=True)
    cursor.execute("SELECT user_id, name FROM users WHERE user_id = %s", (user_id,))
    user_data = cursor.fetchone()
    cursor.close()
    if user_data:
        return User(user_data['user_id'], user_data['name'])
    return None

# --- DATABASE CONNECTION ---
from mysql.connector import pooling

db_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="mypool",
    pool_size=10,
    host=app.config['DB_HOST'],
    user=app.config['DB_USER'],
    password=app.config['DB_PASS'],
    database=app.config['DB_NAME']
)

def get_db():
    if 'db' not in g:
        g.db = db_pool.get_connection()
    return g.db

@app.teardown_appcontext
def teardown_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- HELPER UTILITIES ---
def get_date_filter(period, table_alias='t'):
    """Returns SQL WHERE clause fragment and params for time frames."""
    prefix = f"{table_alias}." if table_alias else ""
    if period == 'last_month':
        return f"AND {prefix}date >= DATE_SUB(DATE_FORMAT(NOW(), '%Y-%m-01'), INTERVAL 1 MONTH) AND {prefix}date < DATE_FORMAT(NOW(), '%Y-%m-01')", []
    elif period == 'last_3':
        return f"AND {prefix}date >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)", []
    elif period == 'lifetime':
        return "", []
    elif period and len(period) == 7 and period[4] == '-': # YYYY-MM format
        year, month = period.split('-')
        return f"AND YEAR({prefix}date) = %s AND MONTH({prefix}date) = %s", [year, month]
    else:
        # Default: Current Month
        return f"AND MONTH({prefix}date) = MONTH(CURRENT_DATE()) AND YEAR({prefix}date) = YEAR(CURRENT_DATE())", []

# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        cursor = get_db().cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE name = %s", (username,))
        user_data = cursor.fetchone()
        cursor.close()
        
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
            db = get_db()
            cursor = db.cursor()
            cursor.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (pw_hash, user_data['user_id']))
            db.commit()
            cursor.close()
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
    period = request.args.get('period', 'current')
    cursor = get_db().cursor(dictionary=True)
    
    date_clause, date_params = get_date_filter(period, table_alias='transactions')
    
    # Helper to check if period is a specific month (YYYY-MM)
    is_specific_month = period and len(period) == 7 and period[4] == '-'

    try:
        # 1. GET NET WORTH (Live or Historical)
        if period == 'current' or period == 'last_3' or period == 'lifetime':
            # Use current live values for "Live" views
            if user_id == 2: # Household
                cursor.execute("SELECT SUM(current_value) as nw FROM assets")
            else:
                cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
            res = cursor.fetchone()
            nw = float(res['nw'] or 0)
        else:
            # Historical lookup from snapshots
            if is_specific_month:
                cursor.execute("SELECT LAST_DAY(STR_TO_DATE(CONCAT(%s, '-01'), '%Y-%m-%d')) as ld", (period,))
                target_date = cursor.fetchone()['ld']
            elif period == 'last_month':
                cursor.execute("SELECT LAST_DAY(DATE_SUB(NOW(), INTERVAL 1 MONTH)) as ld")
                target_date = cursor.fetchone()['ld']
            else:
                target_date = datetime.now().strftime('%Y-%m-%d')

            if user_id == 2:
                cursor.execute("""
                    SELECT SUM(total_value) as nw FROM net_worth_history 
                    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM net_worth_history WHERE snapshot_date <= %s)
                """, (target_date,))
            else:
                cursor.execute("""
                    SELECT total_value as nw FROM net_worth_history 
                    WHERE user_id = %s AND snapshot_date <= %s 
                    ORDER BY snapshot_date DESC LIMIT 1
                """, (user_id, target_date))
            res = cursor.fetchone()
            nw = float(res['nw'] or 0)

        # 2. GET INCOME (Aggregated for period)
        cursor.execute("SELECT id FROM categories WHERE name = 'One-Off Income'")
        one_off_cat = cursor.fetchone()
        one_off_cat_id = one_off_cat['id'] if one_off_cat else -1

        # Calculate monthly recurring net based on current streams
        if user_id == 2:
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams")
        else:
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()
        monthly_recurring_net = float(res['inc'] or 0)

        if is_specific_month or period == 'last_month':
            # For specific months, use the snapshot
            if is_specific_month:
                cursor.execute("SELECT LAST_DAY(STR_TO_DATE(CONCAT(%s, '-01'), '%Y-%m-%d')) as ld", (period,))
                target_date = cursor.fetchone()['ld']
            else:
                cursor.execute("SELECT LAST_DAY(DATE_SUB(NOW(), INTERVAL 1 MONTH)) as ld")
                target_date = cursor.fetchone()['ld']

            if user_id == 2:
                cursor.execute("""
                    SELECT SUM(total_net_income) as inc FROM income_history 
                    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM income_history WHERE snapshot_date <= %s)
                """, (target_date,))
            else:
                cursor.execute("""
                    SELECT total_net_income as inc FROM income_history 
                    WHERE user_id = %s AND snapshot_date <= %s 
                    ORDER BY snapshot_date DESC LIMIT 1
                """, (user_id, target_date))
            res = cursor.fetchone()
            inc = float(res['inc'] or 0)
        else:
            # Aggregated income for current, last_3, or lifetime
            if period == 'last_3':
                num_months = 3
            elif period == 'lifetime':
                cursor.execute("SELECT TIMESTAMPDIFF(MONTH, MIN(date), NOW()) + 1 as mos FROM transactions")
                res = cursor.fetchone()
                num_months = float(res['mos'] or 1)
            else:
                num_months = 1

            # Sum One-off Income in the period
            cursor.execute(f"SELECT SUM(ABS(total_amount)) as one_off FROM transactions WHERE category_id = %s {date_clause}", [one_off_cat_id] + date_params)
            res = cursor.fetchone()
            one_off_inc_total = float(res['one_off'] or 0)
            
            inc = (monthly_recurring_net * num_months) + one_off_inc_total

        # 3. GET SPENDING (Always aggregated for period)
        if user_id == 2:
            cursor.execute(f"SELECT SUM(Gus_share + Joules_share) as spent FROM transactions WHERE category_id != %s {date_clause}", [one_off_cat_id] + date_params)
        else:
            share_col = "Gus_share" if user_id == 0 else "Joules_share"
            cursor.execute(f"SELECT SUM({share_col}) as spent FROM transactions WHERE category_id != %s AND (user_id = %s OR ({share_col} > 0 AND user_id != %s)) {date_clause}", [one_off_cat_id, user_id, user_id] + date_params)
        res = cursor.fetchone()
        spent = float(res['spent'] or 0)
        
        # 4. GET GOALS
        cursor.execute("SELECT * FROM user_settings WHERE user_id = %s", (0 if user_id == 2 else user_id,))
        settings = cursor.fetchone()
        if not settings:
            settings = {"savings_goal_pct": 20.0, "expenses_goal_pct": 50.0}

        return jsonify({
            "net_worth": float(nw), "income": float(inc),
            "spent": float(spent), "savings": float(inc - spent),
            "savings_goal_pct": float(settings['savings_goal_pct']),
            "expenses_goal_pct": float(settings['expenses_goal_pct'])
        })
    finally:
        cursor.close()


@app.route('/api/spending/parent-categories', methods=['GET'])
@login_required
def get_parent_spending():
    user_id = int(request.args.get('user_id', 0))
    period = request.args.get('period', 'current')
    cursor = get_db().cursor(dictionary=True)
    
    date_clause, date_params = get_date_filter(period)
    
    # Get One-Off Income Category ID to exclude
    cursor.execute("SELECT id FROM categories WHERE name = 'One-Off Income'")
    one_off_cat = cursor.fetchone()
    one_off_cat_id = one_off_cat['id'] if one_off_cat else -1

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
            WHERE {user_filter} AND t.category_id != %s {date_clause}
            GROUP BY parent_class ORDER BY total DESC
        """
        cursor.execute(query, [one_off_cat_id] + date_params)
        rows = cursor.fetchall()
        return jsonify({"labels": [r['parent_class'] for r in rows], "values": [float(r['total']) for r in rows]})
    finally:
        cursor.close()

@app.route('/api/spending/sub-categories', methods=['GET'])
@login_required
def get_sub_spending():
    user_id = int(request.args.get('user_id', 0))
    parent_name = request.args.get('parent_name')
    period = request.args.get('period', 'current')
    cursor = get_db().cursor(dictionary=True)
    
    date_clause, date_params = get_date_filter(period)
    
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
        cursor.execute(query, [parent_name] + date_params)
        rows = cursor.fetchall()
        return jsonify({"labels": [r['sub_category'] for r in rows], "values": [float(r['total']) for r in rows]})
    finally:
        cursor.close()

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
    period = request.args.get('period', 'lifetime') # Default to lifetime for explorer
    category_id = request.args.get('category_id')
    offset = (page - 1) * 20
    
    cursor = get_db().cursor(dictionary=True)
    
    # Base filters
    where_clauses = []
    params = []
    
    if period != 'lifetime':
        date_clause, date_params = get_date_filter(period, table_alias='t')
        if date_clause:
            # get_date_filter returns "AND ...", so we strip leading AND if it's the first clause
            where_clauses.append(date_clause.lstrip('AND '))
            params.extend(date_params)
            
    if category_id and category_id != 'all':
        where_clauses.append("t.category_id = %s")
        params.append(int(category_id))
        
    where_stmt = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    # Count total with filters
    count_query = f"SELECT COUNT(*) as count FROM transactions t {where_stmt}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()['count']
    
    # Fetch rows with filters
    query = f"""
        SELECT t.id, DATE_FORMAT(t.date, '%Y-%m-%d') as clean_date, t.description, 
               t.total_amount, t.Gus_share, t.Joules_share, t.category_id, c.name as category_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        {where_stmt}
        ORDER BY t.date DESC LIMIT 20 OFFSET %s
    """
    cursor.execute(query, params + [offset])
    rows = cursor.fetchall()
    cursor.close()
    
    # Ensure all numbers are float for JSON
    for r in rows:
        r['total_amount'] = float(r['total_amount'])
        r['Gus_share'] = float(r['Gus_share'])
        r['Joules_share'] = float(r['Joules_share'])
        
    return jsonify({"transactions": rows, "total": total_count, "page": page})

@app.route('/api/transactions/update', methods=['POST'])
@login_required
def update_transaction():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        query = """
            UPDATE transactions SET category_id = %s, description = %s, total_amount = %s, 
            Gus_share = %s, Joules_share = %s WHERE id = %s
        """
        cursor.execute(query, (int(data['category_id']), data['description'], float(data['total_amount']),
                               float(data['Gus_share']), float(data['Joules_share']), int(data['id'])))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating transaction: {e}")
        return jsonify({"error": "Failed to update transaction"}), 500
    finally:
        cursor.close()

@app.route('/api/transactions/delete', methods=['POST'])
@login_required
def delete_transaction():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        query = "DELETE FROM transactions WHERE id = %s"
        cursor.execute(query, (int(data['id']),))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting transaction: {e}")
        return jsonify({"error": "Failed to delete transaction"}), 500
    finally:
        cursor.close()

@app.route('/cleanup')
@login_required
def cleanup_page():
    return render_template('cleanup.html')

@app.route('/api/uncategorized')
@login_required
def get_uncategorized():
    cursor = get_db().cursor(dictionary=True)
    query = """
        SELECT t.id, DATE_FORMAT(t.date, '%Y-%m-%d') as date, t.description, t.total_amount, c.name as current_category
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE c.name = 'General' OR c.parent_name = 'Uncategorized'
        ORDER BY t.date DESC
    """
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return jsonify(results)

# ==========================================
# INPUT (MANUAL & CSV) PAGES
# ==========================================

@app.route('/input')
@login_required
def input_page():
    db = get_db()
    
    # Fetch all users
    user_cursor = db.cursor(dictionary=True)
    user_cursor.execute("SELECT user_id, name FROM users WHERE user_id IN (0, 1) ORDER BY user_id ASC")
    users = user_cursor.fetchall()
    user_cursor.close()

    # Fetch Expense Categories
    exp_cursor = db.cursor(dictionary=True)
    exp_cursor.execute("""
        SELECT id, name FROM categories 
        WHERE parent_name NOT IN ('Savings', 'Income') 
        OR parent_name IS NULL 
        ORDER BY name ASC
    """)
    expense_cats = exp_cursor.fetchall()
    exp_cursor.close()

    # Fetch Savings Categories (with fallback)
    sav_cursor = db.cursor(dictionary=True)
    sav_cursor.execute("SELECT id, name FROM categories WHERE parent_name = 'Savings' ORDER BY name ASC")
    savings_cats = sav_cursor.fetchall()
    if not savings_cats:
        sav_cursor.execute("SELECT id, name FROM categories WHERE parent_name = 'Life' ORDER BY name ASC")
        savings_cats = sav_cursor.fetchall()
    sav_cursor.close()

    # Fetch Income Categories (with fallback)
    inc_cursor = db.cursor(dictionary=True)
    inc_cursor.execute("SELECT id, name FROM categories WHERE parent_name = 'Income' OR name LIKE '%Income%' ORDER BY name ASC")
    income_cats = inc_cursor.fetchall()
    if not income_cats:
        inc_cursor.execute("SELECT id, name FROM categories WHERE parent_name = 'Uncategorized' ORDER BY name ASC")
        income_cats = inc_cursor.fetchall()
    inc_cursor.close()

    return render_template('input.html', 
                           users=users,
                           expense_categories=expense_cats, 
                           savings_categories=savings_cats,
                           income_categories=income_cats)

@app.route('/api/income/manual', methods=['POST'])
@login_required
def save_manual_income_entry():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        total = float(data['amount'])
        user_id = int(data.get('user_id', current_user.id))
        
        # Create a unique hash
        mock_row = {'Date': data.get('date'), 'Description': data.get('description'),
                    'Cost': -total, 'Category': 'Income'}
        t_hash = generate_transaction_hash(mock_row)
        
        query = """
            INSERT INTO transactions (date, description, total_amount, user_id, category_id, payer_id, 
            Gus_share, Joules_share, is_split, transaction_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        g_share = -total if user_id == 0 else 0
        j_share = -total if user_id == 1 else 0

        cursor.execute(query, (data['date'], data['description'], -total, user_id, int(data['category_id']), 
                               user_id, g_share, j_share, 0, t_hash))
        db.commit()
        return jsonify({"status": "success"}), 201
    except Exception as e:
        logger.error(f"Error saving manual income: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/savings/manual', methods=['POST'])
@login_required
def save_manual_saving():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        user_id = int(data.get('user_id', current_user.id))
        query = """
            INSERT INTO savings (user_id, date, category_id, amount, description) 
            VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            user_id,
            data['date'],
            data['category_id'],
            data['amount'],
            data.get('description')
        ))
        db.commit()
        return jsonify({"status": "success"}), 201
    except mysql.connector.errors.ProgrammingError as e:
        if e.errno == 1146:
            return jsonify({"error": "The 'savings' table is missing. Please run schema.sql to initialize it."}), 500
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error saving manual saving/investment: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

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

@app.route('/api/sync_splitwise', methods=['POST'])
@login_required
def sync_splitwise():
    try:
        from splitwise_sync import run_splitwise_sync
        success = run_splitwise_sync()
        if success:
            return jsonify({"status": "Splitwise sync successful."})
        else:
            return jsonify({"error": "Sync failed. Check server logs."}), 500
    except Exception as e:
        logger.error(f"Sync route failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync_splitwise_full', methods=['POST'])
@login_required
def sync_splitwise_full():
    try:
        from splitwise_sync import run_full_history_sync
        success = run_full_history_sync()
        if success:
            return jsonify({"status": "Splitwise FULL history sync successful."})
        else:
            return jsonify({"error": "Full Sync failed. Check server logs."}), 500
    except Exception as e:
        logger.error(f"Full Sync route failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/expense/splitwise', methods=['POST'])
@login_required
def push_splitwise_expense():
    data = request.json
    try:
        from splitwise_sync import push_expense_to_splitwise
        success, result = push_expense_to_splitwise(
            description=data['description'],
            cost=data['amount'],
            date_str=data.get('date')
        )
        if success:
            return jsonify({"status": "Successfully pushed to Splitwise!", "id": result})
        else:
            return jsonify({"error": f"Splitwise API Error: {result}"}), 500
    except Exception as e:
        logger.error(f"Push to Splitwise failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/expense/manual', methods=['POST'])
@login_required
def save_manual_expense():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        # Create a unique hash for the transaction to prevent duplicates
        mock_row = {
            'Date': data.get('date'), 
            'Description': data.get('description'),
            'Cost': data.get('amount'), 
            'Category': data.get('category_name', 'Manual')
        }
        t_hash = generate_transaction_hash(mock_row)
        
        query = """
            INSERT INTO transactions (date, description, total_amount, user_id, category_id, payer_id, 
            Gus_share, Joules_share, is_split, transaction_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        # For manual expenses from the input page, we usually treat them as Household (user_id=2)
        # and use the provided splits.
        cursor.execute(query, (
            data['date'], 
            data['description'], 
            data['amount'], 
            2, # Household
            int(data['category_id']), 
            0, # Default payer to Gus (0) for manual entries, or could be adjusted
            data['split_gus'], 
            data['split_joules'], 
            1 if data['split_gus'] > 0 and data['split_joules'] > 0 else 0,
            t_hash
        ))
        db.commit()
        return jsonify({"status": "success"}), 201
    except mysql.connector.errors.IntegrityError:
        return jsonify({"error": "This transaction already exists (duplicate hash)."}), 409
    except Exception as e:
        logger.error(f"Error saving manual expense: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

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
    cursor = get_db().cursor(dictionary=True)
    query = """
        SELECT c.id, c.name, c.parent_name, COALESCE(b.target_amount, 0) as amount
        FROM categories c
        LEFT JOIN budgets b ON c.name = b.category_name AND b.user_id = %s
        ORDER BY c.name ASC
    """
    cursor.execute(query, (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    return jsonify(rows)

@app.route('/api/budget/settings', methods=['GET', 'POST'])
@login_required
def budget_settings():
    user_id = request.args.get('user_id', 0)
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if request.method == 'POST':
            data = request.json
            query = """
                INSERT INTO user_settings (user_id, savings_goal_pct, expenses_goal_pct) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    savings_goal_pct = VALUES(savings_goal_pct), 
                    expenses_goal_pct = VALUES(expenses_goal_pct)
            """
            cursor.execute(query, (user_id, data['savings_goal_pct'], data['expenses_goal_pct']))
            db.commit()
            return jsonify({"status": "success"})

        cursor.execute("SELECT * FROM user_settings WHERE user_id = %s", (user_id,))
        settings = cursor.fetchone()
        if not settings:
            settings = {"user_id": user_id, "savings_goal_pct": 20.0, "expenses_goal_pct": 50.0}
        
        return jsonify(settings)
    
    except mysql.connector.errors.ProgrammingError as e:
        if e.errno == 1146: # Table 'user_settings' doesn't exist
            logger.warning("user_settings table not found. Returning default values. Please run migrations.")
            return jsonify({"user_id": user_id, "savings_goal_pct": 20.0, "expenses_goal_pct": 50.0})
        else:
            logger.error(f"Database error in budget_settings: {e}")
            return jsonify({"error": "A database error occurred."}), 500
    finally:
        cursor.close()

@app.route('/api/budget/save_items', methods=['POST'])
@login_required
def save_budget_items():
    data = request.json
    user_id = data.get('user_id')
    db = get_db()
    cursor = db.cursor()
    try:
        # Save itemized budgets
        for item in data.get('items', []):
            query = """
                INSERT INTO budgets (user_id, category_name, target_amount) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE target_amount = VALUES(target_amount)
            """
            cursor.execute(query, (user_id, item['name'], item['amount']))
        
        # Save global strategy settings if provided
        if 'savings_goal_pct' in data and 'expenses_goal_pct' in data:
            query = """
                INSERT INTO user_settings (user_id, savings_goal_pct, expenses_goal_pct) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    savings_goal_pct = VALUES(savings_goal_pct), 
                    expenses_goal_pct = VALUES(expenses_goal_pct)
            """
            cursor.execute(query, (user_id, data['savings_goal_pct'], data['expenses_goal_pct']))
            
        db.commit()
        return jsonify({"status": "success"})
    except mysql.connector.errors.ProgrammingError as e:
        if e.errno == 1146: # Table doesn't exist
            logger.error("user_settings table not found. Cannot save global goals. Please run migrations.")
            # We can still try to commit the budget items
            db.commit()
            return jsonify({"status": "success", "warning": "Itemized budgets saved, but global goals could not be. Please run database migrations."})
        else:
            logger.error(f"Error saving budget data: {e}")
            return jsonify({"error": "Failed to save budget"}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred while saving budget data: {e}")
        return jsonify({"error": "An unexpected error occurred."}), 500
    finally:
        cursor.close()

@app.route('/api/income', methods=['GET', 'POST'])
@login_required
def handle_income():
    user_id = request.args.get('user_id', 0)
    db = get_db()
    cursor = db.cursor(dictionary=True)
    if request.method == 'POST':
        data = request.json
        try:
            query = """
                INSERT INTO income_streams (user_id, source_name, monthly_gross, tax_rate) VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE monthly_gross = VALUES(monthly_gross), tax_rate = VALUES(tax_rate)
            """
            cursor.execute(query, (user_id, data['source'], data['gross'], data['tax']))
            db.commit()
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"Error saving income: {e}")
            return jsonify({"error": "Failed to save income"}), 500
        finally:
            cursor.close()
            
    cursor.execute("SELECT * FROM income_streams WHERE user_id = %s", (user_id,))
    streams = cursor.fetchall()
    cursor.close()
    return jsonify(streams)

@app.route('/api/income/update', methods=['POST'])
@login_required
def update_income():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        query = """
            UPDATE income_streams SET source_name = %s, monthly_gross = %s, tax_rate = %s 
            WHERE id = %s
        """
        cursor.execute(query, (data['source'], data['gross'], data['tax'], data['id']))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating income: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/income/delete', methods=['POST'])
@login_required
def delete_income():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        query = "DELETE FROM income_streams WHERE id = %s"
        cursor.execute(query, (int(data['id']),))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting income: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/budget/progress')
@login_required
def budget_progress():
    user_id = int(request.args.get('user_id', 0))
    parent_name = request.args.get('parent_name')
    period = request.args.get('period', 'current')
    cursor = get_db().cursor(dictionary=True)
    
    date_clause, date_params = get_date_filter(period, table_alias='t')
    
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
                LEFT JOIN transactions t ON c.id = t.category_id {date_clause}
                WHERE c.parent_name = %s
                GROUP BY c.name, b.target_amount
            """
            cursor.execute(query, [user_id] + date_params + [parent_name])
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
                LEFT JOIN transactions t ON c.id = t.category_id {date_clause}
                WHERE {user_filter}
                GROUP BY c.parent_name
            """
            cursor.execute(query, [user_id] + date_params)
            
        rows = cursor.fetchall()
        return jsonify(rows)
    finally:
        cursor.close()

@app.route('/api/finance/available-months')
@login_required
def get_available_months():
    cursor = get_db().cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT DATE_FORMAT(date, '%Y-%m') as month
        FROM transactions
        ORDER BY month DESC
    """)
    rows = cursor.fetchall()
    cursor.close()
    return jsonify([r['month'] for r in rows])

@app.route('/api/finance/burn-rate')
@login_required
def calculate_burn_rate():
    user_id = int(request.args.get('user_id', 0))
    cursor = get_db().cursor(dictionary=True)
    
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
    cursor = get_db().cursor(dictionary=True)
    cursor.execute("SELECT * FROM assets WHERE user_id = %s ORDER BY current_value DESC", (user_id,))
    assets = cursor.fetchall()
    cursor.close()
    return jsonify(assets)

@app.route('/api/networth/update', methods=['POST'])
@login_required
def update_asset():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        if data.get('id'):
            cursor.execute("UPDATE assets SET current_value = %s WHERE id = %s", (data['value'], data['id']))
        else:
            cursor.execute("INSERT INTO assets (user_id, asset_name, asset_type, current_value) VALUES (%s, %s, %s, %s)",
                           (data['user_id'], data['name'], data['type'], data['value']))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating asset: {e}")
        return jsonify({"error": "Failed to update asset"}), 500
    finally:
        cursor.close()

@app.route('/api/networth/edit-name', methods=['POST'])
@login_required
def edit_asset_name():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("UPDATE assets SET asset_name = %s WHERE id = %s", (data['name'], data['id']))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating asset name: {e}")
        return jsonify({"error": "Failed to update asset name"}), 500
    finally:
        cursor.close()

@app.route('/api/networth/delete', methods=['POST'])
@login_required
def delete_asset():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM assets WHERE id = %s", (int(data['id']),))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting asset: {e}")
        return jsonify({"error": "Failed to delete asset"}), 500
    finally:
        cursor.close()

@app.route('/api/finance/history')
@login_required
def finance_history():
    user_id = int(request.args.get('user_id', 0))
    cursor = get_db().cursor(dictionary=True)
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
    return jsonify({
        "dates": [r['snapshot_date'].strftime('%d %b') for r in rows],
        "nw_values": [float(r['nw_total'] or 0) for r in rows],
        "inc_values": [float(r['inc_total'] or 0) for r in rows]
    })

# ==========================================
# SHARED CORE APIS (Categories)
# ==========================================

@app.route('/api/categories')
@login_required
def get_categories():
    cursor = get_db().cursor(dictionary=True)
    cursor.execute("SELECT id, name, parent_name FROM categories ORDER BY parent_name, name")
    results = cursor.fetchall()
    cursor.close()
    return jsonify(results)

@app.route('/api/finance/housing-ratio')
@login_required
def get_housing_ratio():
    user_id = int(request.args.get('user_id', 0))
    period = request.args.get('period', 'current')
    cursor = get_db().cursor(dictionary=True)
    
    date_clause, date_params = get_date_filter(period, table_alias='t')
    
    try:
        # 1. Get Monthly Net Income
        if user_id == 2: # Household
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams")
        else: # Gus (0) or Joules (1)
            cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
        inc_res = cursor.fetchone()
        income = float(inc_res['inc'] or 0)

        # 2. Get Monthly Housing Costs (Rent, Utilities, Home Maintenance)
        # Categories with parent_name 'Home' or 'Utilities'
        if user_id == 2:
            share_calc = "SUM(t.Gus_share + t.Joules_share)"
        else:
            share_calc = f"SUM(t.{'Gus' if user_id == 0 else 'Joules'}_share)"

        query = f"""
            SELECT {share_calc} as total
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE (c.parent_name IN ('Home', 'Utilities'))
            {date_clause}
        """
        cursor.execute(query, date_params)
        cost_res = cursor.fetchone()
        housing_cost = float(cost_res['total'] or 0)

        ratio = (housing_cost / income * 100) if income > 0 else 0
        
        return jsonify({
            "income": income,
            "housing_cost": housing_cost,
            "ratio": round(ratio, 1)
        })
    finally:
        cursor.close()

@app.route('/api/update_category', methods=['POST'])
@login_required
def update_transaction_category():
    data = request.json
    db = get_db()
    cursor = db.cursor()
    try:
        query = "UPDATE transactions SET category_id = %s WHERE id = %s"
        cursor.execute(query, (int(data['category_id']), int(data['transaction_id'])))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating category: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/finance/snapshot', methods=['POST'])
@login_required
def take_financial_snapshot():
    data = request.json
    user_id = int(data.get('user_id', 0))
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # 1. Calculate Total Net Worth for current user
        cursor.execute("SELECT SUM(current_value) as nw FROM assets WHERE user_id = %s", (user_id,))
        nw_res = cursor.fetchone()
        nw_total = float(nw_res['nw'] or 0)
        
        # 2. Calculate Total Net Income for current user
        cursor.execute("SELECT SUM(monthly_gross * (1 - tax_rate/100)) as inc FROM income_streams WHERE user_id = %s", (user_id,))
        inc_res = cursor.fetchone()
        inc_total = float(inc_res['inc'] or 0)
        
        # 3. Save to History for current user
        cursor.execute("""
            INSERT INTO net_worth_history (user_id, snapshot_date, total_value) 
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE total_value = VALUES(total_value)
        """, (user_id, today, nw_total))
        
        cursor.execute("""
            INSERT INTO income_history (user_id, snapshot_date, total_net_income) 
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE total_net_income = VALUES(total_net_income)
        """, (user_id, today, inc_total))

        # --- SMEARING LOGIC FOR HOUSEHOLD VIEW ---
        other_user_id = 1 if user_id == 0 else 0
        
        # Ensure other user has records for today (LOF)
        # Net Worth
        cursor.execute("SELECT id FROM net_worth_history WHERE user_id = %s AND snapshot_date = %s", (other_user_id, today))
        if not cursor.fetchone():
            cursor.execute("""
                SELECT total_value FROM net_worth_history 
                WHERE user_id = %s AND snapshot_date < %s 
                ORDER BY snapshot_date DESC LIMIT 1
            """, (other_user_id, today))
            prev = cursor.fetchone()
            if prev:
                cursor.execute("INSERT INTO net_worth_history (user_id, snapshot_date, total_value) VALUES (%s, %s, %s)", 
                               (other_user_id, today, float(prev['total_value'])))

        # Income
        cursor.execute("SELECT id FROM income_history WHERE user_id = %s AND snapshot_date = %s", (other_user_id, today))
        if not cursor.fetchone():
            cursor.execute("""
                SELECT total_net_income FROM income_history 
                WHERE user_id = %s AND snapshot_date < %s 
                ORDER BY snapshot_date DESC LIMIT 1
            """, (other_user_id, today))
            prev = cursor.fetchone()
            if prev:
                cursor.execute("INSERT INTO income_history (user_id, snapshot_date, total_net_income) VALUES (%s, %s, %s)", 
                               (other_user_id, today, float(prev['total_net_income'])))
        
        db.commit()
        return jsonify({"status": "success", "nw": nw_total, "inc": inc_total})
    except Exception as e:
        logger.error(f"Snapshot Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/setup-db')
@login_required
def setup_db():
    """Temporary route to ensure database consistency for Raspberry Pi migration."""
    if current_user.name != 'Gus':
        return "Unauthorized", 403
        
    db = get_db()
    cursor = db.cursor()
    try:
        # 1. Ensure Savings Table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS savings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                date DATE NOT NULL,
                category_id INT,
                amount DECIMAL(10, 2) NOT NULL,
                description VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
        """)
        
        # 2. Add missing critical categories
        cats = [
            ('Bank Account', 'Savings'),
            ('Brokerage', 'Savings'),
            ('Pension', 'Savings'),
            ('One-Off Income', 'Income')
        ]
        for name, parent in cats:
            cursor.execute("INSERT IGNORE INTO categories (name, parent_name) VALUES (%s, %s)", (name, parent))
            
        db.commit()
        return "Database Setup Successful! Savings table created and categories initialized."
    except Exception as e:
        return f"Setup Failed: {e}"
    finally:
        cursor.close()

@app.route('/networth-explorer')
@login_required
def networth_explorer():
    return render_template('networth_explorer.html')

@app.route('/api/finance/history/raw')
@login_required
def get_raw_history():
    user_id = int(request.args.get('user_id', 0))
    cursor = get_db().cursor(dictionary=True)
    
    if user_id == 2:
        # Household: Combined view + Individual breakdowns
        query = """
            SELECT 
                n.snapshot_date, 
                SUM(n.total_value) as nw_total, 
                SUM(i.total_net_income) as inc_total,
                MAX(CASE WHEN n.user_id = 0 THEN n.total_value ELSE 0 END) as nw_gus,
                MAX(CASE WHEN n.user_id = 1 THEN n.total_value ELSE 0 END) as nw_joules,
                MAX(CASE WHEN i.user_id = 0 THEN i.total_net_income ELSE 0 END) as inc_gus,
                MAX(CASE WHEN i.user_id = 1 THEN i.total_net_income ELSE 0 END) as inc_joules
            FROM net_worth_history n
            LEFT JOIN income_history i ON n.snapshot_date = i.snapshot_date AND n.user_id = i.user_id
            GROUP BY n.snapshot_date
            ORDER BY n.snapshot_date DESC
        """
        cursor.execute(query)
    else:
        # Individual view
        query = """
            SELECT n.id as nw_id, i.id as inc_id, n.snapshot_date, 
                   n.total_value as nw_total, i.total_net_income as inc_total
            FROM net_worth_history n
            LEFT JOIN income_history i ON n.snapshot_date = i.snapshot_date AND n.user_id = i.user_id
            WHERE n.user_id = %s
            ORDER BY n.snapshot_date DESC
        """
        cursor.execute(query, (user_id,))
        
    rows = cursor.fetchall()
    cursor.close()
    
    # Format dates for JSON
    for r in rows:
        if r['snapshot_date']:
            r['snapshot_date'] = r['snapshot_date'].strftime('%Y-%m-%d')
            
    return jsonify(rows)

@app.route('/api/finance/history/update', methods=['POST'])
@login_required
def update_history_entry():
    data = request.json
    user_id = int(data.get('user_id', 0))
    date_str = data.get('date')
    nw_val = float(data.get('nw_total', 0))
    inc_val = float(data.get('inc_total', 0))
    
    if user_id == 2:
        return jsonify({"error": "Cannot edit combined household data directly. Edit Gus or Joules instead."}), 400
        
    db = get_db()
    cursor = db.cursor()
    try:
        # Update Net Worth History for current user
        cursor.execute("""
            INSERT INTO net_worth_history (user_id, snapshot_date, total_value)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE total_value = VALUES(total_value)
        """, (user_id, date_str, nw_val))
        
        # Update Income History for current user
        cursor.execute("""
            INSERT INTO income_history (user_id, snapshot_date, total_net_income)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE total_net_income = VALUES(total_net_income)
        """, (user_id, date_str, inc_val))

        # --- SMEARING LOGIC FOR HOUSEHOLD VIEW ---
        other_user_id = 1 if user_id == 0 else 0
        
        # Check if other user has NW for this date
        cursor.execute("SELECT id FROM net_worth_history WHERE user_id = %s AND snapshot_date = %s", (other_user_id, date_str))
        if not cursor.fetchone():
            cursor.execute("""
                SELECT total_value FROM net_worth_history 
                WHERE user_id = %s AND snapshot_date < %s 
                ORDER BY snapshot_date DESC LIMIT 1
            """, (other_user_id, date_str))
            prev = cursor.fetchone()
            if prev:
                cursor.execute("INSERT INTO net_worth_history (user_id, snapshot_date, total_value) VALUES (%s, %s, %s)", 
                               (other_user_id, date_str, float(prev[0])))

        # Check if other user has Income for this date
        cursor.execute("SELECT id FROM income_history WHERE user_id = %s AND snapshot_date = %s", (other_user_id, date_str))
        if not cursor.fetchone():
            cursor.execute("""
                SELECT total_net_income FROM income_history 
                WHERE user_id = %s AND snapshot_date < %s 
                ORDER BY snapshot_date DESC LIMIT 1
            """, (other_user_id, date_str))
            prev = cursor.fetchone()
            if prev:
                cursor.execute("INSERT INTO income_history (user_id, snapshot_date, total_net_income) VALUES (%s, %s, %s)", 
                               (other_user_id, date_str, float(prev[0])))
        
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating history: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

@app.route('/api/finance/history/delete', methods=['POST'])
@login_required
def delete_history_entry():
    data = request.json
    user_id = int(data.get('user_id', 0))
    date_str = data.get('date')
    
    if user_id == 2:
        return jsonify({"error": "Cannot delete household data. Delete individual user snapshots instead."}), 400
        
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("DELETE FROM net_worth_history WHERE user_id = %s AND snapshot_date = %s", (user_id, date_str))
        cursor.execute("DELETE FROM income_history WHERE user_id = %s AND snapshot_date = %s", (user_id, date_str))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting history: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()

if __name__ == '__main__':
    # For production, use gunicorn
    # Enabled threaded=True to handle parallel dashboard API calls efficiently
    app.run(debug=app.config['DEBUG'], host='0.0.0.0', port=5001, threaded=True)
