"""Shift mode: a world that keeps running, viewed hour by hour, and the rolling engine."""

from collections import defaultdict

import pytest

from benchmark.shift import MAX_ATTEMPTS, run_shift
from detection import OperatorView
from simulator import generate_scenario


@pytest.fixture(scope="module")
def shift():
    return generate_scenario(seed=4, n_shipments=900, capacity_tightness=0.7, shift_h=10.0)


def test_snapshot_view_is_unchanged_by_shift_support():
    sc = generate_scenario(seed=3, n_shipments=300)
    v = OperatorView.from_scenario(sc)
    assert v.now == sc.now and v.scans == sc.scans and v.shipments == sc.shipments
    assert v.disruptions == sc.disruptions  # a snapshot knows all of its disruptions at `now`


def test_shift_world_spreads_over_the_shift(shift):
    start, end = shift.config.now_h, shift.config.end_h
    assert end == start + 10.0
    assert any(t.stranded_at_h > start for t in shift.truth)     # parcels strand during the shift
    assert any(t.stranded_at_h <= start for t in shift.truth)    # and some were already stuck
    assert max(e.recorded_h for e in shift.scans) <= end
    assert any(d.start_h > start + 4 for d in shift.disruptions)


def test_hourly_views_only_reveal_the_past(shift):
    earlier = OperatorView.at(shift, shift.config.now_h + 2)
    later = OperatorView.at(shift, shift.config.now_h + 8)
    assert all(e.recorded_h <= earlier.now for e in earlier.scans)
    assert all(s.created_h <= earlier.now for s in earlier.shipments)
    assert all(d.start_h - 8.0 <= earlier.now for d in earlier.disruptions)
    assert len(earlier.scans) < len(later.scans)
    assert {e.scan_id for e in earlier.scans} <= {e.scan_id for e in later.scans}


@pytest.mark.parametrize("bad", [{"shift_h": -1.0}, {"shift_h": 100.0}])
def test_shift_length_is_validated(bad):
    with pytest.raises(ValueError):
        generate_scenario(seed=1, n_shipments=100, **bad)


def test_doing_nothing_leaves_every_stranding_unrecovered(shift):
    r = run_shift(shift, "do_nothing")
    assert r.kpis["bookings"] == 0 and r.kpis["unrecovered"] == r.kpis["stranded"] > 0
    assert r.kpis["unnecessary"] == 0 and len(r.timeline) == 11  # one look per hour, both ends included


def test_rolling_engine_respects_capacity_and_its_own_rules(shift):
    r = run_shift(shift, "next_service")
    k = r.kpis
    assert k["on_time"] + k["late"] + k["failed"] + k["unrecovered"] == k["stranded"]
    attempts = defaultdict(int)
    for e in r.events:
        if e["event"] in ("booked", "rebooked"):
            attempts[e["shipment_id"]] += 1
            assert e["t"] >= shift.config.now_h
    assert max(attempts.values()) <= MAX_ATTEMPTS
    # A broken booking is only acted on after it could have been noticed.
    for e in r.events:
        if e["event"] == "rebooked":
            broke = [b for b in r.events if b["shipment_id"] == e["shipment_id"] and b["event"] == "broke"
                     and b["t"] <= e["t"]]
            assert broke, e
    # Bookings in force never hold more than a leg's spare room.
    final = {}
    for e in r.events:
        if e["event"] in ("booked", "rebooked"):
            final[e["shipment_id"]] = e
        elif e["event"] == "broke":
            final.pop(e["shipment_id"], None)
    held = defaultdict(float)
    for e in final.values():
        for leg in e["capacity_legs"]:
            held[leg] += e["weight_kg"]
    spare = {l.leg_id: l.spare_kg for l in shift.legs}
    assert held and all(kg <= spare[leg] + 1e-6 for leg, kg in held.items())


def test_aftercare_terminates_when_a_break_is_due_between_rounded_hours():
    # Regression: aftercare once rounded its clock to 0.01 h; a break due at x.xx4 rounded
    # down, was never noticed, and the loop spun forever (seed 5 at 0.9 tightness).
    sc = generate_scenario(seed=5, capacity_tightness=0.9, shift_h=24.0)
    r = run_shift(sc, "next_service")
    assert r.kpis["stranded"] == r.kpis["on_time"] + r.kpis["late"] + r.kpis["failed"] + r.kpis["unrecovered"]


def test_piggyback_runs_a_shift(shift):
    r = run_shift(shift, "piggyback")
    assert r.kpis["stranded"] > 0 and r.kpis["bookings"] > 0
    assert r.timeline[-1]["t"] == shift.config.end_h
