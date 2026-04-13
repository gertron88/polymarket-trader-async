"""Tests for circuit breaker panel."""

import time
import pytest
from risk.circuit_breakers import (
    RPCLatencyBreaker,
    WebSocketHealthBreaker,
    ConsecutiveLossBreaker,
    WinRateBreaker,
    DailyLossBreaker,
    SlippageMonitor,
    CircuitBreakerPanel,
)


class TestRPCLatencyBreaker:
    def test_trip_on_high_latency(self):
        b = RPCLatencyBreaker(max_latency_ms=100.0, sample_window=3)
        for _ in range(3):
            b.record_latency(200.0)
        assert b.check() is False
        assert b.is_tripped

    def test_ok_with_low_latency(self):
        b = RPCLatencyBreaker(max_latency_ms=500.0, sample_window=3)
        b.record_latency(50.0)
        assert b.check() is True


class TestWebSocketHealthBreaker:
    def test_trip_on_disconnect(self):
        b = WebSocketHealthBreaker(max_disconnect_seconds=0.1)
        time.sleep(0.15)
        assert b.check() is False
        assert "disconnected" in b.reason

    def test_ok_after_heartbeat(self):
        b = WebSocketHealthBreaker(max_disconnect_seconds=5.0)
        b.heartbeat_price_feed()
        b.heartbeat_polymarket()
        assert b.check() is True


class TestConsecutiveLossBreaker:
    def test_trip_after_three_losses(self):
        b = ConsecutiveLossBreaker(max_consecutive_losses=3)
        b.record_trade(-1.0)
        b.record_trade(-2.0)
        assert b.check() is True
        b.record_trade(-3.0)
        assert b.check() is False
        assert "3 consecutive losses" in b.reason

    def test_reset_on_win(self):
        b = ConsecutiveLossBreaker(max_consecutive_losses=3)
        b.record_trade(-1.0)
        b.record_trade(-2.0)
        b.record_trade(-3.0)
        b.check()
        assert b.is_tripped
        b.record_trade(5.0)
        assert b.is_tripped is False


class TestWinRateBreaker:
    def test_trip_below_threshold(self):
        b = WinRateBreaker(min_win_rate=0.65, window_size=10, min_samples=10)
        for _ in range(10):
            b.record_trade(-1.0)
        assert b.check() is False
        assert "win rate 0.0%" in b.reason

    def test_ok_above_threshold(self):
        b = WinRateBreaker(min_win_rate=0.50, window_size=10, min_samples=5)
        for _ in range(10):
            b.record_trade(1.0)
        assert b.check() is True


class TestDailyLossBreaker:
    def test_trip_on_daily_loss(self):
        b = DailyLossBreaker(max_daily_loss_pct=0.05)
        b.set_bankroll(100.0)
        b.record_pnl(-6.0)
        assert b.check() is False
        assert "Daily loss $-6.00" in b.reason

    def test_reset_clears_pnl(self):
        b = DailyLossBreaker(max_daily_loss_pct=0.05)
        b.set_bankroll(100.0)
        b.record_pnl(-6.0)
        b.check()
        b.reset_daily_pnl()
        assert b.is_tripped is False


class TestSlippageMonitor:
    def test_warning_on_high_slippage(self):
        m = SlippageMonitor(warning_threshold_pct=0.01)
        m.record_entry(expected_price=0.46, actual_price=0.48, size=5.0)
        avg = m.get_avg_slippage()
        assert avg["entry"] == pytest.approx(0.0435, rel=1e-2)


class TestCircuitBreakerPanel:
    def test_all_breakers_ok_initially(self):
        panel = CircuitBreakerPanel()
        ok, reason = panel.check_all()
        assert ok is True
        assert reason == "OK"

    def test_any_tripped_after_losses(self):
        panel = CircuitBreakerPanel(max_consecutive_losses=2)
        panel.record_trade(-1.0)
        panel.record_trade(-2.0)
        ok, _ = panel.check_all()
        assert ok is False
        assert panel.any_tripped() is True

    def test_status_dict(self):
        panel = CircuitBreakerPanel()
        status = panel.get_status()
        assert "RPCLatencyBreaker" in status
        assert "ConsecutiveLossBreaker" in status
