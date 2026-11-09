"""Turn results/benchmark.csv into results/REPORT.md.

Comparisons are *paired*: every policy ran on exactly the same scenarios, so
for each scenario we take the difference between Piggyback and the other policy,
then report the mean difference with a 95% confidence interval. A claim is only
made when that interval excludes zero.

    python -m benchmark.report [results/benchmark.csv]
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from .policies import BY_NAME, POLICIES

HERO = "piggyback"
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04}


def _t(df: int) -> float:
    if df > 30:
        return 1.96
    return _T95[max(k for k in _T95 if k <= df)] if df >= 1 else float("nan")


def ci(xs: list[float]) -> tuple[float, float]:
    """Mean and 95% half-width (t distribution)."""
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return m, float("nan")
    return m, _t(len(xs) - 1) * statistics.stdev(xs) / math.sqrt(len(xs))


def load(path: Path) -> list[dict]:
    num = {"seed", "flagged", "stranded", "on_time", "late", "failed", "unrecovered", "unnecessary"}
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = {k: (v if k in ("policy", "family", "variant") else int(v) if k in num else float(v))
                   for k, v in r.items()}
            row.setdefault("variant", "default")
            rows.append(row)
    return rows


def _case(r: dict) -> tuple:
    return r.get("variant", "default"), r["seed"], r["capacity_tightness"], r["anomaly_rate"]


def _metrics(r: dict) -> dict[str, float]:
    s = max(r["stranded"], 1)
    return {"on_time_pct": 100 * r["on_time"] / s,
            "failed_pct": 100 * r["failed"] / s,
            "unrecovered_pct": 100 * r["unrecovered"] / s,
            "late_h_per_parcel": r["weighted_late_h"] / s,
            "cost_lakh": r["cost_inr"] / 1e5,
            "penalty_lakh": r["realized_penalty_inr"] / 1e5,
            "plan_s": r["plan_s"]}


def _fmt(m: float, h: float, nd: int = 1) -> str:
    return f"{m:.{nd}f}" if math.isnan(h) else f"{m:.{nd}f} ± {h:.{nd}f}"


def summary_table(rows: list[dict], policies: list[str]) -> list[str]:
    by = defaultdict(list)
    for r in rows:
        by[r["policy"]].append(_metrics(r))
    out = ["| Policy | On time % | Failed % | Unrecovered % | Weighted late h / parcel | Cost (₹ lakh) "
           "| Realized penalty (₹ lakh) | Plan time (s) |",
           "|---|---|---|---|---|---|---|---|"]
    for p in policies:
        ms = by[p]
        cell = lambda k, nd=1: _fmt(*ci([m[k] for m in ms]), nd)  # noqa: E731
        name = f"**{BY_NAME[p].label}**" if p == HERO else BY_NAME[p].label
        out.append(f"| {name} | {cell('on_time_pct')} | {cell('failed_pct')} | {cell('unrecovered_pct')} | "
                   f"{cell('late_h_per_parcel')} | {cell('cost_lakh', 2)} | {cell('penalty_lakh', 2)} | "
                   f"{cell('plan_s', 2)} |")
    return out


def paired(rows: list[dict], other: str) -> dict:
    """Per scenario: hero minus other. Positive on-time delta and negative penalty delta favour the hero."""
    by_case = defaultdict(dict)
    for r in rows:
        by_case[_case(r)][r["policy"]] = r
    d_ontime, d_pen_pct, d_cost, wins = [], [], [], 0
    for pol in by_case.values():
        if HERO not in pol or other not in pol:
            continue
        h, o = _metrics(pol[HERO]), _metrics(pol[other])
        d_ontime.append(h["on_time_pct"] - o["on_time_pct"])
        base = o["penalty_lakh"]
        d_pen_pct.append(100 * (h["penalty_lakh"] - base) / base if base else 0.0)
        d_cost.append(h["cost_lakh"] - o["cost_lakh"])
        wins += h["penalty_lakh"] < o["penalty_lakh"]
    n = len(d_ontime)
    return {"n": n, "ontime_pp": ci(d_ontime), "penalty_pct": ci(d_pen_pct), "cost_lakh": ci(d_cost),
            "wins": wins}


def sensitivity_table(rows: list[dict], others: list[str], values=(50, 100, 250, 500, 1000)) -> list[str]:
    """Re-price the same outcomes at other values of a late hour. Plans were made at
    ₹250/h; this only changes how outcomes are valued, not the decisions."""
    by_case = defaultdict(dict)
    for r in rows:
        by_case[_case(r)][r["policy"]] = r
    head = "| Piggyback vs | " + " | ".join(f"₹{v}/h" for v in values) + " |"
    out = [head, "|---|" + "---|" * len(values)]
    for o in others:
        cells = []
        for v in values:
            ds = []
            for pol in by_case.values():
                if HERO in pol and o in pol:
                    h = pol[HERO]["cost_inr"] + v * pol[HERO]["weighted_late_h"]
                    x = pol[o]["cost_inr"] + v * pol[o]["weighted_late_h"]
                    ds.append(100 * (h - x) / x if x else 0.0)
            m, hw = ci(ds)
            cells.append(f"**{_fmt(m, hw)}%**" if _significant(m, hw) else f"{_fmt(m, hw)}%")
        out.append(f"| {BY_NAME[o].label} | " + " | ".join(cells) + " |")
    return out


def _significant(m: float, h: float) -> bool:
    return not math.isnan(h) and abs(m) > h


def paired_table(rows: list[dict], others: list[str]) -> list[str]:
    out = ["| Piggyback vs | Scenarios | Δ on time (pp) | Δ realized penalty (%) | Δ cost (₹ lakh) "
           "| Lower penalty in |",
           "|---|---|---|---|---|---|"]
    for o in others:
        p = paired(rows, o)
        if not p["n"]:
            continue
        mark = lambda mh: ("**" + _fmt(*mh) + "**") if _significant(*mh) else _fmt(*mh)  # noqa: E731
        out.append(f"| {BY_NAME[o].label} | {p['n']} | {mark(p['ontime_pp'])} | {mark(p['penalty_pct'])} | "
                   f"{_fmt(*p['cost_lakh'], 2)} | {p['wins']}/{p['n']} |")
    return out


def headline(rows: list[dict], other: str) -> str:
    p = paired(rows, other)
    (dm, dh), (pm, ph) = p["ontime_pp"], p["penalty_pct"]
    label = BY_NAME[other].label
    parts = []
    if _significant(dm, dh):
        parts.append(f"{'+' if dm > 0 else ''}{dm:.1f} pp stranded parcels on time "
                     f"(95% CI {dm - dh:+.1f} to {dm + dh:+.1f})")
    else:
        parts.append(f"no clear difference in on-time rate ({dm:+.1f} ± {dh:.1f} pp)")
    if _significant(pm, ph):
        parts.append(f"{'lower' if pm < 0 else 'higher'} realized penalty by {abs(pm):.1f}% "
                     f"(95% CI {pm - ph:+.1f}% to {pm + ph:+.1f}%), better in {p['wins']}/{p['n']} scenarios")
    else:
        parts.append(f"no clear difference in realized penalty ({pm:+.1f} ± {ph:.1f}%)")
    return f"- vs **{label}**: " + "; ".join(parts) + "."


def write_report(csv_path: Path, out: Path | None = None) -> Path:
    rows = load(csv_path)
    out = out or csv_path.with_name("REPORT.md")
    present = [p.name for p in POLICIES if any(r["policy"] == p.name for r in rows)]
    baselines = [p for p in present if BY_NAME[p].family == "baseline"]
    ablations = [p for p in present if BY_NAME[p].family == "ablation"]
    cases = sorted({_case(r) for r in rows})
    seeds = sorted({c[1] for c in cases})
    tights = sorted({c[2] for c in cases})
    anoms = sorted({c[3] for c in cases})

    lines = [
        "# Piggyback benchmark",
        "",
        f"{len(cases)} simulated scenarios (seeds {seeds[0]}–{seeds[-1]} × capacity tightness "
        f"{', '.join(map(str, tights))} × anomaly rate {', '.join(map(str, anoms))}), "
        f"{len(present)} recovery policies, each plan replayed against the simulator's ground truth "
        "(real parcel locations, real trip delays). Generated by `python -m benchmark.run`; "
        "every row in `benchmark.csv` is reproducible from its seed and knobs.",
        "",
        "All policies get the same detections, so differences come from recovery alone. "
        "± is a 95% confidence interval over scenarios. Bold deltas are significant (the interval excludes 0).",
        "",
        "## Headline",
        "",
        *[headline(rows, b) for b in baselines if b != "do_nothing"],
        "",
        "## All policies",
        "",
        *summary_table(rows, present),
        "",
        "## Piggyback vs baselines (paired, same scenarios)",
        "",
        *paired_table(rows, baselines),
        "",
        "## Where the gain comes from (ablations)",
        "",
        "Each ablation changes one ingredient of Piggyback (− removes it, + adds it). A positive Δ on time "
        "and a negative Δ penalty mean Piggyback's choice is the better one.",
        "",
        *paired_table(rows, ablations),
        "",
        "## Sensitivity to the value of a late hour",
        "",
        "Realized penalty depends on how much an hour late is worth. The same outcomes, re-priced "
        "(Δ realized penalty, Piggyback vs each policy):",
        "",
        *sensitivity_table(rows, [b for b in baselines if b != "do_nothing"] + ablations),
        "",
        "## By capacity tightness",
        "",
    ]
    for t in tights:
        sub = [r for r in rows if r["capacity_tightness"] == t]
        lines += [f"### Tightness {t}", "", *summary_table(sub, present), "",
                  *paired_table(sub, [p for p in baselines + ablations if p != "do_nothing"]), ""]
    lines += [
        "## Caveats",
        "",
        "- These are results on Piggyback's own simulator. Its assumptions (whole-trip delays, sampled spare "
        "capacity, no knock-on effects between recovered parcels) favour no policy in particular, but real "
        "networks will differ.",
        "- Realized penalty = money spent + ₹250 × priority weight (EXPRESS 3, STANDARD 2, ECONOMY 1) × hours "
        "late, with failed and unrecovered parcels charged 48 h late.",
        "- Plan time includes generating options and, for CP-SAT, solving (2 threads, 10 s limit).",
        "- Reactive policies (the next-service baselines and the reactive ablation) get a second chance: an "
        "AT_RISK parcel they ignored is rebooked by their own rules when it really strands, 2 h (the detection "
        "grace) after its late arrival. Without that, the proactive advantage would be overstated.",
        "- The replay checks each parcel's plan against the truth but doesn't re-simulate the network, so a "
        "recovery can't delay other parcels.",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


VARIANT_NOTES = {
    "default": "the benchmark's normal world",
    "mid_trip_delays": "delays start at a later leg, so early scans look on time",
    "silent_cancellations": "3% of trips never run, and nobody announces it",
    "scarce_peaks": "spare room is scarce on busy corridors and evening departures",
    "noisy_scans": "3× as many scans upload 1–6 h late",
    "heavy_delays": "twice as many trips run late",
}


def write_robustness(csv_path: Path, out: Path | None = None) -> Path:
    """Does the win hold under conditions Piggyback wasn't designed around?"""
    rows = load(csv_path)
    out = out or csv_path.with_name("ROBUSTNESS.md")
    variants = [v for v in VARIANT_NOTES if any(r["variant"] == v for r in rows)]
    cases = sorted({_case(r) for r in rows if r["variant"] == variants[0]})
    seeds = sorted({c[1] for c in cases})
    tights = sorted({c[2] for c in cases})
    lines = [
        "# Piggyback robustness",
        "",
        f"The head-to-head benchmark re-run under conditions Piggyback was not designed around: "
        f"{len(variants)} conditions × seeds {seeds[0]}–{seeds[-1]} × capacity tightness "
        f"{', '.join(map(str, tights))} ({len(cases)} scenarios each). Generated by "
        "`python -m benchmark.run --stress`. Deltas are Piggyback minus the other policy on the same "
        "scenarios, with 95% confidence intervals; bold = significant.",
        "",
        "| Condition | What changes | Piggyback on time | Δ on time vs next service | Δ penalty vs next service "
        "| Failed: Piggyback / next service | Δ penalty vs truck rule | Lower penalty than next service in |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for v in variants:
        sub = [r for r in rows if r["variant"] == v]
        hero = [_metrics(r) for r in sub if r["policy"] == HERO]
        ns = [_metrics(r) for r in sub if r["policy"] == "next_service"]
        p, t = paired(sub, "next_service"), paired(sub, "next_service_express_truck")
        mark = lambda mh: ("**" + _fmt(*mh) + "**") if _significant(*mh) else _fmt(*mh)  # noqa: E731
        lines.append(
            f"| {v.replace('_', ' ')} | {VARIANT_NOTES[v]} | {_fmt(*ci([m['on_time_pct'] for m in hero]))}% | "
            f"{mark(p['ontime_pp'])} pp | {mark(p['penalty_pct'])}% | "
            f"{statistics.fmean(m['failed_pct'] for m in hero):.1f}% / "
            f"{statistics.fmean(m['failed_pct'] for m in ns):.1f}% | "
            f"{mark(t['penalty_pct'])}% | {p['wins']}/{p['n']} |")
    lines += ["", "## Where the gain comes from, per condition", "",
              "Δ realized penalty of Piggyback vs each ablation (negative = that ingredient helps).", "",
              "| Condition | − optimizer (greedy) | − transfer buffer | − delay awareness | + proactive |",
              "|---|---|---|---|---|"]
    for v in variants:
        sub = [r for r in rows if r["variant"] == v]
        cells = []
        for o in ("piggyback_greedy", "piggyback_no_buffer", "piggyback_blind", "piggyback_proactive"):
            pr = paired(sub, o)
            cells.append(("**" + _fmt(*pr["penalty_pct"]) + "%**") if _significant(*pr["penalty_pct"])
                         else _fmt(*pr["penalty_pct"]) + "%")
        lines.append(f"| {v.replace('_', ' ')} | " + " | ".join(cells) + " |")
    lines += ["", "## All policies, per condition", ""]
    present = [p.name for p in POLICIES if any(r["policy"] == p.name for r in rows)]
    for v in variants:
        lines += [f"### {v.replace('_', ' ')}", "", *summary_table([r for r in rows if r["variant"] == v], present), ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(write_report(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/benchmark.csv")))
