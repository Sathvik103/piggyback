"""Score the detector over many seeds: python scripts/eval_detection.py [n_seeds]"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detection import DetectorConfig, OperatorView, detect, score  # noqa: E402
from simulator import generate_scenario  # noqa: E402
from simulator.models import ANOMALY_TYPES  # noqa: E402


def main(n_seeds: int = 10, cfg: DetectorConfig = DetectorConfig()) -> dict:
    tot = Counter()
    caught, total, fp_kind = Counter(), Counter(), Counter()
    for seed in range(1, n_seeds + 1):
        sc = generate_scenario(seed=seed)
        dets = detect(OperatorView.from_scenario(sc), cfg)
        s = score(dets, sc.truth)
        tot.update({k: s[k] for k in ("tp", "fp", "fn", "at_risk")})
        fp_kind.update(s["false_positives_by_kind"])
        for k, v in s["recall_by_type"].items():
            c, t = map(int, v.split("/"))
            caught[k] += c
            total[k] += t
        print(f"seed {seed:>2}: precision {s['precision']:.3f} recall {s['recall']:.3f} "
              f"type {s['type_accuracy']:.3f} hub {s['hub_accuracy']:.3f}")
    p = tot["tp"] / (tot["tp"] + tot["fp"])
    r = tot["tp"] / (tot["tp"] + tot["fn"])
    out = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3),
           "at_risk": tot["at_risk"],
           "recall_by_type": {k: f"{caught[k]}/{total[k]}" for k in ANOMALY_TYPES if total[k]},
           "false_positives_by_kind": dict(fp_kind)}
    print(out)
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
