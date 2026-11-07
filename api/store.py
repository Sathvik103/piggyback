"""What the API needs, on top of the Postgres repository.

Postgres is the system of record (scenarios, plans, decisions). Derived objects
that are expensive and immutable per scenario (the operator view, the planner's
network, detections per grace period) are cached in memory. Truth is read only
by `truth()`, which the API uses for /evaluation routes alone.
"""

from __future__ import annotations

import re
import threading
from dataclasses import replace

from db import CapacityConflict, Repo
from db.repo import ACCEPT, DISMISS, REVOKE, option_from_json
from detection import Detection, DetectorConfig, OperatorView, detect
from recovery import SOLVERS, Network, OptionsConfig, Plan, RecoveryOption, generate_options
from recovery.options import MODE_NONE
from simulator import generate_scenario
from simulator.models import TruthRecord

SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PLAN_ID = re.compile(r"^p([0-9]{1,6})$")

__all__ = ["ScenarioStore", "NotFound", "Conflict", "Invalid", "CapacityConflict", "SCENARIO_ID"]


class NotFound(KeyError):
    pass


class Conflict(ValueError):
    pass


class Invalid(ValueError):
    pass


def plan_no(plan_id: str) -> int:
    m = PLAN_ID.match(plan_id)
    if not m:
        raise NotFound(plan_id)
    return int(m.group(1))


class ScenarioStore:
    def __init__(self, repo: Repo):
        self.repo = repo
        self._lock = threading.RLock()
        self._views: dict[str, OperatorView] = {}
        self._networks: dict[str, Network] = {}
        self._detections: dict[tuple[str, float], list[Detection]] = {}

    # ------------------------------------------------------------------ scenarios

    def check(self, sid: str) -> None:
        if not SCENARIO_ID.match(sid) or not self.repo.exists(sid):
            raise NotFound(sid)

    def list(self) -> list[dict]:
        return self.repo.scenarios()

    def summary(self, sid: str) -> dict:
        s = self.repo.scenario(sid) if SCENARIO_ID.match(sid) else None
        if s is None:
            raise NotFound(sid)
        return s

    def create(self, sid: str, seed: int, **knobs) -> dict:
        if self.repo.exists(sid):
            raise Conflict(sid)
        return self.repo.save_scenario(sid, generate_scenario(seed=seed, **knobs))

    # ------------------------------------------------------------------ operator data

    def view(self, sid: str) -> OperatorView:
        with self._lock:
            if sid not in self._views:
                v = self.repo.view(sid) if SCENARIO_ID.match(sid) else None
                if v is None:
                    raise NotFound(sid)
                self._views[sid] = v
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

    def decisions(self, sid: str) -> dict[str, dict]:
        """Decisions in force, by shipment."""
        return {d["shipment_id"]: d for d in self.repo.decisions(sid)}

    def open_detections(self, sid: str, grace_h: float) -> list[Detection]:
        decided = self.decisions(sid)
        return [d for d in self.detections(sid, grace_h) if d.shipment_id not in decided]

    def options_for(self, sid: str, shipment_id: str, grace_h: float) -> list[RecoveryOption]:
        """Live options for one flagged shipment, against what capacity is left. The
        shipment's own accepted hold, if any, counts as free for it."""
        view = self.view(sid)
        det = next((d for d in self.detections(sid, grace_h) if d.shipment_id == shipment_id), None)
        if det is None:
            raise NotFound(f"{shipment_id} is not flagged at grace {grace_h} h")
        cap = self.repo.residual_capacity(sid, view)
        mine = self.decisions(sid).get(shipment_id)
        if mine and mine["action"] == ACCEPT:
            o = mine["option"]
            for leg in o["capacity_legs"]:
                kg, m3 = cap[leg]
                cap[leg] = (kg + o["weight_kg"], m3 + o["volume_m3"])
        return generate_options(view, [det], OptionsConfig(), network=self.network(sid), capacity=cap)[shipment_id]

    # ------------------------------------------------------------------ plans

    def create_plan(self, sid: str, solver: str, grace_h: float, time_limit_s: float) -> dict:
        """Plan every flagged shipment nobody has decided on yet, against the capacity
        accepted recoveries have left."""
        view = self.view(sid)
        dets = self.open_detections(sid, grace_h)
        cap = self.repo.residual_capacity(sid, view)
        options = generate_options(view, dets, OptionsConfig(), network=self.network(sid), capacity=cap)
        solve = SOLVERS[solver]
        plan = solve(options, cap, time_limit_s=time_limit_s) if solver == "cpsat" else solve(options, cap)
        n = self.repo.save_plan(sid, plan, grace_h, sum(map(len, options.values())))
        return self.plan_summary(sid, f"p{n}")

    def plans(self, sid: str) -> list[dict]:
        self.check(sid)
        return [_with_id(p) for p in self.repo.plans(sid)]

    def plan_summary(self, sid: str, plan_id: str) -> dict:
        p = next((p for p in self.plans(sid) if p["plan_id"] == plan_id), None)
        if p is None:
            raise NotFound(plan_id)
        return p

    def plan(self, sid: str, plan_id: str) -> tuple[dict, list[RecoveryOption]]:
        summary = self.plan_summary(sid, plan_id)
        return summary, self.repo.plan_choices(sid, plan_no(plan_id))

    def as_plan(self, summary: dict, choices: list[RecoveryOption]) -> Plan:
        return Plan(summary["solver"], {c.shipment_id: c for c in choices}, summary["objective_inr"],
                    summary["status"], summary["solve_s"])

    # ------------------------------------------------------------------ decisions

    def decide(self, sid: str, shipment_id: str, action: str, plan_id: str | None, option_id: str | None,
               note: str, decided_by: str, grace_h: float) -> dict:
        self.check(sid)
        if not any(s.shipment_id == shipment_id for s in self.view(sid).shipments):
            raise NotFound(shipment_id)
        option, n = None, None
        if action == ACCEPT:
            if plan_id is not None:
                n = plan_no(plan_id)
                choice = self.repo.plan_choices(sid, n, shipment_id)
                if not choice:
                    raise NotFound(f"{shipment_id} is not in plan {plan_id}")
                option = choice[0]
                if option_id is not None and option_id != option.option_id:
                    raise Invalid(f"plan {plan_id} chose {option.option_id}, not {option_id}")
            elif option_id is not None:
                option = next((o for o in self.options_for(sid, shipment_id, grace_h)
                               if o.option_id == option_id), None)
                if option is None:
                    raise NotFound(f"option {option_id} (options change as capacity is taken; refetch them)")
            else:
                raise Invalid("ACCEPT needs plan_id or option_id")
            if option.mode == MODE_NONE:
                raise Invalid("accepting 'leave it' is not a recovery; DISMISS the flag instead")
        elif plan_id is not None or option_id is not None:
            raise Invalid(f"{action} takes no plan_id or option_id")
        try:
            return self.repo.decide(sid, shipment_id, action, option, n, note, decided_by)
        except LookupError as e:
            raise Conflict(str(e)) from None

    def accepted_plan(self, sid: str) -> Plan:
        """The operator's committed recoveries, as a Plan (for replay)."""
        chosen = {}
        for sid_, d in self.decisions(sid).items():
            if d["action"] == ACCEPT:
                chosen[sid_] = option_from_json(d["option"])
        return Plan("decisions", chosen, round(sum(o.penalty_inr for o in chosen.values()), 2), "COMMITTED", 0.0)

    # ------------------------------------------------------------------ truth (evaluation only)

    def truth(self, sid: str) -> tuple[list[TruthRecord], dict[str, float]]:
        self.check(sid)
        return self.repo.truth(sid)


def _with_id(p: dict) -> dict:
    return {"plan_id": f"p{p.pop('plan_no')}", **p}


# Re-exported for the app's exception handlers.
ACTIONS = (ACCEPT, DISMISS, REVOKE)
