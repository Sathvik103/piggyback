import json

import pytest
from fastapi.testclient import TestClient

from api import create_app

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
def client(tmp_path_factory):
    c = TestClient(create_app(tmp_path_factory.mktemp("data")))
    r = c.post("/scenarios", json={"scenario_id": "s7", "seed": 7, "n_shipments": 1200})
    assert r.status_code == 201, r.text
    return c


def test_health_and_listing(client):
    assert client.get("/health").json() == {"status": "ok", "scenarios": 1}
    [s] = client.get("/scenarios").json()
    assert s["scenario_id"] == "s7" and s["shipments"] == 1200
    assert client.get("/scenarios/s7").json()["seed"] == 7


def test_create_rejects_duplicates_and_bad_input(client):
    assert client.post("/scenarios", json={"scenario_id": "s7", "seed": 1}).status_code == 409
    assert client.post("/scenarios", json={"scenario_id": "../evil", "seed": 1}).status_code == 422
    assert client.post("/scenarios", json={"scenario_id": "x", "capacity_tightness": 2}).status_code == 422


@pytest.mark.parametrize("path", ["/scenarios/nope", "/scenarios/..%2F..%2Fetc", "/scenarios/S7",
                                  "/scenarios/s7/shipments/S99999", "/scenarios/s7/hubs/XXX",
                                  "/scenarios/s7/plans/p999", "/scenarios/s7/plans/..%2Fsummary"])
def test_unknown_things_are_404(client, path):
    assert client.get(path).status_code == 404


def test_network_is_geojson(client):
    fc = client.get("/scenarios/s7/network").json()
    assert fc["type"] == "FeatureCollection"
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert kinds == {"hub", "lane"}
    hub = next(f for f in fc["features"] if f["id"] == "DEL")
    assert hub["geometry"] == {"type": "Point", "coordinates": [77.209, 28.6139]}
    lane = next(f for f in fc["features"] if f["properties"]["kind"] == "lane")
    assert lane["geometry"]["type"] == "LineString" and len(lane["geometry"]["coordinates"]) == 2


def test_detections_filter_and_page(client):
    all_ = client.get("/scenarios/s7/detections").json()
    assert all_["total"] == len(all_["items"]) > 0 and all_["now_h"] == 36.0
    kind = next(iter(all_["by_kind"]))
    some = client.get("/scenarios/s7/detections", params={"kind": kind, "limit": 2}).json()
    assert some["total"] == all_["by_kind"][kind] and len(some["items"]) <= 2
    assert all(d["kind"] == kind for d in some["items"])
    hub = next(iter(all_["by_hub"]))
    assert all(d["suspected_hub"] == hub for d in
               client.get("/scenarios/s7/detections", params={"hub": hub}).json()["items"])
    assert client.get("/scenarios/s7/detections", params={"grace_h": -1}).status_code == 422


def test_operator_routes_never_expose_truth(client):
    sid = client.get("/scenarios/s7/detections").json()["items"][0]["shipment_id"]
    for path in ["/scenarios/s7", "/scenarios/s7/network", "/scenarios/s7/detections",
                 "/scenarios/s7/disruptions", f"/scenarios/s7/shipments/{sid}", "/scenarios/s7/hubs/DEL"]:
        body = client.get(path).json()
        assert not _keys(body) & TRUTH_KEYS, path


def test_shipment_and_hub_detail(client):
    det = client.get("/scenarios/s7/detections").json()["items"][0]
    body = client.get(f"/scenarios/s7/shipments/{det['shipment_id']}").json()
    assert body["detection"]["kind"] == det["kind"]
    assert [l["leg_id"] for l in body["booked_legs"]] == body["shipment"]["planned_legs"].split(";")
    assert all(e["shipment_id"] == det["shipment_id"] for e in body["scans"])
    hub = client.get(f"/scenarios/s7/hubs/{det['suspected_hub']}").json()
    assert any(d["shipment_id"] == det["shipment_id"] for d in hub["detections"])
    assert all(36.0 <= d["dep"] <= 60.0 and d["from_hub"] == det["suspected_hub"] for d in hub["departures"])


def test_plans_round_trip_and_evaluate(client, tmp_path_factory):
    r = client.post("/scenarios/s7/plans", json={"solver": "cpsat", "time_limit_s": 10})
    assert r.status_code == 201, r.text
    cp = r.json()
    assert cp["plan_id"] == "p1" and cp["status"] == "OPTIMAL" and "choices" not in cp
    gr = client.post("/scenarios/s7/plans", json={"solver": "greedy"}).json()
    assert gr["plan_id"] == "p2" and cp["objective_inr"] <= gr["objective_inr"]
    assert [p["plan_id"] for p in client.get("/scenarios/s7/plans").json()] == ["p1", "p2"]

    full = client.get("/scenarios/s7/plans/p1").json()
    assert len(full["choices"]) == full["shipments"] == sum(full["by_mode"].values())
    pig = client.get("/scenarios/s7/plans/p1", params={"mode": "PIGGYBACK"}).json()["choices"]
    assert pig and all(c["mode"] == "PIGGYBACK" and c["legs"] for c in pig)

    sid = pig[0]["shipment_id"]
    ship = client.get(f"/scenarios/s7/shipments/{sid}").json()
    assert {p["plan_id"] for p in ship["plans"]} == {"p1", "p2"}

    ev = client.get("/scenarios/s7/evaluation/plans/p1").json()
    k = ev["kpis"]
    assert k["on_time"] + k["late"] + k["failed"] + k["unrecovered"] == k["stranded"]
    none_eval = client.get("/scenarios/s7/evaluation/plans/p2", params={"outcomes": True}).json()
    assert len(none_eval["outcomes"]) == sum(none_eval["by_status"].values())
    assert client.post("/scenarios/s7/plans", json={"solver": "magic"}).status_code == 422


def test_plan_choices_carry_routes_for_the_map(client):
    if not client.get("/scenarios/s7/plans").json():
        client.post("/scenarios/s7/plans", json={"solver": "greedy"})
    plan = client.get("/scenarios/s7/plans/p1").json()
    hubs = {f["id"] for f in client.get("/scenarios/s7/network").json()["features"]
            if f["properties"]["kind"] == "hub"}
    for c in plan["choices"]:
        assert c["route"][0] == c["from_hub"] and set(c["route"]) <= hubs
        if c["mode"] == "PIGGYBACK":
            assert len(c["route"]) == len(c["legs"]) + 1
        elif c["mode"] == "DEDICATED":
            assert len(c["route"]) == 2


def test_dashboard_is_served(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/dashboard/"
    page = client.get("/dashboard/")
    assert page.status_code == 200 and "maplibre-gl" in page.text and "app.js" in page.text
    js = client.get("/dashboard/app.js")
    assert js.status_code == 200 and "/evaluation/" in js.text
    assert client.get("/dashboard/style.css").status_code == 200


def test_detection_evaluation_reads_truth(client):
    s = client.get("/scenarios/s7/evaluation/detections").json()
    assert 0.0 < s["precision"] <= 1.0 and 0.0 < s["recall"] <= 1.0


def test_plans_survive_restart(client):
    data_dir = client.app.state.store.data_dir
    fresh = TestClient(create_app(data_dir))
    assert [p["plan_id"] for p in fresh.get("/scenarios/s7/plans").json()] == ["p1", "p2"]
    saved = json.loads((data_dir / "s7" / "plans" / "p1.json").read_text(encoding="utf-8"))
    assert saved["solver"] == "cpsat"
