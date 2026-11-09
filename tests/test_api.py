"""The HTTP API against a real Postgres (skipped if none is reachable)."""

import pytest
from fastapi.testclient import TestClient

from api import create_app
from db import Repo

# Fields that exist only in the sim_* truth tables. (`delay_h` is not here: known
# LANE_DELAY disruptions carry one too, and operators are meant to see those.)
TRUTH_KEYS = {"true_hub_id", "anomaly_type", "stranded_at_h", "incident_id", "truth", "trip_delays"}


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj)) if obj else set()
    return set()


@pytest.fixture(scope="module")
def client(pg_url):
    r = Repo(pg_url, max_size=1)
    r.delete_scenario("s7")
    r.close()
    with TestClient(create_app(pg_url)) as c:
        r = c.post("/scenarios", json={"scenario_id": "s7", "seed": 7, "n_shipments": 1200})
        assert r.status_code == 201, r.text
        yield c


def _flagged(client, **params):
    return client.get("/scenarios/s7/detections", params=params).json()


def test_health_and_listing(client):
    assert client.get("/health").json()["status"] == "ok"
    s = next(s for s in client.get("/scenarios").json() if s["scenario_id"] == "s7")
    assert s["shipments"] == 1200 and s["start_at"].startswith("2026-11-02T00:00")
    one = client.get("/scenarios/s7").json()
    assert one["seed"] == 7 and one["now_at"] == "2026-11-03T12:00+05:30"


def test_create_rejects_duplicates_and_bad_input(client):
    assert client.post("/scenarios", json={"scenario_id": "s7", "seed": 1}).status_code == 409
    assert client.post("/scenarios", json={"scenario_id": "../evil", "seed": 1}).status_code == 422
    assert client.post("/scenarios", json={"scenario_id": "x", "capacity_tightness": 2}).status_code == 422


@pytest.mark.parametrize("path", ["/scenarios/nope", "/scenarios/..%2F..%2Fetc", "/scenarios/S7",
                                  "/scenarios/s7/shipments/S99999", "/scenarios/s7/hubs/XXX",
                                  "/scenarios/s7/plans/p999", "/scenarios/s7/plans/..%2Fsummary",
                                  "/scenarios/nope/decisions", "/scenarios/nope/evaluation/decisions"])
def test_unknown_things_are_404(client, path):
    assert client.get(path).status_code == 404


def test_network_is_geojson(client):
    fc = client.get("/scenarios/s7/network").json()
    assert fc["type"] == "FeatureCollection"
    assert {f["properties"]["kind"] for f in fc["features"]} == {"hub", "lane"}
    hub = next(f for f in fc["features"] if f["id"] == "DEL")
    assert hub["geometry"] == {"type": "Point", "coordinates": [77.209, 28.6139]}


def test_detections_filter_and_page(client):
    all_ = _flagged(client)
    assert all_["total"] == len(all_["items"]) > 0 and all_["now_h"] == 36.0
    kind = next(iter(all_["by_kind"]))
    some = _flagged(client, kind=kind, limit=2)
    assert some["total"] == all_["by_kind"][kind] and len(some["items"]) <= 2
    assert all(d["kind"] == kind for d in some["items"])
    assert client.get("/scenarios/s7/detections", params={"grace_h": -1}).status_code == 422


def test_operator_routes_never_expose_truth(client):
    sid = _flagged(client)["items"][0]["shipment_id"]
    for path in ["/scenarios/s7", "/scenarios/s7/network", "/scenarios/s7/detections",
                 "/scenarios/s7/disruptions", f"/scenarios/s7/shipments/{sid}",
                 f"/scenarios/s7/shipments/{sid}/options", "/scenarios/s7/hubs/DEL", "/scenarios/s7/decisions"]:
        assert not _keys(client.get(path).json()) & TRUTH_KEYS, path


def test_plans_round_trip_and_evaluate(client):
    cp = client.post("/scenarios/s7/plans", json={"solver": "cpsat"}).json()
    assert cp["status"] == "OPTIMAL" and cp["plan_id"].startswith("p") and "choices" not in cp
    gr = client.post("/scenarios/s7/plans", json={"solver": "greedy"}).json()
    assert cp["objective_inr"] <= gr["objective_inr"]
    ids = [p["plan_id"] for p in client.get("/scenarios/s7/plans").json()]
    assert ids[-2:] == [cp["plan_id"], gr["plan_id"]]
    full = client.get(f"/scenarios/s7/plans/{cp['plan_id']}").json()
    assert len(full["choices"]) == full["shipments"] == sum(full["by_mode"].values())
    for c in full["choices"]:
        assert c["route"][0] == c["from_hub"]
        if c["mode"] == "PIGGYBACK":
            assert len(c["route"]) == len(c["legs"]) + 1
    k = client.get(f"/scenarios/s7/evaluation/plans/{cp['plan_id']}").json()["kpis"]
    assert k["on_time"] + k["late"] + k["failed"] + k["unrecovered"] == k["stranded"]
    assert client.post("/scenarios/s7/plans", json={"solver": "magic"}).status_code == 422


def test_decision_workflow(client):
    plan = client.post("/scenarios/s7/plans", json={"solver": "cpsat"}).json()
    choices = client.get(f"/scenarios/s7/plans/{plan['plan_id']}", params={"mode": "PIGGYBACK"}).json()["choices"]
    a, b = choices[0], choices[1]

    # Accept a plan's choice: it holds capacity and leaves the open queue.
    r = client.post("/scenarios/s7/decisions", json={"shipment_id": a["shipment_id"], "action": "ACCEPT",
                                                     "plan_id": plan["plan_id"], "note": "looks right"})
    assert r.status_code == 201, r.text
    assert r.json()["option"]["option_id"] == a["option_id"]
    flagged = {d["shipment_id"]: d for d in _flagged(client)["items"]}
    assert flagged[a["shipment_id"]]["decision"] == "ACCEPT"
    assert a["shipment_id"] not in {d["shipment_id"] for d in _flagged(client, status="open")["items"]}
    hub = client.get(f"/scenarios/s7/hubs/{a['from_hub']}").json()
    leg = next(d for d in hub["departures"] if d["leg_id"] in a["capacity_legs"]) if a["capacity_legs"] else None
    if leg:
        assert leg["free_kg"] == pytest.approx(leg["spare_kg"] - a["weight_kg"], abs=0.1)

    # A new plan skips decided shipments.
    nxt = client.post("/scenarios/s7/plans", json={"solver": "greedy"}).json()
    planned = {c["shipment_id"] for c in client.get(f"/scenarios/s7/plans/{nxt['plan_id']}").json()["choices"]}
    assert a["shipment_id"] not in planned

    # Accept one of the live options instead of the plan's pick.
    opts = client.get(f"/scenarios/s7/shipments/{b['shipment_id']}/options").json()
    alt = next(o for o in opts if o["mode"] != "NONE")
    r = client.post("/scenarios/s7/decisions", json={"shipment_id": b["shipment_id"], "action": "ACCEPT",
                                                     "option_id": alt["option_id"]})
    assert r.status_code == 201, r.text
    detail = client.get(f"/scenarios/s7/shipments/{b['shipment_id']}").json()
    assert detail["decision"]["option"]["option_id"] == alt["option_id"]

    # Bad requests.
    none = next(o for o in opts if o["mode"] == "NONE")
    for body, code in [({"action": "ACCEPT", "option_id": none["option_id"]}, 422),
                       ({"action": "ACCEPT"}, 422),
                       ({"action": "DISMISS", "plan_id": plan["plan_id"]}, 422),
                       ({"action": "ACCEPT", "option_id": "nope"}, 404),
                       ({"action": "MAYBE"}, 422)]:
        r = client.post("/scenarios/s7/decisions", json={"shipment_id": b["shipment_id"], **body})
        assert r.status_code == code, (body, r.text)
    assert client.post("/scenarios/s7/decisions",
                       json={"shipment_id": "S99999", "action": "DISMISS"}).status_code == 404

    # Committed decisions can be replayed against the truth.
    ev = client.get("/scenarios/s7/evaluation/decisions").json()
    assert ev["accepted"] == 2 and ev["kpis"]["stranded"] > 0

    # Dismiss, then revoke everything; revoking twice is a conflict.
    assert client.post("/scenarios/s7/decisions", json={"shipment_id": b["shipment_id"],
                                                        "action": "DISMISS"}).status_code == 201
    for sid in (a["shipment_id"], b["shipment_id"]):
        assert client.post("/scenarios/s7/decisions", json={"shipment_id": sid, "action": "REVOKE"}).status_code == 201
    assert client.post("/scenarios/s7/decisions", json={"shipment_id": a["shipment_id"],
                                                        "action": "REVOKE"}).status_code == 409
    assert client.get("/scenarios/s7/decisions").json() == []


def test_plans_watch_at_risk_parcels_unless_asked(client):
    at_risk = {d["shipment_id"] for d in _flagged(client, kind="AT_RISK", status="open")["items"]}
    assert at_risk, "scenario s7 should have AT_RISK parcels"
    default = client.post("/scenarios/s7/plans", json={"solver": "greedy"}).json()
    planned = {c["shipment_id"] for c in client.get(f"/scenarios/s7/plans/{default['plan_id']}").json()["choices"]}
    assert not planned & at_risk
    eager = client.post("/scenarios/s7/plans", json={"solver": "greedy", "include_at_risk": True}).json()
    planned = {c["shipment_id"] for c in client.get(f"/scenarios/s7/plans/{eager['plan_id']}").json()["choices"]}
    assert at_risk <= planned


def test_options_report_truck_load_after_booking(client):
    sid = next(d["shipment_id"] for d in _flagged(client, status="open")["items"] if d["kind"] != "AT_RISK")
    opts = client.get(f"/scenarios/s7/shipments/{sid}/options").json()
    for o in opts:
        if o["capacity_legs"]:
            assert 0 < o["load_after_pct"] <= 100.0 + 1e-6
        else:
            assert o["load_after_pct"] is None


def test_decision_history_keeps_every_row(client):
    sid = next(d["shipment_id"] for d in _flagged(client, status="open")["items"])
    client.post("/scenarios/s7/decisions", json={"shipment_id": sid, "action": "DISMISS"})
    client.post("/scenarios/s7/decisions", json={"shipment_id": sid, "action": "REVOKE"})
    history = [d for d in client.get("/scenarios/s7/decisions", params={"history": True}).json()
               if d["shipment_id"] == sid]
    assert [d["action"] for d in history][-2:] == ["DISMISS", "REVOKE"]
    assert sid not in {d["shipment_id"] for d in client.get("/scenarios/s7/decisions").json()}


def test_benchmark_summary(client):
    b = client.get("/benchmark").json()
    assert b["hero"] == "piggyback" and b["available"]
    vs = b["versus"]["next_service"]
    assert vs["n"] == b["meta"]["scenarios"] and vs["penalty_pct"]["significant"]
    assert {p["name"] for p in b["policies"]} >= {"piggyback", "next_service"}
    assert b["robustness"] and all("vs_next_service" in r for r in b["robustness"])


def test_detection_evaluation_reads_truth(client):
    s = client.get("/scenarios/s7/evaluation/detections").json()
    assert 0.0 < s["precision"] <= 1.0 and 0.0 < s["recall"] <= 1.0


def test_web_app_is_served_when_built(client):
    from api.app import STATIC_DIR
    r = client.get("/", follow_redirects=False)
    if not (STATIC_DIR / "index.html").exists():
        assert r.status_code == 200 and "npm run build" in r.json()["detail"]
        pytest.skip("web app not built (npm run build in web/)")
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    assert client.get("/dashboard/", follow_redirects=False).headers["location"] == "/app/"
    page = client.get("/app/")
    assert page.status_code == 200 and '<div id="root">' in page.text
