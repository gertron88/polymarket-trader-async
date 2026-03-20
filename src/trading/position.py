"""
Position management module for tracking and managing trading positions.

Handles fill tracking, exit logic with dynamic stop losses, and P&L calculation.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict
from enum import Enum
import time


class PositionState(Enum):
    """Position lifecycle states."""
    PENDING = "pending"           # Orders created, waiting for fills
    PARTIAL_FILL = "partial"      # One side filled
    ACTIVE = "active"             # Both sides filled
    EXITING = "exiting"           # Exit orders in progress
    CLOSED = "closed"             # Position fully closed


@dataclass
class Position:
    """
    Represents a market-neutral position with both UP and DOWN tokens.
    
    Tracks fills, exits, and P&L for both sides independently.
    
    Attributes:
        window_ts: Window timestamp (unique position ID)
        up_token: UP token identifier
        down_token: DOWN token identifier
        up_id: UP order ID
        down_id: DOWN order ID
        up_filled: Whether UP order filled
        down_filled: Whether DOWN order filled
        up_exited: Whether UP position exited
        down_exited: Whether DOWN position exited
        up_exit_price: UP exit price (if exited)
        down_exit_price: DOWN exit price (if exited)
        entry_time: Position entry timestamp
        entry_price: Entry price for P&L calculation (default 0.46)
        size: Position size in USD (default 5.0)
        summary_sent: Whether summary has been sent
    """
    window_ts: int
    up_token: str
    down_token: str
    up_id: Optional[str] = None
    down_id: Optional[str] = None
    up_filled: bool = False
    down_filled: bool = False
    up_exited: bool = False
    down_exited: bool = False
    up_exit_price: float = 0.0
    down_exit_price: float = 0.0
    entry_time: float = 0.0
    entry_price: float = 0.46
    size: float = 5.0
    summary_sent: bool = False
    
    # Internal state tracking
    up_entry_price: float = 0.0
    down_entry_price: float = 0.0
    
    def __post_init__(self):
        """Set entry time if not provided."""
        if self.entry_time == 0.0:
            self.entry_time = time.time()
    
    @property
    def is_fully_filled(self) -> bool:
        """Check if both sides are filled."""
        return self.up_filled and self.down_filled
    
    @property
    def is_exited(self) -> bool:
        """Check if position is fully exited."""
        return self.up_exited and self.down_exited
    
    @property
    def is_partially_exited(self) -> bool:
        """Check if position is partially exited."""
        return self.up_exited != self.down_exited
    
    @property
    def state(self) -> PositionState:
        """Determine current position state."""
        if self.is_exited:
            return PositionState.CLOSED
        elif self.up_exited or self.down_exited:
            return PositionState.EXITING
        elif self.is_fully_filled:
            return PositionState.ACTIVE
        elif self.up_filled or self.down_filled:
            return PositionState.PARTIAL_FILL
        else:
            return PositionState.PENDING
    
    def to_dict(self) -> Dict:
        """Convert position to dictionary for serialization."""
        return {
            "window_ts": self.window_ts,
            "up_token": self.up_token,
            "down_token": self.down_token,
            "up_id": self.up_id,
            "down_id": self.down_id,
            "up_filled": self.up_filled,
            "down_filled": self.down_filled,
            "up_exited": self.up_exited,
            "down_exited": self.down_exited,
            "up_exit_price": self.up_exit_price,
            "down_exit_price": self.down_exit_price,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "size": self.size,
            "summary_sent": self.summary_sent,
            "up_entry_price": self.up_entry_price,
            "down_entry_price": self.down_entry_price,
            "state": self.state.value
        }


class PositionManager:
    """
    Manages a collection of positions with lifecycle tracking.
    
    Handles position creation, fill updates, exit calculations,
    and P&L tracking for market-neutral strategies.
    
    Attributes:
        positions: Dictionary mapping window_ts to Position objects
    """
    
    def __init__(self):
        """Initialize empty position manager."""
        self.positions: Dict[int, Position] = {}
    
    def create_position(
        self,
        window_ts: int,
        up_token: str,
        down_token: str,
        size: float = 5.0,
        entry_price: float = 0.46
    ) -> Position:
        """
        Create a new position for a trading window.
        
        Args:
            window_ts: Window timestamp (used as position ID)
            up_token: UP token identifier
            down_token: DOWN token identifier
            size: Position size in USD
            entry_price: Entry price for P&L calculation
            
        Returns:
            New Position instance
            
        Raises:
            ValueError: If position for window_ts already exists
        """
        if window_ts in self.positions:
            raise ValueError(f"Position already exists for window {window_ts}")
        
        position = Position(
            window_ts=window_ts,
            up_token=up_token,
            down_token=down_token,
            size=size,
            entry_price=entry_price,
            entry_time=time.time()
        )
        self.positions[window_ts] = position
        return position
    
    def get_position(self, window_ts: int) -> Optional[Position]:
        """
        Get position by window timestamp.
        
        Args:
            window_ts: Window timestamp
            
        Returns:
            Position if found, None otherwise
        """
        return self.positions.get(window_ts)
    
    def update_fill(
        self,
        window_ts: int,
        side: str,
        filled: bool,
        fill_price: float = 0.0
    ) -> bool:
        """
        Update fill status for a position side.
        
        Args:
            window_ts: Window timestamp
            side: "up" or "down"
            filled: New fill status
            fill_price: Fill price if filled (optional)
            
        Returns:
            True if update successful, False if position not found
            
        Raises:
            ValueError: If side is not "up" or "down"
        """
        position = self.positions.get(window_ts)
        if not position:
            return False
        
        side = side.lower()
        if side == "up":
            position.up_filled = filled
            if filled and fill_price > 0:
                position.up_entry_price = fill_price
        elif side == "down":
            position.down_filled = filled
            if filled and fill_price > 0:
                position.down_entry_price = fill_price
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'up' or 'down'")
        
        return True
    
    def update_order_id(
        self,
        window_ts: int,
        side: str,
        order_id: str
    ) -> bool:
        """
        Update order ID for a position side.
        
        Args:
            window_ts: Window timestamp
            side: "up" or "down"
            order_id: Order ID to set
            
        Returns:
            True if update successful, False if position not found
            
        Raises:
            ValueError: If side is not "up" or "down"
        """
        position = self.positions.get(window_ts)
        if not position:
            return False
        
        side = side.lower()
        if side == "up":
            position.up_id = order_id
        elif side == "down":
            position.down_id = order_id
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'up' or 'down'")
        
        return True
    
    def update_exit(
        self,
        window_ts: int,
        side: str,
        exit_price: float
    ) -> bool:
        """
        Record exit for a position side.
        
        Args:
            window_ts: Window timestamp
            side: "up" or "down"
            exit_price: Price at which position was exited
            
        Returns:
            True if update successful, False if position not found
        """
        position = self.positions.get(window_ts)
        if not position:
            return False
        
        side = side.lower()
        if side == "up":
            position.up_exited = True
            position.up_exit_price = exit_price
        elif side == "down":
            position.down_exited = True
            position.down_exit_price = exit_price
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'up' or 'down'")
        
        return True
    
    def calculate_exit_price(
        self,
        position: Position,
        time_remaining: float,
        window_duration: float = 300.0  # 5 minutes default
    ) -> Tuple[float, float]:
        """
        Calculate dynamic stop loss prices based on time remaining.
        
        As time progresses, stops widen from tight (early) to wide (late).
        Early exits prioritize capital preservation; late exits maximize
        probability of profit.
        
        Args:
            position: Position to calculate exits for
            time_remaining: Seconds remaining in window
            window_duration: Total window duration in seconds
            
        Returns:
            Tuple of (up_exit_price, down_exit_price)
        """
        # Calculate progress ratio (0 = start, 1 = end)
        progress = 1.0 - (time_remaining / window_duration)
        progress = max(0.0, min(1.0, progress))  # Clamp to [0, 1]
        
        # Dynamic stop range:
        # Early (0%): tight stop at 0.35
        # Late (100%): wide stop at 0.25
        early_stop = 0.35
        late_stop = 0.25
        
        # Linear interpolation between early and late stops
        stop_threshold = early_stop + (late_stop - early_stop) * progress
        
        # Calculate exit prices from entry price
        # UP side: exit if price drops to threshold
        # DOWN side: exit if price rises to threshold
        up_exit_price = stop_threshold
        down_exit_price = 1.0 - stop_threshold
        
        return (up_exit_price, down_exit_price)
    
    def should_exit(
        self,
        position: Position,
        up_price: float,
        down_price: float,
        time_remaining: float,
        window_duration: float = 300.0
    ) -> Tuple[bool, bool]:
        """
        Determine if position sides should be exited based on current prices.
        
        Args:
            position: Position to evaluate
            up_price: Current UP token price
            down_price: Current DOWN token price
            time_remaining: Seconds remaining in window
            window_duration: Total window duration in seconds
            
        Returns:
            Tuple of (should_exit_up, should_exit_down)
        """
        if not position.is_fully_filled or position.is_exited:
            return (False, False)
        
        up_exit_price, down_exit_price = self.calculate_exit_price(
            position, time_remaining, window_duration
        )
        
        should_exit_up = up_price <= up_exit_price and not position.up_exited
        should_exit_down = down_price >= down_exit_price and not position.down_exited
        
        return (should_exit_up, should_exit_down)
    
    def calculate_pnl(self, position: Position) -> float:
        """
        Calculate current P&L for a position.
        
        For active positions, estimates P&L based on entry price vs current
        theoretical midpoint. For closed positions, uses actual exit prices.
        
        Args:
            position: Position to calculate P&L for
            
        Returns:
            Estimated or realized P&L in USD
        """
        if not position.is_fully_filled:
            return 0.0
        
        # Determine exit prices
        if position.is_exited:
            # Use actual exit prices
            up_exit = position.up_exit_price if position.up_exited else position.entry_price
            down_exit = position.down_exit_price if position.down_exited else position.entry_price
        else:
            # For active positions, use entry price as theoretical
            up_exit = position.entry_price
            down_exit = position.entry_price
        
        # Calculate P&L per side
        # P&L = (exit_price - entry_price) * size
        # UP: profit if exit > entry
        # DOWN: profit if exit < entry (but price is inverted, so exit > entry means loss)
        
        up_pnl = (up_exit - position.entry_price) * position.size
        down_pnl = ((1.0 - down_exit) - (1.0 - position.entry_price)) * position.size
        down_pnl = -down_pnl  # Invert for DOWN token
        
        total_pnl = up_pnl + down_pnl
        
        return total_pnl
    
    def calculate_realized_pnl(self, position: Position) -> float:
        """
        Calculate realized P&L for a closed position.
        
        Args:
            position: Position to calculate P&L for
            
        Returns:
            Realized P&L in USD (0.0 if position not closed)
        """
        if not position.is_exited:
            return 0.0
        
        up_pnl = 0.0
        down_pnl = 0.0
        
        if position.up_exited:
            up_pnl = (position.up_exit_price - position.entry_price) * position.size
        
        if position.down_exited:
            # DOWN profit when price drops (exit < entry)
            down_pnl = (position.entry_price - position.down_exit_price) * position.size
        
        return up_pnl + down_pnl
    
    def get_active_positions(self) -> list[Position]:
        """Get all active (fully filled, not exited) positions."""
        return [
            p for p in self.positions.values()
            if p.is_fully_filled and not p.is_exited
        ]
    
    def get_pending_positions(self) -> list[Position]:
        """Get all pending (not fully filled) positions."""
        return [
            p for p in self.positions.values()
            if not p.is_fully_filled
        ]
    
    def get_closed_positions(self) -> list[Position]:
        """Get all closed positions."""
        return [
            p for p in self.positions.values()
            if p.is_exited
        ]
    
    def mark_summary_sent(self, window_ts: int) -> bool:
        """
        Mark that summary has been sent for a position.
        
        Args:
            window_ts: Window timestamp
            
        Returns:
            True if successful, False if position not found
        """
        position = self.positions.get(window_ts)
        if position:
            position.summary_sent = True
            return True
        return False
    
    def remove_position(self, window_ts: int) -> bool:
        """
        Remove a position from tracking.
        
        Args:
            window_ts: Window timestamp
            
        Returns:
            True if removed, False if not found
        """
        if window_ts in self.positions:
            del self.positions[window_ts]
            return True
        return False
    
    def clear_closed_positions(self) -> int:
        """
        Remove all closed positions from tracking.
        
        Returns:
            Number of positions removed
        """
        closed = self.get_closed_positions()
        for position in closed:
            del self.positions[position.window_ts]
        return len(closed)
    
    def get_stats(self) -> Dict:
        """Get summary statistics of all positions."""
        total = len(self.positions)
        active = len(self.get_active_positions())
        pending = len(self.get_pending_positions())
        closed = len(self.get_closed_positions())
        
        total_pnl = sum(
            self.calculate_realized_pnl(p) for p in self.get_closed_positions()
        )
        
        return {
            "total_positions": total,
            "active": active,
            "pending": pending,
            "closed": closed,
            "total_realized_pnl": total_pnl
        }
