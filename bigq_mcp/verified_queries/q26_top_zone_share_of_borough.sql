-- question: What are the top 3 pickup zones in each borough and what share of the borough's pickups does each take?
-- tables: silver_yellow_taxi_trips
-- tags: zones,borough,share,join,ranking,window
-- difficulty: hard
-- notes: Zone-level counts are joined to borough totals to get the share, then ranked per borough.
-- verified_by: seed
-- verified_at: 2026-11-08
WITH zone_trips AS (
  SELECT pu_borough, pu_zone, COUNT(*) AS trips
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
    AND pu_borough NOT IN ('Unknown', 'N/A')
    AND pu_zone IS NOT NULL
  GROUP BY pu_borough, pu_zone
),
borough_trips AS (
  SELECT pu_borough, SUM(trips) AS borough_total
  FROM zone_trips
  GROUP BY pu_borough
)
SELECT pu_borough, pu_zone, trips, pct_of_borough, rnk
FROM (
  SELECT
    z.pu_borough,
    z.pu_zone,
    z.trips,
    ROUND(100 * z.trips / b.borough_total, 2) AS pct_of_borough,
    ROW_NUMBER() OVER (PARTITION BY z.pu_borough ORDER BY z.trips DESC) AS rnk
  FROM zone_trips AS z
  JOIN borough_trips AS b
    ON z.pu_borough = b.pu_borough
)
WHERE rnk <= 3
ORDER BY pu_borough, rnk;
