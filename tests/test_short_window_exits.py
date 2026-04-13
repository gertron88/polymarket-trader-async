"""Tests for short-window exit strategy."""

import time
import pytest
from trading.short_window_exits import ShortWindowExitManager, ExitTrigger


class TestShortWindowExitManager:
    """Test short-window exit logic with realistic Polygon timing."""

    def test_min_hold_blocks_early_exit(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP")
        result = mgr.check_exit(current_pm_price=0.60, window_time_remaining=120)
        assert result.should_exit is False
        assert result.trigger == ExitTrigger.MIN_HOLD_NOT_MET

    def test_profit_target_after_min_hold(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP", profit_target_pct=0.03)
        mgr.entry_time = time.time() - 3.0  # Fake min hold elapsed
        result = mgr.check_exit(current_pm_price=0.49, window_time_remaining=120)
        assert result.should_exit is True
        assert result.trigger == ExitTrigger.PROFIT_TARGET

    def test_stop_loss_after_min_hold(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP", stop_loss_pct=0.05)
        mgr.entry_time = time.time() - 3.0
        result = mgr.check_exit(current_pm_price=0.40, window_time_remaining=120)
        assert result.should_exit is True
        assert result.trigger == ExitTrigger.STOP_LOSS

    def test_max_hold_time_triggers_exit(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP")
        mgr.entry_time = time.time() - 10.0  # Fake entry time to exceed max hold
        result = mgr.check_exit(current_pm_price=0.47, window_time_remaining=120)
        assert result.should_exit is True
        assert result.trigger == ExitTrigger.MAX_HOLD_TIME

    def test_window_ending_triggers_exit(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP")
        mgr.entry_time = time.time() - 3.0
        result = mgr.check_exit(current_pm_price=0.47, window_time_remaining=20)
        assert result.should_exit is True
        assert result.trigger == ExitTrigger.WINDOW_ENDING

    def test_down_side_profit_target(self):
        mgr = ShortWindowExitManager(entry_price=0.53, side="DOWN", profit_target_pct=0.03)
        mgr.entry_time = time.time() - 3.0
        result = mgr.check_exit(current_pm_price=0.50, window_time_remaining=120)
        assert result.should_exit is True
        assert result.trigger == ExitTrigger.PROFIT_TARGET

    def test_no_exit_when_holding(self):
        mgr = ShortWindowExitManager(entry_price=0.47, side="UP")
        mgr.entry_time = time.time() - 3.0
        result = mgr.check_exit(current_pm_price=0.471, window_time_remaining=120)
        assert result.should_exit is False
        assert result.trigger is None
