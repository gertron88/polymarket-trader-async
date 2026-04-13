"""
Market State Logger - Deep post-hoc analysis logging

Captures periodic snapshots and entry signal snapshots of the trading engine
state for later analysis. Uses async batched JSONL writes for zero-blocking I/O.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
import aiofiles
import logging

logger = logging.getLogger(__name__)


class MarketStateLogger:
    """
    Async batched JSONL logger for market state snapshots and signals.

    Features:
    - Buffered async writes (no blocking I/O)
    - Periodic snapshots of engine state
    - Signal-triggered enriched snapshots
    - JSONL append-only log format
    """

    def __init__(self, filepath: str = "data/market_state.jsonl", flush_interval: float = 5.0):
        self.filepath = Path(filepath)
        self.flush_interval = flush_interval

        self._buffer: List[str] = []
        self._buffer_lock = asyncio.Lock()
        self._last_flush = time.time()
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """Ensure directory exists and file is ready for writing."""
        logger.info(f"Initializing MarketStateLogger with file: {self.filepath}")
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        if not self.filepath.exists():
            async with aiofiles.open(self.filepath, "w") as f:
                pass
            logger.info("Created new market state log file")

        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info("MarketStateLogger initialized")

    def _extract_window_data(self, engine: Any) -> Dict[str, Any]:
        """Extract current window data from engine if available."""
        # engine does not store window directly; we rely on poly_prices + engine internals
        # The engine may have gamma client data cached, but we can't call async here.
        # We log what is synchronously available on the engine.
        return {}

    def _build_snapshot(self, engine: Any) -> Dict[str, Any]:
        """Build a snapshot dict from the engine state."""
        snapshot: Dict[str, Any] = {
            "timestamp": time.time(),
            "btc_price": getattr(engine, "current_btc_price", 0.0),
            "btc_change_30s": getattr(engine, "current_btc_change", 0.0),
            "daily_pnl": getattr(engine, "daily_pnl", 0.0),
            "total_trades": getattr(engine, "total_trades", 0),
            "active_positions": len(getattr(engine, "position_manager", None).get_active_positions()) if getattr(engine, "position_manager", None) else 0,
            "has_traded": getattr(engine, "has_traded", False),
            "breaker_status": self._get_breaker_status(engine),
        }

        # Enrich with cached window data from engine if available
        current_window = getattr(engine, "_current_window", None) or {}
        snapshot["active_window_ts"] = current_window.get("timestamp") if current_window else None
        snapshot["up_ask"] = current_window.get("up_ask", 0.0) if current_window else 0.0
        snapshot["down_ask"] = current_window.get("down_ask", 0.0) if current_window else 0.0
        snapshot["combined"] = current_window.get("combined", 1.0) if current_window else 1.0
        snapshot["edge"] = current_window.get("edge", 0.0) if current_window else 0.0

        return snapshot

    def _get_breaker_status(self, engine: Any) -> List[str]:
        """Return list of tripped breaker names."""
        breaker_panel = getattr(engine, "breaker_panel", None)
        if not breaker_panel:
            return []
        try:
            status = breaker_panel.get_status()
            return [name for name, info in status.items() if info.get("tripped")]
        except Exception:
            return []

    async def log_snapshot(self, engine: Any) -> None:
        """Log a periodic snapshot of engine state."""
        snapshot = self._build_snapshot(engine)
        snapshot["type"] = "snapshot"
        line = json.dumps(snapshot, default=str) + "\n"
        async with self._buffer_lock:
            self._buffer.append(line)
        logger.debug("Buffered market state snapshot")

    async def log_signal(self, engine: Any, signal: Any) -> None:
        """Log an enriched snapshot when an entry signal is generated."""
        snapshot = self._build_snapshot(engine)
        snapshot["type"] = "signal"
        snapshot["signal_side"] = getattr(signal, "side", None)
        snapshot["signal_confidence"] = getattr(signal, "confidence", 0.0)
        snapshot["signal_btc_change"] = getattr(signal, "btc_change", 0.0)

        # Enrich with current polymarket ask prices if available
        poly_prices = getattr(engine, "poly_prices", {})
        # We don't have token IDs synchronously here, but if engine stores
        # _last_window we could use it. Engine doesn't store it.
        # We'll leave up_ask/down_ask as 0.0 in signal snapshot unless enriched elsewhere.
        line = json.dumps(snapshot, default=str) + "\n"
        async with self._buffer_lock:
            self._buffer.append(line)
        logger.debug("Buffered market state signal snapshot")

    async def flush(self, force: bool = False) -> None:
        """Flush buffered lines to disk."""
        async with self._buffer_lock:
            if not self._buffer and not force:
                return
            lines = self._buffer[:]
            self._buffer.clear()

        if lines:
            async with aiofiles.open(self.filepath, "a") as f:
                await f.writelines(lines)
            self._last_flush = time.time()
            logger.debug(f"Flushed {len(lines)} market state lines to disk")

    async def _periodic_flush(self) -> None:
        """Background task to flush buffer periodically."""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                if not self._running:
                    break
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic market state flush: {e}")

    async def close(self) -> None:
        """Stop background flush and perform final flush."""
        logger.info("Closing MarketStateLogger")
        self._running = False

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        try:
            await self.flush(force=True)
            logger.info("Final market state flush completed")
        except Exception as e:
            logger.error(f"Error during final market state flush: {e}")
            raise
