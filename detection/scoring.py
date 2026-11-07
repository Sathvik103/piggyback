"""Score detections against ground truth. The only detection module that reads truth."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from simulator.models import ANOMALY_TYPES, TruthRecord

from .detector import NOT_STRANDED_KINDS, Detection
from .view import read_table


def load_truth(path: Path) -> list[TruthRecord]:
    return read_table(Path(path), "sim_truth")


def score(detections: list[Detection], truth: list[TruthRecord]) -> dict:
    """AT_RISK detections are predictions about the future, not strandings, so they
    are counted separately and never as false positives."""
    at_risk = sum(d.kind in NOT_STRANDED_KINDS for d in detections)
    flagged = {d.shipment_id: d for d in detections if d.kind not in NOT_STRANDED_KINDS}
    actual = {t.shipment_id: t for t in truth}
    hits = [(flagged[sid], actual[sid]) for sid in flagged.keys() & actual.keys()]
    tp, fp, fn = len(hits), len(flagged) - len(hits), len(actual) - len(hits)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    by_type = Counter(t.anomaly_type for t in truth)
    caught = Counter(t.anomaly_type for _, t in hits)
    return {
        "flagged": len(flagged),
        "at_risk": at_risk,
        "stranded": len(actual),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "type_accuracy": round(sum(d.kind == t.anomaly_type for d, t in hits) / tp, 3) if tp else 0.0,
        "hub_accuracy": round(sum(d.suspected_hub == t.true_hub_id for d, t in hits) / tp, 3) if tp else 0.0,
        "recall_by_type": {k: f"{caught[k]}/{by_type[k]}" for k in ANOMALY_TYPES if by_type[k]},
        "false_positives_by_kind": dict(sorted(Counter(
            d.kind for sid, d in flagged.items() if sid not in actual).items())),
    }
