This `README.md` is designed to position your project as a professional engineering tool from **Caronte Developers**. It balances technical architecture with a clear product vision, making it perfect for a public GitHub profile.

### `README.md`

# 📊 BudgetArchitect

**A strategic financial management engine for precision budgeting.** Developed by **Caronte Developers**, this application transforms raw transaction data from Splitwise into a high-level financial roadmap. It is designed to handle individual and household-level financial planning with a focus on data integrity and real-time projections.

---

## 🛠 Engineering Architecture

The core of the application is built on a **Relational Bridge Architecture**. Unlike simple trackers, BudgetArchitect decouples transaction imports from budget targets.

### Key Logic:
* **Natural Key Mapping**: Uses `category_name` as the primary bridge between CSV imports and user targets, ensuring persistence even if internal database IDs shift.
* **Upsert Pattern**: Implements `ON DUPLICATE KEY UPDATE` logic for batch-saving budget strategies, minimizing API latency and database overhead.
* **Projection Engine**: Real-time JavaScript logic calculates "Allowed Spend" and "Target Savings" dynamically as itemized costs are adjusted.

---

## 🚀 Features

* **Multi-Profile Support**: Seamlessly toggle between individual views (**Gus**, **Joules**) and a consolidated **🏠 Household** view.
* **Itemized Budgeting**: Set granular targets for every category imported via Splitwise.
* **High-Contrast UI**: A custom-engineered dark mode interface designed for data density and maximum readability.
* **Real-time Burn Rate**: Live percentage calculations to visualize how much of your net income is committed to fixed costs vs. savings.



---

## 🏗 Database Schema

The application utilizes a MySQL/MariaDB backend with the following relational structure:

* **`categories`**: The source of truth for all transaction types.
* **`budgets`**: Stores user-specific limits, linked via `category_name` with a `UNIQUE` constraint on `(user_id, category_name)`.



---

## ⚙️ Installation & Setup

1. **Clone the repository**
   ```bash
   git clone [https://github.com/Elric-dev/BudgetApp.git](https://github.com/Elric-dev/BudgetApp.git)
   cd BudgetApp

```

2. **Initialize the Database**
Execute the `schema.sql` file in your MySQL environment to set up tables and constraints:
```bash
mysql -u [user] -p [database_name] < schema.sql

```


3. **Configure Environment**
Create a `.env` file to manage your database credentials (ensure this is ignored by git):
```text
DB_HOST=localhost
DB_USER=your_user
DB_PASS=your_password
DB_NAME=budget_db

```


4. **Run the Server**
```bash
python app.py

```



---

## 🔮 Roadmap & Future Development

I will be actively evolving this tool to include advanced wealth management features:

### 1. Budget vs. Actual (BvA) Visualization

Implementation of real-time progress bars on the main dashboard to compare current month spending against the targets defined in the Budget Planner.

### 2. Automated Buffer Allocation

Logic to automatically calculate "Excess Liquidity" after targets and savings goals are met, suggesting rebalancing amounts for investment accounts.

### 3. Splitwise API Integration

Moving from manual CSV imports to a direct API sync to provide hourly updates on household spending.

### 4. Machine Learning Forecasting

Integration of a predictive model to forecast future "Burn Rates" based on historical seasonality (e.g., higher utility costs in winter).


---
Interested in helping out?
Contact me on linkedin or my email to discuss colaboration avenues!