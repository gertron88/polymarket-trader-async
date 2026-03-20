"""
Kelly Criterion Position Sizing Module

Implements the Kelly criterion for optimal position sizing with safety features:
- Half-Kelly default to reduce variance
- Minimum trade requirement before using Kelly
- Maximum position cap (25% of bankroll)
- Confidence-based sizing multiplier
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from statistics import mean
import json


@dataclass
class Trade:
    """Represents a single trade result."""
    pnl: float  # Profit/loss in dollars (positive = win, negative = loss)


class KellySizer:
    """
    Position sizer using the Kelly criterion with safety adjustments.
    
    The Kelly criterion calculates the optimal fraction of bankroll to bet
    based on historical win rate and payoff ratio.
    
    Formula: Kelly = (bp - q) / b
    where:
        b = average win / average loss (payoff ratio)
        p = win rate (probability of win)
        q = 1 - p (probability of loss)
    
    Safety features:
        - Half-Kelly by default (reduces variance by 50%)
        - Minimum 10 trades required before Kelly calculation
        - Maximum 25% of bankroll per position
        - Confidence multiplier (0.5-1.0) for discretionary sizing
    
    Args:
        bankroll: Total trading capital in dollars
        kelly_fraction: Fraction of Kelly to use (default 0.5 = Half-Kelly)
        min_trades_for_kelly: Minimum trades before using Kelly (default 10)
        max_position_pct: Maximum position size as fraction of bankroll (default 0.25)
        daily_loss_limit: Maximum daily loss before stopping (default -0.05 = -5%)
    """
    
    def __init__(
        self,
        bankroll: float,
        kelly_fraction: float = 0.5,
        min_trades_for_kelly: int = 10,
        max_position_pct: float = 0.25,
        daily_loss_limit: float = -0.05
    ):
        self.initial_bankroll = bankroll
        self.bankroll = bankroll
        self.kelly_fraction = max(0.0, min(1.0, kelly_fraction))
        self.min_trades_for_kelly = max(1, min_trades_for_kelly)
        self.max_position_pct = max(0.0, min(1.0, max_position_pct))
        self.daily_loss_limit = daily_loss_limit
        self.trades: List[Trade] = []
        self._current_daily_pnl = 0.0
    
    def update_trade(self, pnl: float) -> None:
        """
        Record a trade result and update bankroll.
        
        Args:
            pnl: Profit/loss from the trade in dollars
                 (positive = win, negative = loss)
        
        Example:
            >>> sizer = KellySizer(bankroll=1000)
            >>> sizer.update_trade(50)   # Winning trade
            >>> sizer.update_trade(-30)  # Losing trade
        """
        self.trades.append(Trade(pnl=pnl))
        self.bankroll += pnl
        self._current_daily_pnl += pnl
    
    def get_stats(self) -> Dict[str, float]:
        """
        Calculate and return trading statistics.
        
        Returns:
            Dictionary containing:
                - win_rate: Percentage of winning trades (0.0-1.0)
                - avg_win: Average winning trade amount
                - avg_loss: Average losing trade amount (positive value)
                - total_pnl: Cumulative profit/loss
                - total_trades: Number of trades taken
                - winning_trades: Count of winning trades
                - losing_trades: Count of losing trades
                - profit_factor: Gross profits / Gross losses
                - current_bankroll: Current bankroll after all trades
                - return_pct: Return percentage since inception
        
        Example:
            >>> sizer = KellySizer(bankroll=1000)
            >>> sizer.update_trade(50)
            >>> sizer.update_trade(-30)
            >>> stats = sizer.get_stats()
            >>> print(f"Win rate: {stats['win_rate']:.1%}")
        """
        if not self.trades:
            return {
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'total_pnl': 0.0,
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'profit_factor': 0.0,
                'current_bankroll': self.bankroll,
                'return_pct': 0.0
            }
        
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        
        total_pnl = sum(t.pnl for t in self.trades)
        
        avg_win = mean(wins) if wins else 0.0
        avg_loss = abs(mean(losses)) if losses else 0.0  # Return positive value
        
        win_rate = len(wins) / len(self.trades) if self.trades else 0.0
        
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return_pct = (self.bankroll - self.initial_bankroll) / self.initial_bankroll
        
        return {
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'total_pnl': total_pnl,
            'total_trades': len(self.trades),
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'profit_factor': profit_factor,
            'current_bankroll': self.bankroll,
            'return_pct': return_pct
        }
    
    def calculate_size(self, confidence: float = 1.0) -> float:
        """
        Calculate position size using the Kelly criterion.
        
        The Kelly formula determines the optimal fraction of bankroll to bet:
        Kelly = (bp - q) / b
        
        Where:
            b = avg_win / avg_loss (payoff ratio, aka "odds")
            p = win_rate (probability of winning)
            q = 1 - p (probability of losing)
        
        Safety adjustments applied:
            1. Use half-Kelly (or configured fraction) to reduce variance
            2. Cap at maximum position percentage (default 25%)
            3. Scale by confidence multiplier (0.5-1.0)
            4. Fall back to conservative sizing if insufficient history
        
        Args:
            confidence: Confidence multiplier for this trade (0.5-1.0)
                       1.0 = Full Kelly sizing
                       0.5 = Half the calculated size
        
        Returns:
            Position size in dollars
        
        Example:
            >>> sizer = KellySizer(bankroll=1000)
            >>> # Simulate some trade history
            >>> for _ in range(10):
            ...     sizer.update_trade(20)
            >>> size = sizer.calculate_size(confidence=0.8)
            >>> print(f"Recommended position: ${size:.2f}")
        """
        # Clamp confidence to valid range
        confidence = max(0.5, min(1.0, confidence))
        
        # Maximum position cap
        max_position = self.bankroll * self.max_position_pct
        
        # If insufficient trade history, use conservative default
        if len(self.trades) < self.min_trades_for_kelly:
            # Conservative: 1% of bankroll until we have data
            conservative_size = self.bankroll * 0.01
            return min(conservative_size, max_position)
        
        stats = self.get_stats()
        win_rate = stats['win_rate']
        avg_win = stats['avg_win']
        avg_loss = stats['avg_loss']
        
        # Handle edge cases
        if avg_loss == 0:
            # No losses yet - be conservative
            return min(self.bankroll * 0.01, max_position)
        
        if win_rate == 0:
            # No wins - don't trade
            return 0.0
        
        # Calculate Kelly components
        b = avg_win / avg_loss  # Payoff ratio (odds)
        p = win_rate
        q = 1 - p
        
        # Kelly formula: (bp - q) / b
        kelly_fraction_raw = (b * p - q) / b
        
        # Kelly fraction can be negative (don't bet) or >1 (aggressive)
        if kelly_fraction_raw <= 0:
            return 0.0  # Edge case: negative expected value
        
        # Apply safety fraction (half-Kelly default)
        adjusted_kelly = kelly_fraction_raw * self.kelly_fraction
        
        # Calculate position size
        position_size = self.bankroll * adjusted_kelly
        
        # Apply confidence multiplier
        position_size *= confidence
        
        # Cap at maximum position size
        position_size = min(position_size, max_position)
        
        return position_size
    
    def should_trade(self, daily_pnl: Optional[float] = None) -> bool:
        """
        Check if trading should continue based on risk limits.
        
        Evaluates:
            1. Daily loss limit reached
            2. Bankroll depleted below minimum threshold
        
        Args:
            daily_pnl: Current day's P&L. If None, uses internally tracked value.
        
        Returns:
            True if trading is allowed, False if limits hit
        
        Example:
            >>> sizer = KellySizer(bankroll=1000, daily_loss_limit=-0.05)
            >>> sizer.should_trade()  # True initially
            True
            >>> # After hitting daily limit
            >>> sizer.should_trade(daily_pnl=-60)
            False
        """
        # Use provided daily_pnl or internal tracker
        pnl = daily_pnl if daily_pnl is not None else self._current_daily_pnl
        
        # Check daily loss limit
        daily_loss_pct = pnl / self.initial_bankroll if self.initial_bankroll > 0 else 0
        if daily_loss_pct <= self.daily_loss_limit:
            return False
        
        # Check minimum bankroll (don't trade below 10% of initial)
        if self.bankroll < self.initial_bankroll * 0.1:
            return False
        
        return True
    
    def reset_daily_pnl(self) -> None:
        """Reset the daily P&L tracker (call at start of each trading day)."""
        self._current_daily_pnl = 0.0
    
    def to_dict(self) -> Dict:
        """Serialize sizer state to dictionary."""
        return {
            'initial_bankroll': self.initial_bankroll,
            'current_bankroll': self.bankroll,
            'kelly_fraction': self.kelly_fraction,
            'min_trades_for_kelly': self.min_trades_for_kelly,
            'max_position_pct': self.max_position_pct,
            'daily_loss_limit': self.daily_loss_limit,
            'trades': [{'pnl': t.pnl} for t in self.trades],
            'current_daily_pnl': self._current_daily_pnl
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'KellySizer':
        """Restore sizer from dictionary."""
        sizer = cls(
            bankroll=data['initial_bankroll'],
            kelly_fraction=data['kelly_fraction'],
            min_trades_for_kelly=data['min_trades_for_kelly'],
            max_position_pct=data['max_position_pct'],
            daily_loss_limit=data['daily_loss_limit']
        )
        sizer.bankroll = data['current_bankroll']
        sizer._current_daily_pnl = data.get('current_daily_pnl', 0.0)
        for trade_data in data.get('trades', []):
            sizer.trades.append(Trade(pnl=trade_data['pnl']))
        return sizer
    
    def save(self, filepath: str) -> None:
        """Save sizer state to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, filepath: str) -> 'KellySizer':
        """Load sizer state from JSON file."""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)


# Example usage and calculations
def example():
    """
    Demonstrate KellySizer with example calculations.
    """
    print("=" * 60)
    print("Kelly Criterion Position Sizing Example")
    print("=" * 60)
    
    # Initialize with $1000 bankroll, half-Kelly default
    sizer = KellySizer(bankroll=1000.0, kelly_fraction=0.5)
    
    print(f"\nInitial bankroll: ${sizer.bankroll:.2f}")
    print(f"Kelly fraction: {sizer.kelly_fraction} (Half-Kelly)")
    print(f"Min trades for Kelly: {sizer.min_trades_for_kelly}")
    print(f"Max position: {sizer.max_position_pct:.0%} of bankroll")
    
    # Before enough trades - uses conservative sizing
    print("\n--- Phase 1: Insufficient History ---")
    size = sizer.calculate_size(confidence=1.0)
    print(f"Position size (5 trades, confidence=1.0): ${size:.2f}")
    print("(Using conservative 1% default - need 10+ trades for Kelly)")
    
    # Simulate trade history (10 wins, 5 losses)
    print("\n--- Phase 2: Building Trade History ---")
    wins = [25, 30, 20, 35, 28, 22, 32, 26, 24, 31]  # 10 wins
    losses = [-15, -18, -12, -20, -16]  # 5 losses
    
    for pnl in wins + losses:
        sizer.update_trade(pnl)
    
    stats = sizer.get_stats()
    print(f"\nTrade History:")
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Wins: {stats['winning_trades']}")
    print(f"  Losses: {stats['losing_trades']}")
    print(f"  Win rate: {stats['win_rate']:.1%}")
    print(f"  Average win: ${stats['avg_win']:.2f}")
    print(f"  Average loss: ${stats['avg_loss']:.2f}")
    print(f"  Total P&L: ${stats['total_pnl']:.2f}")
    print(f"  Current bankroll: ${stats['current_bankroll']:.2f}")
    
    # Calculate Kelly
    print("\n--- Phase 3: Kelly Calculation ---")
    b = stats['avg_win'] / stats['avg_loss']  # Payoff ratio
    p = stats['win_rate']
    q = 1 - p
    
    print(f"\nKelly Formula:")
    print(f"  b (payoff ratio) = {b:.3f}")
    print(f"  p (win rate) = {p:.3f}")
    print(f"  q (loss rate) = {q:.3f}")
    print(f"\n  Full Kelly = (bp - q) / b")
    print(f"             = ({b:.3f} × {p:.3f} - {q:.3f}) / {b:.3f}")
    
    full_kelly = (b * p - q) / b
    print(f"             = {full_kelly:.3f} ({full_kelly:.1%})")
    
    print(f"\n  Half-Kelly = {full_kelly:.3f} × 0.5 = {full_kelly * 0.5:.3f}")
    
    # Position sizes at different confidence levels
    print("\n--- Phase 4: Position Sizes ---")
    for conf in [1.0, 0.8, 0.6, 0.5]:
        size = sizer.calculate_size(confidence=conf)
        pct = size / sizer.bankroll
        print(f"  Confidence {conf:.1f}: ${size:.2f} ({pct:.1%} of bankroll)")
    
    # Max position cap demonstration
    print("\n--- Phase 5: Safety Limits ---")
    print(f"Maximum position cap: {sizer.max_position_pct:.0%} = ${sizer.bankroll * sizer.max_position_pct:.2f}")
    
    # Daily loss limit
    print("\n--- Phase 6: Risk Management ---")
    sizer.reset_daily_pnl()
    print(f"Daily loss limit: {sizer.daily_loss_limit:.0%}")
    print(f"Should trade (daily_pnl=0): {sizer.should_trade()}")
    print(f"Should trade (daily_pnl=-30): {sizer.should_trade(-30)}")
    print(f"Should trade (daily_pnl=-60): {sizer.should_trade(-60)}")
    
    print("\n" + "=" * 60)
    print("Example complete!")
    print("=" * 60)
    
    return sizer


if __name__ == "__main__":
    example()
