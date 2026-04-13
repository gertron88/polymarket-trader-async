"""
Intra-Window Exit Strategy

Implements micro-arbitrage exits within the 5-minute prediction window.
Instead of holding to expiration, exit when Polymarket catches up to the BTC signal.

Strategy:
    1. Enter position on BTC signal (e.g., BTC up 0.5%)
    2. Monitor Polymarket price convergence
    3. Exit when PM price reflects BTC move (typically 100-500ms)
    4. Profit from the convergence, not the expiration

Why This Works:
    - BTC moves on Coinbase (t=0)
    - Polymarket lags by 100-500ms due to slower participants
    - We buy at 0.46 when PM hasn't moved yet
    - PM catches up to 0.52-0.55
    - We sell at profit within seconds, not minutes

Benefits:
    - Lower risk (not exposed to 5-min price reversals)
    - Higher frequency (multiple trades per window)
    - Compounding (faster capital turnover)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class ExitTrigger(Enum):
    """Reason for intra-window exit."""
    PRICE_CONVERGENCE = "price_convergence"
    TIME_DECAY = "time_decay"
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    MAX_HOLD_TIME = "max_hold_time"


@dataclass
class ConvergenceSignal:
    """Signal that Polymarket has converged to BTC price."""
    triggered: bool
    pm_price: float
    expected_price: float
    convergence_ratio: float  # 0.0 to 1.0
    confidence: float


class IntraWindowExitManager:
    """
    Manages exits within the 5-minute prediction window.
    
    Monitors Polymarket price convergence and exits when:
    - Price has converged to BTC signal (optimal)
    - Profit target hit
    - Stop loss hit
    - Time decay too strong
    
    Usage:
        exit_mgr = IntraWindowExitManager(
            entry_price=0.46,
            side="UP",
            btc_signal_strength=0.008,  # 0.8% move
        )
        
        # In your main loop
        should_exit, reason = exit_mgr.check_exit(
            current_pm_price=0.52,
            time_elapsed_ms=250,
            btc_price_change=0.008
        )
        
        if should_exit:
            await place_exit_order()
    """
    
    def __init__(
        self,
        entry_price: float,
        side: str,  # 'UP' or 'DOWN'
        btc_signal_strength: float,  # e.g., 0.008 for 0.8%
        convergence_threshold: float = 0.80,  # Exit at 80% convergence
        profit_target: float = 0.08,  # 8% profit (0.46 -> 0.50)
        stop_loss: float = 0.05,  # 5% loss
        max_hold_ms: float = 2000,  # 2 seconds max (intra-window!)
        time_decay_factor: float = 0.5,  # How fast to exit on time
    ):
        """
        Initialize intra-window exit manager.
        
        Args:
            entry_price: Entry price (e.g., 0.46)
            side: Position side ('UP' or 'DOWN')
            btc_signal_strength: Strength of BTC signal (0.005 = 0.5%)
            convergence_threshold: Exit when PM reaches X% of expected move
            profit_target: Exit at this profit level
            stop_loss: Exit at this loss level
            max_hold_ms: Maximum hold time (intra-window, not 5 min!)
            time_decay_factor: How aggressively to exit on time
        """
        self.entry_price = entry_price
        self.side = side
        self.btc_signal_strength = btc_signal_strength
        self.convergence_threshold = convergence_threshold
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.max_hold_ms = max_hold_ms
        self.time_decay_factor = time_decay_factor
        
        self.entry_time = time.time()
        self.exit_triggered = False
        self.exit_reason: Optional[ExitTrigger] = None
        
        # Calculate expected PM price based on BTC signal
        # Typical: 0.5% BTC move = 0.08-0.10 PM price move
        self.expected_price_move = self._estimate_pm_move(btc_signal_strength)
        self.expected_exit_price = entry_price + self.expected_price_move
        
        logger.info(
            f"IntraWindowExit initialized: entry={entry_price}, "
            f"expected_exit={self.expected_exit_price:.3f}, "
            f"max_hold={max_hold_ms}ms"
        )
    
    def _estimate_pm_move(self, btc_change: float) -> float:
        """
        Estimate Polymarket price move from BTC change.
        
        Empirical: 0.5% BTC move ≈ 0.08-0.10 PM price move
        """
        # Scale factor: PM moves ~16-20x the BTC percentage
        # 0.5% BTC = 0.08 PM (16x)
        # 1.0% BTC = 0.18 PM (18x)
        scale_factor = 16.0 + (abs(btc_change) * 200)  # 16-20x range
        return btc_change * scale_factor
    
    def check_exit(
        self,
        current_pm_price: float,
        time_elapsed_ms: float,
        btc_price_change: float
    ) -> tuple[bool, Optional[ExitTrigger], float]:
        """
        Check if position should be exited.
        
        Args:
            current_pm_price: Current Polymarket price
            time_elapsed_ms: Time since entry (milliseconds)
            btc_price_change: Current BTC price change
            
        Returns:
            (should_exit, reason, target_price)
        """
        if self.exit_triggered:
            return True, self.exit_reason, 0.0
        
        # Calculate current P&L
        if self.side == "UP":
            pnl_pct = (current_pm_price - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - current_pm_price) / self.entry_price
        
        # Check 1: Profit target hit
        if pnl_pct >= self.profit_target:
            self.exit_triggered = True
            self.exit_reason = ExitTrigger.PROFIT_TARGET
            logger.info(f"Profit target hit: {pnl_pct:.2%}")
            return True, ExitTrigger.PROFIT_TARGET, current_pm_price
        
        # Check 2: Stop loss hit
        if pnl_pct <= -self.stop_loss:
            self.exit_triggered = True
            self.exit_reason = ExitTrigger.STOP_LOSS
            logger.warning(f"Stop loss hit: {pnl_pct:.2%}")
            return True, ExitTrigger.STOP_LOSS, current_pm_price
        
        # Check 3: Price convergence (THE MAIN STRATEGY)
        convergence = self._check_convergence(current_pm_price, btc_price_change)
        if convergence.triggered:
            self.exit_triggered = True
            self.exit_reason = ExitTrigger.PRICE_CONVERGENCE
            logger.info(
                f"Price convergence: {convergence.convergence_ratio:.1%} "
                f"at {current_pm_price:.3f}"
            )
            return True, ExitTrigger.PRICE_CONVERGENCE, current_pm_price
        
        # Check 4: Time-based decay (aggressive intra-window)
        if time_elapsed_ms > self.max_hold_ms:
            self.exit_triggered = True
            self.exit_reason = ExitTrigger.MAX_HOLD_TIME
            logger.info(f"Max hold time reached: {time_elapsed_ms:.0f}ms")
            return True, ExitTrigger.MAX_HOLD_TIME, current_pm_price
        
        # Check 5: Time decay affecting profit
        time_pressure = self._calculate_time_pressure(time_elapsed_ms, pnl_pct)
        if time_pressure > 0.7:  # 70% time pressure
            self.exit_triggered = True
            self.exit_reason = ExitTrigger.TIME_DECAY
            logger.info(f"Time decay exit: pressure={time_pressure:.1%}")
            return True, ExitTrigger.TIME_DECAY, current_pm_price
        
        return False, None, self.expected_exit_price
    
    def _check_convergence(
        self,
        current_pm_price: float,
        btc_price_change: float
    ) -> ConvergenceSignal:
        """
        Check if Polymarket has converged to BTC price.
        
        This is the core micro-arbitrage signal.
        """
        # Recalculate expected price based on current BTC
        expected_move = self._estimate_pm_move(btc_price_change)
        
        if self.side == "UP":
            expected_price = self.entry_price + expected_move
            actual_move = current_pm_price - self.entry_price
        else:
            expected_price = self.entry_price - expected_move
            actual_move = self.entry_price - current_pm_price
        
        # Calculate convergence ratio
        if expected_move <= 0:
            return ConvergenceSignal(False, current_pm_price, expected_price, 0.0, 0.0)
        
        convergence_ratio = actual_move / expected_move
        
        # Confidence based on how close to expectation
        confidence = min(convergence_ratio / self.convergence_threshold, 1.0)
        
        triggered = convergence_ratio >= self.convergence_threshold
        
        return ConvergenceSignal(
            triggered=triggered,
            pm_price=current_pm_price,
            expected_price=expected_price,
            convergence_ratio=convergence_ratio,
            confidence=confidence
        )
    
    def _calculate_time_pressure(self, time_elapsed_ms: float, pnl_pct: float) -> float:
        """
        Calculate time pressure - how urgently we should exit.
        
        Higher pressure = exit sooner.
        """
        # Normalize time (0.0 to 1.0)
        time_ratio = time_elapsed_ms / self.max_hold_ms
        
        # If profitable, less pressure
        if pnl_pct > 0:
            profit_relief = min(pnl_pct / self.profit_target, 1.0) * 0.3
            time_ratio -= profit_relief
        
        # If losing, more pressure
        if pnl_pct < 0:
            loss_pressure = min(abs(pnl_pct) / self.stop_loss, 1.0) * 0.3
            time_ratio += loss_pressure
        
        return max(0.0, min(time_ratio, 1.0))
    
    def get_status(self) -> dict:
        """Get current status of position."""
        time_elapsed = (time.time() - self.entry_time) * 1000
        
        return {
            'entry_price': self.entry_price,
            'expected_exit_price': self.expected_exit_price,
            'expected_price_move': self.expected_price_move,
            'time_elapsed_ms': time_elapsed,
            'max_hold_ms': self.max_hold_ms,
            'convergence_threshold': self.convergence_threshold,
            'exit_triggered': self.exit_triggered,
            'exit_reason': self.exit_reason.value if self.exit_reason else None,
        }


class IntraWindowStrategy:
    """
    High-level intra-window trading strategy.
    
    Combines entry detection with intra-window exits for micro-arbitrage.
    """
    
    def __init__(
        self,
        entry_callback: Callable,
        exit_callback: Callable,
        min_signal_strength: float = 0.005,  # 0.5%
        max_positions_per_window: int = 3
    ):
        """
        Initialize intra-window strategy.
        
        Args:
            entry_callback: Function to call for entry (side, size)
            exit_callback: Function to call for exit (price)
            min_signal_strength: Minimum BTC move to trigger
            max_positions_per_window: Max intra-window trades
        """
        self.entry_callback = entry_callback
        self.exit_callback = exit_callback
        self.min_signal_strength = min_signal_strength
        self.max_positions_per_window = max_positions_per_window
        
        self.active_exit_managers: dict = {}
        self.window_trade_count = 0
        self.window_start_time: Optional[float] = None
    
    def on_btc_signal(self, btc_change: float, pm_price: float) -> bool:
        """
        Process BTC signal and potentially enter position.
        
        Args:
            btc_change: BTC price change (e.g., 0.008 for 0.8%)
            pm_price: Current Polymarket price
            
        Returns:
            True if entry triggered
        """
        if abs(btc_change) < self.min_signal_strength:
            return False
        
        if self.window_trade_count >= self.max_positions_per_window:
            logger.debug("Max intra-window trades reached")
            return False
        
        side = "UP" if btc_change > 0 else "DOWN"
        
        # Create exit manager for this position
        exit_mgr = IntraWindowExitManager(
            entry_price=pm_price,
            side=side,
            btc_signal_strength=abs(btc_change)
        )
        
        position_id = f"{int(time.time() * 1000)}"
        self.active_exit_managers[position_id] = exit_mgr
        self.window_trade_count += 1
        
        # Trigger entry
        self.entry_callback(side, position_id)
        
        logger.info(
            f"Intra-window entry: {side} at {pm_price:.3f}, "
            f"expected exit {exit_mgr.expected_exit_price:.3f}"
        )
        
        return True
    
    def on_pm_update(self, position_id: str, pm_price: float, btc_change: float) -> bool:
        """
        Process Polymarket price update and check for exit.
        
        Args:
            position_id: Position identifier
            pm_price: Current Polymarket price
            btc_change: Current BTC price change
            
        Returns:
            True if exit triggered
        """
        if position_id not in self.active_exit_managers:
            return False
        
        exit_mgr = self.active_exit_managers[position_id]
        time_elapsed = (time.time() - exit_mgr.entry_time) * 1000
        
        should_exit, reason, target_price = exit_mgr.check_exit(
            current_pm_price=pm_price,
            time_elapsed_ms=time_elapsed,
            btc_price_change=btc_change
        )
        
        if should_exit:
            self.exit_callback(position_id, pm_price, reason)
            del self.active_exit_managers[position_id]
            return True
        
        return False
    
    def reset_window(self):
        """Reset for new 5-minute window."""
        self.window_trade_count = 0
        self.window_start_time = time.time()
        # Note: Don't clear active_exit_managers - let positions complete


# Example usage
async def example():
    """Example of intra-window strategy."""
    
    def on_entry(side: str, position_id: str):
        print(f"📈 ENTRY: {side} position {position_id}")
    
    def on_exit(position_id: str, price: float, reason: ExitTrigger):
        print(f"📉 EXIT: position {position_id} at {price:.3f} ({reason.value})")
    
    strategy = IntraWindowStrategy(on_entry, on_exit)
    
    # Simulate BTC signal
    strategy.on_btc_signal(btc_change=0.008, pm_price=0.46)
    
    # Simulate PM catching up
    await asyncio.sleep(0.1)
    strategy.on_pm_update("0", pm_price=0.48, btc_change=0.008)
    
    await asyncio.sleep(0.1)
    strategy.on_pm_update("0", pm_price=0.52, btc_change=0.008)  # Should exit


if __name__ == "__main__":
    asyncio.run(example())
