-- question: What share of trips had 3 or more passengers?
-- tables: silver_yellow_taxi_trips
-- tags: passengers,share
-- difficulty: medium
-- notes: passenger_count can be NULL or 0 (driver didn't enter it); report those separately.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  CASE
    WHEN passenger_count IS NULL OR passenger_count = 0 THEN 'Unknown'
    WHEN passenger_count >= 3 THEN '3+'
    ELSE '1-2'
  END AS passenger_group,
  COUNT(*) AS trips,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY passenger_group
ORDER BY trips DESC;
