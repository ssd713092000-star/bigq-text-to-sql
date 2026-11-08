-- question: How many trips started at JFK Airport each day in March 2025?
-- tables: silver_yellow_taxi_trips
-- tags: airport,daily
-- difficulty: medium
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT pickup_date, COUNT(*) AS jfk_pickups
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-03-01' AND '2025-03-31'
  AND pu_zone = 'JFK Airport'
GROUP BY pickup_date
ORDER BY pickup_date;
