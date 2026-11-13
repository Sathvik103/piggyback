"""HTTP API (step 6), backed by Postgres.

    python -m uvicorn api.app:create_app --factory --port 8000
    # uses $DATABASE_URL (default: the docker compose database on port 5433)

Operator routes only ever see what an operator could: the timetable, shipments,
visible scans, disruptions, detections, plans and decisions. Everything that
reads ground truth lives under /scenarios/{id}/evaluation/ so a dashboard can't
leak it by accident.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from benchmark.report import benchmark_summary
from db import DEFAULT_URL, Repo, migrate
from detection import DetectorConfig, score
from detection.detector import KIND_AT_RISK
from detection.view import scans_by_shipment
from recovery import OptionsConfig, RecoveryOption, replay
from simulator.models import ScenarioConfig

from .events import EventHub
from .store import SCENARIO_ID, CapacityConflict, Conflict, Invalid, NotFound, RouteContext, ScenarioStore

DEFAULT_GRACE_H = DetectorConfig().grace_h
_defaults = ScenarioConfig()
STATIC_DIR = Path(__file__).parent / "static"  # web/ builds here (npm run build)
HEARTBEAT_S = 15.0  # keeps proxies from closing an idle event stream
Grace = Query(DEFAULT_GRACE_H, ge=0.0, le=24.0)


class ScenarioCreate(BaseModel):
    scenario_id: str = Field(pattern=SCENARIO_ID.pattern, examples=["seed7_tight"])
    seed: int = 42
    n_shipments: int = Field(_defaults.n_shipments, ge=50, le=20_000)
    capacity_tightness: float = Field(_defaults.capacity_tightness, ge=0.0, le=0.99)
    anomaly_rate: float = Field(_defaults.anomaly_rate, ge=0.0, le=1.0)
    n_incidents: int = Field(_defaults.n_incidents, ge=0, le=10)
    n_disruptions: int = Field(_defaults.n_disruptions, ge=0, le=50)
    trip_delay_rate: float = Field(_defaults.trip_delay_rate, ge=0.0, le=1.0)
    scan_lag_rate: float = Field(_defaults.scan_lag_rate, ge=0.0, le=1.0)
    delay_mode: Literal["trip", "leg"] = _defaults.delay_mode
    cancel_rate: float = Field(_defaults.cancel_rate, ge=0.0, le=0.5)
    capacity_correlation: float = Field(_defaults.capacity_correlation, ge=0.0, le=1.0)


class PlanCreate(BaseModel):
    solver: Literal["cpsat", "greedy", "none"] = "cpsat"
    grace_h: float = Field(DEFAULT_GRACE_H, ge=0.0, le=24.0)
    time_limit_s: float = Field(10.0, gt=0.0, le=60.0)
    include_at_risk: bool = Field(False, description="Also rebook AT_RISK parcels now. Off by default: the "
                                  "benchmark found acting early doesn't pay (see docs/DECISIONS.md).")


class DecisionCreate(BaseModel):
    shipment_id: str = Field(max_length=32)
    action: Literal["ACCEPT", "DISMISS", "REVOKE"]
    plan_id: str | None = Field(None, description="ACCEPT this plan's choice for the shipment")
    option_id: str | None = Field(None, description="ACCEPT one of the shipment's live options")
    note: str = Field("", max_length=500)
    decided_by: str = Field("operator", max_length=64)
    grace_h: float = Field(DEFAULT_GRACE_H, ge=0.0, le=24.0)


def _choice(c: RecoveryOption, ctx: RouteContext) -> dict:
    """An option plus what the UI draws and shows: `route`, the hubs it passes through,
    and `load_after_pct`, how full the fullest truck on it would be once this parcel
    is added (None when it uses no spare capacity)."""
    if c.legs:
        route = [c.from_hub] + [ctx.legs[l].to_hub for l in c.legs]
    elif c.mode == "DEDICATED":
        route = [c.from_hub, ctx.dest[c.shipment_id]]
    else:
        route = [c.from_hub]
    loads = []
    for leg in c.capacity_legs:
        cap_kg, cap_m3 = ctx.totals[leg]
        free_kg, free_m3 = ctx.free[leg]
        loads.append(max((cap_kg - free_kg + c.weight_kg) / cap_kg, (cap_m3 - free_m3 + c.volume_m3) / cap_m3))
    return {**asdict(c), "route": route, "load_after_pct": round(100 * max(loads), 1) if loads else None}


def create_app(database_url: str = DEFAULT_URL, run_migrations: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_migrations:
            migrate(database_url)
        repo = Repo(database_url)
        app.state.store = ScenarioStore(repo)
        app.state.events = EventHub(database_url)
        app.state.events.start(asyncio.get_running_loop())
        try:
            yield
        finally:
            app.state.events.stop()
            repo.close()

    app = FastAPI(title="Piggyback", version="0.7.0", lifespan=lifespan,
                  description="Delay-aware shipment recovery on scheduled trips with spare capacity.")
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    def store() -> ScenarioStore:
        return app.state.store

    def _error(status: int):
        async def handler(_: Request, exc: Exception):
            return JSONResponse(status_code=status, content={"detail": str(exc.args[0]) if exc.args else str(exc)})
        return handler

    app.add_exception_handler(NotFound, _error(404))
    app.add_exception_handler(Conflict, _error(409))
    app.add_exception_handler(Invalid, _error(422))

    @app.exception_handler(CapacityConflict)
    async def _capacity(_: Request, exc: CapacityConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc), "legs": exc.legs})

    # ------------------------------------------------------------------ scenarios

    @app.get("/health")
    def health():
        return {"status": "ok", "scenarios": len(store().list())}

    @app.get("/scenarios")
    def list_scenarios():
        return store().list()

    @app.post("/scenarios", status_code=201)
    def create_scenario(body: ScenarioCreate):
        knobs = body.model_dump(exclude={"scenario_id", "seed"})
        return {"scenario_id": body.scenario_id, **store().create(body.scenario_id, body.seed, **knobs)}

    @app.get("/scenarios/{sid}")
    def get_scenario(sid: str):
        return store().summary(sid)

    @app.get("/scenarios/{sid}/network")
    def network(sid: str):
        """Hubs and lanes as GeoJSON (lon, lat), ready for a map."""
        v = store().view(sid)
        hubs = {h.hub_id: h for h in v.hubs}
        feats = [{"type": "Feature", "id": h.hub_id,
                  "geometry": {"type": "Point", "coordinates": [h.lon, h.lat]},
                  "properties": {"kind": "hub", **asdict(h)}} for h in v.hubs]
        for l in v.lanes:
            a, b = hubs[l.from_hub], hubs[l.to_hub]
            feats.append({"type": "Feature", "id": l.lane_id,
                          "geometry": {"type": "LineString", "coordinates": [[a.lon, a.lat], [b.lon, b.lat]]},
                          "properties": {"kind": "lane", **asdict(l)}})
        return {"type": "FeatureCollection", "features": feats}

    @app.get("/scenarios/{sid}/disruptions")
    def disruptions(sid: str):
        return [asdict(d) for d in store().view(sid).disruptions]

    # ------------------------------------------------------------------ detections

    @app.get("/scenarios/{sid}/detections")
    def detections(sid: str, grace_h: float = Grace,
                   status: Literal["all", "open", "decided"] = "all",
                   kind: str | None = None, hub: str | None = None, priority: str | None = None,
                   limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)):
        """Flags, each with the operator decision in force on it (or null)."""
        s = store()
        dets = s.detections(sid, grace_h)
        decided = s.decisions(sid)
        rows = [d for d in dets
                if (kind is None or d.kind == kind) and (hub is None or d.suspected_hub == hub)
                and (priority is None or d.priority == priority)
                and (status == "all" or (status == "open") == (d.shipment_id not in decided))]
        return {
            "now_h": s.view(sid).now,
            "grace_h": grace_h,
            "total": len(rows),
            "stranded": sum(d.kind != KIND_AT_RISK for d in rows),
            "open": sum(d.shipment_id not in decided for d in rows),
            "by_kind": dict(sorted(Counter(d.kind for d in rows).items())),
            "by_hub": dict(Counter(d.suspected_hub for d in rows).most_common()),
            "items": [{**asdict(d), "decision": decided.get(d.shipment_id, {}).get("action")}
                      for d in rows[offset:offset + limit]],
        }

    @app.get("/scenarios/{sid}/shipments/{shipment_id}")
    def shipment(sid: str, shipment_id: str, grace_h: float = Grace):
        s = store()
        v = s.view(sid)
        ship = next((x for x in v.shipments if x.shipment_id == shipment_id), None)
        if ship is None:
            raise NotFound(shipment_id)
        legs = {l.leg_id: l for l in v.legs}
        ctx = s.route_context(sid)
        det = next((d for d in s.detections(sid, grace_h) if d.shipment_id == shipment_id), None)
        plans = []
        for p in s.plans(sid):
            choice = s.repo.plan_choices(sid, int(p["plan_id"][1:]), shipment_id)
            if choice:
                plans.append({"plan_id": p["plan_id"], "solver": p["solver"],
                              "choice": _choice(choice[0], ctx)})
        return {
            "shipment": asdict(ship),
            "booked_legs": [asdict(legs[i]) for i in ship.planned_legs.split(";")],
            "scans": [asdict(e) for e in scans_by_shipment(v.scans).get(shipment_id, [])],
            "detection": asdict(det) if det else None,
            "decision": s.decisions(sid).get(shipment_id),
            "plans": plans,
        }

    @app.get("/scenarios/{sid}/shipments/{shipment_id}/options")
    def shipment_options(sid: str, shipment_id: str, grace_h: float = Grace):
        """Live recovery options for a flagged shipment, against capacity still free."""
        s = store()
        opts = s.options_for(sid, shipment_id, grace_h)
        ctx = s.route_context(sid, free_for=shipment_id)
        return [_choice(o, ctx) for o in opts]

    @app.get("/scenarios/{sid}/hubs/{hub_id}")
    def hub(sid: str, hub_id: str, window_h: float = Query(24.0, gt=0.0, le=144.0), grace_h: float = Grace):
        """A hub's flagged parcels and the departures (with spare room) it can use soon."""
        s = store()
        v = s.view(sid)
        h = next((x for x in v.hubs if x.hub_id == hub_id), None)
        if h is None:
            raise NotFound(hub_id)
        cap = s.repo.residual_capacity(sid, v)
        departures = [{**asdict(e), "free_kg": round(cap[e.leg_id][0], 1), "free_m3": round(cap[e.leg_id][1], 2)}
                      for e in s.network(sid).legs
                      if e.from_hub == hub_id and e.can_board and v.now <= e.dep <= v.now + window_h]
        decided = s.decisions(sid)
        here = [{**asdict(d), "decision": decided.get(d.shipment_id, {}).get("action")}
                for d in s.detections(sid, grace_h) if d.suspected_hub == hub_id]
        return {"hub": asdict(h), "now_h": v.now, "detections": here, "departures": departures}

    # ------------------------------------------------------------------ plans

    @app.post("/scenarios/{sid}/plans", status_code=201)
    def create_plan(sid: str, body: PlanCreate):
        """Plan every stranded shipment without a decision (AT_RISK too if asked), against
        capacity still free."""
        s = store()
        s.check(sid)
        return s.create_plan(sid, body.solver, body.grace_h, body.time_limit_s, body.include_at_risk)

    @app.get("/scenarios/{sid}/plans")
    def list_plans(sid: str):
        return store().plans(sid)

    @app.get("/scenarios/{sid}/plans/{plan_id}")
    def get_plan(sid: str, plan_id: str, mode: str | None = None):
        s = store()
        summary, choices = s.plan(sid, plan_id)
        ctx = s.route_context(sid)
        return {**summary, "choices": [_choice(c, ctx) for c in choices if mode is None or c.mode == mode]}

    # ------------------------------------------------------------------ decisions

    @app.post("/scenarios/{sid}/decisions", status_code=201)
    def create_decision(sid: str, body: DecisionCreate):
        """ACCEPT a recovery (from a plan or the live options), DISMISS a false flag, or
        REVOKE the decision in force. ACCEPT holds the spare capacity it uses; 409 if
        another accepted recovery got there first."""
        return store().decide(sid, body.shipment_id, body.action, body.plan_id, body.option_id,
                              body.note, body.decided_by, body.grace_h)

    @app.get("/scenarios/{sid}/events", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}}})
    async def events(sid: str, request: Request):
        """Server-sent events for this scenario: `decision` and `plan` whenever one is
        committed (by anyone, via any API process), and `resync` if updates may have
        been missed. Payloads carry ids only; clients re-fetch what changed."""
        store().check(sid)
        hub: EventHub = app.state.events
        q = hub.subscribe(sid)

        async def stream():
            try:
                yield f"retry: 3000\nevent: ready\ndata: {json.dumps({'scenario_id': sid})}\n\n"
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
            finally:
                hub.unsubscribe(sid, q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/scenarios/{sid}/decisions")
    def list_decisions(sid: str, history: bool = False):
        """Decisions in force, or with `history=true` the whole append-only log."""
        s = store()
        s.check(sid)
        return s.repo.decisions(sid, in_force_only=not history)

    # ------------------------------------------------------------------ benchmark

    @app.get("/benchmark")
    def benchmark():
        """The committed head-to-head and stress results (results/*.csv) as JSON."""
        return benchmark_summary()

    # ------------------------------------------------------------------ evaluation (reads truth)

    def _replay(sid: str, plan, grace_h: float, outcomes: bool) -> dict:
        s = store()
        truth, delays = s.truth(sid)
        outs, k = replay(s.view(sid), truth, delays, s.detections(sid, grace_h), plan, OptionsConfig())
        body = {"kpis": k, "by_status": dict(sorted(Counter(o.status for o in outs).items()))}
        if outcomes:
            body["outcomes"] = [asdict(o) for o in outs]
        return body

    @app.get("/scenarios/{sid}/evaluation/detections")
    def evaluate_detections(sid: str, grace_h: float = Grace):
        truth, _ = store().truth(sid)
        return score(store().detections(sid, grace_h), truth)

    @app.get("/scenarios/{sid}/evaluation/plans/{plan_id}")
    def evaluate_plan(sid: str, plan_id: str, outcomes: bool = False):
        """Replay one plan on its own. Shipments it doesn't cover (e.g. decided before
        it ran) count as left alone."""
        s = store()
        summary, choices = s.plan(sid, plan_id)
        return {"plan_id": plan_id, "solver": summary["solver"],
                **_replay(sid, s.as_plan(summary, choices), summary["grace_h"], outcomes)}

    @app.get("/scenarios/{sid}/evaluation/decisions")
    def evaluate_decisions(sid: str, grace_h: float = Grace, outcomes: bool = False):
        """Replay what the operator has committed to: accepted recoveries only."""
        s = store()
        s.check(sid)
        return {"accepted": len(s.accepted_plan(sid).chosen),
                **_replay(sid, s.accepted_plan(sid), grace_h, outcomes)}

    # ------------------------------------------------------------------ web app

    built = (STATIC_DIR / "index.html").exists()

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard/", include_in_schema=False)  # the old address
    def root():
        if built:
            return RedirectResponse("/app/")
        return JSONResponse({"detail": "The web app isn't built. Run `npm ci && npm run build` in web/, "
                                       "or use the API directly at /docs."})

    if built:
        app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="app")
    return app
