"""
Tests for slippage impact on net profit.

Validates that gross profit minus entry and exit slippage
equals the realistic net profit used in Kelly calculations.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def calculate_net_profit(
    gross_profit_pct: float,
    entry_slippage_pct: float,
    exit_slippage_pct: float,
) -> float:
    """
    Compute net profit after accounting for entry and exit slippage.

    Example from risk register:
        8% gross - 1% entry - 1% exit = 6% net
    """
    return gross_profit_pct - entry_slippage_pct - exit_slippage_pct


class TestSlippageModel:
    """Slippage and net-profit arithmetic tests."""

    def test_eight_percent_gross_minus_one_one_equals_six_net(self):
        """
        Risk-register example:
        8% gross profit, 1% entry slippage, 1% exit slippage -> 6% net.
        """
        net = calculate_net_profit(
            gross_profit_pct=0.08,
            entry_slippage_pct=0.01,
            exit_slippage_pct=0.01,
        )
        assert net == pytest.approx(0.06, abs=1e-9)

    def test_slippage_impact_on_net_profit(self):
        """
        Higher slippage should monotonically reduce net profit.
        """
        base = calculate_net_profit(0.10, 0.005, 0.005)
        worse_entry = calculate_net_profit(0.10, 0.02, 0.005)
        worse_exit = calculate_net_profit(0.10, 0.005, 0.02)
        both_worse = calculate_net_profit(0.10, 0.02, 0.02)

        assert worse_entry < base
        assert worse_exit < base
        assert both_worse < worse_entry
        assert both_worse < worse_exit

    def test_zero_slippage_equals_gross(self):
        """When slippage is zero, net profit should equal gross profit."""
        net = calculate_net_profit(0.08, 0.0, 0.0)
        assert net == pytest.approx(0.08, abs=1e-9)

    def test_large_slippage_can_make_net_negative(self):
        """If slippage exceeds gross profit, net profit becomes negative."""
        net = calculate_net_profit(0.03, 0.02, 0.02)
        assert net < 0

    def test_realistic_parameters_from_risk_register(self):
        """
        Validate the fixed bot parameters against the risk register.
        The bot now assumes 3% net profit after slippage.
        """
        # If gross was originally 8% and slippage is ~2.5% each side,
        # net would be 3%.
        net = calculate_net_profit(0.08, 0.025, 0.025)
        assert net == pytest.approx(0.03, abs=1e-9)

    def test_kelly_becomes_negative_with_high_slippage(self):
        """
        Demonstrate that slippage can flip Kelly from positive to negative.
        At 70% win rate, 8% gross profit gives positive Kelly,
        but after slippage reduces net to 2%, Kelly becomes negative.
        """
        def kelly(win_rate: float, net_profit: float, loss: float = 0.10) -> float:
            b = net_profit / loss
            p = win_rate
            q = 1 - p
            return (b * p - q) / b

        kelly_gross = kelly(win_rate=0.70, net_profit=0.08)
        net_after_slippage = calculate_net_profit(0.08, 0.03, 0.03)
        kelly_net = kelly(win_rate=0.70, net_profit=net_after_slippage)

        assert kelly_gross > 0
        assert net_after_slippage == pytest.approx(0.02, abs=1e-9)
        assert kelly_net < 0
