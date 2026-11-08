-- question: What is the average fare per mile in each pickup borough?
-- tables: silver_yellow_taxi_trips
-- tags: fare,borough
-- difficulty: medium
-- notes: Exclude zero-distance and very long trips so the ratio isn't distorted.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_borough,
  ROUND(SUM(fare_amount) / SUM(trip_distance), 2) AS fare_per_mile_usd,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND trip_distance BETWEEN 0.1 AND 100
  AND fare_amount > 0
GROUP BY pu_borough
ORDER BY fare_per_mile_usd DESC;
