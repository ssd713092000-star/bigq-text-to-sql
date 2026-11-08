-- question: What is the average tip percentage for credit card trips on each day of the week?
-- tables: silver_yellow_taxi_trips
-- tags: tips,weekday
-- difficulty: medium
-- notes: Tips are only recorded for credit-card payments, so filter to them. Tip % = tip / fare.
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT
  EXTRACT(DAYOFWEEK FROM pickup_date) AS dow_num,
  pickup_day_of_week,
  ROUND(AVG(SAFE_DIVIDE(tip_amount, fare_amount)) * 100, 2) AS avg_tip_pct,
  COUNT(*) AS trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31'
  AND payment_type = 'Credit card'
  AND fare_amount > 0
GROUP BY dow_num, pickup_day_of_week
ORDER BY dow_num;
