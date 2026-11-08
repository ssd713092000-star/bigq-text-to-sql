-- question: How many trips were there each month in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: trend,monthly
-- difficulty: easy
-- notes: Order by month number, not month name (names sort alphabetically).
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  EXTRACT(MONTH FROM pickup_date) AS month_num,
  pickup_month,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY month_num, pickup_month
ORDER BY month_num;
