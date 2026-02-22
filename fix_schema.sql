1USE budget_db;

-- Update users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255) DEFAULT NULL;
INSERT IGNORE INTO users (user_id, name) VALUES (2, 'Household');

-- Update categories table
ALTER TABLE categories MODIFY COLUMN name VARCHAR(255) NOT NULL;
ALTER TABLE categories MODIFY COLUMN parent_name VARCHAR(255) DEFAULT 'Other';

-- Ensure other tables have correct column sizes for foreign keys
ALTER TABLE budgets MODIFY COLUMN category_name VARCHAR(255) NOT NULL;
