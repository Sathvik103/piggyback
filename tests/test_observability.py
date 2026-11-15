"""Metrics, request IDs and access logs (real Postgres)."""

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from api import create_app
from db import Repo


@pytest.fixture(scope="module")
def client(pg_url, make_user):
    password = make_user("obs-op")
    r = Repo(pg_url, max_size=1)
    r.delete_scenario("ob1")
    r.close()
    with TestClient(create_app(pg_url)) as c:
        assert c.post("/auth/login", json={"username": "obs-op", "password": password}).status_code == 200
        assert c.post("/scenarios", json={"scenario_id": "ob1", "seed": 5, "n_shipments": 300}).status_code == 201
        yield c


def _metric(text: str, name: str, **labels) -> float:
    """The value of one sample in Prometheus text format (0 if absent)."""
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


def test_every_response_has_a_request_id(client):
    r = client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{16}", r.headers["x-request-id"])
    assert client.get("/health", headers={"X-Request-ID": "trace-42.a_b"}).headers["x-request-id"] == "trace-42.a_b"
    bad = client.get("/health", headers={"X-Request-ID": "no spaces\nor newlines"}).headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{16}", bad)  # untrusted ids are replaced, not echoed into logs


def test_metrics_count_requests_by_route_template_not_raw_path(client):
    client.get("/scenarios/ob1")
    client.get("/scenarios/ob1/detections")
    client.get("/no/such/path/12345")
    text = client.get("/metrics").text
    assert _metric(text, "piggyback_http_requests_total", method="GET", route="/scenarios/{sid}", status="200") >= 1
    assert _metric(text, "piggyback_http_requests_total", method="GET", route="unmatched", status="404") >= 1
    assert "/no/such/path" not in text and 'route="/scenarios/ob1"' not in text
    assert _metric(text, "piggyback_http_request_duration_seconds_count", method="GET",
                   route="/scenarios/{sid}/detections") >= 1


def test_metrics_cover_plans_decisions_pool_and_live_updates(client):
    before = client.get("/metrics").text
    plan = client.post("/scenarios/ob1/plans", json={"solver": "greedy"}).json()
    sid = next(d["shipment_id"] for d in client.get("/scenarios/ob1/detections").json()["items"] if not d["decision"])
    client.post("/scenarios/ob1/decisions", json={"shipment_id": sid, "action": "DISMISS"})
    client.post("/scenarios/ob1/decisions", json={"shipment_id": sid, "action": "REVOKE"})
    after = client.get("/metrics").text

    def delta(name, **labels):
        return _metric(after, name, **labels) - _metric(before, name, **labels)

    assert delta("piggyback_plans_total", solver="greedy", status=plan["status"]) == 1
    assert delta("piggyback_plan_duration_seconds_count", solver="greedy") == 1
    assert delta("piggyback_decisions_total", action="DISMISS") == 1
    assert delta("piggyback_decisions_total", action="REVOKE") == 1
    assert _metric(after, "piggyback_db_pool_size") >= 1
    assert "piggyback_live_subscribers" in after and _metric(after, "piggyback_live_listener_up") == 1


def test_metrics_access(client, monkeypatch):
    assert client.get("/metrics").status_code == 200  # the test client counts as loopback
    monkeypatch.setenv("PIGGYBACK_METRICS_TOKEN", "s3cret-scrape-token")
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret-scrape-token"}).status_code == 200


def test_access_log_is_one_json_line_per_request(client):
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger("piggyback.access")
    log.addHandler(handler)
    try:
        rid = client.get("/scenarios/ob1", headers={"X-Request-ID": "log-check-1"}).headers["x-request-id"]
    finally:
        log.removeHandler(handler)
    line = json.loads(next(r.getMessage() for r in records if "log-check-1" in r.getMessage()))
    assert line["request_id"] == rid == "log-check-1"
    assert line["route"] == "/scenarios/{sid}" and line["path"] == "/scenarios/ob1" and line["status"] == 200
    assert line["user"] == "obs-op" and line["ttfb_ms"] >= 0 and line["total_ms"] >= line["ttfb_ms"]
