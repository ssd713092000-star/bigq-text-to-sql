-- question: Show daily trips with a 7-day moving average for Q1 2025.
-- tables: silver_yellow_taxi_trips
-- tags: trend,window,daily
-- difficulty: hard
-- verified_by: seed
-- verified_at: 2026-11-08
WITH daily AS (
  SELECT pickup_date, COUNT(*) AS trips
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-03-31'
  GROUP BY pickup_date
)
SELECT
  pickup_date,
  trips,
  ROUND(AVG(trips) OVER (ORDER BY pickup_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 0) AS trips_7d_avg
FROM daily
ORDER BY pickup_date;
