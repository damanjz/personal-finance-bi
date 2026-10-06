-- Each check returns the rows that break a rule. A clean build returns zero rows for every check.

-- name: every transaction has a category
SELECT txn_id, description FROM fact_transactions WHERE category = 'Uncategorized';

-- name: bank running balance chains line by line
SELECT line_no FROM (
    SELECT line_no, balance,
           coalesce(lag(balance) OVER (ORDER BY line_no), 200000) + credit - debit AS expected
    FROM stg_bank
) WHERE abs(balance - expected) > 0.01;

-- name: bank balance never negative
SELECT line_no, balance FROM stg_bank WHERE balance < 0;

-- name: salary credit equals payslip net pay
SELECT p.month FROM fact_payroll p
LEFT JOIN (SELECT month, sum(amount) AS amt FROM fact_transactions WHERE category = 'Salary' GROUP BY month) s USING (month)
WHERE s.amt IS NULL OR abs(s.amt - p.net_pay) > 1;

-- name: EMI debits match the amortisation schedule
SELECT s.loan_id, s.month FROM fact_loan_schedule s
JOIN dim_month d USING (month)
LEFT JOIN (SELECT month, regexp_extract(description, '[A-Z]+-\d+$') AS loan_id, -sum(amount) AS paid
           FROM fact_transactions WHERE category = 'Loan EMIs' GROUP BY ALL) b USING (loan_id, month)
WHERE b.paid IS NULL OR abs(b.paid - s.emi) > 1;

-- name: card bill paid equals last month's net card charges
SELECT d.month FROM dim_month d
JOIN (SELECT month, -sum(amount) AS paid FROM fact_transactions
      WHERE source = 'bank' AND category = 'Card bill payment' GROUP BY month) p USING (month)
JOIN (SELECT (month + INTERVAL 1 MONTH)::DATE AS month, -sum(amount) AS charged FROM fact_transactions
      WHERE source = 'card' AND category <> 'Card bill payment' GROUP BY 1) c USING (month)
WHERE abs(p.paid - c.charged) > 1;

-- name: fund purchases in bank match the fund transaction log
SELECT month FROM (
    SELECT date_trunc('month', txn_date)::DATE AS month, sum(amount) AS logged
    FROM stg_mf WHERE txn_type <> 'OPENING' GROUP BY 1
) l
FULL JOIN (SELECT month, -sum(amount) AS paid FROM fact_transactions
           WHERE category IN ('Equity fund', 'Debt fund') GROUP BY month) b USING (month)
WHERE abs(coalesce(l.logged, 0) - coalesce(b.paid, 0)) > 1;

-- name: cash saved equals change in bank minus change in card debt
SELECT month FROM (
    SELECT m.month, m.cash_saved,
           h.value - coalesce(lag(h.value) OVER (ORDER BY m.month), 200000) AS bank_change,
           n.card_balance - coalesce(lag(n.card_balance) OVER (ORDER BY m.month), 0) AS card_change
    FROM mart_monthly m
    JOIN fact_holdings h ON h.month = m.month AND h.instrument = 'Savings account'
    JOIN mart_net_worth n ON n.month = m.month
) WHERE abs(cash_saved - (bank_change - card_change)) > 1;

-- name: sankey money-in hub balances every month
SELECT month FROM (
    SELECT month,
           sum(amount) FILTER (target = 'Money in') AS into_hub,
           sum(amount) FILTER (source = 'Money in') AS out_of_hub
    FROM mart_cashflow_sankey GROUP BY month
) WHERE abs(into_hub - out_of_hub) > 1;

-- name: sankey category flows add up to their parent
SELECT month, node FROM (
    SELECT month, target AS node, sum(amount) AS parent_in FROM mart_cashflow_sankey
    WHERE target IN ('Fixed costs', 'Discretionary', 'Investments') GROUP BY ALL
) p
JOIN (SELECT month, source AS node, sum(amount) AS children FROM mart_cashflow_sankey
      WHERE source IN ('Fixed costs', 'Discretionary', 'Investments') GROUP BY ALL) c USING (month, node)
WHERE abs(parent_in - children) > 1;

-- name: gross pay splits fully into deductions and take-home
SELECT month FROM fact_payroll
WHERE abs(gross - tds - epf_employee - professional_tax - net_pay) > 1;

-- name: no category spend is negative in any month
SELECT month, category, spend FROM mart_category_monthly WHERE spend < 0;

-- name: holdings are never negative
SELECT month, instrument FROM fact_holdings WHERE value < 0;

-- name: loans are fully repaid by the final installment
SELECT loan_id FROM fact_loan_schedule s
WHERE installment = (SELECT max(installment) FROM fact_loan_schedule t WHERE t.loan_id = s.loan_id)
  AND abs(closing) > 0.01;

-- name: FIRE projection starts at today's investable assets
SELECT * FROM mart_fire_projection p, mart_fire_inputs i
WHERE p.year_offset = 0 AND abs(p.projected_assets - i.investable_assets) > 1;
