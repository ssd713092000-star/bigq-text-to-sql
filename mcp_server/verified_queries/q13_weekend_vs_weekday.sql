-- question: How do average fares and trip counts compare between weekends and weekdays?
-- tables: silver_yellow_taxi_trips
-- tags: comparison,weekday
-- difficulty: medium
-- notes: In BigQuery DAYOFWEEK, 1 = Sunday and 7 = Saturday.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  IF(EXTRACT(DAYOFWEEK FROM pickup_date) IN (1, 7), 'Weekend', 'Weekday') AS day_type,
  COUNT(*) AS trips,
  ROUND(COUNT(*) / COUNT(DISTINCT pickup_date), 0) AS avg_trips_per_day,
  ROUND(AVG(total_amount), 2) AS avg_total_usd
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND total_amount > 0
GROUP BY day_type;
