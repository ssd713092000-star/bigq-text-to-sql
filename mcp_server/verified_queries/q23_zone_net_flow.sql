-- question: Which zones have the biggest imbalance between pickups and dropoffs in 2025?
-- tables: taxi_zone_lookup, silver_yellow_taxi_trips
-- tags: zones,flow,join,full-outer-join
-- difficulty: hard
-- notes: Pickups and dropoffs are aggregated separately and FULL OUTER JOINed so zones with only one side still appear. net_inflow > 0 means more trips end in the zone than start there.
-- verified_by: seed
-- verified_at: 2026-11-08
WITH pickups AS (
  SELECT pu_location_id AS location_id, COUNT(*) AS pickups
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  GROUP BY location_id
),
dropoffs AS (
  SELECT do_location_id AS location_id, COUNT(*) AS dropoffs
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  GROUP BY location_id
),
flow AS (
  SELECT
    COALESCE(p.location_id, d.location_id) AS location_id,
    IFNULL(p.pickups, 0) AS pickups,
    IFNULL(d.dropoffs, 0) AS dropoffs
  FROM pickups AS p
  FULL OUTER JOIN dropoffs AS d
    ON p.location_id = d.location_id
)
SELECT
  z.borough,
  z.zone,
  f.pickups,
  f.dropoffs,
  f.dropoffs - f.pickups AS net_inflow
FROM flow AS f
JOIN `{{PROJECT}}.nyc_taxi.taxi_zone_lookup` AS z
  ON f.location_id = z.location_id
WHERE z.borough NOT IN ('Unknown', 'N/A')
ORDER BY ABS(f.dropoffs - f.pickups) DESC
LIMIT 20;
