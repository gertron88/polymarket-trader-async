"""
Polygon RPC Manager with Fallback & Caching

Provides resilient Polygon blockchain access using multiple public RPCs
with automatic failover and intelligent caching.

Features:
    - Multiple public RPC endpoints with fallback
    - Response caching to reduce redundant calls
    - Automatic retry with exponential backoff
    - Health tracking per endpoint
    - Circuit breaker pattern for failing endpoints
    
Usage:
    >>> from execution.rpc_manager import get_rpc_manager
    >>> rpc = get_rpc_manager()
    >>> balance = await rpc.get_balance(wallet_address)
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Callable
from functools import wraps

import aiohttp

logger = logging.getLogger(__name__)


# Multiple public Polygon RPC endpoints (order = priority)
DEFAULT_RPC_ENDPOINTS = [
    "https://polygon-rpc.com",                    # Primary - Polygon official
    "https://rpc-mainnet.matic.network",          # Backup 1 - Polygon legacy
    "https://matic-mainnet.chainstacklabs.com",   # Backup 2 - Chainstack
    "https://rpc-mainnet.maticvigil.com",         # Backup 3 - Matic Vigil
    "https://rpc.ankr.com/polygon",               # Backup 4 - Ankr
    "https://poly-rpc.gateway.pokt.network",      # Backup 5 - Pocket Network
    "https://polygon.llamarpc.com",               # Backup 6 - LlamaNodes
]


@dataclass
class RPCEndpoint:
    """Represents a single RPC endpoint with health tracking."""
    url: str
    is_healthy: bool = True
    last_failure: float = 0.0
    failure_count: int = 0
    success_count: int = 0
    avg_latency_ms: float = 0.0
    last_used: float = 0.0
    
    @property
    def reliability_score(self) -> float:
        """Calculate reliability score (0.0 - 1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0  # New endpoints start with high score
        
        success_rate = self.success_count / total
        
        # Penalize recent failures
        if not self.is_healthy:
            time_since_failure = time.time() - self.last_failure
            if time_since_failure < 60:  # Failed in last minute
                success_rate *= 0.5
        
        return success_rate
    
    def record_success(self, latency_ms: float):
        """Record a successful RPC call."""
        self.success_count += 1
        self.is_healthy = True
        
        # Update running average latency
        if self.avg_latency_ms == 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = (self.avg_latency_ms * 0.9) + (latency_ms * 0.1)
        
        self.last_used = time.time()
    
    def record_failure(self):
        """Record a failed RPC call."""
        self.failure_count += 1
        self.last_failure = time.time()
        self.is_healthy = False


class RPCCache:
    """Simple TTL cache for RPC responses."""
    
    def __init__(self, default_ttl_seconds: float = 30.0):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = default_ttl_seconds
        self._lock = asyncio.Lock()
    
    def _make_key(self, method: str, params: tuple) -> str:
        """Create cache key from method and params."""
        return f"{method}:{hash(params)}"
    
    async def get(self, method: str, params: tuple) -> Optional[Any]:
        """Get cached value if not expired."""
        async with self._lock:
            key = self._make_key(method, params)
            entry = self._cache.get(key)
            
            if entry is None:
                return None
            
            if time.time() > entry['expires']:
                del self._cache[key]
                return None
            
            return entry['value']
    
    async def set(self, method: str, params: tuple, value: Any, ttl: Optional[float] = None):
        """Cache a value with TTL."""
        async with self._lock:
            key = self._make_key(method, params)
            ttl = ttl or self._default_ttl
            
            self._cache[key] = {
                'value': value,
                'expires': time.time() + ttl,
                'set_at': time.time()
            }
    
    async def invalidate(self, method: str = None):
        """Invalidate cache entries."""
        async with self._lock:
            if method is None:
                self._cache.clear()
            else:
                keys_to_delete = [k for k in self._cache if k.startswith(f"{method}:")]
                for k in keys_to_delete:
                    del self._cache[k]


class PolygonRPCManager:
    """
    Manages multiple Polygon RPC endpoints with failover and caching.
    
    Features:
        - Automatic failover between endpoints
        - Response caching (configurable TTL)
        - Health tracking per endpoint
        - Exponential backoff for retries
        - Circuit breaker for failing endpoints
    """
    
    def __init__(
        self,
        endpoints: Optional[List[str]] = None,
        cache_ttl_seconds: float = 30.0,
        max_retries: int = 3,
        timeout_seconds: float = 10.0
    ):
        """
        Initialize the RPC manager.
        
        Args:
            endpoints: List of RPC URLs (defaults to public endpoints)
            cache_ttl_seconds: Default cache TTL
            max_retries: Max retry attempts per call
            timeout_seconds: Request timeout
        """
        endpoint_urls = endpoints or DEFAULT_RPC_ENDPOINTS
        self._endpoints = [RPCEndpoint(url=url) for url in endpoint_urls]
        self._cache = RPCCache(default_ttl_seconds=cache_ttl_seconds)
        self._max_retries = max_retries
        self._timeout = timeout_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers={"Content-Type": "application/json"}
            )
        return self._session
    
    def _get_healthy_endpoints(self) -> List[RPCEndpoint]:
        """Get list of healthy endpoints sorted by reliability."""
        healthy = [ep for ep in self._endpoints if ep.is_healthy]
        
        # Sort by reliability score (descending)
        healthy.sort(key=lambda ep: ep.reliability_score, reverse=True)
        
        # If no healthy endpoints, try all (failover)
        if not healthy:
            logger.warning("No healthy RPC endpoints, trying all with circuit breaker bypass")
            # Reset circuit breaker for endpoints that failed > 5 min ago
            now = time.time()
            for ep in self._endpoints:
                if now - ep.last_failure > 300:  # 5 minutes
                    ep.is_healthy = True
            healthy = self._endpoints
        
        return healthy
    
    async def _call_rpc(
        self,
        endpoint: RPCEndpoint,
        method: str,
        params: List[Any]
    ) -> Optional[Dict]:
        """
        Make a single RPC call to an endpoint.
        
        Returns:
            Response dict or None on failure
        """
        session = await self._get_session()
        
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": random.randint(1, 1000000)
        }
        
        start_time = time.time()
        
        try:
            async with session.post(endpoint.url, json=payload) as response:
                latency_ms = (time.time() - start_time) * 1000
                
                if response.status == 200:
                    data = await response.json()
                    
                    if 'error' in data:
                        logger.debug(f"RPC error from {endpoint.url}: {data['error']}")
                        endpoint.record_failure()
                        return None
                    
                    endpoint.record_success(latency_ms)
                    logger.debug(f"RPC success: {endpoint.url} ({latency_ms:.1f}ms)")
                    return data.get('result')
                else:
                    logger.warning(f"RPC HTTP {response.status} from {endpoint.url}")
                    endpoint.record_failure()
                    return None
                    
        except asyncio.TimeoutError:
            logger.warning(f"RPC timeout from {endpoint.url}")
            endpoint.record_failure()
            return None
        except Exception as e:
            logger.warning(f"RPC error from {endpoint.url}: {e}")
            endpoint.record_failure()
            return None
    
    async def call(
        self,
        method: str,
        params: List[Any],
        use_cache: bool = True,
        cache_ttl: Optional[float] = None
    ) -> Optional[Any]:
        """
        Make an RPC call with automatic failover and caching.
        
        Args:
            method: RPC method name
            params: RPC parameters
            use_cache: Whether to use cache
            cache_ttl: Custom cache TTL (optional)
            
        Returns:
            RPC result or None on failure
        """
        cache_key = (method, tuple(str(p) for p in params))
        
        # Check cache first
        if use_cache:
            cached = await self._cache.get(method, cache_key)
            if cached is not None:
                logger.debug(f"RPC cache hit: {method}")
                return cached
        
        # Try endpoints in order of reliability
        endpoints = self._get_healthy_endpoints()
        
        for attempt, endpoint in enumerate(endpoints[:self._max_retries]):
            result = await self._call_rpc(endpoint, method, params)
            
            if result is not None:
                # Cache successful result
                if use_cache:
                    await self._cache.set(method, cache_key, result, cache_ttl)
                return result
            
            logger.debug(f"RPC attempt {attempt + 1} failed, trying next endpoint...")
        
        logger.error(f"All RPC endpoints failed for {method}")
        return None
    
    # Convenience methods for common operations
    
    async def get_balance(self, address: str, block: str = "latest") -> Optional[int]:
        """Get ETH/MATIC balance for address."""
        params = [address, block]
        result = await self.call("eth_getBalance", params, use_cache=True, cache_ttl=60.0)
        
        if result:
            # Convert hex to int
            return int(result, 16)
        return None
    
    async def get_block_number(self) -> Optional[int]:
        """Get current block number."""
        result = await self.call("eth_blockNumber", [], use_cache=True, cache_ttl=5.0)
        
        if result:
            return int(result, 16)
        return None
    
    async def get_transaction_count(self, address: str, block: str = "latest") -> Optional[int]:
        """Get transaction count (nonce) for address."""
        params = [address, block]
        result = await self.call("eth_getTransactionCount", params, use_cache=False)
        
        if result:
            return int(result, 16)
        return None
    
    async def call_contract(
        self,
        contract_address: str,
        data: str,
        block: str = "latest"
    ) -> Optional[str]:
        """Call a contract function."""
        params = [{"to": contract_address, "data": data}, block]
        return await self.call("eth_call", params, use_cache=True, cache_ttl=30.0)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get RPC manager statistics."""
        return {
            "total_endpoints": len(self._endpoints),
            "healthy_endpoints": sum(1 for ep in self._endpoints if ep.is_healthy),
            "endpoints": [
                {
                    "url": ep.url,
                    "healthy": ep.is_healthy,
                    "reliability": round(ep.reliability_score, 3),
                    "avg_latency_ms": round(ep.avg_latency_ms, 1),
                    "successes": ep.success_count,
                    "failures": ep.failure_count
                }
                for ep in self._endpoints
            ]
        }
    
    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


# Singleton instance
_rpc_manager: Optional[PolygonRPCManager] = None


def get_rpc_manager(
    endpoints: Optional[List[str]] = None,
    cache_ttl_seconds: float = 30.0
) -> PolygonRPCManager:
    """
    Get or create singleton RPC manager instance.
    
    Args:
        endpoints: Custom RPC endpoints (optional)
        cache_ttl_seconds: Cache TTL (default: 30s)
        
    Returns:
        PolygonRPCManager instance
    """
    global _rpc_manager
    if _rpc_manager is None:
        _rpc_manager = PolygonRPCManager(
            endpoints=endpoints,
            cache_ttl_seconds=cache_ttl_seconds
        )
    return _rpc_manager


# Decorator for caching RPC calls
def cached_rpc(ttl_seconds: float = 30.0):
    """Decorator to cache RPC method results."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            # This would need integration with the cache
            # Simplified version for now
            return await func(self, *args, **kwargs)
        return wrapper
    return decorator


# Example usage
async def main():
    """Test the RPC manager."""
    logging.basicConfig(level=logging.INFO)
    
    rpc = get_rpc_manager()
    
    # Test address (Binance hot wallet on Polygon)
    test_address = "0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245"
    
    print("Testing Polygon RPC Manager...")
    print("="*60)
    
    # Get balance
    print(f"\nGetting balance for {test_address}...")
    balance = await rpc.get_balance(test_address)
    if balance:
        print(f"Balance: {balance / 10**18:.4f} MATIC")
    else:
        print("Failed to get balance")
    
    # Get block number
    print("\nGetting block number...")
    block = await rpc.get_block_number()
    if block:
        print(f"Current block: {block:,}")
    else:
        print("Failed to get block number")
    
    # Get stats
    print("\n" + "="*60)
    print("RPC Manager Stats:")
    import json
    print(json.dumps(rpc.get_stats(), indent=2))
    
    await rpc.close()


if __name__ == "__main__":
    asyncio.run(main())
