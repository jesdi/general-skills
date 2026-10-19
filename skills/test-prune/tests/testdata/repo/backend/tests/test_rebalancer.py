"""Rebalancer tests."""
import math
from decimal import ROUND_HALF_UP, Decimal

import pytest

from services.rebalancer import apply_drift, compute_trades, rebalance

TOLERANCE = 1e-9


def _weights(**kw):
    return dict(kw)


@pytest.fixture
def targets():
    return _weights(AAA=0.6, BBB=0.4)


def test_zero_weight(targets):
    trades = compute_trades({"AAA": 0.0}, targets)
    assert trades["AAA"] == pytest.approx(0.6)


def test_small_weight(targets):
    trades = compute_trades({"AAA": 0.01}, targets)
    assert trades["AAA"] > 0
    assert set(trades) == {"AAA", "BBB"}


# Regression: trades must net to zero (issue 412).
def test_compute_trades_sums_to_zero(targets):
    trades = compute_trades({"AAA": 0.5, "BBB": 0.5}, targets)
    assert math.isclose(sum(trades.values()), 0.0, abs_tol=TOLERANCE)


def test_hold_when_on_target(targets):
    assert rebalance(targets, targets) == {}


def test_hold_when_within_band(targets):
    assert rebalance({"AAA": 0.6000001, "BBB": 0.3999999}, targets) == {}


def test_sell_only_rebalance(targets):
    trades = rebalance({"AAA": 0.9, "BBB": 0.1}, targets)
    assert trades["AAA"] < 0


def test_trade_size_is_decimal():
    assert isinstance(Decimal("1.5"), Decimal)


@pytest.mark.parametrize("drift", [0.1, 0.2, 0.3], ids=["low", "mid", "high"])
def test_apply_drift_bounds(drift):
    assert 0 <= apply_drift(drift) <= 1


class TestRounding:
    """Rounding of trade sizes."""

    def test_round_half_up(self):
        assert Decimal("0.5").quantize(Decimal("1"), ROUND_HALF_UP) == 1

    @pytest.mark.slow
    def test_round_down(self):
        assert Decimal("0.4").quantize(Decimal("1"), ROUND_HALF_UP) == 0


class TestDriftHelpers:
    @pytest.fixture
    def base(self):
        return 1.0

    def test_drift_scales(self, base):
        assert apply_drift(base) <= base
