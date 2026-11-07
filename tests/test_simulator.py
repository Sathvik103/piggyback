from collections import defaultdict

import pytest

from simulator import generate_scenario
from simulator.export import to_dict
from simulator.generator import planned_scans
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import (
    ANOMALY_HELD,
    ANOMALY_LOST_SCAN,
    ANOMALY_MISROUTE,
    ANOMALY_MISSORT,
    ANOMALY_TYPES,
    SCAN_HELD,
    SCAN_LOADED,
    SCAN_UNLOADED,
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
        covered.update(zip(stops, stops[1:]))
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
        for a, b in zip(ls, ls[1:]):
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
        for a, b in zip(path, path[1:]):
            assert a.to_hub == b.from_hub
            gap = 0.0 if a.trip_id == b.trip_id else MIN_CONNECTION_H
            assert b.departure_h >= a.arrival_h + gap - 1e-9
        assert s.planned_arrival_h <= s.deadline_h


def test_scans_are_cut_at_now_and_ordered(scenario):
    for sid, scans in _scans_by_shipment(scenario).items():
        ts = [e.ts_h for e in scans]
        assert ts == sorted(ts)
        assert max(ts) <= scenario.now
    # Every shipment was created before now, so each has at least its induction scan.
    assert len(_scans_by_shipment(scenario)) == len(scenario.shipments)


def test_healthy_shipments_follow_their_plan(scenario):
    stranded = {t.shipment_id for t in scenario.truth}
    legs = {l.leg_id: l for l in scenario.legs}
    by_ship = _scans_by_shipment(scenario)
    for s in scenario.shipments:
        if s.shipment_id in stranded:
            continue
        plan = [p for p in planned_scans(s, [legs[i] for i in s.planned_legs.split(";")]) if p[0] <= scenario.now]
        seen = [(e.ts_h, e.hub_id, e.event_type, e.leg_id, e.note) for e in by_ship[s.shipment_id]]
        assert seen == plan


def test_stranded_histories_match_ground_truth(scenario):
    by_ship = _scans_by_shipment(scenario)
    legs = {l.leg_id: l for l in scenario.legs}
    for t in scenario.truth:
        last = by_ship[t.shipment_id][-1]
        assert t.stranded_at_h <= scenario.now
        if t.anomaly_type == ANOMALY_HELD:
            assert last.event_type == SCAN_HELD and last.hub_id == t.true_hub_id and last.note
        elif t.anomaly_type in (ANOMALY_MISSORT, ANOMALY_MISROUTE):
            assert last.event_type == SCAN_UNLOADED and last.hub_id == t.true_hub_id
        elif t.anomaly_type == ANOMALY_LOST_SCAN:
            # Last thing seen is the load; the parcel actually reached the leg's end hub.
            assert last.event_type == SCAN_LOADED
        if t.anomaly_type == ANOMALY_MISROUTE:
            ship = next(s for s in scenario.shipments if s.shipment_id == t.shipment_id)
            planned_legs = ship.planned_legs.split(";")
            planned_hubs = {ship.origin_hub} | {legs[i].to_hub for i in planned_legs}
            assert last.leg_id not in planned_legs and t.true_hub_id not in planned_hubs
        assert (t.incident_id is not None) == (t.anomaly_type == ANOMALY_MISSORT)


def test_every_anomaly_type_occurs(scenario):
    assert {t.anomaly_type for t in scenario.truth} == set(ANOMALY_TYPES)
    assert len({t.incident_id for t in scenario.truth if t.incident_id}) == scenario.config.n_incidents


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

    clean = generate_scenario(seed=3, n_shipments=200, anomaly_rate=0.0, n_incidents=0, n_disruptions=0)
    assert len(clean.shipments) == 200 and clean.truth == [] and clean.disruptions == []


@pytest.mark.parametrize("bad", [{"capacity_tightness": 1.5}, {"anomaly_rate": -0.1}, {"now_h": 100.0}])
def test_invalid_config_is_rejected(bad):
    with pytest.raises(ValueError):
        generate_scenario(seed=1, n_shipments=10, **bad)
