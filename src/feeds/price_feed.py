"""
Price Feed Factory - Unified interface for multiple exchange WebSocket feeds.

Supports:
    - Binance (global, high latency from EU)
    - Coinbase (AWS-based, low latency from EU)
    
Usage:
    >>> from feeds.price_feed import create_price_feed
    >>> 
    >>> # Use Coinbase for low latency from EU
    >>> feed = create_price_feed('coinbase', on_price_update=handler)
    >>> 
    >>> # Or use Binance
    >>> feed = create_price_feed('binance', on_price_update=handler)
"""

from typing import Callable, Optional

from .binance_ws import BinanceWebSocket
from .coinbase_ws import CoinbaseWebSocket


def create_price_feed(
    exchange: str,
    on_price_update: Callable[[float, float], None],
    on_connection_change: Optional[Callable[[bool], None]] = None,
    **kwargs
):
    """
    Factory function to create appropriate price feed WebSocket.
    
    Args:
        exchange: Exchange name ('binance' or 'coinbase')
        on_price_update: Callback for price updates (price, change_30s)
        on_connection_change: Optional callback for connection status
        **kwargs: Additional arguments passed to feed constructor
        
    Returns:
        WebSocket feed instance
        
    Raises:
        ValueError: If exchange is not supported
    """
    exchange = exchange.lower()
    
    if exchange == 'binance':
        return BinanceWebSocket(
            on_price_update=on_price_update,
            on_connection_change=on_connection_change,
            **kwargs
        )
    elif exchange == 'coinbase':
        product_id = kwargs.get('product_id', 'BTC-USD')
        return CoinbaseWebSocket(
            on_price_update=on_price_update,
            on_connection_change=on_connection_change,
            product_id=product_id
        )
    else:
        raise ValueError(f"Unsupported exchange: {exchange}. Use 'binance' or 'coinbase'.")


def get_exchange_info(exchange: str) -> dict:
    """
    Get information about an exchange's WebSocket feed.
    
    Args:
        exchange: Exchange name
        
    Returns:
        Dictionary with exchange info
    """
    info = {
        'binance': {
            'name': 'Binance Futures',
            'websocket_url': 'wss://fstream.binance.com/ws/btcusdt@markPrice@1s',
            'location': 'Tokyo, Singapore (Asia)',
            'latency_from_dublin': '~150ms',
            'latency_from_quantvps': '~150ms',
            'recommendation': 'Use only if in Asia',
        },
        'coinbase': {
            'name': 'Coinbase Exchange',
            'websocket_url': 'wss://ws-feed.exchange.coinbase.com',
            'location': 'AWS (US East, EU West)',
            'latency_from_dublin': '~30ms',
            'latency_from_quantvps': '~5-15ms',
            'recommendation': 'RECOMMENDED for EU/Dublin',
        }
    }
    
    return info.get(exchange.lower(), {'error': 'Unknown exchange'})


# Compatibility alias for existing code
PriceFeed = create_price_feed
