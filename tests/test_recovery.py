from dataclasses import replace

import pytest

from detection import OperatorView, detect
from recovery import Network, OptionsConfig, RecoveryOption, generate_options, leg_capacity, replay
from recovery import cli as recovery_cli
from recovery.evaluate import FAILED, ON_TIME, LATE, UNRECOVERED
from recovery.optimizer import capacity_violations, solve_cpsat, solve_greedy, solve_none
from recovery.options import MODE_DEDICATED, MODE_LOCAL, MODE_NONE, MODE_PIGGYBACK, PRIORITY_WEIGHT
from simulator.export import write_all
from simulator.hubs import MIN_CONNECTION_H


@pytest.fixture(scope="module")
def pipeline(scenario):
    view = OperatorView.from_scenario(scenario)
    dets = detect(view)
    net = Network(view)
    options = generate_options(view, dets, network=net)
    cap = leg_capacity(view)
    plans = {p.solver: p for p in (solve_none(options, cap), solve_greedy(options, cap),
                                   solve_cpsat(options, cap))}
    delays = {d.trip_id: d.delay_h for d in scenario.trip_delays}
    return view, dets, net, options, cap, plans, delays


def test_network_applies_known_disruptions(scenario, pipeline):
    _, _, net, *_ = pipeline
    legs = {l.leg_id: l for l in scenario.legs}
    for d in scenario.disruptions:
        if d.kind == "TRIP_BREAKDOWN":
            assert all(e.arr <= d.start_h for e in net.legs if e.trip_id == d.trip_id)
        elif d.kind == "HUB_CLOSURE":
            for e in net.legs:
                if e.from_hub == d.hub_id and d.start_h <= e.dep <= d.end_h:
                    assert not e.can_board
        elif d.kind == "LANE_DELAY":
            for e in net.legs:
                if legs[e.leg_id].lane_id == d.lane_id and d.start_h <= e.dep <= d.end_h:
                    assert e.arr - legs[e.leg_id].arrival_h >= d.delay_h - 0.01  # times are rounded


def test_earliest_paths_are_rideable(pipeline):
    view, _, net, *_ = pipeline
    # A few long cross-country pairs can't finish before the timetable ends; these can.
    for origin, dest, ready in [("BLR", "DEL", 40.0), ("PAT", "HYD", 36.5), ("MUM", "AMD", 50.0)]:
        path = net.earliest_path(origin, dest, ready)
        assert path, (origin, dest)
        assert path[0].from_hub == origin and path[-1].to_hub == dest
        assert path[0].dep >= ready and path[0].can_board and path[-1].can_alight
        for a, b in zip(path, path[1:]):
            assert a.to_hub == b.from_hub
            if a.trip_id != b.trip_id:
                assert a.can_alight and b.can_board and b.dep >= a.arr + MIN_CONNECTION_H - 1e-9


def test_options_are_well_formed(pipeline):
    view, dets, net, options, cap, *_ = pipeline
    ships = {s.shipment_id: s for s in view.shipments}
    cfg = OptionsConfig()
    assert set(options) == {d.shipment_id for d in dets}
    for sid, opts in options.items():
        s = ships[sid]
        assert sum(o.mode == MODE_NONE for o in opts) == 1
        for o in opts:
            w = PRIORITY_WEIGHT[s.priority]
            assert o.penalty_inr == pytest.approx(o.cost_inr + cfg.value_of_hour_inr * w * o.lateness_h, abs=0.02)
            if o.mode == MODE_PIGGYBACK:
                assert net.by_id[o.legs[0]].from_hub == o.from_hub
                assert net.by_id[o.legs[-1]].to_hub == s.dest_hub
                assert set(o.capacity_legs) <= set(o.legs)
                assert not set(o.capacity_legs) & set(s.planned_legs.split(";"))
                assert all(cap[l][0] >= s.weight_kg and cap[l][1] >= s.volume_m3 for l in o.capacity_legs)
            if o.mode in (MODE_PIGGYBACK, MODE_DEDICATED, MODE_LOCAL):
                assert o.delivered_h > o.ready_h and o.lateness_h == max(0.0, round(o.delivered_h - s.deadline_h, 2))
    assert any(o.mode == MODE_PIGGYBACK for opts in options.values() for o in opts)


def test_plans_are_feasible_and_ordered(pipeline):
    _, _, _, options, cap, plans, _ = pipeline
    for p in plans.values():
        assert set(p.chosen) == set(options)
        assert capacity_violations(p, cap) == []
    assert plans["cpsat"].status == "OPTIMAL"
    assert plans["cpsat"].objective_inr <= plans["greedy"].objective_inr <= plans["none"].objective_inr


def _opt(sid, mode, legs, penalty, kg=10.0):
    return RecoveryOption(f"{sid}:{mode}", sid, mode, "DEL", 0.0, legs, legs, None, None, 0.0,
                          penalty, penalty, kg, 0.1, "")


def test_cpsat_beats_greedy_when_order_matters():
    # A has more to lose so greedy serves it first and takes the only seat on L;
    # B then has nothing left. Optimal: A takes its dedicated truck, B takes L.
    options = {
        "A": [_opt("A", MODE_PIGGYBACK, ("L",), 100), _opt("A", MODE_DEDICATED, (), 900),
              _opt("A", MODE_NONE, (), 10_000)],
        "B": [_opt("B", MODE_PIGGYBACK, ("L",), 100), _opt("B", MODE_NONE, (), 2_000)],
    }
    cap = {"L": (10.0, 1.0)}
    assert solve_greedy(options, cap).objective_inr == 2_100
    best = solve_cpsat(options, cap)
    assert best.objective_inr == 1_000
    assert {s: o.mode for s, o in best.chosen.items()} == {"A": MODE_DEDICATED, "B": MODE_PIGGYBACK}


def test_replay_accounts_for_every_stranded_parcel(scenario, pipeline):
    view, dets, _, _, _, plans, delays = pipeline
    for name, p in plans.items():
        outcomes, k = replay(view, scenario.truth, delays, dets, p)
        assert k["on_time"] + k["late"] + k["failed"] + k["unrecovered"] == k["stranded"]
        assert k["stranded"] >= len(scenario.truth)
        stranded = {o.shipment_id for o in outcomes if o.stranded}
        assert {t.shipment_id for t in scenario.truth} <= stranded
    none = replay(view, scenario.truth, delays, dets, plans["none"])[1]
    assert none["unrecovered"] == none["stranded"] and none["cost_inr"] == 0


def test_replay_fails_plans_sent_to_the_wrong_hub(scenario, pipeline):
    view, dets, _, _, _, plans, delays = pipeline
    plan = plans["cpsat"]
    truth = {t.shipment_id: t for t in scenario.truth}
    sid = next(s for s, o in plan.chosen.items() if s in truth and o.mode != MODE_NONE)
    wrong = "GAU" if truth[sid].true_hub_id != "GAU" else "COK"
    moved = replace(plan, chosen={**plan.chosen, sid: replace(plan.chosen[sid], from_hub=wrong)})
    outcome = next(o for o in replay(view, scenario.truth, delays, dets, moved)[0] if o.shipment_id == sid)
    assert outcome.status == FAILED and truth[sid].true_hub_id in outcome.reason


def test_seed_42_recovery_quality(scenario, pipeline):
    view, dets, _, _, _, plans, delays = pipeline
    k = {n: replay(view, scenario.truth, delays, dets, p)[1] for n, p in plans.items()}
    assert k["cpsat"]["on_time_rate"] >= 0.55
    assert k["cpsat"]["realized_penalty_inr"] < 0.5 * k["none"]["realized_penalty_inr"]
    statuses = {ON_TIME, LATE, FAILED, UNRECOVERED}
    assert all(k["cpsat"][s.lower()] >= 0 for s in statuses)


def test_cli_plans_and_scores(scenario, tmp_path, capsys):
    write_all(scenario, tmp_path)
    recovery_cli.main([str(tmp_path), "--solver", "greedy"])
    out = capsys.readouterr().out
    assert "greedy" in out and "on_time_rate" in out
    header = (tmp_path / "plan_greedy.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("option_id,shipment_id,mode,")
