-- question: How many yellow taxi trips were there in 2025?
-- tables: silver_yellow_taxi_trips
-- tags: count
-- difficulty: easy
-- verified_by: seed
-- verified_at: 2026-11-08
SELECT COUNT(*) AS total_trips
FROM `{{PROJECT}}.nyc_taxi.silver_yellow_taxi_trips`
WHERE pickup_date BETWEEN '2025-01-01' AND '2025-12-31';
