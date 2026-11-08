-- question: For JFK flat-rate trips (ratecode 2) in 2025, which pickup and dropoff zones are most common?
-- tables: yellow_taxi_trips, taxi_zone_lookup
-- tags: airport,ratecode,join,bronze
-- difficulty: medium
-- notes: Uses the bronze table joined to taxi_zone_lookup twice (pickup and dropoff), since ratecode_id is only a code. Bronze is partitioned by DATE(pickup_datetime), so filter on pickup_datetime.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pz.zone AS pickup_zone,
  dz.zone AS dropoff_zone,
  COUNT(*) AS trips,
  ROUND(AVG(t.fare_amount), 2) AS avg_fare_usd,
  ROUND(AVG(t.total_amount), 2) AS avg_total_usd
FROM `{{PROJECT}}.nyc_taxi.yellow_taxi_trips` AS t
LEFT JOIN `{{PROJECT}}.nyc_taxi.taxi_zone_lookup` AS pz
  ON t.pu_location_id = pz.location_id
LEFT JOIN `{{PROJECT}}.nyc_taxi.taxi_zone_lookup` AS dz
  ON t.do_location_id = dz.location_id
WHERE t.pickup_datetime >= TIMESTAMP('2025-01-01')
  AND t.pickup_datetime < TIMESTAMP('2026-01-01')
  AND t.ratecode_id = 2
GROUP BY pickup_zone, dropoff_zone
ORDER BY trips DESC
LIMIT 15;
