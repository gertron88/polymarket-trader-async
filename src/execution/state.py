"""
Batched State Management Module

Provides in-memory state with async batch flushing for zero-blocking I/O
during trading operations. Uses JSONL append-only log for trade history.
"""

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List
import aiofiles
import logging

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a trading position for a specific window."""
    window_ts: int
    side: str  # 'UP' or 'DOWN'
    size: float
    entry_price: float
    entry_time: float
    order_id: Optional[str] = None
    exit_price: Optional[float] = None
    exit_time: Optional[float] = None
    pnl: Optional[float] = None
    status: str = "open"  # 'open', 'closed', 'cancelling'
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        """Create position from dictionary."""
        return cls(**data)


@dataclass
class TradeEvent:
    """Represents a trade event for logging to JSONL."""
    timestamp: float
    event_type: str  # 'entry', 'exit', 'cancel', 'error'
    window_ts: int
    side: str
    size: float
    price: float
    order_id: Optional[str] = None
    pnl: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert trade event to dictionary."""
        return asdict(self)
    
    def to_jsonl(self) -> str:
        """Convert to JSONL string."""
        return json.dumps(self.to_dict(), default=str) + "\n"


class StateManager:
    """
    Manages trading state with async batch flushing.
    
    Features:
    - In-memory state for zero-latency reads
    - Async batch writes to disk (no blocking I/O)
    - JSONL append-only log for trade history
    - Automatic periodic flushing
    - Position tracking by window timestamp
    
    Usage:
        state = StateManager("trades.jsonl")
        await state.initialize()
        
        # Update position (in-memory only, fast)
        state.update_position(position)
        
        # Log trade event (buffered for batch write)
        await state.log_trade_event(event)
        
        # Start background flush task
        asyncio.create_task(state.periodic_flush())
        
        # Cleanup
        await state.close()
    """
    
    def __init__(self, filepath: str, flush_interval: float = 5.0):
        """
        Initialize state manager.
        
        Args:
            filepath: Path to JSONL file for trade history
            flush_interval: Seconds between automatic flushes
        """
        self.filepath = Path(filepath)
        self.flush_interval = flush_interval
        
        # In-memory state
        self._positions: Dict[int, Position] = {}  # window_ts -> Position
        self._dirty_positions: set = set()  # Track which positions need flushing
        
        # Event buffer for batch writing
        self._event_buffer: List[TradeEvent] = []
        self._buffer_lock = asyncio.Lock()
        
        # State tracking
        self._last_flush = time.time()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Stats
        self._stats = {
            "events_logged": 0,
            "events_written": 0,
            "positions_updated": 0,
            "last_flush_time": 0.0,
        }
    
    async def initialize(self) -> None:
        """
        Initialize state manager and load existing state.
        
        Loads any existing positions from the JSONL file.
        Creates the file and parent directories if they don't exist.
        """
        logger.info(f"Initializing StateManager with file: {self.filepath}")
        
        # Ensure directory exists
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing state if file exists
        if self.filepath.exists():
            await self._load_existing_state()
        else:
            # Create empty file
            async with aiofiles.open(self.filepath, 'w') as f:
                pass
            logger.info("Created new state file")
        
        self._running = True
        logger.info(f"StateManager initialized with {len(self._positions)} positions")
    
    async def _load_existing_state(self) -> None:
        """Load existing positions from JSONL file."""
        try:
            async with aiofiles.open(self.filepath, 'r') as f:
                content = await f.read()
            
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            
            for line in lines:
                try:
                    data = json.loads(line)
                    
                    # Reconstruct position from event data
                    if data.get('event_type') == 'entry':
                        position = Position(
                            window_ts=data['window_ts'],
                            side=data['side'],
                            size=data['size'],
                            entry_price=data['price'],
                            entry_time=data['timestamp'],
                            order_id=data.get('order_id'),
                            status='open'
                        )
                        self._positions[position.window_ts] = position
                    
                    elif data.get('event_type') == 'exit':
                        window_ts = data['window_ts']
                        if window_ts in self._positions:
                            position = self._positions[window_ts]
                            position.exit_price = data['price']
                            position.exit_time = data['timestamp']
                            position.pnl = data.get('pnl')
                            position.status = 'closed'
                
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line in state file: {e}")
                except Exception as e:
                    logger.warning(f"Error processing state line: {e}")
            
            logger.info(f"Loaded {len(self._positions)} positions from existing state")
            
        except Exception as e:
            logger.error(f"Error loading existing state: {e}")
            raise
    
    def update_position(self, position: Position) -> None:
        """
        Update a position in memory (non-blocking).
        
        This method is synchronous and has zero I/O latency.
        Changes are buffered and flushed to disk periodically.
        
        Args:
            position: Position object to update
        """
        self._positions[position.window_ts] = position
        self._dirty_positions.add(position.window_ts)
        self._stats["positions_updated"] += 1
        
        logger.debug(f"Updated position for window {position.window_ts} "
                    f"({position.side}, {position.status})")
    
    def get_position(self, window_ts: int) -> Optional[Position]:
        """
        Get position for a specific window timestamp.
        
        This is a pure memory read with zero latency.
        
        Args:
            window_ts: Window timestamp to look up
            
        Returns:
            Position object or None if not found
        """
        return self._positions.get(window_ts)
    
    def get_open_positions(self) -> Dict[int, Position]:
        """
        Get all currently open positions.
        
        Returns:
            Dictionary of window_ts -> Position for open positions
        """
        return {
            ts: pos for ts, pos in self._positions.items()
            if pos.status == 'open'
        }
    
    def get_all_positions(self) -> Dict[int, Position]:
        """
        Get all positions (open and closed).
        
        Returns:
            Dictionary of window_ts -> Position
        """
        return self._positions.copy()
    
    async def log_trade_event(self, event: TradeEvent) -> None:
        """
        Log a trade event to be written to disk.
        
        Events are buffered and written in batches for efficiency.
        Critical events (errors) trigger an immediate flush.
        
        Args:
            event: TradeEvent to log
        """
        async with self._buffer_lock:
            self._event_buffer.append(event)
            self._stats["events_logged"] += 1
        
        # Critical events trigger immediate flush
        if event.event_type in ('error', 'exit'):
            await self.flush()
        
        logger.debug(f"Buffered {event.event_type} event for window {event.window_ts}")
    
    async def flush(self, force: bool = False) -> None:
        """
        Flush buffered events to disk.
        
        This is the only method that performs I/O. It's async to
        prevent blocking the trading loop.
        
        Args:
            force: Force flush even if buffer is empty
        """
        # Check if flush needed
        if not force and not self._event_buffer and not self._dirty_positions:
            return
        
        # Check interval (unless forced)
        current_time = time.time()
        if not force and (current_time - self._last_flush) < self.flush_interval:
            return
        
        try:
            async with self._buffer_lock:
                if not self._event_buffer and not force:
                    return
                
                # Convert events to JSONL
                lines = [event.to_jsonl() for event in self._event_buffer]
                self._event_buffer.clear()
            
            # Write to disk (append mode)
            if lines:
                async with aiofiles.open(self.filepath, 'a') as f:
                    await f.writelines(lines)
                
                self._stats["events_written"] += len(lines)
                self._stats["last_flush_time"] = current_time
                logger.debug(f"Flushed {len(lines)} events to disk")
            
            # Clear dirty flags
            self._dirty_positions.clear()
            self._last_flush = current_time
            
        except Exception as e:
            logger.error(f"Error flushing state to disk: {e}")
            # Don't clear buffer on error - will retry next flush
            raise
    
    async def periodic_flush(self) -> None:
        """
        Background task to periodically flush state to disk.
        
        This should be run as an asyncio task:
            asyncio.create_task(state.periodic_flush())
        
        The task runs until close() is called.
        """
        logger.info(f"Starting periodic flush task (interval: {self.flush_interval}s)")
        
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                
                if not self._running:
                    break
                
                await self.flush()
                
            except asyncio.CancelledError:
                logger.info("Periodic flush task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic flush: {e}")
                # Continue running despite errors
    
    async def close(self) -> None:
        """
        Close the state manager and perform final flush.
        
        This should be called during shutdown to ensure all
        pending state is written to disk.
        """
        logger.info("Closing StateManager")
        
        self._running = False
        
        # Cancel periodic flush task if running
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Final flush
        try:
            await self.flush(force=True)
            logger.info("Final flush completed")
        except Exception as e:
            logger.error(f"Error during final flush: {e}")
            raise
        
        logger.info(f"StateManager closed. Stats: {self._stats}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about state manager operations.
        
        Returns:
            Dictionary with operation statistics
        """
        return {
            **self._stats,
            "positions_in_memory": len(self._positions),
            "open_positions": len(self.get_open_positions()),
            "events_in_buffer": len(self._event_buffer),
            "dirty_positions": len(self._dirty_positions),
        }
    
    async def clear_all(self) -> None:
        """
        Clear all state (use with caution).
        
        This clears in-memory state and truncates the log file.
        Intended for testing/reset scenarios.
        """
        logger.warning("Clearing all state!")
        
        async with self._buffer_lock:
            self._event_buffer.clear()
        
        self._positions.clear()
        self._dirty_positions.clear()
        
        # Truncate file
        async with aiofiles.open(self.filepath, 'w') as f:
            pass
        
        logger.info("State cleared")


class StateContext:
    """
    Async context manager for StateManager.
    
    Usage:
        async with StateContext("trades.jsonl") as state:
            state.update_position(position)
            await state.log_trade_event(event)
        # Automatically closed and flushed on exit
    """
    
    def __init__(self, filepath: str, flush_interval: float = 5.0):
        self.filepath = filepath
        self.flush_interval = flush_interval
        self._manager: Optional[StateManager] = None
        self._flush_task: Optional[asyncio.Task] = None
    
    async def __aenter__(self) -> StateManager:
        self._manager = StateManager(self.filepath, self.flush_interval)
        await self._manager.initialize()
        
        # Start background flush
        self._flush_task = asyncio.create_task(self._manager.periodic_flush())
        
        return self._manager
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._manager:
            await self._manager.close()
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass


# Convenience functions for creating trade events
def create_entry_event(
    window_ts: int,
    side: str,
    size: float,
    price: float,
    order_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> TradeEvent:
    """Create an entry trade event."""
    return TradeEvent(
        timestamp=time.time(),
        event_type='entry',
        window_ts=window_ts,
        side=side,
        size=size,
        price=price,
        order_id=order_id,
        metadata=metadata
    )


def create_exit_event(
    window_ts: int,
    side: str,
    size: float,
    price: float,
    order_id: Optional[str] = None,
    pnl: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> TradeEvent:
    """Create an exit trade event."""
    return TradeEvent(
        timestamp=time.time(),
        event_type='exit',
        window_ts=window_ts,
        side=side,
        size=size,
        price=price,
        order_id=order_id,
        pnl=pnl,
        metadata=metadata
    )


def create_error_event(
    window_ts: int,
    side: str,
    error_message: str,
    metadata: Optional[Dict[str, Any]] = None
) -> TradeEvent:
    """Create an error trade event."""
    return TradeEvent(
        timestamp=time.time(),
        event_type='error',
        window_ts=window_ts,
        side=side,
        size=0.0,
        price=0.0,
        metadata={**(metadata or {}), 'error': error_message}
    )
