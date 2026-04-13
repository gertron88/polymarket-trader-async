"""
Tests for position P&L calculations.

Covers:
- Market-neutral position P&L (both sides filled)
- One-sided fill P&L (only UP fills, DOWN doesn't)
- Realized P&L with actual exit prices
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading.position import Position, PositionManager


class TestPositionPnL:
    """P&L calculation tests for the PositionManager."""

    def test_market_neutral_position_pnl(self):
        """
        Both sides filled at 0.46 and exited at 0.52 (UP) / 0.40 (DOWN).
        Realized P&L should reflect the sum of both sides.
        """
        pm = PositionManager()
        pos = pm.create_position(window_ts=1, up_token="up1", down_token="down1", size=5.0)

        pm.update_fill(1, "up", filled=True, fill_price=0.46)
        pm.update_fill(1, "down", filled=True, fill_price=0.46)

        # Exit UP at 0.52, DOWN at 0.40
        pm.update_exit(1, "up", exit_price=0.52)
        pm.update_exit(1, "down", exit_price=0.40)

        pnl = pm.calculate_realized_pnl(pos)
        # UP: (0.52 - 0.46) * 5.0 = 0.30
        # DOWN: (0.40 - 0.46) * 5.0 = -0.30
        # Note: calculate_realized_pnl uses actual fill prices directly,
        # so DOWN P&L is negative here because exit < entry.
        assert pnl == pytest.approx(0.0, abs=1e-6)

    def test_market_neutral_no_move(self):
        """If both sides exit at entry price, P&L should be zero."""
        pm = PositionManager()
        pos = pm.create_position(window_ts=2, up_token="up2", down_token="down2", size=5.0)

        pm.update_fill(2, "up", filled=True, fill_price=0.46)
        pm.update_fill(2, "down", filled=True, fill_price=0.46)
        pm.update_exit(2, "up", exit_price=0.46)
        pm.update_exit(2, "down", exit_price=0.46)

        pnl = pm.calculate_realized_pnl(pos)
        assert pnl == pytest.approx(0.0, abs=1e-6)

    def test_one_sided_fill_pnl_up_only(self):
        """
        Only the UP side fills and exits.
        Realized P&L should only include the UP side.
        """
        pm = PositionManager()
        pos = pm.create_position(window_ts=3, up_token="up3", down_token="down3", size=5.0)

        pm.update_fill(3, "up", filled=True, fill_price=0.46)
        # DOWN never fills

        pm.update_exit(3, "up", exit_price=0.52)

        pnl = pm.calculate_realized_pnl(pos)
        # Only UP profit
        assert pnl == pytest.approx((0.52 - 0.46) * 5.0, abs=1e-6)

    def test_one_sided_fill_pnl_down_only(self):
        """
        Only the DOWN side fills and exits.
        Realized P&L should only include the DOWN side.
        """
        pm = PositionManager()
        pos = pm.create_position(window_ts=4, up_token="up4", down_token="down4", size=5.0)

        pm.update_fill(4, "down", filled=True, fill_price=0.46)
        # UP never fills

        pm.update_exit(4, "down", exit_price=0.52)

        pnl = pm.calculate_realized_pnl(pos)
        # DOWN side uses actual fill prices: (exit - entry) * size
        # If exit > entry, that's a positive P&L for the DOWN token
        assert pnl == pytest.approx((0.52 - 0.46) * 5.0, abs=1e-6)

    def test_realized_pnl_with_actual_exit_prices(self):
        """
        Use arbitrary exit prices to verify realized P&L formula directly.
        """
        pm = PositionManager()
        pos = pm.create_position(window_ts=5, up_token="up5", down_token="down5", size=10.0)

        pm.update_fill(5, "up", filled=True, fill_price=0.50)
        pm.update_fill(5, "down", filled=True, fill_price=0.50)

        pm.update_exit(5, "up", exit_price=0.60)
        pm.update_exit(5, "down", exit_price=0.60)

        pnl = pm.calculate_realized_pnl(pos)
        expected_up = (0.60 - 0.50) * 10.0    # 1.00
        expected_down = (0.60 - 0.50) * 10.0  # 1.00
        assert pnl == pytest.approx(expected_up + expected_down, abs=1e-6)

    def test_unexited_position_has_zero_realized_pnl(self):
        """A fully filled position that hasn't been exited has $0 realized P&L."""
        pm = PositionManager()
        pos = pm.create_position(window_ts=6, up_token="up6", down_token="down6", size=5.0)

        pm.update_fill(6, "up", filled=True, fill_price=0.46)
        pm.update_fill(6, "down", filled=True, fill_price=0.46)

        assert pm.calculate_realized_pnl(pos) == 0.0

    def test_partial_fill_zero_pnl(self):
        """A position with no fills should report $0 realized P&L."""
        pm = PositionManager()
        pos = pm.create_position(window_ts=7, up_token="up7", down_token="down7", size=5.0)

        assert pm.calculate_realized_pnl(pos) == 0.0
        assert pm.calculate_pnl(pos) == 0.0

    def test_position_state_transitions(self):
        """Verify that position state advances correctly through lifecycle."""
        pm = PositionManager()
        pos = pm.create_position(window_ts=8, up_token="up8", down_token="down8", size=5.0)

        from trading.position import PositionState
        assert pos.state == PositionState.PENDING

        pm.update_fill(8, "up", filled=True)
        assert pos.state == PositionState.PARTIAL_FILL

        pm.update_fill(8, "down", filled=True)
        assert pos.state == PositionState.ACTIVE

        pm.update_exit(8, "up", exit_price=0.50)
        assert pos.state == PositionState.EXITING

        pm.update_exit(8, "down", exit_price=0.42)
        assert pos.state == PositionState.CLOSED
