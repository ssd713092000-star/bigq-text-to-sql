-- question: What is the average trip duration in minutes by hour of day?
-- tables: silver_yellow_taxi_trips
-- tags: duration,hourly
-- difficulty: medium
-- notes: Keep trips between 1 and 180 minutes to drop meter errors.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  EXTRACT(HOUR FROM pickup_datetime) AS pickup_hour,
  ROUND(AVG(TIMESTAMP_DIFF(dropoff_datetime, pickup_datetime, SECOND)) / 60, 1) AS avg_minutes,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND TIMESTAMP_DIFF(dropoff_datetime, pickup_datetime, MINUTE) BETWEEN 1 AND 180
GROUP BY pickup_hour
ORDER BY pickup_hour;
