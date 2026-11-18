"""Risk-aware recovery: plan against the delays nobody has seen yet.

A recovery that rides several trucks depends on trucks that haven't run yet. Their
delays are unknown when we book, so a tight transfer after one of them is a gamble,
and a truck arriving late at the end makes the parcel late. The original planner
handled this with a fixed rule (demand 1.5 h extra slack after an unobserved truck),
which throws away connections that would usually have worked.

Here the planner learns how late trucks run from the trucks the operator has already
watched (`DelayModel.from_view`: observed delays only, never the simulator's truth),
and prices each option by its *expected* outcome:

    P(ok)  = product over transfers after an unobserved truck of P(delay <= slack)
    E[late | ok] = E[max(0, arrival + D - deadline)]   (D = the last truck's unknown delay)
    expected lateness = P(ok) * E[late | ok] + (1 - P(ok)) * BROKEN_LATENESS_H
    expected penalty  = cost + value of hour * priority weight * P(stranded) * expected lateness

P(stranded) is the calibrated chance that the flag is a real stranding at all
(recovery/data/calibration.json, measured on validation seeds by
scripts/calibrate_detection.py): money is spent for sure, but lateness is only saved if
the parcel really is stuck, so an expensive truck for a doubtful flag rarely pays.

The optimizer then minimises expected penalty, so a connection with an hour of slack
that holds 85% of the time can beat a route that is certainly seven hours late.
`BROKEN_LATENESS_H` (how late a parcel ends up when its booking breaks and has to be
rebooked) is the one tuning knob; it was chosen on validation seeds 101-110 only.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

from detection.detector import Detection, observed_trip_delays
from detection.view import OperatorView
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import Shipment

from .network import Network
from .options import MODE_NONE, MODE_PIGGYBACK, PRIORITY_WEIGHT, OptionsConfig, RecoveryOption, generate_options

BROKEN_LATENESS_H = 12.0   # chosen on validation seeds 101-110; see docs/DECISIONS.md
MIN_SAMPLES = 30           # fewer observed trucks than this: too little to learn from
CALIBRATION = Path(__file__).with_name("data") / "calibration.json"


@cache
def _calibration() -> dict:
    return json.loads(CALIBRATION.read_text(encoding="utf-8"))


def p_stranded(det: Detection) -> float:
    """Calibrated P(this flag is a real stranding), from validation seeds."""
    cal = _calibration()
    return cal["by_bucket"].get(f"{det.kind}|{round(det.confidence, 1):.1f}",
                                cal["by_kind"].get(det.kind, det.confidence))


@dataclass(frozen=True)
class DelayModel:
    """The empirical distribution of how late trucks run (hours, >= 0)."""
    delays: tuple[float, ...]   # sorted

    @classmethod
    def from_view(cls, view: OperatorView) -> DelayModel:
        obs = sorted(max(0.0, d) for d in observed_trip_delays(view).values())
        return cls(tuple(obs) if len(obs) >= MIN_SAMPLES else ())

    def p_later_than(self, x: float) -> float:
        """P(delay > x)."""
        if not self.delays:
            return 0.0
        return 1.0 - bisect.bisect_right(self.delays, x) / len(self.delays)

    def expected_excess(self, x: float) -> float:
        """E[max(0, delay - x)]."""
        if not self.delays:
            return 0.0
        return sum(d - x for d in self.delays if d > x) / len(self.delays)


def assess(o: RecoveryOption, net: Network, model: DelayModel, deadline_h: float) -> tuple[float, float]:
    """(probability the booking holds, expected lateness if it holds) for one option."""
    if o.mode != MODE_PIGGYBACK or not o.legs:
        return 1.0, o.lateness_h
    legs = [net.by_id[i] for i in o.legs if i in net.by_id]
    if len(legs) != len(o.legs):
        return 0.0, o.lateness_h
    observed = net.observed or set()
    p_ok = 1.0
    for prev, nxt in zip(legs, legs[1:], strict=False):
        if prev.trip_id == nxt.trip_id or prev.trip_id in observed:
            continue
        slack = nxt.dep - (prev.arr + MIN_CONNECTION_H)
        p_ok *= 1.0 - model.p_later_than(slack)
    last = legs[-1]
    if last.trip_id in observed:
        late = o.lateness_h
    else:  # the last truck may still run late
        late = model.expected_excess(deadline_h - o.delivered_h) if o.delivered_h is not None else o.lateness_h
    return p_ok, late


def risk_adjust(options: dict[str, list[RecoveryOption]], net: Network, model: DelayModel,
                ships: dict[str, Shipment], p_real: dict[str, float] | None = None,
                cfg: OptionsConfig = OptionsConfig(),
                broken_lateness_h: float = BROKEN_LATENESS_H) -> dict[str, list[RecoveryOption]]:
    """The same options, with `penalty_inr` replaced by the expected penalty. `p_real`:
    P(stranded) per shipment (default 1)."""
    out = {}
    for sid, opts in options.items():
        s = ships[sid]
        w = PRIORITY_WEIGHT[s.priority] * (p_real or {}).get(sid, 1.0)
        adj = []
        for o in opts:
            if o.mode == MODE_PIGGYBACK:
                p_ok, late = assess(o, net, model, s.deadline_h)
                late = p_ok * late + (1.0 - p_ok) * max(broken_lateness_h, o.lateness_h)
            else:  # trucks and local handling are certain; leaving it costs the unrecovered lateness
                late = o.lateness_h if o.mode != MODE_NONE else cfg.unrecovered_lateness_h
            adj.append(replace(o, penalty_inr=round(o.cost_inr + cfg.value_of_hour_inr * w * late, 2)))
        out[sid] = adj
    return out


def merge_options(*option_sets: dict[str, list[RecoveryOption]]) -> dict[str, list[RecoveryOption]]:
    """Union of option sets (e.g. from a buffered and an unbuffered network), one copy of
    each distinct route, with unique option ids."""
    out: dict[str, list[RecoveryOption]] = {}
    for opts_by in option_sets:
        for sid, opts in opts_by.items():
            have = out.setdefault(sid, [])
            seen = {(o.mode, o.legs, o.delivered_h) for o in have}
            for o in opts:
                key = (o.mode, o.legs, o.delivered_h)
                if key not in seen:
                    seen.add(key)
                    have.append(replace(o, option_id=f"{sid}:{o.mode}:{len(have)}"))
    return out


def plan_options(view: OperatorView, dets: list[Detection], cfg: OptionsConfig = OptionsConfig(),
                 capacity: dict[str, tuple[float, float]] | None = None,
                 tight: Network | None = None, buffered: Network | None = None,
                 model: DelayModel | None = None,
                 broken_lateness_h: float = BROKEN_LATENESS_H) -> dict[str, list[RecoveryOption]]:
    """Piggyback's options: routes with and without the transfer buffer, each priced by
    expected penalty. Pass cached networks and delay model to skip rebuilding them."""
    tight = tight or Network(view, transfer_buffer_h=0.0)
    buffered = buffered or Network(view)
    opts = merge_options(generate_options(view, dets, cfg, network=tight, capacity=capacity),
                         generate_options(view, dets, cfg, network=buffered, capacity=capacity))
    ships = {s.shipment_id: s for s in view.shipments}
    return risk_adjust(opts, tight, model or DelayModel.from_view(view), ships,
                       p_real={d.shipment_id: p_stranded(d) for d in dets}, cfg=cfg,
                       broken_lateness_h=broken_lateness_h)
