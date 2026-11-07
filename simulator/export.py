"""Write a scenario to disk: one CSV per table, a full JSON dump and a summary."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .models import (
    Corridor,
    Disruption,
    Hub,
    Lane,
    Scenario,
    ScanEvent,
    Shipment,
    Trip,
    TripLeg,
    TruthRecord,
    column_names,
)

# CSV file (= database table) -> (row class, Scenario attribute)
TABLES: dict[str, tuple[type, str]] = {
    "hubs": (Hub, "hubs"),
    "lanes": (Lane, "lanes"),
    "corridors": (Corridor, "corridors"),
    "trips": (Trip, "trips"),
    "trip_legs": (TripLeg, "legs"),
    "shipments": (Shipment, "shipments"),
    "scan_events": (ScanEvent, "scans"),
    "disruptions": (Disruption, "disruptions"),
    "sim_truth": (TruthRecord, "truth"),
}


def write_csvs(sc: Scenario, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for table, (cls, attr) in TABLES.items():
        cols = column_names(cls)
        with open(out / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            for row in getattr(sc, attr):
                # None -> empty field, which COPY ... CSV reads as NULL.
                w.writerow(["" if v is None else v for v in (getattr(row, c) for c in cols)])
    cfg = asdict(sc.config)
    with open(out / "scenario_meta.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["key", "value"])
        for k, v in cfg.items():
            w.writerow([k, v])


def to_dict(sc: Scenario) -> dict:
    d = {"config": asdict(sc.config)}
    for table, (_, attr) in TABLES.items():
        d[table] = [asdict(r) for r in getattr(sc, attr)]
    return d


def write_json(sc: Scenario, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(sc), f, separators=(",", ":"))


def summary(sc: Scenario) -> dict:
    last_scan: dict[str, str] = {}
    for e in sc.scans:
        last_scan[e.shipment_id] = e.event_type
    states = Counter(last_scan.get(s.shipment_id, "NOT_SCANNED") for s in sc.shipments)
    return {
        "seed": sc.config.seed,
        "now_h": sc.now,
        "hubs": len(sc.hubs),
        "lanes": len(sc.lanes),
        "corridors": len(sc.corridors),
        "trips": len(sc.trips),
        "trip_legs": len(sc.legs),
        "shipments": len(sc.shipments),
        "scan_events": len(sc.scans),
        "last_scan_state": dict(sorted(states.items())),
        "priorities": dict(sorted(Counter(s.priority for s in sc.shipments).items())),
        "stranded": len(sc.truth),
        "stranded_by_type": dict(sorted(Counter(t.anomaly_type for t in sc.truth).items())),
        "incidents": dict(sorted(Counter(t.incident_id for t in sc.truth if t.incident_id).items())),
        "disruptions": dict(sorted(Counter(d.kind for d in sc.disruptions).items())),
        "mean_spare_kg_fraction": round(
            sum(l.spare_kg for l in sc.legs)
            / sum(c.capacity_kg for c in _leg_corridors(sc)), 3),
    }


def _leg_corridors(sc: Scenario):
    corridor = {c.corridor_id: c for c in sc.corridors}
    trip_corridor = {t.trip_id: corridor[t.corridor_id] for t in sc.trips}
    return [trip_corridor[l.trip_id] for l in sc.legs]


def write_all(sc: Scenario, out: Path) -> dict:
    out = Path(out)
    write_csvs(sc, out)
    write_json(sc, out / "scenario.json")
    s = summary(sc)
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    return s
