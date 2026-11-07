-- Piggyback schema v1 (PostgreSQL 16 + PostGIS 3.4).
-- Every scenario lives side by side, keyed by scenario_id. Times are hours since
-- the scenario's start_at. Simulator columns come first in each table, in the
-- order of the matching dataclass (tests enforce this); geometry comes last.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE scenarios (
    scenario_id text PRIMARY KEY CHECK (scenario_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    seed        integer NOT NULL,
    start_at    timestamptz NOT NULL,
    now_h       double precision NOT NULL,
    horizon_h   double precision NOT NULL,
    config      jsonb NOT NULL,
    summary     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hubs (
    scenario_id         text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    hub_id              text NOT NULL,
    name                text NOT NULL,
    state               text NOT NULL,
    lat                 double precision NOT NULL,
    lon                 double precision NOT NULL,
    sort_capacity_per_h integer NOT NULL CHECK (sort_capacity_per_h > 0),
    geom                geography(Point, 4326),
    PRIMARY KEY (scenario_id, hub_id)
);

CREATE TABLE lanes (
    scenario_id text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    lane_id     text NOT NULL,
    from_hub    text NOT NULL,
    to_hub      text NOT NULL,
    distance_km double precision NOT NULL CHECK (distance_km > 0),
    travel_h    double precision NOT NULL CHECK (travel_h > 0),
    geom        geography(LineString, 4326),
    PRIMARY KEY (scenario_id, lane_id),
    FOREIGN KEY (scenario_id, from_hub) REFERENCES hubs,
    FOREIGN KEY (scenario_id, to_hub) REFERENCES hubs,
    CHECK (from_hub <> to_hub)
);

CREATE TABLE corridors (
    scenario_id       text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    corridor_id       text NOT NULL,
    hub_sequence      text NOT NULL,
    vehicle_type      text NOT NULL,
    frequency_h       integer NOT NULL CHECK (frequency_h IN (12, 24)),
    first_departure_h double precision NOT NULL,
    capacity_kg       double precision NOT NULL,
    capacity_m3       double precision NOT NULL,
    cost_per_km_inr   double precision NOT NULL,
    PRIMARY KEY (scenario_id, corridor_id)
);

CREATE TABLE trips (
    scenario_id text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    trip_id     text NOT NULL,
    corridor_id text NOT NULL,
    departure_h double precision NOT NULL,
    arrival_h   double precision NOT NULL,
    PRIMARY KEY (scenario_id, trip_id),
    FOREIGN KEY (scenario_id, corridor_id) REFERENCES corridors,
    CHECK (arrival_h > departure_h)
);

CREATE TABLE trip_legs (
    scenario_id text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    leg_id      text NOT NULL,
    trip_id     text NOT NULL,
    seq         integer NOT NULL CHECK (seq >= 1),
    lane_id     text NOT NULL,
    from_hub    text NOT NULL,
    to_hub      text NOT NULL,
    departure_h double precision NOT NULL,
    arrival_h   double precision NOT NULL,
    spare_kg    double precision NOT NULL CHECK (spare_kg >= 0),
    spare_m3    double precision NOT NULL CHECK (spare_m3 >= 0),
    PRIMARY KEY (scenario_id, leg_id),
    UNIQUE (scenario_id, trip_id, seq),
    FOREIGN KEY (scenario_id, trip_id) REFERENCES trips,
    FOREIGN KEY (scenario_id, lane_id) REFERENCES lanes,
    CHECK (arrival_h > departure_h)
);
CREATE INDEX trip_legs_from_dep ON trip_legs (scenario_id, from_hub, departure_h);

CREATE TABLE shipments (
    scenario_id       text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    shipment_id       text NOT NULL,
    origin_hub        text NOT NULL,
    dest_hub          text NOT NULL,
    priority          text NOT NULL CHECK (priority IN ('EXPRESS', 'STANDARD', 'ECONOMY')),
    weight_kg         double precision NOT NULL CHECK (weight_kg > 0),
    volume_m3         double precision NOT NULL CHECK (volume_m3 > 0),
    created_h         double precision NOT NULL,
    deadline_h        double precision NOT NULL,
    planned_legs      text NOT NULL,
    planned_arrival_h double precision NOT NULL,
    PRIMARY KEY (scenario_id, shipment_id),
    FOREIGN KEY (scenario_id, origin_hub) REFERENCES hubs,
    FOREIGN KEY (scenario_id, dest_hub) REFERENCES hubs,
    CHECK (origin_hub <> dest_hub),
    CHECK (deadline_h > created_h)
);

CREATE TABLE scan_events (
    scenario_id text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    scan_id     text NOT NULL,
    shipment_id text NOT NULL,
    ts_h        double precision NOT NULL,
    recorded_h  double precision NOT NULL,
    hub_id      text NOT NULL,
    event_type  text NOT NULL CHECK (event_type IN ('INDUCTED', 'LOADED', 'UNLOADED', 'HELD', 'DELIVERED')),
    leg_id      text,
    note        text,
    PRIMARY KEY (scenario_id, scan_id),
    FOREIGN KEY (scenario_id, shipment_id) REFERENCES shipments,
    FOREIGN KEY (scenario_id, hub_id) REFERENCES hubs,
    FOREIGN KEY (scenario_id, leg_id) REFERENCES trip_legs,
    CHECK (recorded_h >= ts_h)
);
CREATE INDEX scan_events_shipment_ts ON scan_events (scenario_id, shipment_id, ts_h);

CREATE TABLE disruptions (
    scenario_id   text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    disruption_id text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('TRIP_BREAKDOWN', 'HUB_CLOSURE', 'LANE_DELAY')),
    start_h       double precision NOT NULL,
    end_h         double precision NOT NULL,
    trip_id       text,
    hub_id        text,
    lane_id       text,
    delay_h       double precision,
    PRIMARY KEY (scenario_id, disruption_id),
    FOREIGN KEY (scenario_id, trip_id) REFERENCES trips,
    FOREIGN KEY (scenario_id, hub_id) REFERENCES hubs,
    FOREIGN KEY (scenario_id, lane_id) REFERENCES lanes,
    CHECK (end_h > start_h)
);

-- Ground truth. Only scoring/replay code may read the sim_* tables.
CREATE TABLE sim_truth (
    scenario_id   text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    shipment_id   text NOT NULL,
    anomaly_type  text NOT NULL CHECK (anomaly_type IN
                      ('MISROUTE', 'LOST_SCAN', 'HELD', 'MISSORT', 'MISSED_CONNECTION')),
    stranded_at_h double precision NOT NULL,
    true_hub_id   text NOT NULL,
    incident_id   text,
    PRIMARY KEY (scenario_id, shipment_id),
    FOREIGN KEY (scenario_id, shipment_id) REFERENCES shipments,
    FOREIGN KEY (scenario_id, true_hub_id) REFERENCES hubs
);

CREATE TABLE sim_trip_delays (
    scenario_id text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    trip_id     text NOT NULL,
    delay_h     double precision NOT NULL CHECK (delay_h > 0),
    PRIMARY KEY (scenario_id, trip_id),
    FOREIGN KEY (scenario_id, trip_id) REFERENCES trips
);

-- Recovery plans: one row per run, one choice per flagged shipment.
CREATE TABLE plans (
    scenario_id        text NOT NULL REFERENCES scenarios ON DELETE CASCADE,
    plan_no            integer NOT NULL CHECK (plan_no >= 1),
    solver             text NOT NULL CHECK (solver IN ('cpsat', 'greedy', 'none')),
    grace_h            double precision NOT NULL,
    status             text NOT NULL,
    objective_inr      double precision NOT NULL,
    solve_s            double precision NOT NULL,
    stats              jsonb NOT NULL DEFAULT '{}',
    options_considered integer NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, plan_no)
);

CREATE TABLE plan_choices (
    scenario_id   text NOT NULL,
    plan_no       integer NOT NULL,
    option_id     text NOT NULL,
    shipment_id   text NOT NULL,
    mode          text NOT NULL CHECK (mode IN ('PIGGYBACK', 'DEDICATED', 'LOCAL', 'NONE')),
    from_hub      text NOT NULL,
    ready_h       double precision NOT NULL,
    legs          text[] NOT NULL,
    capacity_legs text[] NOT NULL,
    depart_h      double precision,
    delivered_h   double precision,
    lateness_h    double precision NOT NULL,
    cost_inr      double precision NOT NULL,
    penalty_inr   double precision NOT NULL,
    weight_kg     double precision NOT NULL,
    volume_m3     double precision NOT NULL,
    note          text NOT NULL,
    PRIMARY KEY (scenario_id, plan_no, shipment_id),
    FOREIGN KEY (scenario_id, plan_no) REFERENCES plans ON DELETE CASCADE,
    FOREIGN KEY (scenario_id, shipment_id) REFERENCES shipments
);

-- Operator decisions, append-only. The latest row per shipment is in force.
CREATE TABLE decisions (
    decision_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario_id text NOT NULL,
    shipment_id text NOT NULL,
    action      text NOT NULL CHECK (action IN ('ACCEPT', 'DISMISS', 'REVOKE')),
    plan_no     integer,
    option      jsonb,                -- the RecoveryOption accepted (ACCEPT only)
    note        text NOT NULL DEFAULT '',
    decided_by  text NOT NULL DEFAULT 'operator',
    decided_at  timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (scenario_id, shipment_id) REFERENCES shipments ON DELETE CASCADE,
    FOREIGN KEY (scenario_id, plan_no) REFERENCES plans ON DELETE SET NULL (plan_no),
    CHECK ((action = 'ACCEPT') = (option IS NOT NULL))
);
CREATE INDEX decisions_latest ON decisions (scenario_id, shipment_id, decision_id DESC);

CREATE VIEW current_decisions AS
SELECT DISTINCT ON (scenario_id, shipment_id) *
FROM decisions
ORDER BY scenario_id, shipment_id, decision_id DESC;

-- Spare capacity taken by accepted recoveries, per leg and shipment.
CREATE VIEW capacity_holds AS
SELECT d.scenario_id, l.leg_id, d.shipment_id, d.decision_id,
       (d.option->>'weight_kg')::double precision AS kg,
       (d.option->>'volume_m3')::double precision AS m3
FROM current_decisions d
CROSS JOIN LATERAL jsonb_array_elements_text(d.option->'capacity_legs') AS l(leg_id)
WHERE d.action = 'ACCEPT';
