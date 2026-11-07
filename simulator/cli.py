"""python -m simulator.cli --seed 42 --out data/scenario_42"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .export import write_all
from .generator import generate_scenario
from .models import ScenarioConfig


def main(argv: list[str] | None = None) -> None:
    d = ScenarioConfig()
    p = argparse.ArgumentParser(description="Generate a Piggyback scenario.")
    p.add_argument("--seed", type=int, default=d.seed)
    p.add_argument("--out", type=Path, required=True, help="output directory")
    p.add_argument("--n-shipments", type=int, default=d.n_shipments)
    p.add_argument("--capacity-tightness", type=float, default=d.capacity_tightness)
    p.add_argument("--anomaly-rate", type=float, default=d.anomaly_rate)
    p.add_argument("--n-incidents", type=int, default=d.n_incidents)
    p.add_argument("--n-disruptions", type=int, default=d.n_disruptions)
    p.add_argument("--trip-delay-rate", type=float, default=d.trip_delay_rate)
    p.add_argument("--scan-lag-rate", type=float, default=d.scan_lag_rate)
    a = p.parse_args(argv)

    sc = generate_scenario(
        seed=a.seed,
        n_shipments=a.n_shipments,
        capacity_tightness=a.capacity_tightness,
        anomaly_rate=a.anomaly_rate,
        n_incidents=a.n_incidents,
        n_disruptions=a.n_disruptions,
        trip_delay_rate=a.trip_delay_rate,
        scan_lag_rate=a.scan_lag_rate,
    )
    print(json.dumps(write_all(sc, a.out), indent=2))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
