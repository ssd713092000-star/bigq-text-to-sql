-- question: What are the 10 most common pickup-to-dropoff routes?
-- tables: silver_yellow_taxi_trips
-- tags: routes,zones
-- difficulty: medium
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_zone AS pickup_zone,
  do_zone AS dropoff_zone,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND pu_zone IS NOT NULL
  AND do_zone IS NOT NULL
GROUP BY pickup_zone, dropoff_zone
ORDER BY trips DESC
LIMIT 10;
