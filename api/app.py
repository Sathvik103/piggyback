"""HTTP API (step 6).

    uvicorn api.app:app --reload          # serves ./data, or $PIGGYBACK_DATA_DIR

Operator routes only ever see what an operator could: the timetable, shipments,
visible scans, disruptions, detections and plans. Everything that reads ground
truth lives under /scenarios/{id}/evaluation/ so a dashboard can't leak it by
accident.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from detection import DetectorConfig, score
from detection.detector import KIND_AT_RISK
from detection.view import scans_by_shipment
from recovery import OptionsConfig, replay
from simulator.models import ScenarioConfig

from .store import SCENARIO_ID, Conflict, NotFound, ScenarioStore

DEFAULT_GRACE_H = DetectorConfig().grace_h
_defaults = ScenarioConfig()


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


class PlanCreate(BaseModel):
    solver: Literal["cpsat", "greedy", "none"] = "cpsat"
    grace_h: float = Field(DEFAULT_GRACE_H, ge=0.0, le=24.0)
    time_limit_s: float = Field(10.0, gt=0.0, le=60.0)


def _plan_summary(rec: dict) -> dict:
    s = {k: v for k, v in rec.items() if k != "choices"}
    s["shipments"] = len(rec["choices"])
    s["by_mode"] = dict(sorted(Counter(c["mode"] for c in rec["choices"]).items()))
    s["cost_inr"] = round(sum(c["cost_inr"] for c in rec["choices"]))
    return s


def create_app(data_dir: Path) -> FastAPI:
    store = ScenarioStore(data_dir)
    app = FastAPI(title="Piggyback", version="0.6.0",
                  description="Delay-aware shipment recovery on scheduled trips with spare capacity.")
    app.state.store = store
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": f"not found: {exc.args[0]}"})

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": f"already exists: {exc.args[0]}"})

    # ------------------------------------------------------------------ scenarios

    @app.get("/health")
    def health():
        return {"status": "ok", "scenarios": len(store.list())}

    @app.get("/scenarios")
    def list_scenarios():
        return store.list()

    @app.post("/scenarios", status_code=201)
    def create_scenario(body: ScenarioCreate):
        knobs = body.model_dump(exclude={"scenario_id", "seed"})
        return {"scenario_id": body.scenario_id, **store.create(body.scenario_id, body.seed, **knobs)}

    @app.get("/scenarios/{sid}")
    def get_scenario(sid: str):
        return {"scenario_id": sid, **store.summary(sid)}

    @app.get("/scenarios/{sid}/network")
    def network(sid: str):
        """Hubs and lanes as GeoJSON (lon, lat), ready for a map."""
        v = store.view(sid)
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
        return [asdict(d) for d in store.view(sid).disruptions]

    # ------------------------------------------------------------------ detections

    @app.get("/scenarios/{sid}/detections")
    def detections(sid: str,
                   grace_h: float = Query(DEFAULT_GRACE_H, ge=0.0, le=24.0),
                   kind: Optional[str] = None, hub: Optional[str] = None,
                   priority: Optional[str] = None,
                   limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)):
        dets = store.detections(sid, grace_h)
        rows = [d for d in dets
                if (kind is None or d.kind == kind) and (hub is None or d.suspected_hub == hub)
                and (priority is None or d.priority == priority)]
        return {
            "now_h": store.view(sid).now,
            "grace_h": grace_h,
            "total": len(rows),
            "stranded": sum(d.kind != KIND_AT_RISK for d in rows),
            "by_kind": dict(sorted(Counter(d.kind for d in rows).items())),
            "by_hub": dict(Counter(d.suspected_hub for d in rows).most_common()),
            "items": [asdict(d) for d in rows[offset:offset + limit]],
        }

    @app.get("/scenarios/{sid}/shipments/{shipment_id}")
    def shipment(sid: str, shipment_id: str, grace_h: float = Query(DEFAULT_GRACE_H, ge=0.0, le=24.0)):
        v = store.view(sid)
        s = next((x for x in v.shipments if x.shipment_id == shipment_id), None)
        if s is None:
            raise NotFound(shipment_id)
        legs = {l.leg_id: l for l in v.legs}
        det = next((d for d in store.detections(sid, grace_h) if d.shipment_id == shipment_id), None)
        plans = [{"plan_id": p["plan_id"], "solver": p["solver"], "choice": c}
                 for p in store.plans(sid) for c in p["choices"] if c["shipment_id"] == shipment_id]
        return {
            "shipment": asdict(s),
            "booked_legs": [asdict(legs[i]) for i in s.planned_legs.split(";")],
            "scans": [asdict(e) for e in scans_by_shipment(v.scans).get(shipment_id, [])],
            "detection": asdict(det) if det else None,
            "plans": plans,
        }

    @app.get("/scenarios/{sid}/hubs/{hub_id}")
    def hub(sid: str, hub_id: str, window_h: float = Query(24.0, gt=0.0, le=144.0),
            grace_h: float = Query(DEFAULT_GRACE_H, ge=0.0, le=24.0)):
        """A hub's stranded parcels and the departures (with spare room) it can use soon."""
        v = store.view(sid)
        h = next((x for x in v.hubs if x.hub_id == hub_id), None)
        if h is None:
            raise NotFound(hub_id)
        net = store.network(sid)
        departures = [asdict(e) for e in net.legs
                      if e.from_hub == hub_id and e.can_board and v.now <= e.dep <= v.now + window_h]
        here = [asdict(d) for d in store.detections(sid, grace_h) if d.suspected_hub == hub_id]
        return {"hub": asdict(h), "now_h": v.now, "detections": here, "departures": departures}

    # ------------------------------------------------------------------ plans

    @app.post("/scenarios/{sid}/plans", status_code=201)
    def create_plan(sid: str, body: PlanCreate):
        store.path(sid)
        return _plan_summary(store.create_plan(sid, body.solver, body.grace_h, body.time_limit_s))

    @app.get("/scenarios/{sid}/plans")
    def list_plans(sid: str):
        return [_plan_summary(p) for p in store.plans(sid)]

    @app.get("/scenarios/{sid}/plans/{plan_id}")
    def get_plan(sid: str, plan_id: str, mode: Optional[str] = None):
        rec = store.plan(sid, plan_id)
        choices = [c for c in rec["choices"] if mode is None or c["mode"] == mode]
        return {**_plan_summary(rec), "choices": choices}

    # ------------------------------------------------------------------ evaluation (reads truth)

    @app.get("/scenarios/{sid}/evaluation/detections")
    def evaluate_detections(sid: str, grace_h: float = Query(DEFAULT_GRACE_H, ge=0.0, le=24.0)):
        truth, _ = store.truth(sid)
        return score(store.detections(sid, grace_h), truth)

    @app.get("/scenarios/{sid}/evaluation/plans/{plan_id}")
    def evaluate_plan(sid: str, plan_id: str, outcomes: bool = False):
        rec = store.plan(sid, plan_id)
        truth, delays = store.truth(sid)
        dets = store.detections(sid, rec["grace_h"])
        outs, k = replay(store.view(sid), truth, delays, dets, store.as_plan(rec), OptionsConfig())
        body = {"plan_id": plan_id, "solver": rec["solver"], "kpis": k,
                "by_status": dict(sorted(Counter(o.status for o in outs).items()))}
        if outcomes:
            body["outcomes"] = [asdict(o) for o in outs]
        return body

    return app


app =create_app(Path(os.environ.get("PIGGYBACK_DATA_DIR", "data")))
