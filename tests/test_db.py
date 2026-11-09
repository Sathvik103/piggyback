"""The Postgres repository against a real database (skipped if none is reachable)."""

import threading
from dataclasses import fields, replace

import psycopg
import pytest

from db import CapacityConflict, Repo, migrate
from detection import OperatorView, detect
from recovery import generate_options, solve_greedy
from simulator import generate_scenario


@pytest.fixture(scope="module")
def world():
    return generate_scenario(seed=7, n_shipments=600)


@pytest.fixture(scope="module")
def repo(pg_url, world):
    r = Repo(pg_url, max_size=6)
    r.delete_scenario("t7")
    r.save_scenario("t7", world)
    yield r
    r.close()


def _options(repo, world):
    v = OperatorView.from_scenario(world)
    return generate_options(v, detect(v), capacity=repo.residual_capacity("t7", v))


def test_migrations_are_idempotent_and_recorded(pg_url):
    assert migrate(pg_url) == []
    with psycopg.connect(pg_url) as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] >= 1


def test_saved_scenario_reads_back_as_the_same_operator_view(repo, world):
    from_db = repo.view("t7")
    in_mem = OperatorView.from_scenario(world)
    assert from_db.now == in_mem.now
    for f in fields(OperatorView):
        if f.name != "now":
            assert sorted(getattr(from_db, f.name), key=repr) == sorted(getattr(in_mem, f.name), key=repr), f.name
    s = repo.scenario("t7")
    assert s["start_at"] == world.config.start_at and s["now_at"] == "2026-11-03T12:00+05:30"
    assert repo.view("nope") is None and repo.scenario("nope") is None


def test_truth_lives_apart_from_the_operator_view(repo, world):
    truth, delays = repo.truth("t7")
    assert truth == sorted(world.truth, key=lambda t: t.shipment_id)
    assert delays == {d.trip_id: d for d in world.trip_delays}
    assert not {"truth", "trip_delays"} & {f.name for f in fields(OperatorView)}


def test_geometry_is_built(repo, pg_url):
    with psycopg.connect(pg_url) as conn:
        missing = conn.execute("SELECT count(*) FROM lanes WHERE scenario_id = 't7' AND geom IS NULL").fetchone()[0]
        km = conn.execute("SELECT ST_Distance(a.geom, b.geom) / 1000 FROM hubs a, hubs b WHERE a.scenario_id = 't7' "
                          "AND b.scenario_id = 't7' AND a.hub_id = 'DEL' AND b.hub_id = 'MUM'").fetchone()[0]
    assert missing == 0 and 1130 < km < 1160  # Delhi-Mumbai great-circle distance


def test_plans_round_trip(repo, world):
    opts = _options(repo, world)
    v = OperatorView.from_scenario(world)
    plan = solve_greedy(opts, repo.residual_capacity("t7", v))
    n1 = repo.save_plan("t7", plan, 2.0, sum(map(len, opts.values())))
    n2 = repo.save_plan("t7", plan, 2.0, sum(map(len, opts.values())))
    assert n2 == n1 + 1
    saved = repo.plan("t7", n1)
    assert {c.shipment_id: c for c in saved["choices"]} == plan.chosen
    assert saved["shipments"] == len(plan.chosen) and sum(saved["by_mode"].values()) == len(plan.chosen)


def test_accept_holds_capacity_and_revoke_releases_it(repo, world):
    opt = next(o for os in _options(repo, world).values() for o in os if o.mode == "PIGGYBACK" and o.capacity_legs)
    leg = opt.capacity_legs[0]
    before = repo.holds("t7").get(leg, (0.0, 0.0))
    d = repo.decide("t7", opt.shipment_id, "ACCEPT", opt, note="test")
    assert d["action"] == "ACCEPT" and d["option"]["option_id"] == opt.option_id
    assert repo.holds("t7")[leg][0] == pytest.approx(before[0] + opt.weight_kg)
    # Re-accepting the same shipment replaces its own hold rather than adding to it.
    repo.decide("t7", opt.shipment_id, "ACCEPT", opt)
    assert repo.holds("t7")[leg][0] == pytest.approx(before[0] + opt.weight_kg)
    repo.decide("t7", opt.shipment_id, "REVOKE")
    assert repo.holds("t7").get(leg, (0.0, 0.0))[0] == pytest.approx(before[0])
    with pytest.raises(LookupError):
        repo.decide("t7", opt.shipment_id, "REVOKE")
    assert all(d["shipment_id"] != opt.shipment_id for d in repo.decisions("t7"))
    assert len([d for d in repo.decisions("t7", in_force_only=False) if d["shipment_id"] == opt.shipment_id]) == 3


def test_accept_refuses_to_overbook(repo, world):
    v = OperatorView.from_scenario(world)
    opt = next(o for os in _options(repo, world).values() for o in os if o.mode == "PIGGYBACK" and o.capacity_legs)
    leg = opt.capacity_legs[0]
    free = repo.residual_capacity("t7", v)[leg][0]
    too_big = replace(opt, weight_kg=free + 1.0)
    with pytest.raises(CapacityConflict) as e:
        repo.decide("t7", opt.shipment_id, "ACCEPT", too_big)
    assert e.value.legs == [leg]
    with pytest.raises(ValueError):
        repo.decide("t7", opt.shipment_id, "ACCEPT")  # ACCEPT needs an option


def test_concurrent_accepts_cannot_overbook_a_leg(repo, world):
    """Two operators approve different parcels onto the same leg at the same moment;
    together they need more than is free. Exactly one approval may succeed."""
    v = OperatorView.from_scenario(world)
    opts = _options(repo, world)
    base = next(o for os in opts.values() for o in os if o.mode == "PIGGYBACK" and o.capacity_legs)
    leg = base.capacity_legs[0]
    free_kg, free_m3 = repo.residual_capacity("t7", v)[leg]
    others = [s for s in opts if s != base.shipment_id][:2]
    contenders = [replace(base, shipment_id=s, option_id=f"{s}:race", capacity_legs=(leg,),
                          weight_kg=free_kg * 0.6, volume_m3=min(free_m3 * 0.6, base.volume_m3)) for s in others]
    barrier = threading.Barrier(len(contenders))
    results = []

    def accept(o):
        barrier.wait()
        try:
            repo.decide("t7", o.shipment_id, "ACCEPT", o)
            results.append("ok")
        except CapacityConflict:
            results.append("conflict")

    threads = [threading.Thread(target=accept, args=(o,)) for o in contenders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]
    spare = next(l.spare_kg for l in v.legs if l.leg_id == leg)
    assert repo.holds("t7")[leg][0] <= spare + 1e-6
    for s in others:
        try:
            repo.decide("t7", s, "REVOKE")
        except LookupError:
            pass


def test_deleting_a_scenario_cascades(pg_url):
    r = Repo(pg_url, max_size=1)
    try:
        r.save_scenario("t_del", generate_scenario(seed=3, n_shipments=100))
        assert r.delete_scenario("t_del") and not r.exists("t_del")
        with psycopg.connect(pg_url) as conn:
            assert conn.execute("SELECT count(*) FROM shipments WHERE scenario_id = 't_del'").fetchone()[0] == 0
    finally:
        r.close()
