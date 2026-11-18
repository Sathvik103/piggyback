"""Risk-aware recovery: delay model, expected penalty, calibrated P(stranded)."""

from dataclasses import replace

import pytest

from detection import OperatorView, detect
from detection.detector import KIND_AT_RISK
from recovery import Network, generate_options
from recovery.options import MODE_DEDICATED, MODE_NONE, MODE_PIGGYBACK
from recovery.risk import DelayModel, merge_options, p_stranded, risk_adjust
from simulator import generate_scenario


def test_delay_model_math():
    m = DelayModel((0.0, 0.0, 1.0, 3.0))
    assert m.p_later_than(0.5) == 0.5 and m.p_later_than(3.0) == 0.0
    assert m.expected_excess(1.0) == pytest.approx(2.0 / 4)
    empty = DelayModel(())
    assert empty.p_later_than(0.0) == 0.0 and empty.expected_excess(0.0) == 0.0


def test_delay_model_learns_only_from_observed_trucks():
    sc = generate_scenario(seed=2, n_shipments=1200)
    m = DelayModel.from_view(OperatorView.from_scenario(sc))
    assert len(m.delays) >= 30 and min(m.delays) >= 0.0
    assert 0.0 < m.p_later_than(0.5) < 0.5   # most trucks are on time, some are not


@pytest.fixture(scope="module")
def world():
    sc = generate_scenario(seed=6, n_shipments=1500, capacity_tightness=0.5)
    view = OperatorView.from_scenario(sc)
    dets = [d for d in detect(view) if d.kind != KIND_AT_RISK]
    return view, dets


def test_expected_penalty_prices_risk_and_doubt(world):
    view, dets = world
    tight = Network(view, transfer_buffer_h=0.0)
    opts = generate_options(view, dets, network=tight)
    ships = {s.shipment_id: s for s in view.shipments}
    model = DelayModel((0.0,) * 70 + (2.0,) * 30)       # 30% of trucks run 2 h late
    sure = risk_adjust(opts, tight, model, ships)
    doubtful = risk_adjust(opts, tight, model, ships, p_real={sid: 0.1 for sid in opts})
    for sid, os_ in opts.items():
        by_id = {o.option_id: o for o in os_}
        for o in sure[sid]:
            base = by_id[o.option_id]
            if o.mode == MODE_PIGGYBACK:
                assert o.penalty_inr >= base.penalty_inr - 0.01  # uncertainty never makes a ride look better
            if o.mode in (MODE_DEDICATED, MODE_NONE):
                assert o.penalty_inr == pytest.approx(base.penalty_inr, abs=0.01)
        # Doubt shrinks the value of acting, never the money: a truck for a doubtful flag
        # looks worse relative to leaving it.
        t_sure = next((o for o in sure[sid] if o.mode == MODE_DEDICATED), None)
        t_doubt = next((o for o in doubtful[sid] if o.mode == MODE_DEDICATED), None)
        n_sure = next(o for o in sure[sid] if o.mode == MODE_NONE)
        n_doubt = next(o for o in doubtful[sid] if o.mode == MODE_NONE)
        if t_sure:
            assert t_doubt.penalty_inr - n_doubt.penalty_inr >= t_sure.penalty_inr - n_sure.penalty_inr - 0.01


def test_tighter_transfers_cost_more_in_expectation(world):
    view, dets = world
    tight = Network(view, transfer_buffer_h=0.0)
    opts = generate_options(view, dets, network=tight)
    ships = {s.shipment_id: s for s in view.shipments}
    calm = risk_adjust(opts, tight, DelayModel((0.0,) * 100), ships)
    stormy = risk_adjust(opts, tight, DelayModel((0.0,) * 50 + (4.0,) * 50), ships)
    rides = [(c, s) for sid in opts for c, s in zip(calm[sid], stormy[sid], strict=True) if c.mode == MODE_PIGGYBACK]
    assert rides and all(s.penalty_inr >= c.penalty_inr - 0.01 for c, s in rides)
    assert any(s.penalty_inr > c.penalty_inr + 1 for c, s in rides)


def test_calibration_distrusts_weak_flags(world):
    _, dets = world
    d = dets[0]
    assert p_stranded(replace(d, kind="STALLED", confidence=0.6)) < 0.2
    assert p_stranded(replace(d, kind="MISSED_CONNECTION", confidence=0.9)) > 0.9


def test_merge_options_keeps_one_copy_with_unique_ids(world):
    view, dets = world
    a = generate_options(view, dets[:20], network=Network(view))
    b = generate_options(view, dets[:20], network=Network(view, transfer_buffer_h=0.0))
    merged = merge_options(a, b, a)
    for sid, opts in merged.items():
        assert len({o.option_id for o in opts}) == len(opts)
        assert len({(o.mode, o.legs, o.delivered_h) for o in opts}) == len(opts)
        assert len(opts) >= len(a[sid])
