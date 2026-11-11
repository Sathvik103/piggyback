from collections import defaultdict

import pytest

from simulator import generate_scenario
from simulator.export import to_dict
from simulator.generator import SCAN_LAG_RANGE_H, actual_scans
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import (
    ANOMALY_HELD,
    ANOMALY_LOST_SCAN,
    ANOMALY_MISROUTE,
    ANOMALY_MISSED_CONNECTION,
    ANOMALY_MISSORT,
    ANOMALY_TRIP_CANCELLED,
    ANOMALY_TYPES,
    SCAN_HELD,
    SCAN_LOADED,
    SCAN_UNLOADED,
    leg_shift,
)


def _scans_by_shipment(sc):
    out = defaultdict(list)
    for e in sc.scans:
        out[e.shipment_id].append(e)
    return out


def test_same_seed_is_identical():
    assert to_dict(generate_scenario(seed=7, n_shipments=300)) == to_dict(generate_scenario(seed=7, n_shipments=300))


def test_different_seeds_differ():
    a = generate_scenario(seed=1, n_shipments=300)
    b = generate_scenario(seed=2, n_shipments=300)
    assert [s.origin_hub for s in a.shipments] != [s.origin_hub for s in b.shipments]


def test_lane_graph_is_strongly_connected_and_symmetric(scenario):
    hubs = {h.hub_id for h in scenario.hubs}
    assert len(hubs) == 25
    pairs = {(l.from_hub, l.to_hub) for l in scenario.lanes}
    assert all((b, a) in pairs for a, b in pairs)
    adj = defaultdict(set)
    for a, b in pairs:
        adj[a].add(b)
    seen, stack = set(), ["DEL"]
    while stack:
        h = stack.pop()
        if h not in seen:
            seen.add(h)
            stack.extend(adj[h])
    assert seen == hubs


def test_corridors_cover_every_lane(scenario):
    covered = set()
    for c in scenario.corridors:
        assert c.frequency_h in (12, 24)
        stops = c.hub_sequence.split(">")
        assert len(set(stops)) == len(stops)
        covered.update(zip(stops, stops[1:], strict=False))
    assert covered == {(l.from_hub, l.to_hub) for l in scenario.lanes}


def test_trip_legs_are_continuous_and_within_capacity(scenario):
    corridor = {c.corridor_id: c for c in scenario.corridors}
    trip = {t.trip_id: t for t in scenario.trips}
    legs = defaultdict(list)
    for l in scenario.legs:
        legs[l.trip_id].append(l)
    for tid, ls in legs.items():
        ls.sort(key=lambda l: l.seq)
        c = corridor[trip[tid].corridor_id]
        assert ls[0].departure_h == trip[tid].departure_h
        assert ls[-1].arrival_h == trip[tid].arrival_h
        for a, b in zip(ls, ls[1:], strict=False):
            assert a.to_hub == b.from_hub and b.departure_h > a.arrival_h
        for l in ls:
            assert 0 <= l.spare_kg <= c.capacity_kg and 0 <= l.spare_m3 <= c.capacity_m3
    assert all(t.departure_h < scenario.config.horizon_h for t in scenario.trips)


def test_planned_itineraries_are_feasible(scenario):
    legs = {l.leg_id: l for l in scenario.legs}
    for s in scenario.shipments:
        path = [legs[i] for i in s.planned_legs.split(";")]
        assert path[0].from_hub == s.origin_hub and path[-1].to_hub == s.dest_hub
        assert path[0].departure_h >= s.created_h
        for a, b in zip(path, path[1:], strict=False):
            assert a.to_hub == b.from_hub
            gap = 0.0 if a.trip_id == b.trip_id else MIN_CONNECTION_H
            assert b.departure_h >= a.arrival_h + gap - 1e-9
        assert s.planned_arrival_h <= s.deadline_h


def test_scans_are_visible_only_once_recorded_and_ordered(scenario):
    by_ship = _scans_by_shipment(scenario)
    for scans in by_ship.values():
        ts = [e.ts_h for e in scans]
        assert ts == sorted(ts)
        assert all(e.ts_h <= e.recorded_h <= scenario.now for e in scans)
    # Only a parcel inducted within the last few hours can have no visible scan yet.
    assert len(by_ship) >= 0.99 * len(scenario.shipments)
    assert any(e.recorded_h - e.ts_h >= SCAN_LAG_RANGE_H[0] for e in scenario.scans)


def test_healthy_shipments_follow_their_plan_shifted_by_trip_delays(scenario):
    stranded = {t.shipment_id for t in scenario.truth}
    legs = {l.leg_id: l for l in scenario.legs}
    delay = {d.trip_id: d.delay_h for d in scenario.trip_delays}
    by_ship = _scans_by_shipment(scenario)
    for s in scenario.shipments:
        if s.shipment_id in stranded:
            continue
        expected, _ = actual_scans(s, [legs[i] for i in s.planned_legs.split(";")], delay)
        happened = [p for p in expected if p[0] <= scenario.now]
        seen = [(e.ts_h, e.hub_id, e.event_type, e.leg_id, e.note) for e in by_ship[s.shipment_id]]
        assert set(seen) <= set(happened)
        # Upload lag hides a scan for at most SCAN_LAG_RANGE_H[1] hours.
        assert {p for p in happened if p[0] < scenario.now - SCAN_LAG_RANGE_H[1]} <= set(seen)


@pytest.fixture(scope="module")
def no_lag():
    """Seed 42 with every scan uploaded on time, so histories end where truth says."""
    return generate_scenario(seed=42, scan_lag_rate=0.0)


def test_stranded_histories_match_ground_truth(no_lag):
    scenario = no_lag
    by_ship = _scans_by_shipment(scenario)
    legs = {l.leg_id: l for l in scenario.legs}
    for t in scenario.truth:
        last = by_ship[t.shipment_id][-1]
        assert t.stranded_at_h <= scenario.now
        if t.anomaly_type == ANOMALY_HELD:
            assert last.event_type == SCAN_HELD and last.hub_id == t.true_hub_id and last.note
        elif t.anomaly_type in (ANOMALY_MISSORT, ANOMALY_MISROUTE, ANOMALY_MISSED_CONNECTION):
            assert last.event_type == SCAN_UNLOADED and last.hub_id == t.true_hub_id
            assert last.ts_h == t.stranded_at_h
        elif t.anomaly_type == ANOMALY_LOST_SCAN:
            # Last thing seen is the load; the parcel actually reached the leg's end hub.
            assert last.event_type == SCAN_LOADED
        if t.anomaly_type == ANOMALY_MISSED_CONNECTION:
            ship = next(s for s in scenario.shipments if s.shipment_id == t.shipment_id)
            path = [legs[i] for i in ship.planned_legs.split(";")]
            nxt = path[[l.leg_id for l in path].index(last.leg_id) + 1]
            delay = {d.trip_id: d.delay_h for d in scenario.trip_delays}
            assert last.ts_h + MIN_CONNECTION_H > nxt.departure_h + delay.get(nxt.trip_id, 0.0)
        if t.anomaly_type == ANOMALY_MISROUTE:
            ship = next(s for s in scenario.shipments if s.shipment_id == t.shipment_id)
            planned_legs = ship.planned_legs.split(";")
            planned_hubs = {ship.origin_hub} | {legs[i].to_hub for i in planned_legs}
            assert last.leg_id not in planned_legs and t.true_hub_id not in planned_hubs
        assert (t.incident_id is not None) == (t.anomaly_type == ANOMALY_MISSORT)


def test_every_anomaly_type_occurs(scenario):
    # TRIP_CANCELLED only happens under the cancel_rate stress knob.
    assert {t.anomaly_type for t in scenario.truth} == set(ANOMALY_TYPES) - {ANOMALY_TRIP_CANCELLED}
    assert len({t.incident_id for t in scenario.truth if t.incident_id}) == scenario.config.n_incidents


def test_stress_knobs_are_off_by_default(scenario):
    assert all(d.from_seq == 1 and not d.cancelled for d in scenario.trip_delays)


def test_mid_trip_delays_leave_early_legs_on_time():
    sc = generate_scenario(seed=42, delay_mode="leg")
    n_legs = {}
    for l in sc.legs:
        n_legs[l.trip_id] = max(n_legs.get(l.trip_id, 0), l.seq)
    multi = [d for d in sc.trip_delays if n_legs[d.trip_id] > 1]
    assert multi and all(2 <= d.from_seq <= n_legs[d.trip_id] for d in multi)
    assert leg_shift({d.trip_id: d for d in multi}, multi[0].trip_id, 1) == 0.0
    assert leg_shift({d.trip_id: d for d in multi}, multi[0].trip_id, multi[0].from_seq) == multi[0].delay_h


def test_cancelled_trips_strand_their_parcels_where_they_wait():
    sc = generate_scenario(seed=42, cancel_rate=0.03, scan_lag_rate=0.0)
    cancelled = {d.trip_id for d in sc.trip_delays if d.cancelled}
    assert cancelled and all(d.delay_h == 0.0 for d in sc.trip_delays if d.cancelled)
    legs = {l.leg_id: l for l in sc.legs}
    ships = {s.shipment_id: s for s in sc.shipments}
    stuck = [t for t in sc.truth if t.anomaly_type == ANOMALY_TRIP_CANCELLED]
    assert stuck
    for t in stuck:
        path = [legs[i] for i in ships[t.shipment_id].planned_legs.split(";")]
        waits_for = next(l for l in path if l.from_hub == t.true_hub_id and l.trip_id in cancelled)
        assert t.stranded_at_h >= waits_for.departure_h and t.stranded_at_h <= sc.now
        # Nobody was ever scanned onto a cancelled trip.
        assert not any(e.leg_id and legs[e.leg_id].trip_id in cancelled for e in sc.scans)


def test_correlated_capacity_makes_evenings_scarce():
    flat = generate_scenario(seed=8, n_shipments=200)
    corr = generate_scenario(seed=8, n_shipments=200, capacity_correlation=0.9)

    def evening_share(sc):
        cap = {t.trip_id: next(c for c in sc.corridors if c.corridor_id == t.corridor_id).capacity_kg
               for t in sc.trips}
        ev = [l.spare_kg / cap[l.trip_id] for l in sc.legs if 18 <= l.departure_h % 24 < 23]
        rest = [l.spare_kg / cap[l.trip_id] for l in sc.legs if not 18 <= l.departure_h % 24 < 23]
        return sum(ev) / len(ev) / (sum(rest) / len(rest))

    assert evening_share(corr) < 0.75 * evening_share(flat)


def test_disruptions_start_at_now_and_reference_real_ids(scenario):
    trips = {t.trip_id for t in scenario.trips}
    hubs = {h.hub_id for h in scenario.hubs}
    lanes = {l.lane_id for l in scenario.lanes}
    assert len(scenario.disruptions) == scenario.config.n_disruptions
    for d in scenario.disruptions:
        assert scenario.now <= d.start_h < d.end_h
        ref = {"TRIP_BREAKDOWN": (d.trip_id, trips), "HUB_CLOSURE": (d.hub_id, hubs),
               "LANE_DELAY": (d.lane_id, lanes)}[d.kind]
        assert ref[0] in ref[1]


def test_benchmark_knobs():
    loose = generate_scenario(seed=3, n_shipments=200, capacity_tightness=0.1)
    tight = generate_scenario(seed=3, n_shipments=200, capacity_tightness=0.9)
    assert sum(l.spare_kg for l in tight.legs) < 0.5 * sum(l.spare_kg for l in loose.legs)

    clean = generate_scenario(seed=3, n_shipments=200, anomaly_rate=0.0, n_incidents=0, n_disruptions=0,
                              trip_delay_rate=0.0, scan_lag_rate=0.0)
    assert len(clean.shipments) == 200 and clean.truth == [] and clean.disruptions == []
    assert clean.trip_delays == [] and all(e.recorded_h - e.ts_h <= 0.25 + 1e-9 for e in clean.scans)


@pytest.mark.parametrize("bad", [{"capacity_tightness": 1.5}, {"anomaly_rate": -0.1}, {"now_h": 200.0},
                                 {"trip_delay_rate": 2.0}, {"scan_lag_rate": -1.0}])
def test_invalid_config_is_rejected(bad):
    with pytest.raises(ValueError):
        generate_scenario(seed=1, n_shipments=10, **bad)
