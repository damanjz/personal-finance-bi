-- Staging: load raw exports as typed tables. No business logic here.

CREATE OR REPLACE TABLE stg_bank AS
SELECT row_number() OVER () AS line_no, CAST(txn_date AS DATE) AS txn_date, description,
       CAST(debit AS DECIMAL(14, 2)) AS debit, CAST(credit AS DECIMAL(14, 2)) AS credit,
       CAST(balance AS DECIMAL(14, 2)) AS balance
FROM read_csv('data/raw/bank_statement.csv', header = true);

CREATE OR REPLACE TABLE stg_card AS
SELECT row_number() OVER () AS line_no, CAST(txn_date AS DATE) AS txn_date, description,
       CAST(amount AS DECIMAL(14, 2)) AS amount, dr_cr
FROM read_csv('data/raw/credit_card_statement.csv', header = true);

-- One signed ledger: money in is positive, money out is negative.
CREATE OR REPLACE TABLE stg_transactions AS
SELECT 'bank' AS source, 'B' || line_no AS txn_id, txn_date, description, credit - debit AS amount
FROM stg_bank
UNION ALL
SELECT 'card', 'C' || line_no, txn_date, description, CASE WHEN dr_cr = 'Dr' THEN -amount ELSE amount END
FROM stg_card;

CREATE OR REPLACE TABLE stg_rules AS
SELECT * FROM read_csv('config/category_rules.csv', header = true, all_varchar = true);

CREATE OR REPLACE TABLE stg_categories AS
SELECT * FROM read_csv('config/categories.csv', header = true);

CREATE OR REPLACE TABLE stg_payslips AS
SELECT CAST(pay_month || '-01' AS DATE) AS month, * EXCLUDE (pay_month)
FROM read_csv('data/raw/payslips.csv', header = true);

CREATE OR REPLACE TABLE stg_mf AS
SELECT CAST(txn_date AS DATE) AS txn_date, scheme AS instrument, txn_type, amount, nav AS price, units
FROM read_csv('data/raw/mf_transactions.csv', header = true);

CREATE OR REPLACE TABLE stg_reit AS
SELECT CAST(txn_date AS DATE) AS txn_date, instrument, txn_type, units * price AS amount, price, units
FROM read_csv('data/raw/reit_transactions.csv', header = true);

CREATE OR REPLACE TABLE stg_prices AS
SELECT date_trunc('month', CAST(month_end AS DATE))::DATE AS month, instrument, price
FROM read_csv('data/raw/price_history.csv', header = true);

CREATE OR REPLACE TABLE stg_epf AS
SELECT CAST(month || '-01' AS DATE) AS month, contribution, interest, balance
FROM read_csv('data/raw/epf_passbook.csv', header = true);

CREATE OR REPLACE TABLE stg_ppf AS
SELECT CAST(month || '-01' AS DATE) AS month, deposit, interest, balance
FROM read_csv('data/raw/ppf_passbook.csv', header = true);

CREATE OR REPLACE TABLE stg_fd AS
SELECT fd_id, CAST(open_date AS DATE) AS open_date, principal, annual_rate, CAST(close_date AS DATE) AS close_date
FROM read_csv('data/raw/fd_register.csv', header = true);

CREATE OR REPLACE TABLE stg_loans AS
SELECT loan_id, lender, loan_type, principal, annual_rate, tenure_months, CAST(first_emi_date AS DATE) AS first_emi_date
FROM read_csv('data/raw/loans.csv', header = true);
