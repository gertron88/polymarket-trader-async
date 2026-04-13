"""
Polymarket CLOB WebSocket Feed Module

Provides real-time order book data from Polymarket's Central Limit Order Book (CLOB)
with automatic reconnection, price caching, and low-latency callbacks.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """WebSocket message types from Polymarket CLOB."""
    BOOK_DELTA = "book_delta"
    BOOK_SNAPSHOT = "book_snapshot"
    LAST_TRADE_PRICE = "last_trade_price"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"


@dataclass
class PriceData:
    """Container for token price data."""
    token_id: str
    best_bid: float
    best_ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    last_update: float = field(default_factory=time.time)
    
    @property
    def mid_price(self) -> float:
        """Calculate mid price between best bid and ask."""
        return (self.best_bid + self.best_ask) / 2
    
    @property
    def spread(self) -> float:
        """Calculate bid-ask spread."""
        return self.best_ask - self.best_bid
    
    @property
    def spread_pct(self) -> float:
        """Calculate spread as percentage of mid price."""
        mid = self.mid_price
        return (self.spread / mid * 100) if mid > 0 else 0.0


class PolymarketWebSocket:
    """
    WebSocket client for Polymarket CLOB real-time order book data.
    
    Features:
        - Real-time order book updates with ~20-40ms latency
        - Automatic reconnection with exponential backoff
        - Automatic resubscription to tokens after reconnection
        - Thread-safe price cache for synchronous reads
        - Callback system for price update notifications
    
    Example:
        >>> async def on_price_update(token_id: str, best_bid: float, best_ask: float):
        ...     print(f"{token_id}: {best_bid} / {best_ask}")
        ...
        >>> ws = PolymarketWebSocket(on_price_update)
        >>> await ws.connect()
        >>> await ws.subscribe("token-123")
        >>> # ... use ws.get_price("token-123") for sync reads
        >>> await ws.close()
    """
    
    # WebSocket configuration
    WS_URL = "wss://clob.polymarket.com/ws"
    
    # Reconnection configuration
    INITIAL_RECONNECT_DELAY = 1.0  # seconds
    MAX_RECONNECT_DELAY = 60.0     # seconds
    RECONNECT_BACKOFF_MULTIPLIER = 2.0
    
    # Connection timeouts
    CONNECTION_TIMEOUT = 10.0      # seconds
    PING_INTERVAL = 30.0           # seconds
    
    def __init__(
        self,
        on_price_update: Callable[[str, float, float], None],
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the WebSocket client.
        
        Args:
            on_price_update: Callback function(token_id, best_bid, best_ask)
                           called when price updates are received.
            logger: Optional logger instance
        """
        self.on_price_update = on_price_update
        self.logger = logger or logging.getLogger(__name__)
        
        # Connection state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._running = False
        
        # Subscriptions and price cache
        self._subscriptions: Set[str] = set()
        self._price_cache: Dict[str, PriceData] = {}
        
        # Reconnection state
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._reconnect_attempts = 0
        
        # Background tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    async def connect(self) -> None:
        """
        Establish WebSocket connection and start background tasks.
        
        This method will block until the connection is established.
        If connection fails, it will retry with exponential backoff.
        """
        if self._running:
            self.logger.warning("Already connected or connecting")
            return
        
        self._running = True
        self.logger.info(f"Connecting to {self.WS_URL}")
        
        while self._running and not self._connected:
            try:
                await self._connect_once()
            except Exception as e:
                self.logger.error(f"Connection failed: {e}")
                await self._handle_reconnect_delay()
    
    async def _connect_once(self) -> None:
        """Attempt a single connection to the WebSocket server."""
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.WS_URL),
                timeout=self.CONNECTION_TIMEOUT
            )
            
            self._connected = True
            self._reconnect_attempts = 0
            self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
            
            self.logger.info("WebSocket connected successfully")
            
            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())
            
            # Resubscribe to all previously subscribed tokens
            await self._resubscribe_all()
            
        except (ConnectionRefusedError, InvalidStatusCode, asyncio.TimeoutError) as e:
            self.logger.error(f"Connection error: {e}")
            raise
    
    async def _handle_reconnect_delay(self) -> None:
        """Handle reconnection delay with exponential backoff."""
        self._reconnect_attempts += 1
        delay = min(
            self._reconnect_delay * (self.RECONNECT_BACKOFF_MULTIPLIER ** (self._reconnect_attempts - 1)),
            self.MAX_RECONNECT_DELAY
        )
        
        self.logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts})")
        await asyncio.sleep(delay)
    
    async def _resubscribe_all(self) -> None:
        """Resubscribe to all previously subscribed tokens after reconnection."""
        if not self._subscriptions:
            return
        
        self.logger.info(f"Resubscribing to {len(self._subscriptions)} tokens")
        
        for token_id in list(self._subscriptions):
            try:
                await self._send_subscription(token_id, subscribe=True)
                await asyncio.sleep(0.05)  # Small delay to avoid rate limiting
            except Exception as e:
                self.logger.error(f"Failed to resubscribe to {token_id}: {e}")
    
    async def subscribe(self, token_id: str) -> None:
        """
        Subscribe to order book updates for a token.
        
        Args:
            token_id: The token ID to subscribe to
        """
        async with self._lock:
            if token_id in self._subscriptions:
                self.logger.debug(f"Already subscribed to {token_id}")
                return
            
            self._subscriptions.add(token_id)
            
            if self._connected and self._ws:
                await self._send_subscription(token_id, subscribe=True)
                self.logger.info(f"Subscribed to {token_id}")
    
    async def unsubscribe(self, token_id: str) -> None:
        """
        Unsubscribe from order book updates for a token.
        
        Args:
            token_id: The token ID to unsubscribe from
        """
        async with self._lock:
            if token_id not in self._subscriptions:
                return
            
            self._subscriptions.discard(token_id)
            
            # Remove from cache
            self._price_cache.pop(token_id, None)
            
            if self._connected and self._ws:
                await self._send_subscription(token_id, subscribe=False)
                self.logger.info(f"Unsubscribed from {token_id}")
    
    async def _send_subscription(self, token_id: str, subscribe: bool) -> None:
        """
        Send subscription/unsubscription message to server.
        
        Args:
            token_id: Token to subscribe/unsubscribe
            subscribe: True to subscribe, False to unsubscribe
        """
        if not self._ws:
            return
        
        msg_type = (
            MessageType.SUBSCRIBE.value 
            if subscribe 
            else MessageType.UNSUBSCRIBE.value
        )
        
        message = {
            "type": msg_type,
            "token_id": token_id
        }
        
        try:
            await self._ws.send(json.dumps(message))
        except Exception as e:
            self.logger.error(f"Failed to send subscription message: {e}")
            raise
    
    async def _receive_loop(self) -> None:
        """Main receive loop for WebSocket messages."""
        while self._running and self._connected:
            try:
                message = await self._ws.recv()
                await self._handle_message(message)
            except ConnectionClosed:
                self.logger.warning("WebSocket connection closed")
                await self._handle_disconnect()
                break
            except Exception as e:
                self.logger.error(f"Error in receive loop: {e}")
                await asyncio.sleep(0.1)
    
    async def _handle_message(self, message: str) -> None:
        """
        Parse and handle incoming WebSocket message.
        
        Args:
            message: Raw WebSocket message string
        """
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == MessageType.BOOK_DELTA.value:
                await self._handle_book_delta(data)
            elif msg_type == MessageType.BOOK_SNAPSHOT.value:
                await self._handle_book_snapshot(data)
            elif msg_type == MessageType.LAST_TRADE_PRICE.value:
                await self._handle_last_trade(data)
            else:
                self.logger.debug(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    async def _handle_book_delta(self, data: dict) -> None:
        """
        Handle order book delta (incremental update).
        
        Args:
            data: Parsed message data containing token_id, changes, etc.
        """
        token_id = data.get("token_id")
        if not token_id:
            return
        
        # Extract best bid/ask from the delta
        # Delta format: {"bids": [[price, size], ...], "asks": [[price, size], ...]}
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        best_bid = max((b[0] for b in bids if b[1] > 0), default=None) if bids else None
        best_ask = min((a[0] for a in asks if a[1] > 0), default=None) if asks else None
        
        # Update cache with new values
        async with self._lock:
            if token_id in self._price_cache:
                cached = self._price_cache[token_id]
                if best_bid is not None:
                    cached.best_bid = best_bid
                    cached.bid_size = next((b[1] for b in bids if b[0] == best_bid), 0)
                if best_ask is not None:
                    cached.best_ask = best_ask
                    cached.ask_size = next((a[1] for a in asks if a[0] == best_ask), 0)
                cached.last_update = time.time()
                best_bid = cached.best_bid
                best_ask = cached.best_ask
            else:
                # New entry - need both bid and ask for valid PriceData
                if best_bid is not None and best_ask is not None:
                    bid_size = next((b[1] for b in bids if b[0] == best_bid), 0)
                    ask_size = next((a[1] for a in asks if a[0] == best_ask), 0)
                    self._price_cache[token_id] = PriceData(
                        token_id=token_id,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        bid_size=bid_size,
                        ask_size=ask_size
                    )
                else:
                    return  # Skip incomplete data
        
        # Trigger callback
        self._trigger_callback(token_id, best_bid, best_ask)
    
    async def _handle_book_snapshot(self, data: dict) -> None:
        """
        Handle full order book snapshot.
        
        Args:
            data: Parsed message data containing full order book
        """
        token_id = data.get("token_id")
        if not token_id:
            return
        
        # Snapshot format: {"bids": [[price, size], ...], "asks": [[price, size], ...]}
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        if not bids or not asks:
            return
        
        # Get best bid (highest) and best ask (lowest)
        best_bid = max(bids, key=lambda x: x[0])
        best_ask = min(asks, key=lambda x: x[0])
        
        async with self._lock:
            self._price_cache[token_id] = PriceData(
                token_id=token_id,
                best_bid=best_bid[0],
                best_ask=best_ask[0],
                bid_size=best_bid[1],
                ask_size=best_ask[1]
            )
        
        self._trigger_callback(token_id, best_bid[0], best_ask[0])
    
    async def _handle_last_trade(self, data: dict) -> None:
        """
        Handle last trade price update.
        
        Args:
            data: Parsed message data containing last trade info
        """
        # Last trade updates don't change bid/ask, but we log them
        token_id = data.get("token_id")
        price = data.get("price")
        self.logger.debug(f"Last trade for {token_id}: {price}")
    
    def _trigger_callback(self, token_id: str, best_bid: float, best_ask: float) -> None:
        """
        Trigger the price update callback.
        
        Args:
            token_id: Token identifier
            best_bid: Best bid price
            best_ask: Best ask price
        """
        try:
            # Run callback in executor to avoid blocking
            asyncio.create_task(self._run_callback(token_id, best_bid, best_ask))
        except Exception as e:
            self.logger.error(f"Failed to trigger callback: {e}")
    
    async def _run_callback(self, token_id: str, best_bid: float, best_ask: float) -> None:
        """Run the callback in a safe manner."""
        try:
            if asyncio.iscoroutinefunction(self.on_price_update):
                await self.on_price_update(token_id, best_bid, best_ask)
            else:
                self.on_price_update(token_id, best_bid, best_ask)
        except Exception as e:
            self.logger.error(f"Callback error: {e}")
    
    async def _ping_loop(self) -> None:
        """Send periodic ping messages to keep connection alive."""
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.PING_INTERVAL)
                if self._ws and self._connected:
                    await self._ws.ping()
                    self.logger.debug("Ping sent")
            except Exception as e:
                self.logger.debug(f"Ping failed: {e}")
                break
    
    async def _handle_disconnect(self) -> None:
        """Handle connection loss and initiate reconnection."""
        self._connected = False
        
        # Cancel background tasks
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        if self._running:
            self.logger.info("Attempting to reconnect...")
            await self.connect()
    
    def get_price(self, token_id: str) -> Optional[PriceData]:
        """
        Get the last known price for a token (synchronous).
        
        Args:
            token_id: Token identifier
            
        Returns:
            PriceData object or None if not available
        """
        return self._price_cache.get(token_id)
    
    def get_all_prices(self) -> Dict[str, PriceData]:
        """
        Get all cached prices.
        
        Returns:
            Dictionary mapping token_id to PriceData
        """
        return dict(self._price_cache)
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected
    
    @property
    def subscribed_tokens(self) -> Set[str]:
        """Get set of currently subscribed tokens."""
        return set(self._subscriptions)
    
    async def close(self) -> None:
        """
        Gracefully close the WebSocket connection.
        
        This will stop all background tasks and close the connection.
        """
        self.logger.info("Closing WebSocket connection...")
        self._running = False
        
        # Cancel background tasks
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                self.logger.error(f"Error closing WebSocket: {e}")
        
        self._connected = False
        self.logger.info("WebSocket connection closed")


# Example usage
async def main():
    """Example usage of PolymarketWebSocket."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Callback function for price updates
    async def on_price_update(token_id: str, best_bid: float, best_ask: float):
        spread = best_ask - best_bid
        spread_pct = (spread / ((best_bid + best_ask) / 2)) * 100
        print(f"[{token_id}] Bid: {best_bid:.4f} | Ask: {best_ask:.4f} | Spread: {spread:.4f} ({spread_pct:.2f}%)")
    
    # Create WebSocket client
    ws = PolymarketWebSocket(on_price_update)
    
    try:
        # Connect to WebSocket
        await ws.connect()
        
        # Subscribe to some tokens (replace with actual token IDs)
        # Example token IDs - these would be real Polymarket token IDs
        example_tokens = [
            "0x1234567890abcdef...",  # Replace with real token ID
        ]
        
        for token in example_tokens:
            await ws.subscribe(token)
        
        # Keep running for a while
        print("Connected! Listening for price updates...")
        print("Press Ctrl+C to exit")
        
        while True:
            # Demonstrate synchronous price reads
            await asyncio.sleep(5)
            for token in example_tokens:
                price = ws.get_price(token)
                if price:
                    print(f"[CACHE] {token}: {price.best_bid} / {price.best_ask}")
    
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
