-- question: What was the total revenue by pickup borough in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: revenue,borough
-- difficulty: easy
-- notes: Revenue = SUM(total_amount) excluding Voided trips and negative amounts (refunds).
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_borough,
  ROUND(SUM(total_amount), 2) AS revenue_usd,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND payment_type != 'Voided'
  AND total_amount > 0
GROUP BY pu_borough
ORDER BY revenue_usd DESC;
