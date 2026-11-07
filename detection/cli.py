"""python -m detection.cli data/scenario_42 [--grace 2] [--sweep]"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, fields, replace
from pathlib import Path

from .detector import Detection, DetectorConfig, detect
from .scoring import load_truth, score
from .view import OperatorView

SWEEP_GRACE_H = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0)


def write_detections(dets: list[Detection], path: Path) -> None:
    cols = [f.name for f in fields(Detection)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for d in dets:
            w.writerow(["" if v is None else v for v in (getattr(d, c) for c in cols)])


def main(argv: list[str] | None = None) -> None:
    d = DetectorConfig()
    p = argparse.ArgumentParser(description="Detect stranded shipments in a scenario directory.")
    p.add_argument("scenario", type=Path, help="directory written by simulator.cli")
    p.add_argument("--grace", type=float, default=d.grace_h, help="hours overdue before flagging")
    p.add_argument("--cluster-min", type=int, default=d.cluster_min)
    p.add_argument("--no-score", action="store_true", help="don't read sim_truth.csv")
    p.add_argument("--sweep", action="store_true", help="score a range of grace values")
    a = p.parse_args(argv)

    view = OperatorView.from_csv_dir(a.scenario)
    cfg = replace(d, grace_h=a.grace, cluster_min=a.cluster_min)

    if a.sweep:
        truth = load_truth(a.scenario)
        print(f"{'grace_h':>7} {'flagged':>7} {'prec':>6} {'recall':>6} {'f1':>6} {'type':>6} {'hub':>6}")
        for g in SWEEP_GRACE_H:
            s = score(detect(view, replace(cfg, grace_h=g)), truth)
            print(f"{g:>7} {s['flagged']:>7} {s['precision']:>6} {s['recall']:>6} {s['f1']:>6} "
                  f"{s['type_accuracy']:>6} {s['hub_accuracy']:>6}")
        return

    dets = detect(view, cfg)
    out = a.scenario / "detections.csv"
    write_detections(dets, out)
    print(json.dumps({"config": asdict(cfg),
                      "by_kind": dict(sorted(Counter(x.kind for x in dets).items())),
                      "clusters": dict(sorted(Counter(x.cluster_id for x in dets if x.cluster_id).items()))},
                     indent=2))
    if not a.no_score:
        print(json.dumps({"score": score(dets, load_truth(a.scenario))}, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
