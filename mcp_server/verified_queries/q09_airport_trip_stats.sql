-- question: What is the average distance, duration and fare of trips picked up at airports?
-- tables: silver_yellow_taxi_trips
-- tags: airport,duration,distance
-- difficulty: medium
-- notes: Airports are identified by pu_service_zone = 'Airports' (JFK, LaGuardia); Newark is service zone 'EWR'.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_zone AS airport,
  COUNT(*) AS trips,
  ROUND(AVG(trip_distance), 2) AS avg_miles,
  ROUND(AVG(TIMESTAMP_DIFF(dropoff_datetime, pickup_datetime, SECOND)) / 60, 1) AS avg_minutes,
  ROUND(AVG(total_amount), 2) AS avg_total_usd
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND pu_service_zone = 'Airports'
  AND trip_distance > 0
  AND dropoff_datetime > pickup_datetime
GROUP BY airport
ORDER BY trips DESC;
