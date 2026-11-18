"""Rolling shift: every policy works a whole shift, hour by hour, like an operator.

    python -m benchmark.shift                      # seeds 1-10 x tightness 0.6/0.9, 24 h shifts
    python -m benchmark.shift --seeds 1-3 --quick

The snapshot benchmark (`benchmark.run`) judges one frozen moment. Real operations
are continuous, so here the world keeps running (`ScenarioConfig.shift_h`):
parcels strand all shift, trucks run late, disruptions are announced as they come.
Every hour each policy:

1. sees only what an operator could at that hour (`OperatorView.at`): scans recorded
   by then, shipments created by then, disruptions announced by then, and truck space
   minus what it has already booked;
2. runs detection and plans every stranded parcel it hasn't booked yet;
3. books its choices. A booking holds its space for the rest of the shift.

Each booking's real fate comes from the true timetable. If it breaks (the parcel isn't
where we sent the truck, a connection is missed because a trip ran later than anyone
had seen, a hub is closed), the operator notices `RETRY_GRACE_H` later and the parcel
is open again, from where it really is, for up to `MAX_ATTEMPTS` bookings in all.

At the end every parcel that stranded in time to be seen (`stranded_at_h` at least
`RETRY_GRACE_H` before the shift ends) is scored the way the snapshot replay scores:
money spent on every booking plus INR 250 x priority weight x hours late, with 48 h for
parcels never recovered.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

from detection import OperatorView, detect
from detection.detector import KIND_MISSED_CONNECTION, Detection
from recovery import Network, OptionsConfig, RecoveryOption
from recovery.options import MODE_DEDICATED, MODE_NONE, MODE_PIGGYBACK, PRIORITY_WEIGHT
from simulator import generate_scenario
from simulator.generator import DELIVERY_H
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import Scenario

from .policies import BY_NAME
from .report import ci
from .run import RESULTS_DIR, _parse_seeds

SHIFT_H = 24.0
STEP_H = 1.0
RETRY_GRACE_H = 2.0      # how long before a broken booking is noticed
MAX_ATTEMPTS = 3         # bookings per parcel in one shift
SHIFT_POLICIES = ("do_nothing", "next_service", "next_service_express_truck", "piggyback", "piggyback_fixed_buffer")
HEROES = ("piggyback",)


@dataclass
class Booking:
    option: RecoveryOption
    booked_h: float
    attempt: int
    delivered_h: float | None          # None = it broke
    broke_h: float | None = None
    broke_hub: str | None = None
    reason: str = ""


@dataclass
class ShiftResult:
    policy: str
    kpis: dict
    timeline: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    outcomes: dict[str, dict] = field(default_factory=dict)


# ------------------------------------------------------------------ what really happens to a booking

def _fate(o: RecoveryOption, at_hub: str | None, real: Network) -> tuple[float | None, float | None, str | None, str]:
    """(delivered, broke_h, broke_hub, reason). `at_hub` is where the parcel really is (None:
    it was never stranded, so the booking just moves a healthy parcel)."""
    if at_hub is not None and o.from_hub != at_hub:
        return None, o.ready_h, at_hub, f"parcel was at {at_hub}, not {o.from_hub}"
    if o.mode == MODE_DEDICATED:
        return round(o.delivered_h, 2), None, None, ""
    if o.mode != MODE_PIGGYBACK:  # LOCAL
        return round(o.ready_h + DELIVERY_H, 2), None, None, ""
    t, prev = o.ready_h, None
    for lid in o.legs:
        e = real.by_id.get(lid)
        if e is None:
            return None, o.ready_h, prev.to_hub if prev else o.from_hub, f"{lid} never ran"
        if prev is None or prev.trip_id != e.trip_id:
            here = prev.to_hub if prev else o.from_hub
            if not e.can_board:
                return None, max(t, e.dep), here, f"{e.from_hub} closed at departure"
            if e.dep < t - 1e-9:
                return None, e.dep, here, f"missed {lid}: left {e.dep:.1f} h, parcel ready {t:.1f} h"
        prev, t = e, e.arr + MIN_CONNECTION_H
    if not prev.can_alight:
        return None, prev.arr, prev.from_hub, f"{prev.to_hub} closed at arrival"
    return round(prev.arr + DELIVERY_H, 2), None, None, ""


def _with_holds(view: OperatorView, bookings: dict[str, Booking]) -> OperatorView:
    held: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for b in bookings.values():
        for leg in b.option.capacity_legs:
            held[leg][0] += b.option.weight_kg
            held[leg][1] += b.option.volume_m3
    if not held:
        return view
    legs = [replace(l, spare_kg=l.spare_kg - held[l.leg_id][0], spare_m3=l.spare_m3 - held[l.leg_id][1])
            if l.leg_id in held else l for l in view.legs]
    return replace(view, legs=legs)


# ------------------------------------------------------------------ one policy, one shift

def run_shift(sc: Scenario, policy: str, step_h: float = STEP_H, cfg: OptionsConfig = OptionsConfig()) -> ShiftResult:
    pol = BY_NAME[policy]
    start, end = sc.config.now_h, sc.config.end_h
    delays = {d.trip_id: d for d in sc.trip_delays}
    full = OperatorView.at(sc, end + 48.0)  # every disruption, for the true timetable
    real = Network(full, delays)
    ships = {s.shipment_id: s for s in sc.shipments}
    truth = {t.shipment_id: t for t in sc.truth}
    where = {sid: t.true_hub_id for sid, t in truth.items()}   # where each stranded parcel really is

    history: dict[str, list[Booking]] = defaultdict(list)
    active: dict[str, Booking] = {}          # bookings holding space (not known to have broken)
    last_det: dict[str, Detection] = {}
    res = ShiftResult(policy, {})

    def reopen_broken(t: float) -> list[Detection]:
        """Bookings that broke and have been noticed by `t`: free their space, reopen the parcel."""
        out = []
        for sid, b in list(active.items()):
            if b.delivered_h is None and b.broke_h is not None and b.broke_h + RETRY_GRACE_H <= t:
                del active[sid]
                if b.broke_hub is not None and sid in where:
                    where[sid] = b.broke_hub
                res.events.append({"t": t, "shipment_id": sid, "event": "broke", "hub": b.broke_hub,
                                   "reason": b.reason})
                if len(history[sid]) < MAX_ATTEMPTS and sid in last_det:
                    d = last_det[sid]
                    out.append(replace(d, kind=KIND_MISSED_CONNECTION, suspected_hub=b.broke_hub or d.suspected_hub,
                                       last_scan_h=b.broke_h, expected_h=b.broke_h,
                                       evidence=f"booked recovery broke: {b.reason}"))
        return out

    def book(t: float, view: OperatorView, open_dets: list[Detection]) -> int:
        plan = pol.run(view, open_dets)
        booked = 0
        for sid, o in plan.chosen.items():
            if o.mode == MODE_NONE:
                continue
            delivered, broke_h, broke_hub, reason = _fate(o, where.get(sid), real)
            b = Booking(o, t, len(history[sid]) + 1, delivered, broke_h, broke_hub, reason)
            history[sid].append(b)
            active[sid] = b
            booked += 1
            res.events.append({"t": t, "shipment_id": sid, "event": "booked" if b.attempt == 1 else "rebooked",
                               "mode": o.mode, "from_hub": o.from_hub, "to_hub": ships[sid].dest_hub,
                               "route": list(_route(o)),
                               "attempt": b.attempt, "cost_inr": round(o.cost_inr),
                               "planned_delivery_h": o.delivered_h, "planned_late_h": round(o.lateness_h, 2),
                               "capacity_legs": list(o.capacity_legs), "weight_kg": o.weight_kg,
                               "delivered_h": delivered})
        return booked

    def waiting_to_notice(sid: str, t: float) -> bool:
        bs = history.get(sid)
        return bool(bs) and bs[-1].delivered_h is None and bs[-1].broke_h + RETRY_GRACE_H > t

    # The shift: every hour, everything an operator would see.
    steps = int(round((end - start) / step_h))
    for i in range(steps + 1):
        t = round(start + i * step_h, 2)
        reopened = reopen_broken(t)
        view = _with_holds(OperatorView.at(sc, t), active)
        dets = detect(view)
        reopened_ids = {d.shipment_id for d in reopened}
        open_dets = reopened + [d for d in dets if d.shipment_id not in active and d.shipment_id not in reopened_ids
                                and len(history.get(d.shipment_id, ())) < MAX_ATTEMPTS
                                and not waiting_to_notice(d.shipment_id, t)]
        for d in dets:
            last_det.setdefault(d.shipment_id, d)
        booked = book(t, view, open_dets)
        res.timeline.append({"t": t, "flagged": len(dets), "open": len(open_dets), "booked": booked,
                             "holding": len(active),
                             "spent_inr": round(sum(b.option.cost_inr for bs in history.values() for b in bs))})

    # Aftercare: no new work, but a booking that breaks after the shift is still noticed and
    # replanned by the same policy (the next shift would), so nothing is scored as lost just
    # because it broke after the clock stopped.
    limit = sc.config.horizon_h - 24.0
    while True:
        due = [b.broke_h + RETRY_GRACE_H for b in active.values() if b.delivered_h is None and b.broke_h is not None]
        if not due or min(due) > limit:
            break
        t = max(min(due), end)  # not rounded: rounding down would land before the break is due, forever
        reopened = reopen_broken(t)
        if reopened:
            book(t, _with_holds(OperatorView.at(sc, t), active), reopened)

    res.kpis, res.outcomes = _score(sc, history, truth, ships, end, cfg)
    return res


def _route(o: RecoveryOption) -> tuple[str, ...]:
    return tuple(o.note.split(" via ", 1)[1].split(">")) if " via " in o.note else (o.from_hub,)


def _score(sc, history, truth, ships, end, cfg: OptionsConfig) -> tuple[dict, dict]:
    eligible = {sid for sid, tr in truth.items() if tr.stranded_at_h + RETRY_GRACE_H <= end}
    outcomes, counts = {}, Counter()
    weighted_late = 0.0
    for sid in sorted(eligible):
        bs = history.get(sid, [])
        ok = next((b for b in bs if b.delivered_h is not None), None)
        if ok is not None:
            late = max(0.0, ok.delivered_h - ships[sid].deadline_h)
            status = "ON_TIME" if late <= 1e-9 else "LATE"
        else:
            late = cfg.unrecovered_lateness_h
            status = "FAILED" if bs else "UNRECOVERED"
        counts[status] += 1
        weighted_late += PRIORITY_WEIGHT[ships[sid].priority] * late
        last = bs[-1] if bs else None
        outcomes[sid] = {"status": status, "attempts": len(bs), "lateness_h": round(late, 2),
                         "mode": (ok or last).option.mode if (ok or last) else None,
                         "broke_h": last.broke_h if last and ok is None else None,
                         "reason": last.reason if last and ok is None else ""}
    cost = sum(b.option.cost_inr for sid, bs in history.items() for b in bs if sid in eligible or sid not in truth)
    wasted = [sid for sid, bs in history.items() if bs and sid not in truth]
    n = len(eligible)
    kpis = {
        "stranded": n, "on_time": counts["ON_TIME"], "late": counts["LATE"], "failed": counts["FAILED"],
        "unrecovered": counts["UNRECOVERED"], "on_time_rate": round(counts["ON_TIME"] / n, 4) if n else 1.0,
        "bookings": sum(len(bs) for bs in history.values()),
        "rebookings": sum(max(0, len(bs) - 1) for bs in history.values()),
        "broken_bookings": sum(b.delivered_h is None for bs in history.values() for b in bs),
        "unnecessary": len(wasted), "weighted_late_h": round(weighted_late, 1), "cost_inr": round(cost),
        "realized_penalty_inr": round(cost + cfg.value_of_hour_inr * weighted_late),
    }
    return kpis, outcomes


# ------------------------------------------------------------------ the benchmark

FIELDS = ("seed", "capacity_tightness", "policy", "stranded", "on_time", "late", "failed", "unrecovered",
          "on_time_rate", "bookings", "rebookings", "broken_bookings", "unnecessary", "weighted_late_h",
          "cost_inr", "realized_penalty_inr", "run_s")


def run_case(seed: int, tightness: float, policies: tuple[str, ...], shift_h: float = SHIFT_H) -> list[dict]:
    sc = generate_scenario(seed=seed, capacity_tightness=tightness, shift_h=shift_h)
    rows = []
    for p in policies:
        t0 = time.perf_counter()
        r = run_shift(sc, p)
        rows.append({"seed": seed, "capacity_tightness": tightness, "policy": p, **r.kpis,
                     "run_s": round(time.perf_counter() - t0, 1)})
    return rows


def _diff(xs: list[float]) -> dict:
    mean, hw = ci(xs)
    return {"mean": mean, "half_width": hw, "significant": abs(mean) > hw}


def write_report(rows: list[dict], out: Path, shift_h: float) -> Path:
    by = defaultdict(dict)
    for r in rows:
        by[(r["seed"], r["capacity_tightness"])][r["policy"]] = r
    cases = [c for c in by.values() if all(p in c for p in SHIFT_POLICIES)]
    label = {p: BY_NAME[p].label for p in SHIFT_POLICIES}
    m = lambda p, k: statistics.fmean(float(c[p][k]) for c in cases)  # noqa: E731
    lines = [
        "# Piggyback over a whole shift",
        "",
        f"Each policy works a {shift_h:.0f}-hour shift hour by hour: it sees only what was recorded by each hour, "
        "books recoveries that hold truck space for the rest of the shift, and gets parcels back when a booking "
        f"breaks (noticed {RETRY_GRACE_H:.0f} h later, up to {MAX_ATTEMPTS} bookings per parcel). "
        f"{len(cases)} paired shifts (seeds x capacity tightness). Generated by `python -m benchmark.shift`.",
        "",
        "| Policy | Stranded on time | Failed | Unrecovered | Bookings | Broke mid-shift | Money (INR lakh) "
        "| Realized penalty (INR lakh) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in SHIFT_POLICIES:
        lines.append(
            f"| {'**' + label[p] + '**' if p in HEROES else label[p]} | {100 * m(p, 'on_time_rate'):.1f}% "
            f"| {m(p, 'failed'):.1f} | {m(p, 'unrecovered'):.1f} | {m(p, 'bookings'):.0f} "
            f"| {m(p, 'broken_bookings'):.1f} | {m(p, 'cost_inr') / 1e5:.2f} "
            f"| {m(p, 'realized_penalty_inr') / 1e5:.2f} |")
    lines += ["", "**Piggyback minus each other policy** on the same shifts (95% CI; bold = significant):",
              "", "| Piggyback | vs | Δ on time (pp) | Δ realized penalty | Broken bookings, theirs − ours "
              "| Lower penalty in |", "|---|---|---|---|---|---|"]

    def fmt(s: dict, unit: str) -> str:
        v = f"{s['mean']:+.1f}{unit}"
        return (f"**{v}**" if s["significant"] else v) + f" (±{s['half_width']:.1f})"

    for hero in HEROES:
        for p in [x for x in SHIFT_POLICIES if x != hero]:
            d_on = [100 * (c[hero]["on_time_rate"] - c[p]["on_time_rate"]) for c in cases]
            d_pen = [100 * (c[hero]["realized_penalty_inr"] / c[p]["realized_penalty_inr"] - 1)
                     for c in cases if c[p]["realized_penalty_inr"]]
            wins = sum(c[hero]["realized_penalty_inr"] < c[p]["realized_penalty_inr"] for c in cases)
            broke = m(p, "broken_bookings") - m(hero, "broken_bookings")
            lines.append(f"| {label[hero]} | {label[p]} | {fmt(_diff(d_on), '')} | {fmt(_diff(d_pen), '%')} "
                         f"| {broke:+.1f} per shift | {wins}/{len(cases)} |")
    lines += ["", "Penalty = money spent on every booking (including ones that broke) + INR 250 x priority weight "
              "x hours late, with 48 h for parcels never recovered. Only parcels that stranded at least "
              f"{RETRY_GRACE_H:.0f} h before the shift ended count, so every policy had a chance to act.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=_parse_seeds, default=list(range(1, 11)))
    ap.add_argument("--tightness", type=float, nargs="+", default=[0.6, 0.9])
    ap.add_argument("--shift-h", type=float, default=SHIFT_H)
    ap.add_argument("--quick", action="store_true", help="seeds 1-2, tightness 0.6, 12 h shifts")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "shift.csv")
    a = ap.parse_args(argv)
    if a.quick:
        a.seeds, a.tightness, a.shift_h = [1, 2], [0.6], 12.0
    cases = [(s, t) for s in a.seeds for t in a.tightness]
    rows: list[dict] = []
    with ProcessPoolExecutor(a.workers) as ex:
        seeds, tights = [c[0] for c in cases], [c[1] for c in cases]
        for out in ex.map(run_case, seeds, tights, [SHIFT_POLICIES] * len(cases), [a.shift_h] * len(cases)):
            rows += out
            r = {x["policy"]: x for x in out}
            print(f"seed {out[0]['seed']} t{out[0]['capacity_tightness']}: "
                  + ", ".join(f"{p} {100 * r[p]['on_time_rate']:.0f}%/{r[p]['realized_penalty_inr'] / 1e5:.1f}L"
                              for p in SHIFT_POLICIES), flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    report = write_report(rows, a.out.with_name("SHIFT.md"), a.shift_h)
    print(f"wrote {a.out} and {report}")
    return report


if __name__ == "__main__":
    main()

