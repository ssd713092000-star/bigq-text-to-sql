-- question: For each pickup borough, what are the top 3 dropoff zones?
-- tables: silver_yellow_taxi_trips
-- tags: ranking,window,routes
-- difficulty: hard
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT pu_borough, do_zone, trips, rnk
FROM (
  SELECT
    pu_borough,
    do_zone,
    COUNT(*) AS trips,
    ROW_NUMBER() OVER (PARTITION BY pu_borough ORDER BY COUNT(*) DESC) AS rnk
  FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
  WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
    AND pu_borough NOT IN ('Unknown', 'N/A')
    AND do_zone IS NOT NULL
  GROUP BY pu_borough, do_zone
)
WHERE rnk <= 3
ORDER BY pu_borough, rnk;
