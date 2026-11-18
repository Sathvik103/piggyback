"""Calibrate detection confidence: how often is each kind of flag a real stranding?

    python scripts/calibrate_detection.py          # validation seeds 101-110 -> recovery/data/calibration.json

The detector's confidence ranks flags but isn't a probability (a LOST_SCAN at 0.8 is
real about a third of the time over a shift). This measures, on validation seeds only
(never the reported benchmark seeds), the share of first flags per (kind, confidence)
that were real strandings, with add-one smoothing. Risk-aware planning uses it as
P(stranded) for each detection (recovery/risk.py).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detection import OperatorView, detect  # noqa: E402
from detection.detector import KIND_AT_RISK  # noqa: E402
from simulator import generate_scenario  # noqa: E402

OUT = ROOT / "recovery" / "data" / "calibration.json"
SEEDS = range(101, 111)
MIN_COUNT = 10  # buckets with fewer flags fall back to the kind's overall rate


def main() -> None:
    by_bucket: dict[tuple[str, float], list[int]] = defaultdict(lambda: [0, 0])  # [real, all]
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for seed in SEEDS:
        sc = generate_scenario(seed=seed, shift_h=24.0)
        truth = {t.shipment_id for t in sc.truth}
        seen: set[str] = set()
        for t in range(int(sc.config.now_h), int(sc.config.end_h) + 1):
            for d in detect(OperatorView.at(sc, t)):
                if d.kind == KIND_AT_RISK or d.shipment_id in seen:
                    continue
                seen.add(d.shipment_id)  # the first flag: when a policy would act
                real = int(d.shipment_id in truth)
                for acc in (by_bucket[(d.kind, round(d.confidence, 1))], by_kind[d.kind]):
                    acc[0] += real
                    acc[1] += 1
    table = {
        "source": f"first flags per parcel over 24 h shifts, validation seeds {SEEDS.start}-{SEEDS.stop - 1}",
        "by_kind": {k: round((r + 1) / (n + 2), 3) for k, (r, n) in sorted(by_kind.items())},
        "by_bucket": {f"{k}|{c:.1f}": round((r + 1) / (n + 2), 3)
                      for (k, c), (r, n) in sorted(by_bucket.items()) if n >= MIN_COUNT},
        "counts": {f"{k}|{c:.1f}": n for (k, c), (_, n) in sorted(by_bucket.items())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for k, v in table["by_bucket"].items():
        print(f"  {k:24s} P(real) {v:.2f}  (n={table['counts'][k]})")


if __name__ == "__main__":
    main()
