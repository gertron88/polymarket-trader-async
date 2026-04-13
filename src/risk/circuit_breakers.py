"""
Circuit Breakers and Risk Guards for Polymarket Trading Bot

Implements trading halt mechanisms based on:
- RPC latency spikes
- WebSocket disconnections
- Consecutive losses
- Rolling win rate degradation
- Daily loss limits
- Slippage warnings
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from collections import deque

logger = logging.getLogger(__name__)


class BaseCircuitBreaker(ABC):
    """Abstract base class for all circuit breakers."""

    def __init__(self, name: str):
        self.name = name
        self.is_tripped = False
        self.reason: Optional[str] = None
        self.trip_timestamp: Optional[float] = None

    def trip(self, reason: str) -> None:
        """Trip the circuit breaker."""
        if not self.is_tripped:
            self.is_tripped = True
            self.reason = reason
            self.trip_timestamp = time.time()
            logger.error(f"🚨 CIRCUIT BREAKER TRIPPED: {self.name} | {reason}")

    def reset(self) -> None:
        """Reset the circuit breaker."""
        if self.is_tripped:
            logger.info(f"✅ CIRCUIT BREAKER RESET: {self.name}")
        self.is_tripped = False
        self.reason = None
        self.trip_timestamp = None

    @abstractmethod
    def check(self, **kwargs) -> bool:
        """
        Check the breaker condition.

        Returns:
            True if OK (not tripped by this check), False if tripped.
        """
        pass


class RPCLatencyBreaker(BaseCircuitBreaker):
    """Trip if RPC latency exceeds threshold for sustained period."""

    def __init__(
        self,
        max_latency_ms: float = 500.0,
        sample_window: int = 5,
        name: str = "RPCLatencyBreaker"
    ):
        super().__init__(name)
        self.max_latency_ms = max_latency_ms
        self.sample_window = sample_window
        self._latencies: deque[float] = deque(maxlen=sample_window)

    def record_latency(self, latency_ms: float) -> None:
        """Record an RPC latency sample."""
        self._latencies.append(latency_ms)

    def check(self, **kwargs) -> bool:
        if len(self._latencies) < self.sample_window:
            return True

        avg_latency = sum(self._latencies) / len(self._latencies)
        if avg_latency > self.max_latency_ms:
            self.trip(
                f"Avg RPC latency {avg_latency:.0f}ms > {self.max_latency_ms:.0f}ms "
                f"over {self.sample_window} samples"
            )
            return False
        return True


class WebSocketHealthBreaker(BaseCircuitBreaker):
    """Trip if price feed or Polymarket WS is disconnected too long."""

    def __init__(
        self,
        max_disconnect_seconds: float = 5.0,
        name: str = "WebSocketHealthBreaker"
    ):
        super().__init__(name)
        self.max_disconnect_seconds = max_disconnect_seconds
        self._last_price_feed_at: float = time.time()
        self._last_polymarket_at: float = time.time()

    def heartbeat_price_feed(self) -> None:
        """Record a price feed heartbeat."""
        self._last_price_feed_at = time.time()

    def heartbeat_polymarket(self) -> None:
        """Record a Polymarket WS heartbeat."""
        self._last_polymarket_at = time.time()

    def check(self, **kwargs) -> bool:
        now = time.time()
        price_feed_gap = now - self._last_price_feed_at
        polymarket_gap = now - self._last_polymarket_at

        if price_feed_gap > self.max_disconnect_seconds:
            self.trip(
                f"Price feed disconnected for {price_feed_gap:.1f}s "
                f"(max {self.max_disconnect_seconds:.1f}s)"
            )
            return False

        if polymarket_gap > self.max_disconnect_seconds:
            self.trip(
                f"Polymarket WS disconnected for {polymarket_gap:.1f}s "
                f"(max {self.max_disconnect_seconds:.1f}s)"
            )
            return False

        return True


class ConsecutiveLossBreaker(BaseCircuitBreaker):
    """Trip after N consecutive losses."""

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        name: str = "ConsecutiveLossBreaker"
    ):
        super().__init__(name)
        self.max_consecutive_losses = max_consecutive_losses
        self._consecutive_losses = 0

    def record_trade(self, pnl: float) -> None:
        """Record a trade result."""
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
            # Auto-reset if we were tripped and then won
            if self.is_tripped:
                self.reset()

    def check(self, **kwargs) -> bool:
        if self._consecutive_losses >= self.max_consecutive_losses:
            self.trip(
                f"{self._consecutive_losses} consecutive losses "
                f"(max {self.max_consecutive_losses})"
            )
            return False
        return True


class WinRateBreaker(BaseCircuitBreaker):
    """Trip if rolling win rate falls below threshold."""

    def __init__(
        self,
        min_win_rate: float = 0.65,
        window_size: int = 100,
        min_samples: int = 20,
        name: str = "WinRateBreaker"
    ):
        super().__init__(name)
        self.min_win_rate = min_win_rate
        self.window_size = window_size
        self.min_samples = min_samples
        self._results: deque[bool] = deque(maxlen=window_size)

    def record_trade(self, pnl: float) -> None:
        """Record a trade result (True = win, False = loss)."""
        self._results.append(pnl > 0)

    def check(self, **kwargs) -> bool:
        if len(self._results) < self.min_samples:
            return True

        wins = sum(1 for r in self._results if r)
        win_rate = wins / len(self._results)

        if win_rate < self.min_win_rate:
            self.trip(
                f"Rolling win rate {win_rate:.1%} over last {len(self._results)} trades "
                f"< {self.min_win_rate:.1%}"
            )
            return False
        return True

    def get_win_rate(self) -> Optional[float]:
        """Get current rolling win rate."""
        if not self._results:
            return None
        return sum(1 for r in self._results if r) / len(self._results)


class DailyLossBreaker(BaseCircuitBreaker):
    """Trip if daily P&L drops below threshold."""

    def __init__(
        self,
        max_daily_loss_pct: float = 0.05,
        name: str = "DailyLossBreaker"
    ):
        super().__init__(name)
        self.max_daily_loss_pct = max_daily_loss_pct
        self._daily_pnl = 0.0
        self._bankroll = 100.0

    def set_bankroll(self, bankroll: float) -> None:
        """Set bankroll for percentage calculation."""
        self._bankroll = bankroll

    def record_pnl(self, pnl: float) -> None:
        """Record a P&L update."""
        self._daily_pnl += pnl

    def reset_daily_pnl(self) -> None:
        """Reset daily P&L (e.g., at midnight)."""
        self._daily_pnl = 0.0
        self.reset()

    def check(self, **kwargs) -> bool:
        if self._bankroll <= 0:
            return True

        loss_pct = abs(self._daily_pnl) / self._bankroll
        if self._daily_pnl < 0 and loss_pct >= self.max_daily_loss_pct:
            self.trip(
                f"Daily loss ${self._daily_pnl:+.2f} ({loss_pct:.1%}) "
                f">= {self.max_daily_loss_pct:.1%}"
            )
            return False
        return True


class SlippageMonitor:
    """Monitor slippage and log warnings (does not trip a breaker by default)."""

    def __init__(self, warning_threshold_pct: float = 0.01):
        self.warning_threshold_pct = warning_threshold_pct
        self._entries: List[Dict] = []
        self._exits: List[Dict] = []

    def record_entry(self, expected_price: float, actual_price: float, size: float) -> None:
        """Record an entry fill."""
        slippage = abs(actual_price - expected_price) / expected_price if expected_price > 0 else 0.0
        record = {
            "expected": expected_price,
            "actual": actual_price,
            "slippage_pct": slippage,
            "size": size,
            "timestamp": time.time(),
        }
        self._entries.append(record)
        if slippage > self.warning_threshold_pct:
            logger.warning(
                f"⚠️  ENTRY SLIPPAGE: {slippage:.2%} | "
                f"Expected ${expected_price:.4f}, Actual ${actual_price:.4f}"
            )

    def record_exit(self, expected_price: float, actual_price: float, size: float) -> None:
        """Record an exit fill."""
        slippage = abs(actual_price - expected_price) / expected_price if expected_price > 0 else 0.0
        record = {
            "expected": expected_price,
            "actual": actual_price,
            "slippage_pct": slippage,
            "size": size,
            "timestamp": time.time(),
        }
        self._exits.append(record)
        if slippage > self.warning_threshold_pct:
            logger.warning(
                f"⚠️  EXIT SLIPPAGE: {slippage:.2%} | "
                f"Expected ${expected_price:.4f}, Actual ${actual_price:.4f}"
            )

    def get_avg_slippage(self) -> Dict[str, Optional[float]]:
        """Get average entry and exit slippage."""
        avg_entry = (
            sum(e["slippage_pct"] for e in self._entries) / len(self._entries)
            if self._entries else None
        )
        avg_exit = (
            sum(e["slippage_pct"] for e in self._exits) / len(self._exits)
            if self._exits else None
        )
        return {"entry": avg_entry, "exit": avg_exit}


class CircuitBreakerPanel:
    """Panel that manages all circuit breakers."""

    def __init__(
        self,
        rpc_latency_ms: float = 500.0,
        ws_disconnect_seconds: float = 5.0,
        max_consecutive_losses: int = 3,
        min_win_rate: float = 0.65,
        win_rate_window: int = 100,
        max_daily_loss_pct: float = 0.05,
        slippage_warning_pct: float = 0.01,
    ):
        self.rpc_breaker = RPCLatencyBreaker(max_latency_ms=rpc_latency_ms)
        self.ws_breaker = WebSocketHealthBreaker(max_disconnect_seconds=ws_disconnect_seconds)
        self.loss_breaker = ConsecutiveLossBreaker(max_consecutive_losses=max_consecutive_losses)
        self.win_rate_breaker = WinRateBreaker(
            min_win_rate=min_win_rate,
            window_size=win_rate_window
        )
        self.daily_loss_breaker = DailyLossBreaker(max_daily_loss_pct=max_daily_loss_pct)
        self.slippage_monitor = SlippageMonitor(warning_threshold_pct=slippage_warning_pct)

        self._breakers: List[BaseCircuitBreaker] = [
            self.rpc_breaker,
            self.ws_breaker,
            self.loss_breaker,
            self.win_rate_breaker,
            self.daily_loss_breaker,
        ]

    def check_all(self) -> tuple[bool, Optional[str]]:
        """
        Check all breakers.

        Returns:
            (ok, reason) — ok=True if none tripped, ok=False with reason if any tripped.
        """
        for breaker in self._breakers:
            breaker.check()
            if breaker.is_tripped:
                return False, f"{breaker.name}: {breaker.reason}"
        return True, "OK"

    def any_tripped(self) -> bool:
        """Return True if any breaker is tripped."""
        return any(b.is_tripped for b in self._breakers)

    def reset_all(self) -> None:
        """Reset all breakers."""
        for breaker in self._breakers:
            breaker.reset()

    def record_trade(self, pnl: float) -> None:
        """Feed trade result to all relevant breakers."""
        self.loss_breaker.record_trade(pnl)
        self.win_rate_breaker.record_trade(pnl)
        self.daily_loss_breaker.record_pnl(pnl)

    def set_bankroll(self, bankroll: float) -> None:
        """Set bankroll for daily loss breaker."""
        self.daily_loss_breaker.set_bankroll(bankroll)

    def get_status(self) -> Dict[str, Dict]:
        """Get status of all breakers."""
        return {
            b.name: {
                "tripped": b.is_tripped,
                "reason": b.reason,
                "timestamp": b.trip_timestamp,
            }
            for b in self._breakers
        }
