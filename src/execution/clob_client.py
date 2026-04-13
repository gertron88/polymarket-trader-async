"""
Polymarket CLOB Client wrapper.

Provides authenticated access to Polymarket's Central Limit Order Book
for order placement and account management.
"""

import os
import logging
from typing import Optional, Dict, Any
from pathlib import Path

# Try to import py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    from py_clob_client.constants import POLYGON
    HAS_CLOB = True
except ImportError:
    HAS_CLOB = False
    logging.warning("py-clob-client not installed. Install with: pip install py-clob-client")

logger = logging.getLogger(__name__)


class PolymarketClobClient:
    """
    Wrapper for Polymarket CLOB client with environment-based config.
    
    Loads credentials from environment variables and provides
    a configured ClobClient instance.
    
    Required env vars:
        POLYMARKET_API_KEY
        POLYMARKET_SECRET
        POLYMARKET_PASSPHRASE
        PRIVATE_KEY (for signing)
        POLYGON_RPC_URL (optional, defaults to public)
    """
    
    def __init__(self):
        self.client: Optional[ClobClient] = None
        self._credentials_loaded = False
        self._api_key: Optional[str] = None
        self._secret: Optional[str] = None
        self._passphrase: Optional[str] = None
        self._private_key: Optional[str] = None
        self._chain_id = POLYGON  # Polygon mainnet
        
    def load_credentials(self) -> bool:
        """
        Load API credentials from environment variables.
        
        Returns:
            True if all credentials loaded successfully
            
        Raises:
            ValueError: If required credentials are missing
        """
        # Load from environment
        self._api_key = os.getenv("POLYMARKET_API_KEY")
        self._secret = os.getenv("POLYMARKET_SECRET")
        self._passphrase = os.getenv("POLYMARKET_PASSPHRASE")
        self._private_key = os.getenv("PRIVATE_KEY")
        
        # Validate
        missing = []
        if not self._api_key:
            missing.append("POLYMARKET_API_KEY")
        if not self._secret:
            missing.append("POLYMARKET_SECRET")
        if not self._passphrase:
            missing.append("POLYMARKET_PASSPHRASE")
        if not self._private_key:
            missing.append("PRIVATE_KEY")
        
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"See .env.example for setup instructions."
            )
        
        self._credentials_loaded = True
        logger.info("CLOB credentials loaded successfully")
        return True
    
    def initialize(self) -> ClobClient:
        """
        Initialize and return a configured ClobClient.
        
        Returns:
            Configured ClobClient instance
            
        Raises:
            RuntimeError: If py-clob-client not installed
            ValueError: If credentials not loaded
        """
        if not HAS_CLOB:
            raise RuntimeError(
                "py-clob-client not installed. "
                "Install with: pip install py-clob-client"
            )
        
        if not self._credentials_loaded:
            self.load_credentials()
        
        # Get RPC URL (optional)
        rpc_url = os.getenv(
            "POLYGON_RPC_URL",
            "https://polygon-rpc.com"
        )
        
        # Create API credentials
        api_creds = ApiCreds(
            api_key=self._api_key,
            api_secret=self._secret,
            api_passphrase=self._passphrase
        )
        
        # Initialize ClobClient
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=self._private_key,
            chain_id=self._chain_id,
            creds=api_creds
        )
        
        # Set API credentials
        self.client.set_api_creds(api_creds)
        
        logger.info("ClobClient initialized successfully")
        return self.client
    
    def get_client(self) -> ClobClient:
        """
        Get the initialized client, initializing if needed.
        
        Returns:
            ClobClient instance
        """
        if self.client is None:
            self.initialize()
        return self.client
    
    def get_balance(self) -> Dict[str, float]:
        """
        Get account balance information.
        
        Returns:
            Dictionary with USDC balance info
        """
        if not self.client:
            self.initialize()
        
        try:
            # Try different methods that might exist in the CLOB client
            if hasattr(self.client, 'get_balance'):
                balance = self.client.get_balance()
                return {
                    "usdc": float(balance.get("USDC", 0)),
                    "allowance": float(balance.get("allowance", 0))
                }
            elif hasattr(self.client, 'get_allowance'):
                # Fallback to allowance endpoint
                allowance = self.client.get_allowance()
                return {"usdc": float(allowance), "allowance": float(allowance)}
            else:
                # No balance method available
                logger.warning("ClobClient has no balance method, returning zero")
                return {"usdc": 0.0, "allowance": 0.0}
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return {"usdc": 0.0, "allowance": 0.0}
    
    def create_order(self, order_args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create an order on the CLOB.
        
        Args:
            order_args: Order parameters
                - token_id: str
                - side: "BUY" or "SELL"
                - size: float
                - price: float (0.0 - 1.0)
                - time_in_force: "GTC", "IOC", "FOK"
                
        Returns:
            Order response from CLOB
        """
        if not self.client:
            self.initialize()
        
        try:
            # Build order
            order = self.client.create_order(
                token_id=order_args["token_id"],
                side=order_args["side"],
                size=order_args["size"],
                price=order_args["price"],
                time_in_force=order_args.get("time_in_force", "GTC")
            )
            
            # Post order
            response = self.client.post_order(order)
            
            logger.info(
                f"Order created: {response.get('orderID', 'unknown')} "
                f"for {order_args['token_id'][:10]}..."
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Order creation failed: {e}")
            raise
    
    def get_order(self, order_id: str) -> Dict[str, Any]:
        """
        Get order status by ID.
        
        Args:
            order_id: Order ID from CLOB
            
        Returns:
            Order status information
        """
        if not self.client:
            self.initialize()
        
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            raise
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        if not self.client:
            self.initialize()
        
        try:
            result = self.client.cancel(order_id)
            logger.info(f"Order {order_id} cancelled")
            return result.get("success", False)
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.
        
        Returns:
            Number of orders cancelled
        """
        if not self.client:
            self.initialize()
        
        try:
            result = self.client.cancel_all()
            count = len(result.get("canceled", []))
            logger.info(f"Cancelled {count} orders")
            return count
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return 0


# Singleton instance
_clob_client: Optional[PolymarketClobClient] = None


def get_clob_client() -> PolymarketClobClient:
    """
    Get or create singleton CLOB client instance.
    
    Returns:
        PolymarketClobClient instance
    """
    global _clob_client
    if _clob_client is None:
        _clob_client = PolymarketClobClient()
    return _clob_client


# Example usage
if __name__ == "__main__":
    # Test credential loading
    try:
        client_wrapper = PolymarketClobClient()
        client_wrapper.load_credentials()
        print("✅ Credentials loaded successfully")
        
        # Try to initialize
        client = client_wrapper.initialize()
        print("✅ ClobClient initialized")
        
        # Check balance
        balance = client_wrapper.get_balance()
        print(f"Balance: {balance}")
        
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")
