"""
Jitter Monitor & P&L Impact Tracker

Monitors execution latency variance (jitter) and correlates with P&L impact.
Detects VPS oversubscription and quantifies trading costs.

Features:
    - Real-time latency variance tracking
    - Slippage estimation from jitter
    - P&L impact attribution
    - Alerting when jitter affects profitability
"""

import asyncio
import logging
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class LatencySample:
    """Single latency measurement."""
    timestamp: float
    operation: str  # 'price_feed', 'order_submit', 'fill_confirm'
    latency_ms: float
    expected_price: float
    actual_price: float
    trade_pnl: Optional[float] = None


@dataclass
class JitterReport:
    """Aggregated jitter report."""
    window_start: datetime
    window_end: datetime
    operation: str
    samples: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    std_dev_ms: float
    max_ms: float
    min_ms: float
    estimated_slippage_cost: float
    pnl_impact: float
    recommendation: str


class JitterMonitor:
    """
    Monitors execution latency and correlates with P&L impact.
    
    Tracks:
        - Price feed latency variance
        - Order submission latency variance
        - Fill confirmation latency variance
        - Estimated slippage from jitter
        - Actual P&L attribution
    
    Usage:
        monitor = JitterMonitor()
        
        # Record each operation
        monitor.record_price_feed(latency_ms=15.2)
        monitor.record_order_submit(latency_ms=45.3, expected=0.46, actual=0.47)
        
        # Get reports
        report = monitor.get_jitter_report('order_submit', window_minutes=5)
        if report.std_dev_ms > 20:
            logger.warning(f"High jitter detected: {report.recommendation}")
    """
    
    # Thresholds for alerting
    JITTER_WARNING_THRESHOLD_MS = 10.0  # Std dev >10ms = warning
    JITTER_CRITICAL_THRESHOLD_MS = 25.0  # Std dev >25ms = critical
    SLIPPAGE_COST_PER_MS = 0.001  # Estimated 0.1% slippage per 10ms jitter
    
    def __init__(self, max_samples: int = 1000):
        """
        Initialize jitter monitor.
        
        Args:
            max_samples: Maximum samples to keep per operation type
        """
        self._samples: Dict[str, Deque[LatencySample]] = {
            'price_feed': deque(maxlen=max_samples),
            'signal_processing': deque(maxlen=max_samples),
            'order_submit': deque(maxlen=max_samples),
            'fill_confirm': deque(maxlen=max_samples),
            'total_latency': deque(maxlen=max_samples),
        }
        self._lock = asyncio.Lock()
        self._total_pnl_impact = 0.0
        self._trades_affected = 0
    
    async def record(
        self,
        operation: str,
        latency_ms: float,
        expected_price: Optional[float] = None,
        actual_price: Optional[float] = None,
        trade_pnl: Optional[float] = None
    ) -> None:
        """
        Record a latency sample.
        
        Args:
            operation: Type of operation ('price_feed', 'order_submit', etc.)
            latency_ms: Measured latency in milliseconds
            expected_price: Expected fill price (for orders)
            actual_price: Actual fill price (for orders)
            trade_pnl: Actual P&L from trade (if known)
        """
        if operation not in self._samples:
            logger.warning(f"Unknown operation type: {operation}")
            return
        
        sample = LatencySample(
            timestamp=time.time(),
            operation=operation,
            latency_ms=latency_ms,
            expected_price=expected_price or 0.0,
            actual_price=actual_price or 0.0,
            trade_pnl=trade_pnl
        )
        
        async with self._lock:
            self._samples[operation].append(sample)
            
            # Track P&L impact from slippage
            if expected_price and actual_price and expected_price > 0:
                slippage = abs(actual_price - expected_price)
                if slippage > 0.001:  # More than 0.1% slippage
                    self._trades_affected += 1
                    
            if trade_pnl is not None and trade_pnl < 0:
                # Negative P&L - could be from jitter
                self._total_pnl_impact += trade_pnl
    
    async def record_price_feed(self, latency_ms: float) -> None:
        """Record price feed latency."""
        await self.record('price_feed', latency_ms)
    
    async def record_signal_processing(self, latency_ms: float) -> None:
        """Record signal processing latency."""
        await self.record('signal_processing', latency_ms)
    
    async def record_order_submit(
        self,
        latency_ms: float,
        expected_price: float,
        actual_price: float,
        trade_pnl: Optional[float] = None
    ) -> None:
        """Record order submission with slippage tracking."""
        await self.record('order_submit', latency_ms, expected_price, actual_price, trade_pnl)
    
    async def record_total_latency(self, latency_ms: float) -> None:
        """Record total round-trip latency."""
        await self.record('total_latency', latency_ms)
    
    def _calculate_jitter(self, samples: Deque[LatencySample]) -> Dict[str, float]:
        """Calculate jitter statistics from samples."""
        if len(samples) < 2:
            return {
                'mean': 0.0,
                'median': 0.0,
                'std_dev': 0.0,
                'p95': 0.0,
                'p99': 0.0,
                'min': 0.0,
                'max': 0.0,
            }
        
        latencies = [s.latency_ms for s in samples]
        
        return {
            'mean': statistics.mean(latencies),
            'median': statistics.median(latencies),
            'std_dev': statistics.stdev(latencies) if len(latencies) > 1 else 0.0,
            'p95': sorted(latencies)[int(len(latencies) * 0.95)],
            'p99': sorted(latencies)[int(len(latencies) * 0.99)],
            'min': min(latencies),
            'max': max(latencies),
        }
    
    def _estimate_slippage_cost(self, std_dev_ms: float) -> float:
        """Estimate slippage cost from jitter."""
        # Higher jitter = more slippage
        # Formula: cost = (std_dev / 10) * 0.1% per trade
        return (std_dev_ms / 10.0) * self.SLIPPAGE_COST_PER_MS
    
    async def get_jitter_report(
        self,
        operation: str,
        window_minutes: int = 5
    ) -> Optional[JitterReport]:
        """
        Generate jitter report for an operation.
        
        Args:
            operation: Operation type to report on
            window_minutes: Time window for analysis
            
        Returns:
            JitterReport or None if insufficient data
        """
        if operation not in self._samples:
            return None
        
        async with self._lock:
            samples = self._samples[operation]
            
            # Filter to recent window
            cutoff = time.time() - (window_minutes * 60)
            recent_samples = [s for s in samples if s.timestamp > cutoff]
            
            if len(recent_samples) < 3:
                return None
            
            stats = self._calculate_jitter(deque(recent_samples))
            
            # Calculate P&L impact
            pnl_impact = sum(
                s.trade_pnl for s in recent_samples 
                if s.trade_pnl is not None and s.trade_pnl < 0
            )
            
            # Generate recommendation
            std_dev = stats['std_dev']
            if std_dev > self.JITTER_CRITICAL_THRESHOLD_MS:
                recommendation = (
                    f"CRITICAL: Jitter ({std_dev:.1f}ms) severely impacting trades. "
                    f"Estimated cost: {self._estimate_slippage_cost(std_dev)*100:.2f}% per trade. "
                    "Recommend upgrading to dedicated server."
                )
            elif std_dev > self.JITTER_WARNING_THRESHOLD_MS:
                recommendation = (
                    f"WARNING: Elevated jitter ({std_dev:.1f}ms) detected. "
                    f"Estimated cost: {self._estimate_slippage_cost(std_dev)*100:.2f}% per trade. "
                    "Monitor closely; consider dedicated if persists."
                )
            else:
                recommendation = (
                    f"OK: Jitter ({std_dev:.1f}ms) within acceptable range. "
                    f"VPS performance adequate."
                )
            
            return JitterReport(
                window_start=datetime.fromtimestamp(recent_samples[0].timestamp),
                window_end=datetime.fromtimestamp(recent_samples[-1].timestamp),
                operation=operation,
                samples=len(recent_samples),
                mean_ms=stats['mean'],
                median_ms=stats['median'],
                p95_ms=stats['p95'],
                p99_ms=stats['p99'],
                std_dev_ms=stats['std_dev'],
                max_ms=stats['max'],
                min_ms=stats['min'],
                estimated_slippage_cost=self._estimate_slippage_cost(stats['std_dev']),
                pnl_impact=pnl_impact,
                recommendation=recommendation
            )
    
    async def get_all_reports(self, window_minutes: int = 5) -> Dict[str, JitterReport]:
        """Get jitter reports for all operations."""
        reports = {}
        for operation in self._samples.keys():
            report = await self.get_jitter_report(operation, window_minutes)
            if report:
                reports[operation] = report
        return reports
    
    async def should_alert_upgrade(self) -> tuple[bool, str]:
        """
        Determine if VPS upgrade is recommended.
        
        Returns:
            (should_upgrade, reason)
        """
        # Check order submit jitter
        report = await self.get_jitter_report('order_submit', window_minutes=10)
        if report and report.std_dev_ms > self.JITTER_CRITICAL_THRESHOLD_MS:
            return True, report.recommendation
        
        # Check total latency jitter
        total_report = await self.get_jitter_report('total_latency', window_minutes=10)
        if total_report and total_report.std_dev_ms > self.JITTER_CRITICAL_THRESHOLD_MS:
            return True, total_report.recommendation
        
        # Check P&L impact
        if self._trades_affected > 10 and self._total_pnl_impact < -1.0:
            return True, (
                f"P&L impact: {self._trades_affected} trades affected, "
                f"${self._total_pnl_impact:.2f} lost to slippage. "
                "Recommend upgrading to dedicated server."
            )
        
        return False, "VPS performance adequate"
    
    def get_summary_stats(self) -> Dict:
        """Get summary statistics for logging."""
        return {
            'trades_affected_by_jitter': self._trades_affected,
            'total_pnl_impact': round(self._total_pnl_impact, 4),
            'samples_per_operation': {
                op: len(samples) for op, samples in self._samples.items()
            }
        }


# Global instance
_jitter_monitor: Optional[JitterMonitor] = None


def get_jitter_monitor() -> JitterMonitor:
    """Get singleton jitter monitor instance."""
    global _jitter_monitor
    if _jitter_monitor is None:
        _jitter_monitor = JitterMonitor()
    return _jitter_monitor


# Example usage
async def example():
    """Example of jitter monitoring."""
    monitor = get_jitter_monitor()
    
    # Simulate recording latencies
    for i in range(20):
        # Normal latency
        await monitor.record_price_feed(latency_ms=15.0 + (i % 3))
        
        # Order with slippage
        slippage = 0.01 if i % 5 == 0 else 0.0  # 20% of orders have slippage
        await monitor.record_order_submit(
            latency_ms=35.0 + (i % 10),
            expected_price=0.46,
            actual_price=0.46 + slippage,
            trade_pnl=-0.05 if slippage > 0 else 0.08
        )
    
    # Generate report
    report = await monitor.get_jitter_report('order_submit', window_minutes=5)
    if report:
        print(f"Jitter Report:")
        print(f"  Std Dev: {report.std_dev_ms:.2f}ms")
        print(f"  P95: {report.p95_ms:.2f}ms")
        print(f"  P99: {report.p99_ms:.2f}ms")
        print(f"  Recommendation: {report.recommendation}")
    
    # Check if upgrade needed
    should_upgrade, reason = await monitor.should_alert_upgrade()
    print(f"\nUpgrade recommended: {should_upgrade}")
    print(f"Reason: {reason}")


if __name__ == "__main__":
    asyncio.run(example())
