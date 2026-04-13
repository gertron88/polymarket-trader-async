"""
Tests for circuit breakers and risk controls.

If circuit breakers don't exist as a standalone module yet, these tests
serve as a specification and stub framework that can be wired into the
engine later.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading.sizing import KellySizer


class SimpleCircuitBreaker:
    """
    Minimal circuit-breaker implementation used for testing.
    Can be merged into the engine or extracted into its own module later.
    """

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        rolling_window: int = 100,
        min_win_rate: float = 0.65,
        max_latency_ms: float = 500.0,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.rolling_window = rolling_window
        self.min_win_rate = min_win_rate
        self.max_latency_ms = max_latency_ms
        self._tripped = False
        self._trip_reason: str = ""
        self._trades: list[float] = []
        self._consecutive_losses = 0
        self._latency_readings: list[float] = []

    def is_tripped(self) -> bool:
        return self._tripped

    def trip_reason(self) -> str:
        return self._trip_reason

    def trip(self, reason: str) -> None:
        self._tripped = True
        self._trip_reason = reason

    def reset(self) -> None:
        self._tripped = False
        self._trip_reason = ""

    def record_trade(self, pnl: float) -> None:
        self._trades.append(pnl)
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._consecutive_losses >= self.max_consecutive_losses:
            self.trip(f"Consecutive losses >= {self.max_consecutive_losses}")

        win_rate = self.rolling_win_rate()
        if win_rate is not None and win_rate < self.min_win_rate:
            self.trip(f"Rolling win rate {win_rate:.2%} below {self.min_win_rate:.2%}")

    def rolling_win_rate(self) -> float | None:
        if not self._trades:
            return None
        window = self._trades[-self.rolling_window :]
        wins = sum(1 for t in window if t > 0)
        return wins / len(window)

    def record_latency(self, latency_ms: float) -> None:
        self._latency_readings.append(latency_ms)
        if latency_ms > self.max_latency_ms:
            self.trip(f"Latency {latency_ms:.1f}ms exceeds {self.max_latency_ms:.1f}ms")

    def can_trade(self) -> tuple[bool, str]:
        if self._tripped:
            return False, self._trip_reason
        return True, "OK"


class TestCircuitBreakers:
    """Circuit breaker behavior tests."""

    def test_tripped_breaker_blocks_trading(self):
        """A tripped breaker must block any new trades."""
        cb = SimpleCircuitBreaker()
        assert cb.can_trade() == (True, "OK")
        cb.trip("Manual trip")
        assert cb.can_trade() == (False, "Manual trip")

    def test_reset_allows_trading_again(self):
        cb = SimpleCircuitBreaker()
        cb.trip("Test")
        assert cb.is_tripped() is True
        cb.reset()
        assert cb.is_tripped() is False
        assert cb.can_trade() == (True, "OK")

    def test_consecutive_loss_counter(self):
        """Three consecutive losses should trip the breaker."""
        cb = SimpleCircuitBreaker(max_consecutive_losses=3, min_win_rate=0.0)
        cb.record_trade(-1.0)
        cb.record_trade(-1.0)
        assert cb.is_tripped() is False
        cb.record_trade(-1.0)
        assert cb.is_tripped() is True
        assert "Consecutive losses" in cb.trip_reason()

    def test_win_resets_consecutive_loss_counter(self):
        cb = SimpleCircuitBreaker(max_consecutive_losses=3, min_win_rate=0.0)
        cb.record_trade(-1.0)
        cb.record_trade(-1.0)
        cb.record_trade(2.0)  # win resets
        cb.record_trade(-1.0)
        cb.record_trade(-1.0)
        assert cb.is_tripped() is False

    def test_rolling_win_rate_calculation(self):
        """Rolling win rate over the last N trades."""
        cb = SimpleCircuitBreaker(rolling_window=10)
        for _ in range(5):
            cb.record_trade(1.0)
        for _ in range(5):
            cb.record_trade(-1.0)
        assert cb.rolling_win_rate() == pytest.approx(0.50, abs=1e-6)

    def test_rolling_win_rate_trips_below_threshold(self):
        """If win rate falls below 65% over the rolling window, trip."""
        cb = SimpleCircuitBreaker(rolling_window=10, min_win_rate=0.65)
        # 6 wins, 4 losses = 60% win rate
        for _ in range(6):
            cb.record_trade(1.0)
        for _ in range(4):
            cb.record_trade(-1.0)
        assert cb.is_tripped() is True
        assert "Rolling win rate" in cb.trip_reason()

    def test_latency_trip(self):
        cb = SimpleCircuitBreaker(max_latency_ms=500.0)
        cb.record_latency(200.0)
        assert cb.is_tripped() is False
        cb.record_latency(501.0)
        assert cb.is_tripped() is True
        assert "Latency" in cb.trip_reason()


class TestKellySizerAsCircuitBreaker:
    """The existing KellySizer already implements some circuit-breaker logic."""

    def test_daily_loss_limit_blocks_trading(self):
        sizer = KellySizer(bankroll=100.0, daily_loss_limit=-0.05, max_trades=10)
        sizer.update_trade(pnl=-6.0)  # 6% loss > 5% limit
        allowed, reason = sizer.can_trade()
        assert allowed is False
        assert "Daily loss limit" in reason

    def test_bankroll_half_threshold_blocks_trading(self):
        sizer = KellySizer(bankroll=100.0, max_trades=10, daily_loss_limit=-10.0)
        sizer.update_trade(pnl=-60.0)  # bankroll now 40
        allowed, reason = sizer.can_trade()
        assert allowed is False
        assert "50%" in reason
