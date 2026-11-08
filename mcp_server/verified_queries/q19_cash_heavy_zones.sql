-- question: Which pickup zones have the highest share of cash payments (at least 1,000 trips)?
-- tables: silver_yellow_taxi_trips
-- tags: payment,share,zones
-- difficulty: hard
-- notes: Use a minimum trip count so tiny zones don't top the list by chance.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  pu_zone,
  pu_borough,
  COUNT(*) AS trips,
  ROUND(100 * COUNTIF(payment_type = 'Cash') / COUNT(*), 2) AS cash_pct
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY pu_zone, pu_borough
HAVING COUNT(*) >= 1000
ORDER BY cash_pct DESC
LIMIT 15;
