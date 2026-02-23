import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-very-secret-key-change-it-in-env')
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASS = os.getenv('DB_PASS', '')
    DB_NAME = os.getenv('DB_NAME', 'budget_db')
    UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/tmp/budget_uploads')
    DEBUG = os.getenv('DEBUG', 'False') == 'True'
    
    # Splitwise Credentials
    SPLITWISE_CONSUMER_KEY = os.getenv('SPLITWISE_CONSUMER_KEY')
    SPLITWISE_CONSUMER_SECRET = os.getenv('SPLITWISE_CONSUMER_SECRET')
    SPLITWISE_API_KEY = os.getenv('SPLITWISE_API_KEY') # API Key / Personal Access Token

# Ensure upload folder exists
if not os.path.exists(Config.UPLOAD_FOLDER):
    os.makedirs(Config.UPLOAD_FOLDER)
