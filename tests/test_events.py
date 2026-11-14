"""Live updates: Postgres NOTIFY -> EventHub -> server-sent events (real Postgres)."""

import asyncio
import json
import socket
import threading
import time

import httpx
import psycopg
import pytest
import uvicorn

from api import create_app
from api.events import EventHub
from db import Repo
from simulator import generate_scenario


@pytest.fixture(scope="module")
def repo(pg_url):
    r = Repo(pg_url, max_size=2)
    for sid in ("ev1", "ev2"):
        r.delete_scenario(sid)
        r.save_scenario(sid, generate_scenario(seed=11, n_shipments=200))
    yield r
    for sid in ("ev1", "ev2"):
        r.delete_scenario(sid)
    r.close()


def _shipment(repo, sid):
    return repo.view(sid).shipments[0].shipment_id


async def _next(q, timeout=5.0):
    return await asyncio.wait_for(q.get(), timeout)


def test_hub_relays_committed_decisions_to_that_scenarios_subscribers_only(pg_url, repo):
    async def run():
        hub = EventHub(pg_url)
        hub.start(asyncio.get_running_loop())
        try:
            assert await asyncio.to_thread(hub.connected.wait, 5)
            q1, q2 = hub.subscribe("ev1"), hub.subscribe("ev2")
            sid = _shipment(repo, "ev1")

            # A rolled-back write announces nothing.
            with psycopg.connect(pg_url) as conn:
                conn.execute("INSERT INTO decisions (scenario_id, shipment_id, action) VALUES ('ev1', %s, 'DISMISS')",
                             (sid,))
                conn.rollback()

            d = await asyncio.to_thread(repo.decide, "ev1", sid, "DISMISS", decided_by="ana")
            ev = await _next(q1)
            assert ev == {"type": "decision", "scenario_id": "ev1", "shipment_id": sid, "action": "DISMISS",
                          "decision_id": d["decision_id"], "decided_by": "ana"}
            await asyncio.to_thread(repo.decide, "ev1", sid, "REVOKE")
            assert (await _next(q1))["action"] == "REVOKE"  # in commit order, nothing in between
            await asyncio.sleep(0.3)
            assert q1.empty() and q2.empty()  # ev2's watcher heard nothing

            hub.unsubscribe("ev1", q1)
            assert hub.subscribers("ev1") == 0 and hub.subscribers("ev2") == 1
        finally:
            hub.stop()

    asyncio.run(run())


def test_a_stalled_client_is_told_to_resync_instead_of_buffering_forever(pg_url, repo):
    async def run():
        hub = EventHub(pg_url, queue_size=2)
        hub.start(asyncio.get_running_loop())
        try:
            assert await asyncio.to_thread(hub.connected.wait, 5)
            q = hub.subscribe("ev2")
            sid = _shipment(repo, "ev2")
            for action in ("DISMISS", "REVOKE", "DISMISS", "REVOKE"):
                await asyncio.to_thread(repo.decide, "ev2", sid, action)
            await asyncio.sleep(0.5)
            events = [q.get_nowait() for _ in range(q.qsize())]
            assert {"type": "resync"} in events and len(events) <= 2
        finally:
            hub.stop()

    asyncio.run(run())


@pytest.fixture(scope="module")
def server(pg_url, repo):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(pg_url), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def _events(lines):
    """Parse an SSE line stream into (event, data) pairs."""
    name = None
    for line in lines:
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            yield name, json.loads(line[6:])


def test_events_endpoint_streams_plans_and_decisions(make_user, server, repo):
    sid = _shipment(repo, "ev1")
    password = make_user("ev-op")
    with httpx.Client(base_url=server, timeout=15) as http:
        assert http.get("/scenarios/ev1/events").status_code == 401  # signed-in users only
        assert http.post("/auth/login", json={"username": "ev-op", "password": password}).status_code == 200
        assert http.get("/scenarios/nope/events").status_code == 404
        with http.stream("GET", "/scenarios/ev1/events") as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            stream = _events(r.iter_lines())
            assert next(stream) == ("ready", {"scenario_id": "ev1"})

            plan = http.post("/scenarios/ev1/plans", json={"solver": "greedy", "grace_h": 2}).json()
            name, ev = next(stream)
            assert name == "plan" and ev["plan_id"] == plan["plan_id"] and ev["solver"] == "greedy"

            d = http.post("/scenarios/ev1/decisions", json={"shipment_id": sid, "action": "DISMISS"}).json()
            name, ev = next(stream)
            assert name == "decision" and ev["decision_id"] == d["decision_id"] and ev["shipment_id"] == sid
            http.post("/scenarios/ev1/decisions", json={"shipment_id": sid, "action": "REVOKE"})
