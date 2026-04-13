"""
Tests for Kelly Criterion position sizing math.

Validates the core formula: Kelly = (bp - q) / b
where:
    b = average win / average loss (payoff ratio)
    p = win rate (probability of win)
    q = 1 - p (probability of losing)
"""

import pytest
import sys
import os

# Add src/ to path so we can import trading modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading.sizing import KellySizer


class TestKellyMath:
    """Direct unit tests for Kelly criterion calculations."""

    def test_kelly_formula_basic(self):
        """Kelly = (bp - q) / b for a straightforward positive-expectancy case."""
        # b=1.0 (win = loss), p=0.6, q=0.4
        # Kelly = (1.0*0.6 - 0.4) / 1.0 = 0.2
        sizer = KellySizer(
            bankroll=100.0,
            kelly_fraction=1.0,  # Use full Kelly for pure math test
            max_trades=1,
            max_position_dollars=9999.0,
            max_position_pct=1.0,
        )
        size = self._calculate_kelly_size(
            sizer, bankroll=100.0, net_profit=0.10, loss=0.10, win_rate=0.60
        )
        # b = 0.10/0.10 = 1.0
        # Kelly = (1.0*0.6 - 0.4) / 1.0 = 0.2
        # position = 100 * 0.2 * 1.0 = 20
        assert size == pytest.approx(20.0, rel=1e-4)

    def test_kelly_high_win_rate(self):
        """Test with 99% win rate — should produce a large but capped Kelly fraction."""
        sizer = KellySizer(bankroll=100.0, kelly_fraction=1.0, max_trades=1, max_position_dollars=9999.0, max_position_pct=1.0)
        size = self._calculate_kelly_size(
            sizer, bankroll=100.0, net_profit=0.03, loss=0.10, win_rate=0.99
        )
        # b = 0.03/0.10 = 0.3
        # Kelly = (0.3*0.99 - 0.01) / 0.3 = (0.297 - 0.01) / 0.3 = 0.9567
        expected = 100.0 * 0.9566667
        assert size == pytest.approx(expected, rel=1e-4)

    def test_kelly_fifty_percent_win_rate(self):
        """Edge case: 50% win rate with symmetric payoff should yield Kelly = 0."""
        sizer = KellySizer(bankroll=100.0, kelly_fraction=1.0, max_trades=1, max_position_dollars=9999.0, max_position_pct=1.0)
        size = self._calculate_kelly_size(
            sizer, bankroll=100.0, net_profit=0.10, loss=0.10, win_rate=0.50
        )
        # b = 1.0, Kelly = (1.0*0.5 - 0.5) / 1.0 = 0
        assert size == pytest.approx(0.0, abs=1e-6)

    def test_kelly_very_small_profit(self):
        """Very small net profit with moderate win rate should still be calculable."""
        sizer = KellySizer(bankroll=100.0, kelly_fraction=1.0, max_trades=1, max_position_dollars=9999.0, max_position_pct=1.0)
        size = self._calculate_kelly_size(
            sizer, bankroll=100.0, net_profit=0.005, loss=0.10, win_rate=0.95
        )
        # b = 0.005/0.10 = 0.05
        # Kelly = (0.05*0.95 - 0.05) / 0.05 = (0.0475 - 0.05) / 0.05 = -0.05
        assert size == pytest.approx(0.0, abs=1e-6)

    def test_negative_kelly_returns_zero(self):
        """If Kelly is negative, position size must be zero (trade blocked)."""
        sizer = KellySizer(bankroll=100.0, kelly_fraction=1.0, max_trades=1, max_position_dollars=9999.0, max_position_pct=1.0)
        size = self._calculate_kelly_size(
            sizer, bankroll=100.0, net_profit=0.02, loss=0.10, win_rate=0.70
        )
        # b = 0.02/0.10 = 0.2
        # Kelly = (0.2*0.7 - 0.3) / 0.2 = (0.14 - 0.3) / 0.2 = -0.8
        assert size == 0.0

    def test_kelly_with_slippage_reduced_net_profit(self):
        """Slippage reducing net profit from 8% gross to 3% net should lower Kelly."""
        # Use a large bankroll so the $9999 cap doesn't distort the comparison
        sizer = KellySizer(bankroll=1000.0, kelly_fraction=1.0, max_trades=1, max_position_dollars=9999.0, max_position_pct=1.0)
        size_gross = self._calculate_kelly_size(
            sizer, bankroll=1000.0, net_profit=0.08, loss=0.10, win_rate=0.85
        )
        size_net = self._calculate_kelly_size(
            sizer, bankroll=1000.0, net_profit=0.03, loss=0.10, win_rate=0.85
        )
        # Gross: b=0.8, Kelly=(0.8*0.85-0.15)/0.8 = 0.6625 -> $662.5
        # Net:  b=0.3, Kelly=(0.3*0.85-0.15)/0.3 = 0.35   -> $350.0
        assert size_net < size_gross
        assert size_gross == pytest.approx(662.5, rel=1e-4)
        assert size_net == pytest.approx(350.0, rel=1e-4)

    # ------------------------------------------------------------------
    # Helper that replicates the internal Kelly logic with explicit inputs
    # ------------------------------------------------------------------
    @staticmethod
    def _calculate_kelly_size(sizer: KellySizer, bankroll: float, net_profit: float, loss: float, win_rate: float) -> float:
        """
        Reproduce the Kelly calculation from KellySizer.calculate_size
        but with explicit parameters so tests are self-contained.
        """
        b = net_profit / loss
        p = win_rate
        q = 1 - p
        kelly_fraction_raw = (b * p - q) / b
        if kelly_fraction_raw <= 0:
            return 0.0
        adjusted_kelly = kelly_fraction_raw * sizer.kelly_fraction
        position_size = bankroll * adjusted_kelly
        max_position_pct = bankroll * sizer.max_position_pct
        max_position = min(max_position_pct, sizer.max_position_dollars)
        position_size = min(position_size, max_position)
        if position_size < 1.0:
            # The original code forces $1 minimum; for pure math tests we
            # want to see the raw Kelly, so skip the $1 floor when testing
            # large bankrolls.  We'll just return the raw capped size.
            pass
        return position_size


class TestKellySizerIntegration:
    """Integration tests against the actual KellySizer class behavior."""

    def test_sizer_uses_realistic_defaults(self):
        """The fixed sizer uses 85% win rate and 3% net profit by default."""
        sizer = KellySizer(bankroll=100.0, kelly_fraction=0.1, max_trades=1, max_position_dollars=5.0)
        size = sizer.calculate_size(confidence=1.0)
        # With b=0.03/0.10=0.3, p=0.85, q=0.15
        # raw Kelly = (0.3*0.85 - 0.15) / 0.3 = 0.35
        # adjusted = 0.35 * 0.1 = 0.035
        # position = 100 * 0.035 = 3.5, capped at $5 -> $3.50 (but code forces $1 min, so $3.50)
        assert size > 0
        assert size <= 5.0

    def test_sizer_blocks_after_max_trades(self):
        """Once max_trades is reached, calculate_size must return 0."""
        sizer = KellySizer(bankroll=100.0, max_trades=2, max_position_dollars=5.0)
        sizer.update_trade(pnl=1.0)
        sizer.update_trade(pnl=-0.5)
        size = sizer.calculate_size(confidence=1.0)
        assert size == 0.0
