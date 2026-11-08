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
            rows.append({k: (v if k in ("policy", "family") else int(v) if k in num else float(v))
                         for k, v in r.items()})
    return rows


def _case(r: dict) -> tuple:
    return r["seed"], r["capacity_tightness"], r["anomaly_rate"]


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
    for case, pol in by_case.items():
        if HERO not in pol or other not in pol:
            continue
        h, o = _metrics(pol[HERO]), _metrics(pol[other])
        d_ontime.append(h["on_time_pct"] - o["on_time_pct"])
        d_pen_pct.append(100 * (h["penalty_lakh"] - o["penalty_lakh"]) / o["penalty_lakh"] if o["penalty_lakh"] else 0.0)
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
    out = ["| Piggyback vs | Scenarios | Δ on time (pp) | Δ realized penalty (%) | Δ cost (₹ lakh) | Lower penalty in |",
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
        parts.append(f"{'+' if dm > 0 else ''}{dm:.1f} pp stranded parcels on time (95% CI {dm - dh:+.1f} to {dm + dh:+.1f})")
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
    seeds = sorted({c[0] for c in cases})
    tights = sorted({c[1] for c in cases})
    anoms = sorted({c[2] for c in cases})

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


if __name__ == "__main__":
    print(write_report(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/benchmark.csv")))
