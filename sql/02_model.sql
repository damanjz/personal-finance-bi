-- Model: star schema. Dimensions, categorised transactions, balances.

CREATE OR REPLACE TABLE dim_month AS
SELECT month,
       year(month) AS year,
       strftime(month, '%b %Y') AS month_label,
       CASE WHEN month(month) >= 4 THEN year(month) ELSE year(month) - 1 END AS fy_start,
       'FY ' || fy_start || '-' || right(CAST(fy_start + 1 AS VARCHAR), 2) AS fiscal_year,
       row_number() OVER (ORDER BY month) AS month_index,
       last_day(month) AS month_end
FROM (SELECT DISTINCT date_trunc('month', txn_date)::DATE AS month FROM stg_bank);

CREATE OR REPLACE TABLE dim_category AS
SELECT category, category_group, sort_order FROM stg_categories;

-- First matching rule wins (lowest priority number).
CREATE OR REPLACE TABLE fact_transactions AS
WITH matched AS (
    SELECT t.*, r.category,
           row_number() OVER (PARTITION BY t.txn_id ORDER BY CAST(r.priority AS INTEGER)) AS rn
    FROM stg_transactions t
    LEFT JOIN stg_rules r
      ON (r.source = t.source OR r.source = 'any') AND regexp_matches(t.description, r.pattern)
)
SELECT m.txn_id, m.source, m.txn_date, date_trunc('month', m.txn_date)::DATE AS month,
       m.description, m.amount,
       coalesce(m.category, 'Uncategorized') AS category,
       coalesce(c.category_group, 'Uncategorized') AS category_group
FROM matched m
LEFT JOIN stg_categories c USING (category)
WHERE m.rn = 1;

CREATE OR REPLACE TABLE fact_payroll AS
SELECT * FROM stg_payslips;

-- Full amortisation schedule for every loan.
CREATE OR REPLACE TABLE fact_loan_schedule AS
WITH RECURSIVE terms AS (
    SELECT loan_id, annual_rate / 12 AS r, tenure_months AS n, first_emi_date, principal,
           round(principal * (annual_rate / 12) * pow(1 + annual_rate / 12, tenure_months)
                 / (pow(1 + annual_rate / 12, tenure_months) - 1)) AS emi
    FROM stg_loans
),
s AS (
    SELECT loan_id, 1 AS k, first_emi_date AS emi_date, emi, r, n,
           principal AS opening, principal * r AS interest
    FROM terms
    UNION ALL
    SELECT loan_id, k + 1, (emi_date + INTERVAL 1 MONTH)::DATE, emi, r, n,
           opening - (emi - interest), (opening - (emi - interest)) * r
    FROM s WHERE k < n
)
SELECT loan_id, k AS installment, emi_date, date_trunc('month', emi_date)::DATE AS month, emi,
       round(opening, 2) AS opening,
       round(interest, 2) AS interest,
       round(CASE WHEN k = n THEN opening ELSE emi - interest END, 2) AS principal_paid,
       round(CASE WHEN k = n THEN 0 ELSE opening - (emi - interest) END, 2) AS closing
FROM s;

-- Month-end value of every holding.
CREATE OR REPLACE TABLE fact_holdings AS
WITH units AS (
    SELECT instrument, txn_date, units FROM stg_mf
    UNION ALL
    SELECT instrument, txn_date, units FROM stg_reit
),
market AS (
    SELECT d.month, p.instrument,
           sum(u.units) * p.price AS value
    FROM dim_month d
    JOIN stg_prices p ON p.month = d.month
    JOIN units u ON u.instrument = p.instrument AND u.txn_date <= d.month_end
    GROUP BY d.month, p.instrument, p.price
),
fd AS (
    SELECT d.month, f.fd_id AS instrument,
           f.principal * pow(1 + f.annual_rate / 4,
                             floor(datediff('month', f.open_date, d.month_end) / 3)) AS value
    FROM dim_month d
    JOIN stg_fd f ON f.open_date <= d.month_end AND (f.close_date IS NULL OR f.close_date > d.month_end)
),
bank AS (
    SELECT month, 'Savings account' AS instrument, arg_max(balance, line_no) AS value
    FROM (SELECT *, date_trunc('month', txn_date)::DATE AS month FROM stg_bank)
    GROUP BY month
)
SELECT month, instrument, asset_class, round(value, 2) AS value
FROM (
    SELECT month, 'Nifty 50 index fund' AS instrument, 'Equity' AS asset_class, value FROM market WHERE instrument = 'NIFTY 50 INDEX FUND'
    UNION ALL SELECT month, 'Short-term debt fund', 'Fixed income', value FROM market WHERE instrument = 'SHORT TERM DEBT FUND'
    UNION ALL SELECT month, 'Office REIT', 'Real estate', value FROM market WHERE instrument = 'OFFICE REIT'
    UNION ALL SELECT month, 'EPF', 'Fixed income', balance FROM stg_epf
    UNION ALL SELECT month, 'PPF', 'Fixed income', balance FROM stg_ppf WHERE balance > 0
    UNION ALL SELECT month, 'Fixed deposit ' || right(instrument, 2), 'Cash reserves', value FROM fd
    UNION ALL SELECT month, instrument, 'Cash reserves', value FROM bank
);

-- Month-end debt: loan principal outstanding plus unpaid card balance.
CREATE OR REPLACE TABLE fact_liabilities AS
WITH loan_months AS (
    SELECT d.month, l.loan_id, l.loan_type, l.principal,
           (l.first_emi_date - INTERVAL 1 MONTH)::DATE AS disbursed,
           (SELECT s.closing FROM fact_loan_schedule s
             WHERE s.loan_id = l.loan_id AND s.emi_date <= d.month_end
             ORDER BY s.emi_date DESC LIMIT 1) AS closing
    FROM dim_month d CROSS JOIN stg_loans l
)
SELECT month, loan_type AS liability, round(coalesce(closing, principal), 2) AS outstanding
FROM loan_months
WHERE disbursed <= last_day(month) AND coalesce(closing, principal) > 0
UNION ALL
SELECT d.month, 'Credit card', round(-sum(c.amount), 2)
FROM dim_month d
JOIN fact_transactions c ON c.source = 'card' AND c.txn_date <= d.month_end
GROUP BY d.month;
