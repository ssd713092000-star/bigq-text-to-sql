-- question: What is the busiest hour of the day for taxi pickups?
-- tables: silver_yellow_taxi_trips
-- tags: time,hourly
-- difficulty: easy
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  EXTRACT(HOUR FROM pickup_datetime) AS pickup_hour,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY pickup_hour
ORDER BY trips DESC;
