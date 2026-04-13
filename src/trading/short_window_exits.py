"""
Short-Window Exit Strategy

Replaces the impossible intra-window strategy with realistic short-window exits
that account for Polygon blockchain latency (2.3s block time).

Key realities:
- Minimum hold time: 2.5s (one Polygon block confirmation)
- Target hold time: 3-8s
- Realistic slippage: entry fills at 0.47-0.48, exits at 0.50-0.51
- Binary option delta is non-linear; PM doesn't move 1:1 with BTC
- No millisecond-level timing assumptions

Exit triggers:
1. Profit target hit (with realistic slippage)
2. Stop loss hit
3. Max hold time exceeded (8s)
4. Window ending soon (<30s remaining)
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ExitTrigger(Enum):
    """Reason for short-window exit."""
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    MAX_HOLD_TIME = "max_hold_time"
    WINDOW_ENDING = "window_ending"
    MIN_HOLD_NOT_MET = "min_hold_not_met"


@dataclass
class ExitCheckResult:
    """Result of a short-window exit check."""
    should_exit: bool
    trigger: Optional[ExitTrigger]
    exit_price: float
    reason: str


class ShortWindowExitManager:
    """
    Manages exits within a realistic short window (2.5s - 8s).

    Accounts for Polygon block time and realistic Polymarket slippage.
    """

    # Polygon reality: 2.3s block time
    MIN_HOLD_SECONDS: float = 2.5
    MAX_HOLD_SECONDS: float = 8.0
    WINDOW_END_THRESHOLD: float = 30.0  # Exit if window ends in 30s

    def __init__(
        self,
        entry_price: float,
        side: str,  # 'UP' or 'DOWN'
        profit_target_pct: float = 0.03,  # 3% net profit (realistic)
        stop_loss_pct: float = 0.10,      # 10% stop loss
    ):
        """
        Initialize short-window exit manager.

        Args:
            entry_price: Entry price (actual fill, likely 0.47-0.48)
            side: Position side ('UP' or 'DOWN')
            profit_target_pct: Realistic profit target after slippage
            stop_loss_pct: Stop loss percentage
        """
        self.entry_price = entry_price
        self.side = side.upper()
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct
        self.entry_time = time.time()
        self.exit_triggered = False
        self.trigger_reason: Optional[ExitTrigger] = None

        logger.info(
            f"ShortWindowExit initialized: side={side}, entry={entry_price:.3f}, "
            f"profit_target={profit_target_pct:.1%}, stop_loss={stop_loss_pct:.1%}, "
            f"min_hold={self.MIN_HOLD_SECONDS:.1f}s"
        )

    def check_exit(
        self,
        current_pm_price: float,
        window_time_remaining: float
    ) -> ExitCheckResult:
        """
        Check if position should be exited under short-window rules.

        Args:
            current_pm_price: Current Polymarket price
            window_time_remaining: Seconds remaining in the 5-min window

        Returns:
            ExitCheckResult with decision and reason
        """
        if self.exit_triggered:
            return ExitCheckResult(
                should_exit=True,
                trigger=self.trigger_reason,
                exit_price=current_pm_price,
                reason="Exit already triggered"
            )

        time_held = time.time() - self.entry_time

        # Rule 0: Minimum hold time (Polygon block confirmation)
        if time_held < self.MIN_HOLD_SECONDS:
            return ExitCheckResult(
                should_exit=False,
                trigger=ExitTrigger.MIN_HOLD_NOT_MET,
                exit_price=0.0,
                reason=f"Min hold not met: {time_held:.2f}s < {self.MIN_HOLD_SECONDS:.1f}s"
            )

        # Calculate P&L percentage
        if self.side == "UP":
            pnl_pct = (current_pm_price - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - current_pm_price) / self.entry_price

        # Rule 1: Profit target hit
        if pnl_pct >= self.profit_target_pct:
            self.exit_triggered = True
            self.trigger_reason = ExitTrigger.PROFIT_TARGET
            logger.info(f"Profit target hit: {pnl_pct:.2%} at {current_pm_price:.3f}")
            return ExitCheckResult(
                should_exit=True,
                trigger=ExitTrigger.PROFIT_TARGET,
                exit_price=current_pm_price,
                reason=f"Profit target: {pnl_pct:.2%} >= {self.profit_target_pct:.1%}"
            )

        # Rule 2: Stop loss hit
        if pnl_pct <= -self.stop_loss_pct:
            self.exit_triggered = True
            self.trigger_reason = ExitTrigger.STOP_LOSS
            logger.warning(f"Stop loss hit: {pnl_pct:.2%} at {current_pm_price:.3f}")
            return ExitCheckResult(
                should_exit=True,
                trigger=ExitTrigger.STOP_LOSS,
                exit_price=current_pm_price,
                reason=f"Stop loss: {pnl_pct:.2%} <= -{self.stop_loss_pct:.1%}"
            )

        # Rule 3: Max hold time
        if time_held >= self.MAX_HOLD_SECONDS:
            self.exit_triggered = True
            self.trigger_reason = ExitTrigger.MAX_HOLD_TIME
            logger.info(f"Max hold time reached: {time_held:.2f}s")
            return ExitCheckResult(
                should_exit=True,
                trigger=ExitTrigger.MAX_HOLD_TIME,
                exit_price=current_pm_price,
                reason=f"Max hold: {time_held:.2f}s >= {self.MAX_HOLD_SECONDS:.1f}s"
            )

        # Rule 4: Window ending soon
        if window_time_remaining <= self.WINDOW_END_THRESHOLD:
            self.exit_triggered = True
            self.trigger_reason = ExitTrigger.WINDOW_ENDING
            logger.info(f"Window ending soon: {window_time_remaining:.1f}s left")
            return ExitCheckResult(
                should_exit=True,
                trigger=ExitTrigger.WINDOW_ENDING,
                exit_price=current_pm_price,
                reason=f"Window ending: {window_time_remaining:.1f}s <= {self.WINDOW_END_THRESHOLD:.1f}s"
            )

        # No exit
        return ExitCheckResult(
            should_exit=False,
            trigger=None,
            exit_price=0.0,
            reason=f"Holding: {time_held:.2f}s, P&L {pnl_pct:.2%}"
        )

    def get_status(self) -> dict:
        """Get current status of position."""
        time_held = time.time() - self.entry_time
        return {
            'entry_price': self.entry_price,
            'side': self.side,
            'time_held_seconds': time_held,
            'min_hold_seconds': self.MIN_HOLD_SECONDS,
            'max_hold_seconds': self.MAX_HOLD_SECONDS,
            'profit_target_pct': self.profit_target_pct,
            'stop_loss_pct': self.stop_loss_pct,
            'exit_triggered': self.exit_triggered,
            'trigger_reason': self.trigger_reason.value if self.trigger_reason else None,
        }
