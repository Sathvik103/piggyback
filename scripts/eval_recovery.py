"""Benchmark recovery: python scripts/eval_recovery.py [n_seeds]

For each capacity tightness, runs seeds 1..n through detection -> options ->
each solver -> replay, and prints totals per solver.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detection import OperatorView, detect  # noqa: E402
from recovery import SOLVERS, generate_options, leg_capacity, replay  # noqa: E402
from simulator import generate_scenario  # noqa: E402

TIGHTNESS = (0.3, 0.6, 0.9)
SUMMED = ("stranded", "on_time", "late", "failed", "unrecovered", "cost_inr",
          "weighted_late_h", "realized_penalty_inr", "unnecessary")


def main(n_seeds: int = 10) -> dict:
    results = {}
    for tight in TIGHTNESS:
        tot = defaultdict(lambda: defaultdict(float))
        for seed in range(1, n_seeds + 1):
            sc = generate_scenario(seed=seed, capacity_tightness=tight)
            view = OperatorView.from_scenario(sc)
            dets = detect(view)
            options = generate_options(view, dets)
            cap = leg_capacity(view)
            delays = {d.trip_id: d.delay_h for d in sc.trip_delays}
            for name, solve in SOLVERS.items():
                plan = solve(options, cap)
                k = replay(view, sc.truth, delays, dets, plan)[1]
                for key in SUMMED:
                    tot[name][key] += k[key]
                tot[name]["solve_s"] += plan.solve_s
        print(f"\ncapacity_tightness={tight}, seeds 1-{n_seeds}")
        print(f"{'solver':>7} {'on_time%':>9} {'late':>6} {'failed':>7} {'unrec':>6} "
              f"{'cost_lakh':>10} {'penalty_lakh':>13} {'solve_s':>8}")
        for name, t in tot.items():
            print(f"{name:>7} {100 * t['on_time'] / t['stranded']:>9.1f} {t['late']:>6.0f} "
                  f"{t['failed']:>7.0f} {t['unrecovered']:>6.0f} {t['cost_inr'] / 1e5:>10.2f} "
                  f"{t['realized_penalty_inr'] / 1e5:>13.1f} {t['solve_s']:>8.2f}")
        results[tight] = {k: dict(v) for k, v in tot.items()}
    return results


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
