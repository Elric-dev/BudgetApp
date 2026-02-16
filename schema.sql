-- Database Schema for BudgetApp
-- Created for Caronte Developers

CREATE DATABASE IF NOT EXISTS budget_db;
USE budget_db;

-- 1. Table for Master Categories (Populated by CSV imports)
CREATE TABLE IF NOT EXISTS categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2. Table for Budget Targets
CREATE TABLE IF NOT EXISTS budgets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL COMMENT '0: Gus, 1: Joules, 2: Household',
    category_name VARCHAR(255) NOT NULL,
    target_amount DECIMAL(10, 2) DEFAULT 0.00,
    target_percent DECIMAL(5, 2) DEFAULT 0.00,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- This ensures a user can only have one budget per category
    UNIQUE KEY unique_user_category (user_id, category_name),
    
    -- Logical link to categories table
    CONSTRAINT fk_budget_category 
        FOREIGN KEY (category_name) 
        REFERENCES categories(name) 
        ON DELETE CASCADE 
        ON UPDATE CASCADE
) ENGINE=InnoDB;

-- 3. Optional: Initial Category Seeds (Example)
-- INSERT IGNORE INTO categories (name) VALUES ('Rent'), ('Groceries'), ('Utilities'), ('Entertainment');