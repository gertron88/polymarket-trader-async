"""
Gamma API client for Polymarket market discovery.

Provides real-time market data, active market discovery, and
BTC-prediction market resolution for the trading bot.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import aiohttp
from aiohttp import ClientTimeout

logger = logging.getLogger(__name__)


@dataclass
class TradingWindow:
    """Represents a 5-minute BTC prediction market window."""
    timestamp: int  # Unix timestamp of window start
    market_id: str  # Gamma market ID
    up_token: str   # CLOB token ID for UP outcome
    down_token: str # CLOB token ID for DOWN outcome
    up_price: float
    down_price: float
    end_time: datetime
    
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
    Client for Polymarket's Gamma API.
    
    Used for:
    - Discovering active BTC prediction markets
    - Getting market metadata (token IDs, timing)
    - Historical market data
    
    API Docs: https://docs.polymarket.com/#gamma-api
    """
    
    BASE_URL = "https://gamma-api.polymarket.com"
    TIMEOUT = ClientTimeout(total=10.0, connect=5.0)
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Any] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=30)
    
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
        """Close session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("GammaAPIClient closed")
    
    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make GET request to Gamma API."""
        if not self.session:
            await self.initialize()
        
        url = f"{self.BASE_URL}{endpoint}"
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"Gamma API error: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Gamma API request failed: {e}")
            return None
    
    async def get_active_markets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get all currently active markets.
        
        Args:
            limit: Maximum number of markets to return
            
        Returns:
            List of market dictionaries
        """
        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": limit
        }
        
        data = await self._get("/markets", params)
        return data.get("markets", []) if data else []
    
    async def get_btc_prediction_windows(self) -> List[TradingWindow]:
        """
        Find active BTC 5-minute prediction markets.
        
        Searches for markets matching:
        - "BTC" in title
        - 5-minute resolution
        - Binary (UP/DOWN) outcomes
        - Currently active
        
        Returns:
            List of TradingWindow objects ready for trading
        """
        markets = await self.get_active_markets(limit=100)
        windows = []
        
        for market in markets:
            # Filter for BTC 5-min prediction markets
            title = market.get("question", "").lower()
            description = market.get("description", "").lower()
            
            if not self._is_btc_5min_market(title, description):
                continue
            
            # Extract tokens
            tokens = market.get("tokens", [])
            if len(tokens) != 2:
                continue  # Must be binary market
            
            up_token = None
            down_token = None
            
            for token in tokens:
                outcome = token.get("outcome", "").upper()
                if outcome in ["YES", "UP", "HIGHER"]:
                    up_token = token.get("token_id")
                elif outcome in ["NO", "DOWN", "LOWER"]:
                    down_token = token.get("token_id")
            
            if not up_token or not down_token:
                continue
            
            # Get current prices from market
            up_price = float(tokens[0].get("price", 0.5))
            down_price = float(tokens[1].get("price", 0.5))
            
            # Determine window timestamp from market end time
            end_time_str = market.get("endDate")
            if end_time_str:
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                # 5-minute window ends at end_time, starts 5 min before
                window_start = end_time - timedelta(minutes=5)
                timestamp = int(window_start.timestamp())
            else:
                # Fallback: round current time to 5-min boundary
                now = datetime.utcnow()
                timestamp = int(now.timestamp() // 300 * 300)
                end_time = datetime.fromtimestamp(timestamp + 300)
            
            window = TradingWindow(
                timestamp=timestamp,
                market_id=market.get("id"),
                up_token=up_token,
                down_token=down_token,
                up_price=up_price,
                down_price=down_price,
                end_time=end_time
            )
            
            windows.append(window)
        
        # Sort by timestamp (earliest first)
        windows.sort(key=lambda w: w.timestamp)
        
        logger.info(f"Found {len(windows)} active BTC prediction windows")
        return windows
    
    def _is_btc_5min_market(self, title: str, description: str) -> bool:
        """
        Check if market is a BTC 5-minute prediction market.
        
        Args:
            title: Market question/title
            description: Market description
            
        Returns:
            True if this is a BTC 5-min prediction market
        """
        text = f"{title} {description}"
        
        # Must contain BTC/Bitcoin
        has_btc = any(term in text for term in [
            "btc", "bitcoin", "bitcoin price"
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
    
    async def get_current_window(self) -> Optional[TradingWindow]:
        """
        Get the currently active trading window.
        
        Returns:
            TradingWindow if one is active, None otherwise
        """
        windows = await self.get_btc_prediction_windows()
        
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
    
    async def get_order_book(self, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order book for a specific token.
        
        Note: This uses the CLOB API, not Gamma.
        Kept here for convenience.
        
        Args:
            token_id: CLOB token ID
            
        Returns:
            Order book data or None
        """
        # This would call CLOB API - stub for now
        logger.debug(f"Order book lookup for {token_id} (use CLOB WebSocket instead)")
        return None


# Example usage
async def main():
    """Test Gamma API client."""
    logging.basicConfig(level=logging.INFO)
    
    client = GammaAPIClient()
    await client.initialize()
    
    try:
        print("Fetching BTC prediction windows...")
        windows = await client.get_btc_prediction_windows()
        
        for window in windows[:5]:  # Show first 5
            print(f"\nWindow {window.timestamp}:")
            print(f"  Market: {window.market_id}")
            print(f"  UP: {window.up_token[:20]}... @ ${window.up_price:.4f}")
            print(f"  DOWN: {window.down_token[:20]}... @ ${window.down_price:.4f}")
            print(f"  Combined: ${window.combined_price:.4f} (edge: {window.edge:.4f})")
            print(f"  Active: {window.is_active}")
    
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
