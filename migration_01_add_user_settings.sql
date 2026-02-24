-- Migration to add the user_settings table for storing global goals.

USE budget_tracker;

-- Create the user_settings table if it doesn't exist
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INT PRIMARY KEY,
    savings_goal_pct DECIMAL(5, 2) DEFAULT 20.00,
    expenses_goal_pct DECIMAL(5, 2) DEFAULT 50.00,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Insert default settings for existing users, ignoring duplicates
INSERT IGNORE INTO user_settings (user_id, savings_goal_pct, expenses_goal_pct) VALUES 
(0, 40.00, 50.00),
(1, 20.00, 50.00),
(2, 20.00, 50.00);
