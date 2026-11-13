-- Live updates for operator consoles. Every new decision or plan is announced on
-- the `piggyback_events` channel, whoever wrote it (any API process, the CLI, a
-- script), and only once its transaction commits. The API relays these to browsers
-- as server-sent events. Payloads stay small and carry ids, not data: clients
-- re-fetch what changed.
CREATE FUNCTION piggyback_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'decisions' THEN
        PERFORM pg_notify('piggyback_events', json_build_object(
            'type', 'decision', 'scenario_id', NEW.scenario_id, 'shipment_id', NEW.shipment_id,
            'action', NEW.action, 'decision_id', NEW.decision_id, 'decided_by', NEW.decided_by)::text);
    ELSE
        PERFORM pg_notify('piggyback_events', json_build_object(
            'type', 'plan', 'scenario_id', NEW.scenario_id, 'plan_id', 'p' || NEW.plan_no,
            'solver', NEW.solver)::text);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER decisions_notify AFTER INSERT ON decisions
    FOR EACH ROW EXECUTE FUNCTION piggyback_notify();
CREATE TRIGGER plans_notify AFTER INSERT ON plans
    FOR EACH ROW EXECUTE FUNCTION piggyback_notify();
