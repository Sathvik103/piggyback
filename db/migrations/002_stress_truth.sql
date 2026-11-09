-- Truth for the stress scenarios: delays that start mid-trip, and trips that
-- silently never run. Defaults keep existing rows meaning what they meant.

ALTER TABLE sim_trip_delays ADD COLUMN from_seq integer NOT NULL DEFAULT 1 CHECK (from_seq >= 1);
ALTER TABLE sim_trip_delays ADD COLUMN cancelled boolean NOT NULL DEFAULT false;
ALTER TABLE sim_trip_delays DROP CONSTRAINT sim_trip_delays_delay_h_check;
ALTER TABLE sim_trip_delays ADD CONSTRAINT sim_trip_delays_delay_h_check
    CHECK (cancelled OR delay_h > 0);

ALTER TABLE sim_truth DROP CONSTRAINT sim_truth_anomaly_type_check;
ALTER TABLE sim_truth ADD CONSTRAINT sim_truth_anomaly_type_check CHECK (anomaly_type IN
    ('MISROUTE', 'LOST_SCAN', 'HELD', 'MISSORT', 'MISSED_CONNECTION', 'TRIP_CANCELLED'));
