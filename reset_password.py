import mysql.connector
import sys
from werkzeug.security import generate_password_hash
from config import Config

def reset_password(username, new_password):
    try:
        conn = mysql.connector.connect(
            host=Config.DB_HOST,
            user=Config.DB_USER,
            password=Config.DB_PASS,
            database=Config.DB_NAME
        )
        cursor = conn.cursor()
        
        pw_hash = generate_password_hash(new_password)
        
        # Check if user exists
        cursor.execute("SELECT user_id FROM users WHERE name = %s", (username,))
        user = cursor.fetchone()
        
        if not user:
            print(f"❌ Error: User '{username}' not found in database.")
            return

        cursor.execute("UPDATE users SET password_hash = %s WHERE name = %s", (pw_hash, username))
        conn.commit()
        
        print(f"✅ Success: Password for '{username}' has been reset.")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python reset_password.py [username] [new_password]")
        print("Example: python reset_password.py Joules mynewsecretpw123")
    else:
        reset_password(sys.argv[1], sys.argv[2])
