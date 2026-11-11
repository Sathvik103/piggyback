-- Deleting a scenario that has plans failed: the cascade from scenarios removed
-- shipments before the plans -> plan_choices cascade had run, and the plain
-- plan_choices -> shipments key rejected it. A choice belongs to its shipment, so
-- let it cascade too.
ALTER TABLE plan_choices
    DROP CONSTRAINT plan_choices_scenario_id_shipment_id_fkey,
    ADD CONSTRAINT plan_choices_scenario_id_shipment_id_fkey
        FOREIGN KEY (scenario_id, shipment_id) REFERENCES shipments ON DELETE CASCADE;
