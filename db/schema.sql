-- Piggyback schema (PostgreSQL 16 + PostGIS 3.4).
-- Times are hours since scenario start, matching the simulator.
-- Column order of each table matches the simulator CSVs; geometry columns come
-- last and are filled by db/load.sql.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS scenario_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE TABLE IF NOT EXISTS hubs (
    hub_id              text PRIMARY KEY,
    name                text NOT NULL,
    state               text NOT NULL,
    lat                 double precision NOT NULL,
    lon                 double precision NOT NULL,
    sort_capacity_per_h integer NOT NULL CHECK (sort_capacity_per_h > 0),
    geom                geography(Point, 4326)
);

CREATE TABLE IF NOT EXISTS lanes (
    lane_id     text PRIMARY KEY,
    from_hub    text NOT NULL REFERENCES hubs,
    to_hub      text NOT NULL REFERENCES hubs,
    distance_km double precision NOT NULL CHECK (distance_km > 0),
    travel_h    double precision NOT NULL CHECK (travel_h > 0),
    geom        geography(LineString, 4326),
    CHECK (from_hub <> to_hub),
    UNIQUE (from_hub, to_hub)
);

CREATE TABLE IF NOT EXISTS corridors (
    corridor_id       text PRIMARY KEY,
    hub_sequence      text NOT NULL,
    vehicle_type      text NOT NULL,
    frequency_h       integer NOT NULL CHECK (frequency_h IN (12, 24)),
    first_departure_h double precision NOT NULL,
    capacity_kg       double precision NOT NULL,
    capacity_m3       double precision NOT NULL,
    cost_per_km_inr   double precision NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id     text PRIMARY KEY,
    corridor_id text NOT NULL REFERENCES corridors,
    departure_h double precision NOT NULL,
    arrival_h   double precision NOT NULL,
    CHECK (arrival_h > departure_h)
);

CREATE TABLE IF NOT EXISTS trip_legs (
    leg_id      text PRIMARY KEY,
    trip_id     text NOT NULL REFERENCES trips,
    seq         integer NOT NULL CHECK (seq >= 1),
    lane_id     text NOT NULL REFERENCES lanes,
    from_hub    text NOT NULL REFERENCES hubs,
    to_hub      text NOT NULL REFERENCES hubs,
    departure_h double precision NOT NULL,
    arrival_h   double precision NOT NULL,
    spare_kg    double precision NOT NULL CHECK (spare_kg >= 0),
    spare_m3    double precision NOT NULL CHECK (spare_m3 >= 0),
    UNIQUE (trip_id, seq),
    CHECK (arrival_h > departure_h)
);
CREATE INDEX IF NOT EXISTS trip_legs_from_dep ON trip_legs (from_hub, departure_h);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id       text PRIMARY KEY,
    origin_hub        text NOT NULL REFERENCES hubs,
    dest_hub          text NOT NULL REFERENCES hubs,
    priority          text NOT NULL CHECK (priority IN ('EXPRESS', 'STANDARD', 'ECONOMY')),
    weight_kg         double precision NOT NULL CHECK (weight_kg > 0),
    volume_m3         double precision NOT NULL CHECK (volume_m3 > 0),
    created_h         double precision NOT NULL,
    deadline_h        double precision NOT NULL,
    planned_legs      text NOT NULL,
    planned_arrival_h double precision NOT NULL,
    CHECK (origin_hub <> dest_hub),
    CHECK (deadline_h > created_h)
);

CREATE TABLE IF NOT EXISTS scan_events (
    scan_id     text PRIMARY KEY,
    shipment_id text NOT NULL REFERENCES shipments,
    ts_h        double precision NOT NULL,
    hub_id      text NOT NULL REFERENCES hubs,
    event_type  text NOT NULL CHECK (event_type IN ('INDUCTED', 'LOADED', 'UNLOADED', 'HELD', 'DELIVERED')),
    leg_id      text REFERENCES trip_legs,
    note        text
);
CREATE INDEX IF NOT EXISTS scan_events_shipment_ts ON scan_events (shipment_id, ts_h);

CREATE TABLE IF NOT EXISTS disruptions (
    disruption_id text PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('TRIP_BREAKDOWN', 'HUB_CLOSURE', 'LANE_DELAY')),
    start_h       double precision NOT NULL,
    end_h         double precision NOT NULL,
    trip_id       text REFERENCES trips,
    hub_id        text REFERENCES hubs,
    lane_id       text REFERENCES lanes,
    delay_h       double precision,
    CHECK (end_h > start_h)
);

-- Ground truth for scoring detection. Detection code must never read this table.
CREATE TABLE IF NOT EXISTS sim_truth (
    shipment_id   text PRIMARY KEY REFERENCES shipments,
    anomaly_type  text NOT NULL CHECK (anomaly_type IN ('MISROUTE', 'LOST_SCAN', 'HELD', 'MISSORT')),
    stranded_at_h double precision NOT NULL,
    true_hub_id   text NOT NULL REFERENCES hubs,
    incident_id   text
);
