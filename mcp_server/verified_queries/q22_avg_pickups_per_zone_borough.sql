-- question: How many zones does each borough have, how many were active, and what is the average number of pickups per zone in 2025?
-- tables: taxi_zone_lookup, silver_yellow_taxi_trips
-- tags: zones,borough,join,density
-- difficulty: medium
-- notes: Zone counts come from taxi_zone_lookup so zones with zero pickups still count in the average.
-- verified_by: seed
-- verified_at: 2026-11-08
WITH zone_trips AS (
  SELECT pu_location_id AS location_id, COUNT(*) AS trips
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  GROUP BY location_id
)
SELECT
  z.borough,
  COUNT(*) AS zones,
  COUNTIF(t.trips IS NOT NULL) AS active_zones,
  SUM(IFNULL(t.trips, 0)) AS pickups,
  ROUND(SUM(IFNULL(t.trips, 0)) / COUNT(*), 0) AS avg_pickups_per_zone
FROM `{{PROJECT}}.nyc_taxi.taxi_zone_lookup` AS z
LEFT JOIN zone_trips AS t
  ON z.location_id = t.location_id
WHERE z.borough NOT IN ('Unknown', 'N/A')
GROUP BY z.borough
ORDER BY avg_pickups_per_zone DESC;
