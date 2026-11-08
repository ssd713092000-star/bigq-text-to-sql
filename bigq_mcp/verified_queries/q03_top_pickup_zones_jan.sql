-- question: What were the top 10 pickup zones by number of trips in January 2025?
-- tables: silver_yellow_taxi_trips
-- tags: ranking,zones
-- difficulty: easy
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT pu_zone, pu_borough, COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-01-31'
GROUP BY pu_zone, pu_borough
ORDER BY trips DESC
LIMIT 10;
