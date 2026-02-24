# BudgetArchitect

**A strategic financial management engine for precision budgeting.** 
BudgetArchitect transforms raw transaction data and Splitwise syncs into a high-level financial roadmap. It decouples daily spending from long-term investment strategy, providing a "Command Center" view of individual and household wealth.

---

## 🚀 Raspberry Pi / Remote Server Setup

Follow these steps to deploy BudgetArchitect as a persistent home server.

### 1. OS Preparation & Prerequisites
Connect to your Pi via SSH and ensure your system is up to date:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git python3-pip python3-venv mariadb-server nginx -y
```

### 2. Database Configuration
Secure your MariaDB installation and create the application database:
```bash
# Secure the installation (set a root password if prompted)
sudo mysql_secure_installation

# Log into MariaDB
sudo mysql -u root -p

# Run these commands inside the MariaDB prompt:
CREATE DATABASE budget_db;
CREATE USER 'budget_user'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON budget_db.* TO 'budget_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Initialize the schema:
```bash
mysql -u budget_user -p budget_db < schema.sql
```

### 3. Application Installation
Clone the repository and set up a Python Virtual Environment:
```bash
git clone https://github.com/yourusername/BudgetApp.git
cd BudgetApp

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Environment Configuration
Create a `.env` file based on the template:
```bash
nano .env
```
Add your credentials:
```text
SECRET_KEY=pick-a-random-long-string
DB_HOST=localhost
DB_USER=budget_user
DB_PASS=your_secure_password
DB_NAME=budget_db

# Optional: Splitwise Integration
SPLITWISE_API_KEY=your_key
```

### 5. Initialization (Backfill)
Populate your new instance with baseline data and historical charts:
```bash
python backfill_history.py
```

### 6. Production Deployment (Systemd)
Create a service file to ensure the app starts on boot and restarts if it crashes:
```bash
sudo nano /etc/systemd/system/budgetapp.service
```
Paste the following (adjust `User` and `WorkingDirectory` paths):
```ini
[Unit]
Description=Gunicorn instance to serve BudgetArchitect
After=network.target

[Service]
User=pi
Group=www-data
WorkingDirectory=/home/pi/BudgetApp
Environment="PATH=/home/pi/BudgetApp/venv/bin"
ExecStart=/home/pi/BudgetApp/venv/bin/gunicorn --workers 3 --bind 0.0.0.0:5001 wsgi:app

[Install]
WantedBy=multi-user.target
```

Start and enable the service:
```bash
sudo systemctl start budgetapp
sudo systemctl enable budgetapp
```

---

## 🛠️ Local Development
If running locally for testing:
```bash
source venv/bin/activate
python app.py
```
Access at `http://localhost:5001`. 

**First Login:** Enter any username (Gus or Joules). The system will ask you to create a password on your first successful attempt.

---

## 📈 Technical Architecture
*   **Relational Bridge**: Decouples volatile transaction data from stable strategic targets.
*   **SHA-256 Deduplication**: Ensures CSV imports never create double entries.
*   **Multi-Profile Engine**: Real-time switching between individual and household logic.

**Developed by Gus & Gemini CLI**  
*Strategic Engineering for Personal Finance.*
