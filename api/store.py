"""Scenario directories on disk, with the expensive derived objects cached.

A scenario is a directory under `data_dir` written by `simulator.export.write_all`.
Plans are saved next to it as `plans/<plan_id>.json` so they survive restarts.
Operator-facing reads go through `OperatorView`; truth is read only by the
`truth()` method, which the API uses for /evaluation routes alone.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from detection import Detection, DetectorConfig, OperatorView, detect
from detection.view import read_table
from recovery import SOLVERS, Network, OptionsConfig, Plan, RecoveryOption, generate_options, leg_capacity
from simulator import generate_scenario
from simulator.export import write_all
from simulator.models import TruthRecord

SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PLAN_ID = re.compile(r"^p[0-9]{1,6}$")


class NotFound(KeyError):
    pass


class Conflict(ValueError):
    pass


class ScenarioStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._views: dict[str, OperatorView] = {}
        self._networks: dict[str, Network] = {}
        self._detections: dict[tuple[str, float], list[Detection]] = {}

    # ------------------------------------------------------------------ scenarios

    def path(self, sid: str) -> Path:
        if not SCENARIO_ID.match(sid):
            raise NotFound(sid)
        p = self.data_dir / sid
        if not (p / "summary.json").exists():
            raise NotFound(sid)
        return p

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.data_dir.iterdir()):
            if p.is_dir() and SCENARIO_ID.match(p.name) and (p / "summary.json").exists():
                out.append({"scenario_id": p.name, **self.summary(p.name)})
        return out

    def summary(self, sid: str) -> dict:
        return json.loads((self.path(sid) / "summary.json").read_text(encoding="utf-8"))

    def create(self, sid: str, seed: int, **knobs) -> dict:
        if not SCENARIO_ID.match(sid):
            raise ValueError("scenario_id must be lowercase letters, digits, '-' or '_' (max 64)")
        with self._lock:
            if (self.data_dir / sid / "summary.json").exists():
                raise Conflict(sid)
            sc = generate_scenario(seed=seed, **knobs)
            return write_all(sc, self.data_dir / sid)

    # ------------------------------------------------------------------ operator data

    def view(self, sid: str) -> OperatorView:
        with self._lock:
            if sid not in self._views:
                self._views[sid] = OperatorView.from_csv_dir(self.path(sid))
            return self._views[sid]

    def network(self, sid: str) -> Network:
        with self._lock:
            if sid not in self._networks:
                self._networks[sid] = Network(self.view(sid))
            return self._networks[sid]

    def detections(self, sid: str, grace_h: float) -> list[Detection]:
        key = (sid, round(grace_h, 2))
        with self._lock:
            if key not in self._detections:
                self._detections[key] = detect(self.view(sid), replace(DetectorConfig(), grace_h=grace_h))
            return self._detections[key]

    # ------------------------------------------------------------------ plans

    def create_plan(self, sid: str, solver: str, grace_h: float, time_limit_s: float) -> dict:
        view = self.view(sid)
        dets = self.detections(sid, grace_h)
        options = generate_options(view, dets, OptionsConfig(), network=self.network(sid))
        solve = SOLVERS[solver]
        cap = leg_capacity(view)
        plan = solve(options, cap, time_limit_s=time_limit_s) if solver == "cpsat" else solve(options, cap)
        with self._lock:
            pdir = self.path(sid) / "plans"
            pdir.mkdir(exist_ok=True)
            n = 1 + max((int(p.stem[1:]) for p in pdir.glob("p*.json") if PLAN_ID.match(p.stem)), default=0)
            record = {
                "plan_id": f"p{n}",
                "scenario_id": sid,
                "solver": plan.solver,
                "grace_h": grace_h,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": plan.status,
                "objective_inr": plan.objective_inr,
                "solve_s": plan.solve_s,
                "stats": plan.stats,
                "options_considered": sum(map(len, options.values())),
                "choices": [asdict(o) for o in sorted(plan.chosen.values(), key=lambda o: o.shipment_id)],
            }
            (pdir / f"p{n}.json").write_text(json.dumps(record), encoding="utf-8")
        return record

    def plans(self, sid: str) -> list[dict]:
        pdir = self.path(sid) / "plans"
        if not pdir.exists():
            return []
        recs = [self.plan(sid, p.stem) for p in pdir.glob("p*.json") if PLAN_ID.match(p.stem)]
        return sorted(recs, key=lambda r: int(r["plan_id"][1:]))

    def plan(self, sid: str, plan_id: str) -> dict:
        if not PLAN_ID.match(plan_id):
            raise NotFound(plan_id)
        p = self.path(sid) / "plans" / f"{plan_id}.json"
        if not p.exists():
            raise NotFound(plan_id)
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def as_plan(record: dict) -> Plan:
        chosen = {}
        for c in record["choices"]:
            c = {**c, "legs": tuple(c["legs"]), "capacity_legs": tuple(c["capacity_legs"])}
            chosen[c["shipment_id"]] = RecoveryOption(**c)
        return Plan(record["solver"], chosen, record["objective_inr"], record["status"], record["solve_s"])

    # ------------------------------------------------------------------ truth (evaluation only)

    def truth(self, sid: str) -> tuple[list[TruthRecord], dict[str, float]]:
        p = self.path(sid)
        if not (p / "sim_truth.csv").exists():
            raise NotFound(f"{sid} has no ground truth")
        return (read_table(p, "sim_truth"),
                {d.trip_id: d.delay_h for d in read_table(p, "sim_trip_delays")})
