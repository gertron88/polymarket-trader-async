"""
Kelly Criterion Position Sizing Module

IMPLEMENTATION NOTES:
- Uses configurable slippage model to compute net profit from gross assumptions
- Kelly formula is bulletproof: Kelly = (bp - q) / b
- If Kelly <= 0, ALWAYS returns 0.0 (negative expectancy = no trade)
- Position size is based on actual net profit, not gross profit

The Kelly criterion calculates the optimal fraction of bankroll to bet
based on historical win rate and payoff ratio.

Formula: Kelly = (bp - q) / b
where:
    b = average win / average loss (payoff ratio)
    p = win rate (probability of win)
    q = 1 - p (probability of losing)
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from statistics import mean
import json


@dataclass
class Trade:
    """Represents a single trade result."""
    pnl: float  # Profit/loss in dollars (positive = win, negative = loss)


@dataclass
class SlippageModel:
    """
    Configurable slippage model for entry and exit assumptions.
    
    Attributes:
        entry_slip: Slippage on entry as a fraction (e.g., 0.01 = 1%)
        exit_slip: Slippage on exit as a fraction (e.g., 0.01 = 1%)
    """
    entry_slip: float = 0.01
    exit_slip: float = 0.01

    def apply_to_gross_profit(self, gross_profit_pct: float) -> float:
        """
        Calculate net profit after subtracting entry and exit slippage.
        
        Args:
            gross_profit_pct: Expected gross profit fraction before slippage
            
        Returns:
            Net profit fraction after slippage
        """
        total_slippage = self.entry_slip + self.exit_slip
        net_profit = gross_profit_pct - total_slippage
        return max(0.0, net_profit)


class KellySizer:
    """
    Position sizer using the Kelly criterion with configurable slippage.
    
    CRITICAL BEHAVIOR:
    1. Net profit is computed from gross assumptions minus configurable slippage
    2. Kelly = (bp - q) / b. If Kelly <= 0, position size is ALWAYS 0.0
    3. No magic constants — all slippage and profit assumptions are configurable
    """

    def __init__(
        self,
        bankroll: float,
        slippage_model: Optional[SlippageModel] = None,
        assumed_win_rate: float = 0.85,
        assumed_gross_profit_pct: float = 0.05,
        assumed_loss_pct: float = 0.10,
        kelly_fraction: float = 0.1,
        min_trades_for_kelly: int = 0,
        max_position_pct: float = 0.05,
        daily_loss_limit: float = -0.05,
        max_trades: int = 1,
        max_position_dollars: float = 5.0
    ):
        """
        Initialize the Kelly sizer.
        
        Args:
            bankroll: Starting bankroll in dollars
            slippage_model: Configurable slippage model (default 1% each side)
            assumed_win_rate: Assumed probability of winning (default 85%)
            assumed_gross_profit_pct: Gross profit before slippage (default 5%)
            assumed_loss_pct: Assumed loss on a losing trade (default 10%)
            kelly_fraction: Fraction of raw Kelly to use (default 10% = conservative)
            min_trades_for_kelly: Minimum historical trades before using empirical stats
            max_position_pct: Max position as fraction of bankroll
            daily_loss_limit: Daily loss limit as fraction of bankroll
            max_trades: Hard maximum number of trades
            max_position_dollars: Hard maximum position size in dollars
        """
        self.initial_bankroll = bankroll
        self.bankroll = bankroll
        self.slippage_model = slippage_model or SlippageModel()
        self.assumed_win_rate = max(0.0, min(1.0, assumed_win_rate))
        self.assumed_gross_profit_pct = max(0.0, assumed_gross_profit_pct)
        self.assumed_loss_pct = max(0.0, assumed_loss_pct)
        self.kelly_fraction = max(0.0, min(1.0, kelly_fraction))
        self.min_trades_for_kelly = max(0, min_trades_for_kelly)
        self.max_position_pct = max(0.0, min(1.0, max_position_pct))
        self.daily_loss_limit = daily_loss_limit
        self.max_trades = max_trades
        self.max_position_dollars = max_position_dollars
        self.trades: List[Trade] = []
        self._current_daily_pnl = 0.0
        self._trade_count = 0

        # Safety check for live mode
        self.live_mode = os.environ.get('LIVE_MODE', 'false').lower() == 'true'
        if not self.live_mode:
            print("⚠️  SAFE MODE: LIVE_MODE not set. Using paper/dry_run mode.")

    def can_trade(self) -> tuple[bool, str]:
        """
        Check if trading is allowed. Returns (allowed, reason).
        """
        # Hard trade limit
        if self._trade_count >= self.max_trades:
            return False, f"Trade limit reached: {self._trade_count}/{self.max_trades}"

        # Daily loss limit
        daily_loss_pct = (
            self._current_daily_pnl / self.initial_bankroll
            if self.initial_bankroll > 0 else 0
        )
        if daily_loss_pct <= self.daily_loss_limit:
            return False, f"Daily loss limit hit: {daily_loss_pct:.1%}"

        # Minimum bankroll
        if self.bankroll < self.initial_bankroll * 0.5:
            return False, "Bankroll below 50% threshold"

        return True, "OK"

    def update_trade(self, pnl: float) -> None:
        """Record a trade result and update bankroll."""
        self.trades.append(Trade(pnl=pnl))
        self.bankroll += pnl
        self._current_daily_pnl += pnl
        self._trade_count += 1

    def get_stats(self) -> Dict[str, float]:
        """Calculate and return trading statistics."""
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
        avg_loss = abs(mean(losses)) if losses else 0.0

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
            'return_pct': return_pct,
            'trade_count': self._trade_count,
            'max_trades': self.max_trades
        }

    def _get_kelly_params(self) -> tuple[float, float, float]:
        """
        Determine win rate, net profit, and loss amount for Kelly calculation.
        
        If enough historical trades exist, uses empirical stats.
        Otherwise, uses configured assumptions with slippage applied.
        
        Returns:
            Tuple of (win_rate, net_profit_pct, loss_pct)
        """
        if len(self.trades) >= self.min_trades_for_kelly and self.min_trades_for_kelly > 0:
            wins = [t.pnl for t in self.trades if t.pnl > 0]
            losses = [t.pnl for t in self.trades if t.pnl <= 0]

            win_rate = len(wins) / len(self.trades) if self.trades else 0.0
            avg_win = mean(wins) if wins else 0.0
            avg_loss = abs(mean(losses)) if losses else 0.0

            # Normalize to percentages of bankroll for Kelly consistency
            bankroll = self.initial_bankroll if self.initial_bankroll > 0 else 1.0
            net_profit_pct = avg_win / bankroll
            loss_pct = avg_loss / bankroll
        else:
            win_rate = self.assumed_win_rate
            net_profit_pct = self.slippage_model.apply_to_gross_profit(
                self.assumed_gross_profit_pct
            )
            loss_pct = self.assumed_loss_pct

        return win_rate, net_profit_pct, loss_pct

    def calculate_size(self, confidence: float = 1.0) -> float:
        """
        Calculate position size using the Kelly criterion.
        
        Uses configurable slippage model to compute net profit from gross
        assumptions. If Kelly <= 0, ALWAYS returns 0.0.
        
        Args:
            confidence: Confidence multiplier clamped to [0.5, 1.0]
            
        Returns:
            Position size in dollars (0.0 if negative Kelly or risk limits hit)
        """
        # Check trade limits first
        can_trade, reason = self.can_trade()
        if not can_trade:
            print(f"⛔ Trade blocked: {reason}")
            return 0.0

        # Clamp confidence
        confidence = max(0.5, min(1.0, confidence))

        # Maximum position caps
        max_position_pct = self.bankroll * self.max_position_pct
        max_position = min(max_position_pct, self.max_position_dollars)

        # Get Kelly parameters
        p, net_profit_pct, loss_pct = self._get_kelly_params()
        q = 1.0 - p

        # Guard against zero loss (would divide by zero)
        if loss_pct <= 0:
            print("  ⚠️  Assumed loss is zero — cannot compute Kelly safely.")
            return 0.0

        # Payoff ratio: average win / average loss
        b = net_profit_pct / loss_pct

        # Bulletproof Kelly formula: (bp - q) / b
        kelly_fraction_raw = (b * p - q) / b

        print(f"Kelly calculation:")
        print(f"  Win rate (p): {p:.1%}")
        print(f"  Net profit pct: {net_profit_pct:.1%}")
        print(f"  Loss pct: {loss_pct:.1%}")
        print(f"  Payoff ratio (b): {b:.3f}")
        print(f"  Raw Kelly: {kelly_fraction_raw:.4f}")

        # CRITICAL: If Kelly <= 0, this bet has negative expectancy. DO NOT TRADE.
        if kelly_fraction_raw <= 0:
            print(f"  ⚠️  Negative Kelly ({kelly_fraction_raw:.4f}) — bet has negative expectancy!")
            return 0.0

        # Apply safety fraction (e.g., Half-Kelly, Quarter-Kelly, etc.)
        adjusted_kelly = kelly_fraction_raw * self.kelly_fraction

        # Calculate position size
        position_size = self.bankroll * adjusted_kelly * confidence

        # Hard caps
        position_size = min(position_size, max_position)

        # Ensure minimum viable size
        if position_size < 1.0:
            print(f"  Position size ${position_size:.2f} too small, using $1.00 minimum")
            position_size = 1.0

        print(f"  Adjusted Kelly ({self.kelly_fraction:.0%}): {adjusted_kelly:.4f}")
        print(f"  Position size: ${position_size:.2f}")
        print(f"  Trade {self._trade_count + 1}/{self.max_trades}")

        return position_size

    def should_trade(self, daily_pnl: Optional[float] = None) -> bool:
        """Check if trading should continue based on risk limits."""
        can_trade, _ = self.can_trade()
        return can_trade

    def reset_daily_pnl(self) -> None:
        """Reset the daily P&L tracker."""
        self._current_daily_pnl = 0.0

    def to_dict(self) -> Dict:
        """Serialize sizer state to dictionary."""
        return {
            'initial_bankroll': self.initial_bankroll,
            'current_bankroll': self.bankroll,
            'slippage_model': {
                'entry_slip': self.slippage_model.entry_slip,
                'exit_slip': self.slippage_model.exit_slip,
            },
            'assumed_win_rate': self.assumed_win_rate,
            'assumed_gross_profit_pct': self.assumed_gross_profit_pct,
            'assumed_loss_pct': self.assumed_loss_pct,
            'kelly_fraction': self.kelly_fraction,
            'min_trades_for_kelly': self.min_trades_for_kelly,
            'max_position_pct': self.max_position_pct,
            'daily_loss_limit': self.daily_loss_limit,
            'max_trades': self.max_trades,
            'max_position_dollars': self.max_position_dollars,
            'trades': [{'pnl': t.pnl} for t in self.trades],
            'current_daily_pnl': self._current_daily_pnl,
            'trade_count': self._trade_count
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'KellySizer':
        """Restore sizer from dictionary."""
        slippage_data = data.get('slippage_model', {})
        slippage_model = SlippageModel(
            entry_slip=slippage_data.get('entry_slip', 0.01),
            exit_slip=slippage_data.get('exit_slip', 0.01),
        )
        sizer = cls(
            bankroll=data['initial_bankroll'],
            slippage_model=slippage_model,
            assumed_win_rate=data.get('assumed_win_rate', 0.85),
            assumed_gross_profit_pct=data.get('assumed_gross_profit_pct', 0.05),
            assumed_loss_pct=data.get('assumed_loss_pct', 0.10),
            kelly_fraction=data['kelly_fraction'],
            min_trades_for_kelly=data['min_trades_for_kelly'],
            max_position_pct=data['max_position_pct'],
            daily_loss_limit=data['daily_loss_limit'],
            max_trades=data.get('max_trades', 1),
            max_position_dollars=data.get('max_position_dollars', 5.0)
        )
        sizer.bankroll = data['current_bankroll']
        sizer._current_daily_pnl = data.get('current_daily_pnl', 0.0)
        sizer._trade_count = data.get('trade_count', 0)
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


def test_kelly_calculation():
    """Test the fixed Kelly calculation with realistic parameters."""
    print("=" * 60)
    print("FIXED Kelly Criterion - Realistic Parameters Test")
    print("=" * 60)

    sizer = KellySizer(
        bankroll=100.0,
        slippage_model=SlippageModel(entry_slip=0.01, exit_slip=0.01),
        assumed_win_rate=0.85,
        assumed_gross_profit_pct=0.05,
        assumed_loss_pct=0.10,
        kelly_fraction=0.1,
        max_trades=1,
        max_position_dollars=5.0
    )

    print(f"\nBankroll: ${sizer.bankroll:.2f}")
    print(f"Max trades: {sizer.max_trades}")
    print(f"Max position: ${sizer.max_position_dollars:.2f}")
    print(f"Kelly fraction: {sizer.kelly_fraction:.0%}")

    print("\n--- Calculating position size for first trade ---")
    size = sizer.calculate_size(confidence=1.0)

    print(f"\n{'='*60}")
    if size > 0:
        print(f"✅ Position size approved: ${size:.2f}")
    else:
        print(f"❌ Position size: $0.00 - Trade blocked")
    print(f"{'='*60}")

    return sizer
