-- Load one scenario's CSVs. Replaces whatever scenario is loaded.
--   psql -v ON_ERROR_STOP=1 -v dir=/data/scenario_42 -f db/load.sql
-- `dir` is the CSV directory as psql (the client) sees it.

\cd :dir

BEGIN;

TRUNCATE scenario_meta, hubs, lanes, corridors, trips, trip_legs, shipments,
         scan_events, disruptions, sim_truth;

\copy scenario_meta (key, value) FROM 'scenario_meta.csv' WITH (FORMAT csv, HEADER true)
\copy hubs (hub_id, name, state, lat, lon, sort_capacity_per_h) FROM 'hubs.csv' WITH (FORMAT csv, HEADER true)
\copy lanes (lane_id, from_hub, to_hub, distance_km, travel_h) FROM 'lanes.csv' WITH (FORMAT csv, HEADER true)
\copy corridors (corridor_id, hub_sequence, vehicle_type, frequency_h, first_departure_h, capacity_kg, capacity_m3, cost_per_km_inr) FROM 'corridors.csv' WITH (FORMAT csv, HEADER true)
\copy trips (trip_id, corridor_id, departure_h, arrival_h) FROM 'trips.csv' WITH (FORMAT csv, HEADER true)
\copy trip_legs (leg_id, trip_id, seq, lane_id, from_hub, to_hub, departure_h, arrival_h, spare_kg, spare_m3) FROM 'trip_legs.csv' WITH (FORMAT csv, HEADER true)
\copy shipments (shipment_id, origin_hub, dest_hub, priority, weight_kg, volume_m3, created_h, deadline_h, planned_legs, planned_arrival_h) FROM 'shipments.csv' WITH (FORMAT csv, HEADER true)
\copy scan_events (scan_id, shipment_id, ts_h, hub_id, event_type, leg_id, note) FROM 'scan_events.csv' WITH (FORMAT csv, HEADER true)
\copy disruptions (disruption_id, kind, start_h, end_h, trip_id, hub_id, lane_id, delay_h) FROM 'disruptions.csv' WITH (FORMAT csv, HEADER true)
\copy sim_truth (shipment_id, anomaly_type, stranded_at_h, true_hub_id, incident_id) FROM 'sim_truth.csv' WITH (FORMAT csv, HEADER true)

UPDATE hubs SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography;

UPDATE lanes l
SET geom = ST_MakeLine(f.geom::geometry, t.geom::geometry)::geography
FROM hubs f, hubs t
WHERE f.hub_id = l.from_hub AND t.hub_id = l.to_hub;

COMMIT;

ANALYZE;

SELECT 'hubs' AS tbl, count(*) FROM hubs
UNION ALL SELECT 'lanes', count(*) FROM lanes
UNION ALL SELECT 'trips', count(*) FROM trips
UNION ALL SELECT 'trip_legs', count(*) FROM trip_legs
UNION ALL SELECT 'shipments', count(*) FROM shipments
UNION ALL SELECT 'scan_events', count(*) FROM scan_events
UNION ALL SELECT 'disruptions', count(*) FROM disruptions
UNION ALL SELECT 'sim_truth', count(*) FROM sim_truth;
