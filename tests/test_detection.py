from dataclasses import fields

import pytest

from detection import Detection, DetectorConfig, OperatorView, detect, score
from detection import cli as detection_cli
from detection.detector import KIND_AT_RISK, KIND_LOST_SCAN
from simulator import generate_scenario
from simulator.export import TRUTH_TABLES, write_all
from simulator.models import TruthRecord


@pytest.fixture(scope="module")
def scenario_dir(scenario, tmp_path_factory):
    d = tmp_path_factory.mktemp("scenario")
    write_all(scenario, d)
    return d


def test_operator_view_has_no_truth():
    names = {f.name for f in fields(OperatorView)}
    assert not names & {"truth", "trip_delays", "config"}


def test_csv_view_matches_in_memory_view_without_reading_truth(scenario, scenario_dir):
    for t in TRUTH_TABLES:
        (scenario_dir / f"{t}.csv").rename(scenario_dir / f"{t}.hidden")
    try:
        assert OperatorView.from_csv_dir(scenario_dir) == OperatorView.from_scenario(scenario)
    finally:
        for t in TRUTH_TABLES:
            (scenario_dir / f"{t}.hidden").rename(scenario_dir / f"{t}.csv")


def test_quiet_network_raises_no_flags():
    sc = generate_scenario(seed=11, n_shipments=800, anomaly_rate=0.0, n_incidents=0,
                           trip_delay_rate=0.0, scan_lag_rate=0.0)
    assert detect(OperatorView.from_scenario(sc)) == []


def test_without_noise_every_flag_is_real():
    sc = generate_scenario(seed=11, trip_delay_rate=0.0, scan_lag_rate=0.0)
    s = score(detect(OperatorView.from_scenario(sc)), sc.truth)
    assert s["precision"] == 1.0
    assert s["recall"] >= 0.95
    assert s["hub_accuracy"] == 1.0
    # A burst that strands only 1-2 parcels is indistinguishable from a single
    # stall, so those come out as STALLED rather than MISSORT.
    assert s["type_accuracy"] >= 0.98


def test_seed_42_quality_floor(scenario):
    # Regression floor for the default detector on the default scenario.
    s = score(detect(OperatorView.from_scenario(scenario)), scenario.truth)
    assert s["precision"] >= 0.9 and s["recall"] >= 0.9
    assert s["type_accuracy"] >= 0.97 and s["hub_accuracy"] >= 0.97
    assert all(v.split("/")[0] != "0" for v in s["recall_by_type"].values())


def test_longer_grace_trades_recall_for_precision(scenario):
    view = OperatorView.from_scenario(scenario)
    short = score(detect(view, DetectorConfig(grace_h=0.5)), scenario.truth)
    long = score(detect(view, DetectorConfig(grace_h=6.0)), scenario.truth)
    assert long["precision"] >= short["precision"] and long["recall"] <= short["recall"]


def _det(sid, kind, hub="DEL"):
    return Detection(sid, kind, hub, "STANDARD", 50.0, 30.0, 31.0, 5.0, 0.8, None, "")


def test_score_counts_and_ignores_at_risk():
    truth = [TruthRecord("S1", "LOST_SCAN", 30.0, "DEL"), TruthRecord("S2", "HELD", 30.0, "MUM")]
    dets = [_det("S1", KIND_LOST_SCAN), _det("S3", KIND_LOST_SCAN), _det("S4", KIND_AT_RISK)]
    s = score(dets, truth)
    assert (s["tp"], s["fp"], s["fn"], s["at_risk"]) == (1, 1, 1, 1)
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert s["type_accuracy"] == 1.0 and s["hub_accuracy"] == 1.0
    assert s["recall_by_type"] == {"LOST_SCAN": "1/1", "HELD": "0/1"}


def test_cli_writes_detections(scenario_dir, capsys):
    detection_cli.main([str(scenario_dir)])
    out = capsys.readouterr().out
    assert '"precision"' in out and (scenario_dir / "detections.csv").exists()
    header = (scenario_dir / "detections.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == [f.name for f in fields(Detection)]
