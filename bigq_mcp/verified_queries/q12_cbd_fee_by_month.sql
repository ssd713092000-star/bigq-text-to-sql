-- question: How much congestion relief zone (CBD) toll was collected each month in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: congestion,monthly,fees
-- difficulty: medium
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  EXTRACT(MONTH FROM pickup_date) AS month_num,
  pickup_month,
  ROUND(SUM(cbd_congestion_fee), 2) AS cbd_fee_usd,
  COUNTIF(cbd_congestion_fee > 0) AS trips_charged
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY month_num, pickup_month
ORDER BY month_num;
