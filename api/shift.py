"""Shift replays for the console: a whole shift, worked hour by hour by two policies.

A replay is a pure function of (seed, capacity tightness, shift length), so it is
computed on first request and cached in memory; nothing is written to Postgres. It
reads the simulator's ground truth (to know when bookings really break), so it lives
under /lab/, like the other truth-reading features.
"""

from __future__ import annotations

import csv
import threading
from collections import OrderedDict, defaultdict
from dataclasses import asdict
from functools import cache
from pathlib import Path

from benchmark.policies import BY_NAME
from benchmark.shift import RETRY_GRACE_H, _diff, run_shift
from detection import OperatorView
from simulator import generate_scenario

REPLAY_POLICIES = ("piggyback", "next_service")
CACHE_SIZE = 8
SHIFT_RESULTS = Path(__file__).resolve().parents[1] / "results" / "shift.csv"


@cache
def published() -> dict | None:
    """The committed shift benchmark (results/shift.csv), Piggyback vs next service, so a
    single replayed day can be read against all of them."""
    if not SHIFT_RESULTS.exists():
        return None
    by = defaultdict(dict)
    with open(SHIFT_RESULTS, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            by[(r["seed"], r["capacity_tightness"])][r["policy"]] = float(r["realized_penalty_inr"])
    cases = [c for c in by.values() if "piggyback" in c and c.get("next_service")]
    if not cases:
        return None
    d = _diff([100 * (c["piggyback"] / c["next_service"] - 1) for c in cases])
    return {"shifts": len(cases), "penalty_pct": round(d["mean"], 1), "half_width": round(d["half_width"], 1),
            "significant": d["significant"], "wins": sum(c["piggyback"] < c["next_service"] for c in cases)}


class ShiftReplays:
    def __init__(self):
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._busy: dict[tuple, threading.Lock] = {}

    def get(self, seed: int, tightness: float, shift_h: float, n_shipments: int) -> dict:
        key = (seed, round(tightness, 2), round(shift_h, 1), n_shipments)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            busy = self._busy.setdefault(key, threading.Lock())
        with busy:  # one computation per key, however many people ask at once
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
            result = _replay(*key)
            with self._lock:
                self._cache[key] = result
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
                self._busy.pop(key, None)
            return result


def _replay(seed: int, tightness: float, shift_h: float, n_shipments: int) -> dict:
    sc = generate_scenario(seed=seed, capacity_tightness=tightness, shift_h=shift_h, n_shipments=n_shipments)
    view = OperatorView.at(sc, sc.config.end_h)
    hubs = {h.hub_id: h for h in view.hubs}
    feats = [{"type": "Feature", "id": h.hub_id, "geometry": {"type": "Point", "coordinates": [h.lon, h.lat]},
              "properties": {"kind": "hub", **asdict(h)}} for h in view.hubs]
    for l in view.lanes:
        a, b = hubs[l.from_hub], hubs[l.to_hub]
        feats.append({"type": "Feature", "id": l.lane_id,
                      "geometry": {"type": "LineString", "coordinates": [[a.lon, a.lat], [b.lon, b.lat]]},
                      "properties": {"kind": "lane", **asdict(l)}})
    keep = ("t", "shipment_id", "event", "mode", "from_hub", "to_hub", "route", "attempt", "cost_inr", "hub",
            "reason", "planned_delivery_h")
    runs = {}
    for p in REPLAY_POLICIES:
        r = run_shift(sc, p)
        runs[p] = {
            "label": BY_NAME[p].label,
            "kpis": r.kpis,
            "timeline": r.timeline,
            "events": [{k: e[k] for k in keep if k in e} for e in r.events],
        }
    stranding_times = sorted(round(t.stranded_at_h, 2) for t in sc.truth)
    return {
        "config": {"seed": seed, "capacity_tightness": tightness, "shift_h": shift_h, "n_shipments": n_shipments,
                   "start_h": sc.config.now_h, "end_h": sc.config.end_h, "start_at": sc.config.start_at,
                   "retry_grace_h": RETRY_GRACE_H},
        "network": {"type": "FeatureCollection", "features": feats},
        "disruptions": [asdict(d) for d in sc.disruptions],
        "strandings": stranding_times,
        "policies": runs,
        "published": published(),
    }
