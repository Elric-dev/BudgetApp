-- Database Schema for BudgetApp
-- Production Ready Version

CREATE DATABASE IF NOT EXISTS budget_db;
USE budget_db;

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    user_id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) DEFAULT NULL, -- For future auth
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Initial users
INSERT IGNORE INTO users (user_id, name) VALUES (0, 'Gus'), (1, 'Joules'), (2, 'Household');

-- 2. Master Categories
CREATE TABLE IF NOT EXISTS categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    parent_name VARCHAR(255) DEFAULT 'Other',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 3. Transactions
CREATE TABLE IF NOT EXISTS transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    description VARCHAR(255) NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    user_id INT NOT NULL, -- The user who 'owns' the transaction record
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

-- 4. Budget Targets
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

-- 5. Assets (Net Worth)
CREATE TABLE IF NOT EXISTS assets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    asset_name VARCHAR(255) NOT NULL,
    asset_type VARCHAR(100), -- Savings, Investment, Property, etc.
    current_value DECIMAL(12, 2) DEFAULT 0.00,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX (user_id)
) ENGINE=InnoDB;

-- 6. Income Streams
CREATE TABLE IF NOT EXISTS income_streams (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    monthly_gross DECIMAL(10, 2) DEFAULT 0.00,
    tax_rate DECIMAL(5, 2) DEFAULT 0.00, -- e.g. 20.00 for 20%
    UNIQUE KEY unique_user_source (user_id, source_name),
    INDEX (user_id)
) ENGINE=InnoDB;

-- 7. Net Worth History (Snapshot)
CREATE TABLE IF NOT EXISTS net_worth_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    total_value DECIMAL(15, 2) NOT NULL,
    INDEX (user_id, snapshot_date)
) ENGINE=InnoDB;

-- 8. Income History (Snapshot)
CREATE TABLE IF NOT EXISTS income_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    snapshot_date DATE NOT NULL,
    total_net_income DECIMAL(15, 2) NOT NULL,
    INDEX (user_id, snapshot_date)
) ENGINE=InnoDB;
