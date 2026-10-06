-- Marts: report-ready tables for Power BI.

CREATE OR REPLACE TABLE mart_net_worth AS
WITH a AS (
    SELECT month,
           sum(value) FILTER (asset_class = 'Cash reserves') AS cash_reserves,
           sum(value) FILTER (asset_class = 'Equity') AS equity,
           sum(value) FILTER (asset_class = 'Fixed income') AS fixed_income,
           coalesce(sum(value) FILTER (asset_class = 'Real estate'), 0) AS real_estate,
           sum(value) AS total_assets
    FROM fact_holdings GROUP BY month
),
l AS (
    SELECT month,
           coalesce(sum(outstanding) FILTER (liability <> 'Credit card'), 0) AS loans,
           coalesce(sum(outstanding) FILTER (liability = 'Credit card'), 0) AS card_balance,
           sum(outstanding) AS total_liabilities
    FROM fact_liabilities GROUP BY month
)
SELECT a.*, l.loans, l.card_balance, l.total_liabilities,
       a.total_assets - l.total_liabilities AS net_worth
FROM a JOIN l USING (month);

CREATE OR REPLACE TABLE mart_monthly AS
WITH t AS (
    SELECT month,
           sum(amount) FILTER (category = 'Bonus') AS bonus,
           coalesce(sum(amount) FILTER (category IN ('Investment income', 'Cashback')), 0) AS other_income,
           -sum(amount) FILTER (category_group = 'Fixed') AS fixed_spend,
           -sum(amount) FILTER (category_group = 'Discretionary') AS discretionary_spend,
           coalesce(-sum(amount) FILTER (category_group = 'Investments' AND amount < 0), 0) AS invested,
           coalesce(sum(amount) FILTER (category_group = 'Investments' AND amount > 0), 0) AS withdrawn,
           coalesce(-sum(amount) FILTER (category = 'Loan EMIs'), 0) AS emi
    FROM fact_transactions GROUP BY month
)
SELECT p.month,
       p.gross, p.tds AS income_tax, p.epf_employee, p.epf_employer, p.professional_tax,
       p.net_pay AS take_home,
       coalesce(t.bonus, 0) AS bonus,
       t.other_income,
       p.net_pay + coalesce(t.bonus, 0) + t.other_income AS income,
       t.fixed_spend, t.discretionary_spend,
       t.fixed_spend + t.discretionary_spend AS total_spend,
       t.invested, t.withdrawn,
       income - total_spend - (t.invested - t.withdrawn) AS cash_saved,
       (income - total_spend) / income AS savings_rate,
       t.emi,
       t.emi / p.gross AS debt_to_income,
       n.net_worth, n.total_assets, n.total_liabilities
FROM fact_payroll p
JOIN t USING (month)
JOIN mart_net_worth n USING (month);

-- Spending by category per month (positive = money spent).
CREATE OR REPLACE TABLE mart_category_monthly AS
SELECT month, category, category_group, -sum(amount) AS spend, count(*) AS txn_count
FROM fact_transactions
WHERE category_group IN ('Fixed', 'Discretionary')
GROUP BY month, category, category_group;

-- Sankey: gross pay -> deductions / money in -> fixed, discretionary, investments, cash -> categories.
CREATE OR REPLACE TABLE mart_cashflow_sankey AS
WITH m AS (SELECT * FROM mart_monthly),
flows AS (
    SELECT month, 1 AS stage, 'Gross pay' AS source, 'Income tax' AS target, income_tax AS amount FROM m
    UNION ALL SELECT month, 1, 'Gross pay', 'Provident fund', epf_employee FROM m
    UNION ALL SELECT month, 1, 'Gross pay', 'Professional tax', professional_tax FROM m
    UNION ALL SELECT month, 1, 'Gross pay', 'Take-home pay', take_home FROM m
    UNION ALL SELECT month, 2, 'Take-home pay', 'Money in', take_home FROM m
    UNION ALL SELECT month, 2, 'Bonus', 'Money in', bonus FROM m
    UNION ALL SELECT month, 2, 'Other income', 'Money in', other_income FROM m
    UNION ALL SELECT month, 2, 'Savings withdrawn', 'Money in', withdrawn FROM m
    UNION ALL SELECT month, 2, 'Cash drawn down', 'Money in', greatest(-cash_saved, 0) FROM m
    UNION ALL SELECT month, 3, 'Money in', 'Fixed costs', fixed_spend FROM m
    UNION ALL SELECT month, 3, 'Money in', 'Discretionary', discretionary_spend FROM m
    UNION ALL SELECT month, 3, 'Money in', 'Investments', invested FROM m
    UNION ALL SELECT month, 3, 'Money in', 'Cash saved', greatest(cash_saved, 0) FROM m
    UNION ALL
    SELECT month, 4, CASE category_group WHEN 'Fixed' THEN 'Fixed costs' ELSE 'Discretionary' END, category, spend
    FROM mart_category_monthly
    UNION ALL
    SELECT month, 4, 'Investments', category, -sum(amount)
    FROM fact_transactions
    WHERE category_group = 'Investments' AND amount < 0
    GROUP BY month, category
)
SELECT month, stage, source, target, round(amount, 2) AS amount
FROM flows WHERE amount > 0;

-- FIRE baseline with default assumptions. Power BI recomputes this live from what-if sliders;
-- this table is the reference the DAX is checked against.
CREATE OR REPLACE TABLE mart_fire_inputs AS
WITH last12 AS (SELECT * FROM mart_monthly ORDER BY month DESC LIMIT 12)
SELECT (SELECT max(month) FROM mart_monthly) AS as_of,
       31 AS current_age,
       (SELECT net_worth FROM mart_net_worth ORDER BY month DESC LIMIT 1) AS investable_assets,
       sum(total_spend - emi) - coalesce(sum(spend) FILTER (category = 'Vehicle purchase'), 0) AS annual_spend,
       sum(invested - withdrawn + cash_saved + epf_employee + epf_employer) AS annual_contribution
FROM last12
LEFT JOIN (SELECT month, category, spend FROM mart_category_monthly WHERE category = 'Vehicle purchase') v USING (month);

CREATE OR REPLACE TABLE mart_fire_projection AS
WITH p AS (
    SELECT *, 0.10 AS nominal_return, 0.06 AS inflation, 0.035 AS withdrawal_rate, 0.07 AS contribution_growth
    FROM mart_fire_inputs
)
SELECT y AS year_offset,
       current_age + y AS age,
       year(as_of) + y AS year,
       investable_assets * pow(1 + nominal_return, y)
         + annual_contribution * (pow(1 + nominal_return, y) - pow(1 + contribution_growth, y))
           / (nominal_return - contribution_growth) AS projected_assets,
       annual_spend * pow(1 + inflation, y) / withdrawal_rate AS fire_target
FROM p, range(0, 41) AS r(y);
