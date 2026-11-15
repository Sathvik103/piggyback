"""Metrics, request IDs and structured access logs.

- `GET /metrics` serves Prometheus metrics: request rate and latency per route
  template, requests in flight, plans (count and solve time by solver and status),
  decisions by action, capacity conflicts, live-update subscribers and the database
  pool. It is open to loopback clients, or to anyone presenting
  `Authorization: Bearer $PIGGYBACK_METRICS_TOKEN` when that is set.
- Every response carries an `X-Request-ID` (the caller's, if it sent a sane one), and
  every request is logged as one JSON line on the `piggyback.access` logger with
  that id, the route template, status, the signed-in user, time to first byte and
  total time. Latency metrics use time to first byte, so the long-lived event
  stream doesn't distort them.

Metrics live in a per-app registry, so several apps (e.g. in tests) don't collide.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import time
import uuid

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
PLAN_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60)
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

access_log = logging.getLogger("piggyback.access")


class Metrics:
    def __init__(self):
        self.registry = r = CollectorRegistry()
        self.requests = Counter("piggyback_http_requests_total", "HTTP requests",
                                ["method", "route", "status"], registry=r)
        self.latency = Histogram("piggyback_http_request_duration_seconds", "Time to first response byte",
                                 ["method", "route"], buckets=LATENCY_BUCKETS, registry=r)
        self.in_flight = Gauge("piggyback_http_requests_in_flight", "Requests being handled", registry=r)
        self.plans = Counter("piggyback_plans_total", "Plans created", ["solver", "status"], registry=r)
        self.plan_seconds = Histogram("piggyback_plan_duration_seconds", "Option generation + solve time",
                                      ["solver"], buckets=PLAN_BUCKETS, registry=r)
        self.decisions = Counter("piggyback_decisions_total", "Decisions recorded", ["action"], registry=r)
        self.capacity_conflicts = Counter("piggyback_capacity_conflicts_total",
                                          "Accepts refused because the space was already taken", registry=r)

    def watch(self, pool, events) -> None:
        """Expose the DB pool and live-update subscribers, read at scrape time."""
        self.registry.register(_RuntimeCollector(pool, events))


class _RuntimeCollector:
    def __init__(self, pool, events):
        self.pool, self.events = pool, events

    def describe(self):
        return []

    def collect(self):
        s = self.pool.get_stats()
        for key, help_ in (("pool_size", "Connections open"), ("pool_available", "Connections idle"),
                           ("requests_waiting", "Requests waiting for a connection")):
            g = GaugeMetricFamily(f"piggyback_db_{key}", help_)
            g.add_metric([], s.get(key, 0))
            yield g
        g = GaugeMetricFamily("piggyback_live_subscribers", "Browsers on the live event stream")
        g.add_metric([], self.events.subscribers_total())
        yield g
        g = GaugeMetricFamily("piggyback_live_listener_up", "1 if the Postgres LISTEN connection is up")
        g.add_metric([], 1 if self.events.connected.is_set() else 0)
        yield g


def metrics_allowed(client_host: str | None, authorization: str | None) -> bool:
    token = os.environ.get("PIGGYBACK_METRICS_TOKEN")
    if token:
        return hmac.compare_digest(authorization or "", f"Bearer {token}")
    return client_host in LOOPBACK


def _route_label(scope) -> str:
    route = scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    if scope["path"].startswith("/app/"):
        return "/app/*"
    return "unmatched"  # 404s etc.: never the raw path, which would explode label cardinality


class ObservabilityMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware), so streaming responses pass through untouched."""

    def __init__(self, app, metrics: Metrics):
        self.app, self.metrics = app, metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.perf_counter()
        info = {"status": 500, "ttfb": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                info["status"] = message["status"]
                info["ttfb"] = time.perf_counter() - t0
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        self.metrics.in_flight.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self.metrics.in_flight.dec()
            total = time.perf_counter() - t0
            route, method = _route_label(scope), scope["method"]
            ttfb = info["ttfb"] if info["ttfb"] is not None else total
            self.metrics.requests.labels(method, route, str(info["status"])).inc()
            self.metrics.latency.labels(method, route).observe(ttfb)
            user = scope.get("state", {}).get("user")
            access_log.info(json.dumps({
                "ts": round(time.time(), 3), "request_id": rid, "method": method, "route": route,
                "path": scope["path"], "status": info["status"], "user": user["username"] if user else None,
                "ttfb_ms": round(1000 * ttfb, 1), "total_ms": round(1000 * total, 1),
            }))


def configure_access_log() -> None:
    """JSON lines to stderr, unless the host app already configured the logger."""
    if not access_log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(message)s"))
        access_log.addHandler(h)
        access_log.setLevel(os.environ.get("PIGGYBACK_ACCESS_LOG", "INFO").upper())
        access_log.propagate = False
