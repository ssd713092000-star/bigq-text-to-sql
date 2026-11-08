-- question: What was the month-over-month revenue growth in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: revenue,growth,window
-- difficulty: hard
-- verified_by: seed
-- verified_at: 2026-11-08
WITH monthly AS (
  SELECT
    DATE_TRUNC(pickup_date, MONTH) AS month,
    SUM(total_amount) AS revenue
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
    AND payment_type != 'Voided'
    AND total_amount > 0
  GROUP BY month
)
SELECT
  FORMAT_DATE('%Y-%m', month) AS month,
  ROUND(revenue, 2) AS revenue_usd,
  ROUND(100 * SAFE_DIVIDE(revenue - LAG(revenue) OVER (ORDER BY month),
                          LAG(revenue) OVER (ORDER BY month)), 2) AS mom_growth_pct
FROM monthly
ORDER BY month;
