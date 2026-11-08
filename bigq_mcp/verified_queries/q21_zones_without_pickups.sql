-- question: Which taxi zones had no pickups at all in 2025?
-- tables: taxi_zone_lookup, silver_yellow_taxi_trips
-- tags: zones,join,anti-join,coverage
-- difficulty: medium
-- notes: Anti-join: LEFT JOIN the zone lookup to aggregated trips and keep zones with no match. Starting from silver alone would never show zones with zero trips.
-- verified_by: seed
-- verified_at: 2026-11-08
WITH pickups AS (
  SELECT pu_location_id AS location_id, COUNT(*) AS trips
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  GROUP BY location_id
)
SELECT
  z.location_id,
  z.borough,
  z.zone,
  z.service_zone
FROM `{{PROJECT}}.nyc_taxi.taxi_zone_lookup` AS z
LEFT JOIN pickups AS p
  ON z.location_id = p.location_id
WHERE p.location_id IS NULL
ORDER BY z.borough, z.zone;
