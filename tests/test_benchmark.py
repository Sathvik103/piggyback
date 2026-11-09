import csv

import pytest

from benchmark.policies import BY_NAME, POLICIES, next_service, piggyback
from benchmark.report import ci, paired, write_report
from benchmark.run import FIELDS, run_case
from detection import OperatorView, detect
from detection.detector import KIND_AT_RISK
from recovery import Network
from recovery.optimizer import capacity_violations, leg_capacity
from recovery.options import MODE_DEDICATED, MODE_LOCAL, MODE_NONE, MODE_PIGGYBACK
from simulator import generate_scenario


@pytest.fixture(scope="module")
def world():
    sc = generate_scenario(seed=5, n_shipments=1500, capacity_tightness=0.8)
    view = OperatorView.from_scenario(sc)
    return sc, view, detect(view)


def test_blind_network_is_the_published_timetable(world):
    sc, view, _ = world
    blind = Network(view, trip_delays={}, disruptions=False)
    legs = {l.leg_id: l for l in view.legs}
    assert len(blind.legs) == len(view.legs)  # breakdowns ignored: nothing dropped
    assert all(e.dep == legs[e.leg_id].departure_h and e.arr == legs[e.leg_id].arrival_h for e in blind.legs)
    assert all(e.can_board and e.can_alight for e in blind.legs)  # closures ignored
    aware = Network(view)
    assert len(aware.legs) < len(blind.legs) or not any(d.kind == "TRIP_BREAKDOWN" for d in sc.disruptions)


def test_next_service_is_reactive_fcfs_and_within_capacity(world):
    _, view, dets = world
    cap = leg_capacity(view)
    plan = next_service(view, dets)
    at_risk = {d.shipment_id for d in dets if d.kind == KIND_AT_RISK}
    assert not at_risk & set(plan.chosen)
    assert {o.mode for o in plan.chosen.values()} <= {MODE_PIGGYBACK, MODE_LOCAL, MODE_NONE}
    assert capacity_violations(plan, cap) == []
    assert len(plan.chosen) == sum(d.kind != KIND_AT_RISK for d in dets)


def test_express_truck_rule_only_trucks_express(world):
    _, view, dets = world
    ships = {s.shipment_id: s for s in view.shipments}
    plan = next_service(view, dets, express_truck=True)
    trucked = [sid for sid, o in plan.chosen.items() if o.mode == MODE_DEDICATED]
    assert trucked and all(ships[s].priority == "EXPRESS" for s in trucked)


def test_piggyback_variants(world):
    _, view, dets = world
    cap = leg_capacity(view)
    default = piggyback(view, dets)
    proactive = piggyback(view, dets, proactive=True)
    assert set(default.chosen) == {d.shipment_id for d in dets if d.kind != KIND_AT_RISK}
    assert set(proactive.chosen) == {d.shipment_id for d in dets}
    assert BY_NAME["piggyback"].followup == "best" and BY_NAME["piggyback_proactive"].followup is None
    for p in (default, proactive, piggyback(view, dets, aware=False), piggyback(view, dets, optimizer="greedy")):
        assert capacity_violations(p, cap) == []


def test_reactive_followup_rebooks_only_parcels_that_really_strand(world):
    sc, view, dets = world
    from benchmark.policies import reactive_followup
    from simulator.generator import actual_scans
    delays = {d.trip_id: d.delay_h for d in sc.trip_delays}
    legs = {l.leg_id: l for l in view.legs}
    ships = {s.shipment_id: s for s in view.shipments}
    base = next_service(view, dets)
    after = reactive_followup(view, dets, base, delays, "next_service")
    added = set(after.chosen) - set(base.chosen)
    at_risk = [d for d in dets if d.kind == KIND_AT_RISK]
    misses = {d.shipment_id for d in at_risk
              if actual_scans(ships[d.shipment_id], [legs[i] for i in ships[d.shipment_id].planned_legs.split(";")],
                              delays)[1] is not None}
    assert added == misses and misses
    for sid in added:
        assert after.chosen[sid].ready_h > view.now  # acted on later, when the miss shows up
    assert capacity_violations(after, leg_capacity(view)) == []


def test_transfer_buffer_only_follows_trips_with_unknown_delays(world):
    _, view, _ = world
    from recovery.network import UNOBSERVED_TRANSFER_BUFFER_H
    planner = Network(view)
    seen = next(iter(planner.observed))
    unseen = next(t.trip_id for t in view.trips if t.trip_id not in planner.observed)
    assert planner._buffer(seen) == 0.0 and planner._buffer(unseen) == UNOBSERVED_TRANSFER_BUFFER_H > 0
    # Replay (true delays) and the published timetable never add slack.
    for net in (Network(view, trip_delays={}), Network(view, trip_delays={}, disruptions=False)):
        assert net._buffer(unseen) == 0.0
    assert Network(view, transfer_buffer_h=0.0)._buffer(unseen) == 0.0


def test_stress_cases_record_their_variant_and_report(tmp_path):
    from benchmark.report import write_robustness
    pols = ("next_service", "next_service_express_truck", "piggyback", "piggyback_greedy",
            "piggyback_no_buffer", "piggyback_blind", "piggyback_proactive")
    rows = []
    for variant in ("default", "silent_cancellations"):
        rows += run_case(1, 0.9, 0.04, pols, variant)
    assert {r["variant"] for r in rows} == {"default", "silent_cancellations"}
    path = tmp_path / "stress.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    text = write_robustness(path).read_text(encoding="utf-8")
    assert "silent cancellations" in text and "− transfer buffer" in text


def test_run_case_scores_every_policy_on_the_same_scenario():
    rows = run_case(4, 0.6, 0.04, tuple(p.name for p in POLICIES))
    assert [r["policy"] for r in rows] == [p.name for p in POLICIES]
    assert len({(r["stranded"], r["flagged"]) for r in rows}) == 1  # same truth and detections for all
    for r in rows:
        assert r["on_time"] + r["late"] + r["failed"] + r["unrecovered"] == r["stranded"]
    nothing = next(r for r in rows if r["policy"] == "do_nothing")
    assert nothing["unrecovered"] == nothing["stranded"] and nothing["cost_inr"] == 0


def test_ci_and_paired_deltas():
    m, h = ci([1.0, 2.0, 3.0])
    assert m == 2.0 and h == pytest.approx(4.30 * 1.0 / 3 ** 0.5)
    rows = []
    for seed, (hero, other) in enumerate([(80, 70), (60, 55), (90, 70)], start=1):
        for pol, on_time, penalty in (("piggyback", hero, 100.0), ("next_service", other, 200.0)):
            rows.append({"seed": seed, "capacity_tightness": 0.5, "anomaly_rate": 0.04, "policy": pol,
                         "family": BY_NAME[pol].family, "flagged": 100, "plan_s": 0.1, "stranded": 100,
                         "on_time": on_time, "late": 0, "failed": 0, "unrecovered": 100 - on_time,
                         "on_time_rate": on_time / 100, "weighted_late_h": 0.0, "cost_inr": 0.0,
                         "unnecessary": 0, "unnecessary_cost_inr": 0.0, "realized_penalty_inr": penalty * 1e5})
    p = paired(rows, "next_service")
    assert p["n"] == 3 and p["ontime_pp"][0] == pytest.approx(35 / 3) and p["penalty_pct"][0] == -50.0
    assert p["wins"] == 3


def test_report_from_a_small_run(tmp_path):
    rows = run_case(2, 0.9, 0.04, ("do_nothing", "next_service", "piggyback", "piggyback_greedy"))
    rows += run_case(3, 0.9, 0.04, ("do_nothing", "next_service", "piggyback", "piggyback_greedy"))
    path = tmp_path / "bench.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    text = write_report(path).read_text(encoding="utf-8")
    assert "## Headline" in text and "Next service, FCFS" in text and "− optimizer (greedy)" in text
    assert "2 simulated scenarios" in text
