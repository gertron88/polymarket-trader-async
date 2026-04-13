"""
Gamma API module for Polymarket market discovery.

Fetches active BTC prediction markets and filters for 5-minute windows.
Implements caching to avoid rate limits.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
import time

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger(__name__)


@dataclass
class MarketWindow:
    """Represents a 5-minute BTC prediction market window."""
    timestamp: int          # Unix timestamp of window start
    market_id: str          # Gamma market ID
    up_token: str          # CLOB token ID for UP outcome
    down_token: str        # CLOB token ID for DOWN outcome
    up_price: float
    down_price: float
    end_time: datetime
    question: str          # Market question/title
    
    @property
    def combined_price(self) -> float:
        """Sum of both tokens (should be < 1.0 for arbitrage)."""
        return self.up_price + self.down_price
    
    @property
    def edge(self) -> float:
        """Arbitrage edge (1.0 - combined)."""
        return 1.0 - self.combined_price
    
    @property
    def is_active(self) -> bool:
        """Check if window is still tradable."""
        return datetime.utcnow() < self.end_time


class GammaAPIClient:
    """
    Client for Polymarket's Gamma API with caching support.
    
    API Docs: https://docs.polymarket.com/#gamma-api
    """
    
    BASE_URL = "https://gamma-api.polymarket.com"
    TIMEOUT = ClientTimeout(total=10.0, connect=5.0)
    
    def __init__(self, cache_ttl_seconds: int = 30):
        """
        Initialize the Gamma API client.
        
        Args:
            cache_ttl_seconds: Time-to-live for cached market data
        """
        self.session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[float] = None
        self._cache_ttl = cache_ttl_seconds
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """Initialize aiohttp session."""
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=self.TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PolymarketTrader/1.0"
                }
            )
            logger.info("GammaAPIClient initialized")
    
    async def close(self):
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("GammaAPIClient closed")
    
    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """
        Make GET request to Gamma API.
        
        Args:
            endpoint: API endpoint (e.g., "/markets")
            params: Query parameters
            
        Returns:
            JSON response as dict or None on error
        """
        if not self.session:
            await self.initialize()
        
        url = f"{self.BASE_URL}{endpoint}"
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"Gamma API error: {response.status} for {endpoint}")
                    return None
        except asyncio.TimeoutError:
            logger.error(f"Gamma API timeout: {url}")
            return None
        except Exception as e:
            logger.error(f"Gamma API request failed: {e}")
            return None
    
    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid."""
        if self._cache_timestamp is None:
            return False
        return (time.time() - self._cache_timestamp) < self._cache_ttl
    
    async def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
        archived: bool = False,
        limit: int = 100,
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Fetch markets from Gamma API with optional caching.
        
        Args:
            active: Only active markets
            closed: Include closed markets
            archived: Include archived markets
            limit: Maximum markets to return
            use_cache: Use cached data if available
            
        Returns:
            List of market dictionaries
        """
        cache_key = f"markets_{active}_{closed}_{archived}_{limit}"
        
        # Check cache
        if use_cache and self._is_cache_valid() and cache_key in self._cache:
            logger.debug("Using cached market data")
            return self._cache[cache_key]
        
        params = {
            "active": "true" if active else "false",
            "closed": "true" if closed else "false",
            "archived": "true" if archived else "false",
            "limit": limit
        }
        
        data = await self._get("/markets", params)
        
        # Handle both dict with 'markets' key and direct list response
        if data is None:
            markets = []
        elif isinstance(data, list):
            # API returned list directly
            markets = data
        elif isinstance(data, dict):
            # API returned dict with 'markets' key
            markets = data.get("markets", [])
        else:
            logger.warning(f"Unexpected response type from Gamma API: {type(data)}")
            markets = []
        
        # Update cache
        async with self._lock:
            self._cache[cache_key] = markets
            self._cache_timestamp = time.time()
        
        logger.debug(f"Fetched {len(markets)} markets from Gamma API")
        return markets
    
    def _is_btc_5min_market(self, market: Dict[str, Any]) -> bool:
        """
        Check if market is a BTC 5-minute prediction market.
        
        Args:
            market: Market dictionary from Gamma API
            
        Returns:
            True if this is a BTC 5-min prediction market
        """
        title = market.get("question", "").lower()
        description = market.get("description", "").lower()
        text = f"{title} {description}"
        
        # Must contain BTC/Bitcoin
        has_btc = any(term in text for term in [
            "btc", "bitcoin"
        ])
        
        # Must be 5-minute prediction
        has_5min = any(term in text for term in [
            "5 minute", "5-minute", "5min", "five minute"
        ])
        
        # Must be prediction (price higher/lower)
        is_prediction = any(term in text for term in [
            "higher", "lower", "up", "down", "above", "below"
        ])
        
        return has_btc and has_5min and is_prediction
    
    def _extract_tokens(self, market: Dict[str, Any]) -> Optional[Tuple[str, str, float, float]]:
        """
        Extract UP/DOWN token IDs and prices from market.
        
        Args:
            market: Market dictionary
            
        Returns:
            Tuple of (up_token, down_token, up_price, down_price) or None
        """
        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            return None
        
        up_token = None
        down_token = None
        up_price = 0.5
        down_price = 0.5
        
        for token in tokens:
            outcome = token.get("outcome", "").upper()
            price = float(token.get("price", 0.5))
            token_id = token.get("token_id")
            
            if outcome in ["YES", "UP", "HIGHER"]:
                up_token = token_id
                up_price = price
            elif outcome in ["NO", "DOWN", "LOWER"]:
                down_token = token_id
                down_price = price
        
        if not up_token or not down_token:
            return None
        
        return (up_token, down_token, up_price, down_price)
    
    def _parse_window_time(self, market: Dict[str, Any]) -> Optional[Tuple[int, datetime]]:
        """
        Parse window start timestamp and end time from market.
        
        Args:
            market: Market dictionary
            
        Returns:
            Tuple of (timestamp, end_time) or None
        """
        end_time_str = market.get("endDate") or market.get("end_date")
        
        if end_time_str:
            try:
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                # 5-minute window ends at end_time, starts 5 min before
                window_start = end_time - timedelta(minutes=5)
                timestamp = int(window_start.timestamp())
                return (timestamp, end_time)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse end time: {e}")
        
        # Fallback: round current time to 5-min boundary
        now = datetime.utcnow()
        timestamp = int(now.timestamp() // 300 * 300)
        end_time = datetime.utcfromtimestamp(timestamp + 300)
        return (timestamp, end_time)
    
    async def get_btc_windows(
        self,
        use_cache: bool = True
    ) -> List[MarketWindow]:
        """
        Find all active BTC 5-minute prediction markets.
        
        Args:
            use_cache: Use cached data if available
            
        Returns:
            List of MarketWindow objects
        """
        markets = await self.get_markets(use_cache=use_cache)
        windows = []
        
        for market in markets:
            if not self._is_btc_5min_market(market):
                continue
            
            tokens = self._extract_tokens(market)
            if not tokens:
                continue
            
            up_token, down_token, up_price, down_price = tokens
            
            time_info = self._parse_window_time(market)
            if not time_info:
                continue
            
            timestamp, end_time = time_info
            
            window = MarketWindow(
                timestamp=timestamp,
                market_id=market.get("id", ""),
                up_token=up_token,
                down_token=down_token,
                up_price=up_price,
                down_price=down_price,
                end_time=end_time,
                question=market.get("question", "")
            )
            
            windows.append(window)
        
        # Sort by timestamp (earliest first)
        windows.sort(key=lambda w: w.timestamp)
        
        logger.info(f"Found {len(windows)} BTC 5-min prediction windows")
        return windows
    
    async def get_current_window(
        self,
        use_cache: bool = True
    ) -> Optional[MarketWindow]:
        """
        Get the currently active trading window.
        
        Args:
            use_cache: Use cached data if available
            
        Returns:
            MarketWindow if one is active, None otherwise
        """
        windows = await self.get_btc_windows(use_cache=use_cache)
        
        if not windows:
            return None
        
        # Return the first active window
        for window in windows:
            if window.is_active:
                return window
        
        # If none active, return the soonest upcoming
        return windows[0] if windows else None
    
    async def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Get specific market details by ID.
        
        Args:
            market_id: Gamma market ID
            
        Returns:
            Market dictionary or None
        """
        return await self._get(f"/markets/{market_id}")
    
    def invalidate_cache(self):
        """Invalidate the cache to force fresh fetch on next call."""
        self._cache.clear()
        self._cache_timestamp = None
        logger.debug("Cache invalidated")


# Convenience functions for direct usage
async def fetch_btc_windows(cache_ttl: int = 30) -> List[MarketWindow]:
    """
    Convenience function to fetch BTC windows without managing client.
    
    Args:
        cache_ttl: Cache time-to-live in seconds
        
    Returns:
        List of MarketWindow objects
    """
    client = GammaAPIClient(cache_ttl_seconds=cache_ttl)
    await client.initialize()
    try:
        return await client.get_btc_windows()
    finally:
        await client.close()


async def fetch_current_window(cache_ttl: int = 30) -> Optional[MarketWindow]:
    """
    Convenience function to get current window without managing client.
    
    Args:
        cache_ttl: Cache time-to-live in seconds
        
    Returns:
        Current MarketWindow or None
    """
    client = GammaAPIClient(cache_ttl_seconds=cache_ttl)
    await client.initialize()
    try:
        return await client.get_current_window()
    finally:
        await client.close()


# Example usage
if __name__ == "__main__":
    async def main():
        logging.basicConfig(level=logging.INFO)
        
        client = GammaAPIClient()
        await client.initialize()
        
        try:
            print("Fetching BTC prediction windows...")
            windows = await client.get_btc_windows()
            
            for window in windows[:5]:
                print(f"\nWindow {window.timestamp}:")
                print(f"  Question: {window.question[:60]}...")
                print(f"  Market: {window.market_id}")
                print(f"  UP Token: {window.up_token[:20]}... @ ${window.up_price:.4f}")
                print(f"  DOWN Token: {window.down_token[:20]}... @ ${window.down_price:.4f}")
                print(f"  Combined: ${window.combined_price:.4f} (edge: {window.edge:.4f})")
                print(f"  Active: {window.is_active}")
                print(f"  Ends: {window.end_time}")
        
        finally:
            await client.close()
    
    asyncio.run(main())
