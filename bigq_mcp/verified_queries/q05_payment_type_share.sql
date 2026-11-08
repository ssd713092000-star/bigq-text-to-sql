-- question: What percentage of trips used each payment type in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: share,payment
-- difficulty: easy
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  payment_type,
  COUNT(*) AS trips,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY payment_type
ORDER BY trips DESC;
