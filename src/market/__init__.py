"""
Market module for Polymarket trading bot.

Provides market discovery via Gamma API for BTC prediction markets.
"""

from .gamma_api import (
    GammaAPIClient,
    MarketWindow,
    fetch_btc_windows,
    fetch_current_window,
)

__all__ = [
    "GammaAPIClient",
    "MarketWindow",
    "fetch_btc_windows",
    "fetch_current_window",
]
