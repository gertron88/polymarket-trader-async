"""
Async order execution module for Polymarket trading.

Provides concurrent order placement, fill tracking, and retry logic
for high-frequency trading on Polymarket.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, Callable
from enum import Enum

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

# Type hints for py-clob-client types
# These would normally be imported from py_clob_client
class ClobClient:
    """Placeholder for Polymarket CLOB client."""
    
    def create_order(self, order_args: Dict[str, Any]) -> Dict[str, Any]:
        """Create an order on the CLOB."""
        raise NotImplementedError("ClobClient not implemented")
    
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Get order status by ID."""
        raise NotImplementedError("ClobClient not implemented")


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class FillInfo:
    """
    Information about an order fill.
    
    Attributes:
        order_id: The unique order identifier
        status: Current order status
        filled_size: Total size filled
        avg_fill_price: Average fill price
        remaining_size: Size remaining to be filled
        timestamp: Unix timestamp of last update
        metadata: Additional fill metadata
    """
    order_id: str
    status: OrderStatus
    filled_size: float
    avg_fill_price: float
    remaining_size: float
    timestamp: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class OrderRequest:
    """
    Request to place an order.
    
    Attributes:
        token_id: The token identifier (market outcome)
        side: BUY or SELL
        size: Order size in number of contracts
        price: Limit price (0.0 - 1.0 for Polymarket)
        time_in_force: GTC, IOC, or FOK
    """
    token_id: str
    side: OrderSide
    size: float
    price: float
    time_in_force: str = "GTC"


class OrderExecutor:
    """
    Async order execution handler for Polymarket.
    
    Manages concurrent order placement, fill tracking, and connection pooling
    for high-frequency trading operations.
    
    Example:
        >>> client = ClobClient(...)  # Your CLOB client instance
        >>> executor = OrderExecutor(client)
        >>> await executor.initialize()
        >>> 
        >>> # Place concurrent entry orders
        >>> up_id, down_id = await executor.place_entry_orders(
        ...     up_token="0x123...",
        ...     down_token="0x456...",
        ...     size=100.0,
        ...     price=0.55
        ... )
        >>> 
        >>> # Check fills
        >>> fill_info = await executor.check_fill(up_id)
        >>> await executor.close()
    
    Attributes:
        client: ClobClient instance for order operations
        session: aiohttp ClientSession for HTTP requests
        _logger: Logger instance for this class
        _connector: TCPConnector for connection pooling
    """
    
    def __init__(self, clob_client: ClobClient):
        """
        Initialize the OrderExecutor.
        
        Args:
            clob_client: Configured ClobClient instance for Polymarket API
        """
        self.client = clob_client
        self.session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[TCPConnector] = None
        self._logger = logging.getLogger(__name__)
        
        # Retry configuration
        self._max_retries = 3
        self._retry_delay = 0.1  # seconds
        self._backoff_multiplier = 2.0
        
        # Connection pool settings
        self._pool_limit = 20
        self._pool_limit_per_host = 10
        self._keepalive_timeout = 30
        
        # Request timeout
        self._request_timeout = ClientTimeout(total=10.0, connect=5.0)
    
    async def initialize(self) -> None:
        """
        Initialize the aiohttp session with connection pooling.
        
        Creates a session with TCP connection pooling optimized for
        low-latency trading operations.
        
        Raises:
            RuntimeError: If session is already initialized
        """
        if self.session is not None:
            raise RuntimeError("Session already initialized")
        
        # Create TCP connector with pooling
        self._connector = TCPConnector(
            limit=self._pool_limit,
            limit_per_host=self._pool_limit_per_host,
            keepalive_timeout=self._keepalive_timeout,
            enable_cleanup_closed=True,
            force_close=False,
        )
        
        # Create session with connection pooling
        self.session = aiohttp.ClientSession(
            connector=self._connector,
            timeout=self._request_timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        
        self._logger.info("OrderExecutor initialized with connection pooling")
    
    async def place_entry_orders(
        self,
        up_token: str,
        down_token: str,
        size: float,
        price: float
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Place both UP and DOWN entry orders concurrently.
        
        Places BUY orders on both sides of a binary market simultaneously
        for arbitrage or hedging strategies.
        
        Args:
            up_token: Token ID for the UP outcome
            down_token: Token ID for the DOWN outcome
            size: Number of contracts to buy for each order
            price: Limit price for both orders (0.0 - 1.0)
            
        Returns:
            Tuple of (up_order_id, down_order_id). Either may be None
            if the corresponding order failed.
            
        Raises:
            RuntimeError: If session not initialized
            
        Example:
            >>> up_id, down_id = await executor.place_entry_orders(
            ...     up_token="0xabc...",
            ...     down_token="0xdef...",
            ...     size=100.0,
            ...     price=0.52
            ... )
        """
        if self.session is None:
            raise RuntimeError("Session not initialized. Call initialize() first.")
        
        self._logger.info(
            f"Placing concurrent entry orders: UP={up_token[:10]}..., "
            f"DOWN={down_token[:10]}..., size={size}, price={price}"
        )
        
        # Create order requests
        up_order = OrderRequest(
            token_id=up_token,
            side=OrderSide.BUY,
            size=size,
            price=price,
        )
        
        down_order = OrderRequest(
            token_id=down_token,
            side=OrderSide.BUY,
            size=size,
            price=price,
        )
        
        # Place both orders concurrently
        results = await asyncio.gather(
            self._place_order_with_retry(up_order),
            self._place_order_with_retry(down_order),
            return_exceptions=True
        )
        
        # Process results
        up_order_id: Optional[str] = None
        down_order_id: Optional[str] = None
        
        if isinstance(results[0], str):
            up_order_id = results[0]
        elif isinstance(results[0], Exception):
            self._logger.error(f"UP order failed: {results[0]}")
        
        if isinstance(results[1], str):
            down_order_id = results[1]
        elif isinstance(results[1], Exception):
            self._logger.error(f"DOWN order failed: {results[1]}")
        
        self._logger.info(
            f"Entry orders placed: UP={up_order_id is not None}, "
            f"DOWN={down_order_id is not None}"
        )
        
        return (up_order_id, down_order_id)
    
    async def place_exit_order(
        self,
        token_id: str,
        size: float,
        price: float
    ) -> Optional[str]:
        """
        Place a sell (exit) order.
        
        Places a SELL order to exit an existing position.
        
        Args:
            token_id: Token ID to sell
            size: Number of contracts to sell
            price: Limit price (0.0 - 1.0)
            
        Returns:
            Order ID if successful, None otherwise
            
        Raises:
            RuntimeError: If session not initialized
            
        Example:
            >>> order_id = await executor.place_exit_order(
            ...     token_id="0xabc...",
            ...     size=100.0,
            ...     price=0.58
            ... )
        """
        if self.session is None:
            raise RuntimeError("Session not initialized. Call initialize() first.")
        
        self._logger.info(
            f"Placing exit order: token={token_id[:10]}..., size={size}, price={price}"
        )
        
        order = OrderRequest(
            token_id=token_id,
            side=OrderSide.SELL,
            size=size,
            price=price,
        )
        
        try:
            order_id = await self._place_order_with_retry(order)
            return order_id
        except Exception as e:
            self._logger.error(f"Exit order failed: {e}")
            return None
    
    async def check_fill(self, order_id: str) -> Optional[FillInfo]:
        """
        Check if an order has been filled.
        
        Queries the CLOB for current order status and fill information.
        
        Args:
            order_id: The order ID to check
            
        Returns:
            FillInfo if order found, None otherwise
            
        Raises:
            RuntimeError: If session not initialized
            
        Example:
            >>> fill_info = await executor.check_fill("order-123")
            >>> if fill_info and fill_info.status == OrderStatus.FILLED:
            ...     print(f"Order filled at {fill_info.avg_fill_price}")
        """
        if self.session is None:
            raise RuntimeError("Session not initialized. Call initialize() first.")
        
        if not order_id:
            self._logger.warning("Empty order_id provided to check_fill")
            return None
        
        try:
            # Query order status from CLOB
            order_data = await self._fetch_order_status(order_id)
            
            if order_data is None:
                return None
            
            fill_info = self._parse_fill_info(order_id, order_data)
            return fill_info
            
        except Exception as e:
            self._logger.error(f"Error checking fill for {order_id}: {e}")
            return None
    
    async def close(self) -> None:
        """
        Close the aiohttp session and cleanup resources.
        
        Should be called on shutdown to properly release connections.
        Safe to call multiple times.
        """
        if self.session is not None:
            try:
                await self.session.close()
                self._logger.info("OrderExecutor session closed")
            except Exception as e:
                self._logger.error(f"Error closing session: {e}")
            finally:
                self.session = None
        
        if self._connector is not None:
            try:
                await self._connector.close()
            except Exception as e:
                self._logger.error(f"Error closing connector: {e}")
            finally:
                self._connector = None
    
    async def _place_order_with_retry(
        self,
        order: OrderRequest
    ) -> Optional[str]:
        """
        Place an order with retry logic.
        
        Attempts to place an order with exponential backoff on failure.
        
        Args:
            order: The order request to place
            
        Returns:
            Order ID if successful, None if all retries exhausted
        """
        delay = self._retry_delay
        last_error: Optional[Exception] = None
        
        for attempt in range(self._max_retries):
            try:
                order_id = await self._execute_order_placement(order)
                if order_id:
                    self._logger.debug(
                        f"Order placed successfully on attempt {attempt + 1}: {order_id}"
                    )
                    return order_id
            except Exception as e:
                last_error = e
                self._logger.warning(
                    f"Order placement attempt {attempt + 1} failed for "
                    f"{order.token_id[:10]}...: {e}"
                )
                
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= self._backoff_multiplier
        
        self._logger.error(
            f"Order placement failed after {self._max_retries} attempts: {last_error}"
        )
        raise last_error if last_error else RuntimeError("Order placement failed")
    
    async def _execute_order_placement(
        self,
        order: OrderRequest
    ) -> Optional[str]:
        """
        Execute a single order placement via the CLOB client.
        
        Args:
            order: The order request
            
        Returns:
            Order ID if successful, None otherwise
        """
        # Run blocking CLOB client call in thread pool
        loop = asyncio.get_event_loop()
        
        def _create_order():
            order_args = {
                "token_id": order.token_id,
                "side": order.side.value,
                "size": order.size,
                "price": order.price,
                "time_in_force": order.time_in_force,
            }
            return self.client.create_order(order_args)
        
        try:
            response = await loop.run_in_executor(None, _create_order)
            
            if response and "order_id" in response:
                return response["order_id"]
            elif response and "id" in response:
                return response["id"]
            else:
                self._logger.warning(f"Unexpected order response format: {response}")
                return None
                
        except Exception as e:
            self._logger.error(f"Order creation error: {e}")
            raise
    
    async def _fetch_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch order status from the CLOB.
        
        Args:
            order_id: The order ID to query
            
        Returns:
            Order data dict if found, None otherwise
        """
        loop = asyncio.get_event_loop()
        
        def _get_order():
            return self.client.get_order(order_id)
        
        try:
            response = await loop.run_in_executor(None, _get_order)
            return response
        except Exception as e:
            self._logger.error(f"Error fetching order {order_id}: {e}")
            raise
    
    def _parse_fill_info(
        self,
        order_id: str,
        order_data: Dict[str, Any]
    ) -> FillInfo:
        """
        Parse order data into FillInfo.
        
        Args:
            order_id: The order ID
            order_data: Raw order data from CLOB
            
        Returns:
            Parsed FillInfo object
        """
        # Map string status to enum
        status_str = order_data.get("status", "PENDING").upper()
        try:
            status = OrderStatus[status_str]
        except KeyError:
            status = OrderStatus.PENDING
        
        # Extract fill data
        filled_size = float(order_data.get("filled_size", 0.0))
        avg_fill_price = float(order_data.get("avg_fill_price", 0.0))
        remaining_size = float(order_data.get("remaining_size", 0.0))
        timestamp = float(order_data.get("timestamp", 0.0))
        
        return FillInfo(
            order_id=order_id,
            status=status,
            filled_size=filled_size,
            avg_fill_price=avg_fill_price,
            remaining_size=remaining_size,
            timestamp=timestamp,
            metadata=order_data.get("metadata"),
        )
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
