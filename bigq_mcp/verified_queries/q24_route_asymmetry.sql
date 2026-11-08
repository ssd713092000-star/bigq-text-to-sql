-- question: Which busy routes are the most one-directional, with far more trips A to B than B to A?
-- tables: silver_yellow_taxi_trips
-- tags: routes,self-join,asymmetry
-- difficulty: hard
-- notes: Self-join of route counts on reversed pickup/dropoff IDs. Join on location IDs, not zone names, because a few zone names repeat. Routes with no reverse trips get a NULL ratio and sort last.
-- verified_by: seed
-- verified_at: 2026-11-08
WITH routes AS (
  SELECT
    pu_location_id,
    pu_zone,
    do_location_id,
    do_zone,
    COUNT(*) AS trips
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
    AND pu_location_id <> do_location_id
    AND pu_borough NOT IN ('Unknown', 'N/A')
    AND do_borough NOT IN ('Unknown', 'N/A')
  GROUP BY pu_location_id, pu_zone, do_location_id, do_zone
)
SELECT
  a.pu_zone AS zone_a,
  a.do_zone AS zone_b,
  a.trips AS a_to_b_trips,
  IFNULL(b.trips, 0) AS b_to_a_trips,
  ROUND(a.trips / NULLIF(b.trips, 0), 2) AS direction_ratio
FROM routes AS a
LEFT JOIN routes AS b
  ON a.pu_location_id = b.do_location_id
  AND a.do_location_id = b.pu_location_id
WHERE a.trips >= 1000
ORDER BY direction_ratio DESC
LIMIT 20;
