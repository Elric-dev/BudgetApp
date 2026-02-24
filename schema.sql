-- BudgetApp Consolidated Database Schema
-- Optimized for Raspberry Pi / Remote Server installation and idempotency

CREATE DATABASE IF NOT EXISTS budget_tracker;
USE budget_tracker;

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    user_id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Initial users (Default IDs for multi-profile toggle)
INSERT IGNORE INTO users (user_id, name) VALUES 
(0, 'Gus'), 
(1, 'Joules'), 
(2, 'Household');

-- 2. Categories Table
CREATE TABLE IF NOT EXISTS categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    parent_name VARCHAR(255) DEFAULT 'Other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Standardized Categories
INSERT IGNORE INTO categories (id, name, parent_name) VALUES 
(2, 'Electricity', 'Utilities'),
(4, 'Gas', 'Utilities'),
(6, 'TV/Phone/Internet', 'Utilities'),
(13, 'Rent', 'Home'),
(19, 'Bus/train', 'Transport'),
(39, 'General', 'Other');

-- Expanded Categories for immediate usability
INSERT IGNORE INTO categories (name, parent_name) VALUES 
('Groceries', 'Food'),
('Dining out', 'Food'),
('Entertainment', 'Lifestyle'),
('Health', 'Personal'),
('Shopping', 'Lifestyle'),
('Travel', 'Lifestyle'),
('Insurance', 'Finance'),
('Maintenance', 'Home'),
('Water', 'Utilities'),
('Subscriptions', 'Lifestyle'),
('Other', 'Other'),
('Uncategorized', 'Other'),
('High liquidity (bank accounts)', 'Savings'),
('Brokerage', 'Savings'),
('Pension', 'Savings'),
('Other Savings', 'Savings'),
('Home Office', 'Work'),
('Gifts', 'Personal'),
('One-Off Income', 'Income');

-- 3. Transactions Table
CREATE TABLE IF NOT EXISTS transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    description VARCHAR(255) NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    user_id INT NOT NULL, 
    category_id INT,
    payer_id INT COMMENT '0: Gus, 1: Joules',
    Gus_share DECIMAL(10, 2) DEFAULT 0.00,
    Joules_share DECIMAL(10, 2) DEFAULT 0.00,
    is_split TINYINT(1) DEFAULT 0,
    transaction_hash VARCHAR(64) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
    INDEX (date),
    INDEX (user_id)
) ENGINE=InnoDB;

-- 4. Budget Targets Table
CREATE TABLE IF NOT EXISTS budgets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    category_name VARCHAR(255) NOT NULL,
    target_amount DECIMAL(10, 2) DEFAULT 0.00,
    target_percent DECIMAL(5, 2) DEFAULT 0.00,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_user_category (user_id, category_name),
    FOREIGN KEY (category_name) REFERENCES categories(name) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- 5. Assets (Net Worth) Table
CREATE TABLE IF NOT EXISTS assets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    asset_name VARCHAR(255) NOT NULL,
    asset_type VARCHAR(100), 
    current_value DECIMAL(12, 2) DEFAULT 0.00,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX (user_id)
) ENGINE=InnoDB;

-- 6. Income Streams Table
CREATE TABLE IF NOT EXISTS income_streams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    monthly_gross DECIMAL(10, 2) DEFAULT 0.00,
    tax_rate DECIMAL(5, 2) DEFAULT 0.00,
    UNIQUE KEY unique_user_source (user_id, source_name),
    INDEX (user_id)
) ENGINE=InnoDB;

-- 7. Net Worth History Table
CREATE TABLE IF NOT EXISTS net_worth_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    total_value DECIMAL(15, 2) NOT NULL,
    UNIQUE KEY unique_user_date (user_id, snapshot_date),
    INDEX (user_id, snapshot_date)
) ENGINE=InnoDB;

-- 8. Income History Table
CREATE TABLE IF NOT EXISTS income_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    total_net_income DECIMAL(15, 2) NOT NULL,
    UNIQUE KEY unique_user_date (user_id, snapshot_date),
    INDEX (user_id, snapshot_date)
) ENGINE=InnoDB;

-- 9. User Settings Table
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INT PRIMARY KEY,
    savings_goal_pct DECIMAL(5, 2) DEFAULT 20.00,
    expenses_goal_pct DECIMAL(5, 2) DEFAULT 50.00,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Default Settings Initialization
INSERT IGNORE INTO user_settings (user_id, savings_goal_pct, expenses_goal_pct) VALUES 
(0, 20.00, 50.00),
(1, 20.00, 50.00),
(2, 20.00, 50.00);

-- 10. Savings/Investments Table
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
) ENGINE=InnoDB;
