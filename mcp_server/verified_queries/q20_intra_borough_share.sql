-- question: What percentage of trips from each borough stay within the same borough?
-- tables: silver_yellow_taxi_trips
-- tags: routes,share,borough
-- difficulty: hard
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_borough,
  COUNT(*) AS trips,
  ROUND(100 * COUNTIF(do_borough = pu_borough) / COUNT(*), 2) AS same_borough_pct
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND pu_borough NOT IN ('Unknown', 'N/A')
GROUP BY pu_borough
ORDER BY trips DESC;
