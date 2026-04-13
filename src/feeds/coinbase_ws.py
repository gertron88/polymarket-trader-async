"""
Coinbase Exchange WebSocket Feed Module

Provides real-time BTC price data from Coinbase Exchange via WebSocket.
Optimized for low latency from EU/Dublin VPS (20-40ms vs 150ms from Binance).

Features:
    - Auto-reconnect with exponential backoff
    - 30-second rolling price change calculation
    - Connection status tracking
    - Low-latency callbacks (< 30ms target from EU)
    - Compatible drop-in replacement for BinanceWebSocket

Coinbase Infrastructure:
    - Uses AWS with EU regions (Ireland, London, Frankfurt)
    - From Dublin VPS: ~20-40ms latency
    - From US VPS: ~5-20ms latency
    
WebSocket URL: wss://ws-feed.exchange.coinbase.com

Example:
    >>> def on_price(price: float, change_30s: float) -> None:
    ...     print(f"BTC: ${price:,.2f} (30s: {change_30s:+.4f}%)")
    >>> 
    >>> ws = CoinbaseWebSocket(on_price_update=on_price)
    >>> await ws.connect()
    >>> # ... later
    >>> await ws.close()
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

logger = logging.getLogger(__name__)


class CoinbaseWebSocket:
    """
    Real-time Coinbase Exchange WebSocket feed for BTC price.
    
    Drop-in replacement for BinanceWebSocket with same interface.
    Optimized for EU/Dublin VPS location.
    
    Features:
        - Auto-reconnect with exponential backoff (1s → 2s → 4s ... max 30s)
        - 30-second rolling price change calculation
        - Connection status tracking
        - Low-latency callbacks (~20-40ms from Dublin VPS)
    """
    
    # Coinbase Exchange WebSocket endpoint
    WS_URL = "wss://ws-feed.exchange.coinbase.com"
    
    # Exponential backoff settings
    INITIAL_BACKOFF = 1.0  # seconds
    MAX_BACKOFF = 30.0     # seconds
    BACKOFF_MULTIPLIER = 2.0
    
    # Price history window for change calculation (30 seconds)
    PRICE_HISTORY_WINDOW = 30.0
    
    def __init__(
        self,
        on_price_update: Callable[[float, float], None],
        on_connection_change: Optional[Callable[[bool], None]] = None,
        product_id: str = "BTC-USD"
    ):
        """
        Initialize the Coinbase WebSocket feed.
        
        Args:
            on_price_update: Callback for price updates.
                Receives (current_price, change_30s_percent) where:
                - current_price: Current BTC price in USD
                - change_30s_percent: Percentage change over last 30 seconds
            on_connection_change: Optional callback for connection status changes.
                Receives (is_connected: bool)
            product_id: Trading pair to subscribe to (default: BTC-USD)
        """
        self.on_price_update = on_price_update
        self.on_connection_change = on_connection_change
        self.product_id = product_id
        
        # Connection state
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._is_connected = False
        self._is_running = False
        self._connection_task: Optional[asyncio.Task] = None
        
        # Price history for 30s change calculation
        # Stores tuples of (timestamp, price)
        self._price_history: deque[tuple[float, float]] = deque()
        
        # Current price state
        self._current_price: Optional[float] = None
        self._last_price_time: Optional[float] = None
        
        # Reconnection state
        self._reconnect_delay = self.INITIAL_BACKOFF
        
        # Stats for monitoring
        self._messages_received = 0
        self._connection_attempts = 0
        self._start_time: Optional[float] = None
    
    @property
    def is_connected(self) -> bool:
        """Return True if currently connected to Coinbase WebSocket."""
        return self._is_connected
    
    @property
    def current_price(self) -> Optional[float]:
        """Return the most recent BTC price, or None if not yet received."""
        return self._current_price
    
    async def connect(self) -> None:
        """
        Start the WebSocket connection with auto-reconnect.
        
        This method returns immediately and runs the connection
        loop in a background task. Use close() to stop.
        """
        if self._is_running:
            logger.warning("CoinbaseWebSocket already running")
            return
        
        self._is_running = True
        self._start_time = time.time()
        self._connection_task = asyncio.create_task(
            self._connection_loop(),
            name="coinbase_ws_connection"
        )
        logger.info("CoinbaseWebSocket connection started")
    
    async def close(self) -> None:
        """
        Gracefully close the WebSocket connection.
        
        Stops the connection loop and closes any active WebSocket.
        Safe to call multiple times.
        """
        if not self._is_running:
            return
        
        logger.info("Closing CoinbaseWebSocket connection...")
        self._is_running = False
        
        # Cancel the connection task
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        
        # Close the websocket connection
        await self._disconnect()
        
        # Clear price history
        self._price_history.clear()
        self._current_price = None
        
        logger.info("CoinbaseWebSocket connection closed")
    
    async def _connection_loop(self) -> None:
        """
        Main connection loop with exponential backoff reconnection.
        
        Continuously attempts to maintain a connection to Coinbase.
        On disconnect, waits with exponential backoff before retrying.
        """
        while self._is_running:
            try:
                self._connection_attempts += 1
                logger.info(
                    f"Connecting to Coinbase WebSocket (attempt #{self._connection_attempts})..."
                )
                
                await self._connect_and_listen()
                
                # If we get here, connection closed normally
                if self._is_running:
                    logger.warning("WebSocket closed, will reconnect...")
                
            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except InvalidStatusCode as e:
                logger.error(f"Invalid status code from Coinbase: {e}")
                # Don't retry immediately on auth/server errors
                await asyncio.sleep(5)
                continue
            except Exception as e:
                logger.error(f"Unexpected error in connection loop: {e}", exc_info=True)
            
            # Exponential backoff before reconnect
            if self._is_running:
                logger.info(f"Reconnecting in {self._reconnect_delay:.1f}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * self.BACKOFF_MULTIPLIER,
                    self.MAX_BACKOFF
                )
    
    async def _connect_and_listen(self) -> None:
        """
        Establish WebSocket connection and listen for messages.
        
        Handles the WebSocket handshake, subscription, sets up connection state,
        and processes incoming price updates.
        """
        try:
            async with websockets.connect(
                self.WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            ) as websocket:
                self._websocket = websocket
                await self._set_connected(True)
                
                # Reset backoff on successful connection
                self._reconnect_delay = self.INITIAL_BACKOFF
                
                # Subscribe to ticker channel
                subscribe_msg = {
                    "type": "subscribe",
                    "product_ids": [self.product_id],
                    "channels": ["ticker"]
                }
                await websocket.send(json.dumps(subscribe_msg))
                logger.info(f"Subscribed to {self.product_id} ticker channel")
                
                # Listen for messages
                async for message in websocket:
                    if not self._is_running:
                        break
                    
                    await self._handle_message(message)
                    
        except Exception as e:
            await self._set_connected(False)
            raise
        finally:
            await self._set_connected(False)
            self._websocket = None
    
    async def _disconnect(self) -> None:
        """Close the current WebSocket connection."""
        if self._websocket is not None:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.debug(f"Error closing websocket: {e}")
            finally:
                self._websocket = None
                await self._set_connected(False)
    
    async def _set_connected(self, connected: bool) -> None:
        """
        Update connection state and notify callback if changed.
        
        Args:
            connected: New connection state
        """
        if self._is_connected != connected:
            self._is_connected = connected
            status = "connected" if connected else "disconnected"
            logger.info(f"Coinbase WebSocket {status}")
            
            if self.on_connection_change:
                try:
                    self.on_connection_change(connected)
                except Exception as e:
                    logger.error(f"Error in connection change callback: {e}")
    
    async def _handle_message(self, message: str) -> None:
        """
        Process incoming WebSocket message.
        
        Parses the price update, updates price history, calculates
        30-second change, and triggers the callback.
        
        Args:
            message: Raw JSON message from WebSocket
        """
        receive_time = time.time()
        
        try:
            data = json.loads(message)
            
            # Handle different message types
            msg_type = data.get("type")
            
            if msg_type == "subscriptions":
                logger.debug(f"Subscription confirmed: {data}")
                return
            
            if msg_type == "heartbeat":
                # Coinbase sends heartbeats
                return
            
            if msg_type != "ticker":
                # Skip non-ticker messages
                return
            
            # Extract price from ticker message
            # Coinbase format: {"type":"ticker","product_id":"BTC-USD","price":"50000.00",...}
            if "price" not in data:
                logger.warning(f"Unexpected ticker format: {data}")
                return
            
            price = float(data["price"])
            
            # Calculate latency (Coinbase doesn't provide server timestamp in ticker)
            # We use local receive time as proxy
            # In production, you might want to use sequence numbers for ordering
            
            # Update price history
            self._update_price_history(price, receive_time)
            
            # Calculate 30-second change
            change_30s = self._calculate_30s_change(price)
            
            # Update current price
            self._current_price = price
            self._last_price_time = receive_time
            self._messages_received += 1
            
            # Log stats periodically
            if self._messages_received % 60 == 0:
                logger.debug(
                    f"Message #{self._messages_received}: "
                    f"price=${price:,.2f}, 30s_change={change_30s:+.4f}%"
                )
            
            # Trigger callback (handle both sync and async)
            try:
                result = self.on_price_update(price, change_30s)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.error(f"Error in price update callback: {e}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except ValueError as e:
            logger.error(f"Failed to parse price value: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
    
    def _update_price_history(self, price: float, timestamp: float) -> None:
        """
        Add price to history and remove old entries outside the window.
        
        Args:
            price: Current price
            timestamp: Unix timestamp of the price
        """
        self._price_history.append((timestamp, price))
        
        # Remove entries older than PRICE_HISTORY_WINDOW seconds
        cutoff_time = timestamp - self.PRICE_HISTORY_WINDOW
        while self._price_history and self._price_history[0][0] < cutoff_time:
            self._price_history.popleft()
    
    def _calculate_30s_change(self, current_price: float) -> float:
        """
        Calculate percentage change over the last 30 seconds.
        
        Args:
            current_price: Current BTC price
            
        Returns:
            Percentage change from 30 seconds ago (0.0 if no history)
        """
        if len(self._price_history) < 2:
            return 0.0
        
        # Get the oldest price in our window (closest to 30s ago)
        oldest_price = self._price_history[0][1]
        
        if oldest_price == 0:
            return 0.0
        
        return ((current_price - oldest_price) / oldest_price) * 100.0
    
    def get_stats(self) -> dict:
        """
        Get connection and message statistics.
        
        Returns:
            Dictionary with stats including:
            - is_connected: Current connection state
            - messages_received: Total messages received
            - connection_attempts: Number of connection attempts
            - current_price: Last known price
            - uptime_seconds: Connection uptime
        """
        uptime = 0.0
        if self._start_time:
            uptime = time.time() - self._start_time
        
        return {
            "is_connected": self._is_connected,
            "messages_received": self._messages_received,
            "connection_attempts": self._connection_attempts,
            "current_price": self._current_price,
            "uptime_seconds": round(uptime, 2),
            "price_history_points": len(self._price_history),
            "exchange": "coinbase",
            "product_id": self.product_id,
        }


# Example usage
async def main():
    """Example demonstrating CoinbaseWebSocket usage."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Price update callback
    def on_price(price: float, change_30s: float) -> None:
        direction = "📈" if change_30s >= 0 else "📉"
        print(
            f"{direction} BTC: ${price:,.2f} "
            f"(30s: {change_30s:+.4f}%)"
        )
    
    # Connection status callback
    def on_connection_change(connected: bool) -> None:
        status = "🟢 CONNECTED" if connected else "🔴 DISCONNECTED"
        print(f"Connection status: {status}")
    
    # Create and start the WebSocket feed
    ws = CoinbaseWebSocket(
        on_price_update=on_price,
        on_connection_change=on_connection_change,
        product_id="BTC-USD"
    )
    
    try:
        print("Starting Coinbase WebSocket feed...")
        print("Press Ctrl+C to stop\n")
        
        await ws.connect()
        
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
            
            # Print stats every 10 seconds
            if int(time.time()) % 10 == 0:
                stats = ws.get_stats()
                logger.info(f"Stats: {stats}")
                
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await ws.close()
        print(f"\nFinal stats: {ws.get_stats()}")


if __name__ == "__main__":
    asyncio.run(main())
