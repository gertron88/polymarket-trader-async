"""
Trading Engine - Core event-driven trading logic
SINGLE TEST TRADE VERSION - Fixed Kelly sizing + Hard trade limit

Strategy: Wait for Coinbase BTC price move, then buy on Polymarket
in the direction that profits when prices re-align.
"""
import asyncio
import logging
import time
import os
from typing import Optional, Dict, Callable
from dataclasses import dataclass

from feeds.price_feed import create_price_feed
from feeds.polymarket_ws import PolymarketWebSocket
from market.gamma_api import GammaAPIClient, MarketWindow
from execution.orders import OrderExecutor
from execution.state import StateManager
from execution.clob_client import get_clob_client
from trading.position import PositionManager, Position
from trading.sizing import KellySizer
from trading.short_window_exits import ShortWindowExitManager, ExitTrigger
from risk import CircuitBreakerPanel

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Trading signal from BTC movement"""
    side: str  # 'UP' or 'DOWN'
    btc_change: float
    confidence: float  # 0.5-1.0


class TradingEngine:
    """
    Event-driven trading engine for Polymarket BTC arbitrage
    
    ⚠️  SINGLE TRADE MODE: Max 1 trade enforced
    Fixed Kelly with realistic parameters (3% net profit, 85% win rate required)
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # Components (initialized in initialize())
        self.price_feed = None
        self.polymarket_ws: Optional[PolymarketWebSocket] = None
        self.order_executor: Optional[OrderExecutor] = None
        self.state_manager: Optional[StateManager] = None
        self.position_manager = PositionManager()
        self.sizer: Optional[KellySizer] = None
        self.gamma_client: Optional[GammaAPIClient] = None
        self.breaker_panel: Optional[CircuitBreakerPanel] = None
        self._exit_managers: Dict[int, ShortWindowExitManager] = {}
        
        # Trading state
        self.daily_pnl = 0.0
        self.daily_loss_hit = False
        self.last_trade_time = 0
        self.cooldown_until = 0
        self.window_trades: Dict[int, int] = {}
        self.total_trades = 0  # ⛔ HARD LIMIT TRACKER
        self.has_traded = False  # Flag to block after 1 trade
        
        # Current market data
        self.current_btc_price = 0.0
        self.current_btc_change = 0.0
        self.poly_prices: Dict[str, dict] = {}
        
        # Running flag
        self.running = False
        
        # Trading mode check
        self.live_mode = os.environ.get('LIVE_MODE', 'false').lower() == 'true'
        self.trading_mode = config.get('trading_mode', 'dry_run')
        
    async def initialize(self):
        """Initialize all components"""
        logger.info("=" * 60)
        logger.info("INITIALIZING TRADING ENGINE - SINGLE TEST TRADE MODE")
        logger.info("=" * 60)
        
        # Log mode
        if self.trading_mode == 'live' and not self.live_mode:
            logger.error("⚠️  LIVE MODE REQUESTED but LIVE_MODE env var not set!")
            logger.error("Set LIVE_MODE=true to enable live trading")
            self.trading_mode = 'dry_run'
        
        logger.info(f"Trading mode: {self.trading_mode}")
        logger.info(f"Max total trades: {self.config.get('max_total_trades', 1)}")
        logger.info(f"Max position: ${self.config.get('max_position_dollars', 5.0)}")
        
        # Initialize Kelly sizer with realistic parameters
        self.sizer = KellySizer(
            bankroll=self.config.get('bankroll', 100.0),
            kelly_fraction=self.config.get('kelly_fraction', 0.1),
            max_position_pct=self.config.get('max_position_pct', 0.05),
            max_trades=self.config.get('max_total_trades', 1),
            max_position_dollars=self.config.get('max_position_dollars', 5.0)
        )
        
        # Initialize state manager
        self.state_manager = StateManager(
            filepath='data/state.jsonl',
            flush_interval=5.0
        )
        await self.state_manager.initialize()
        
        # Load existing positions
        for pos_data in self.state_manager.get_all_positions():
            pos = Position.from_dict(pos_data)
            self.position_manager.add_position(pos)
        
        # Initialize Gamma API client for market discovery
        self.gamma_client = GammaAPIClient()
        await self.gamma_client.initialize()
        
        # Initialize CLOB client with real credentials
        clob_wrapper = get_clob_client()
        clob_client = clob_wrapper.initialize()
        
        # Initialize order executor with real client
        self.order_executor = OrderExecutor(clob_client=clob_client)
        await self.order_executor.initialize()
        
        # Check balance (graceful fallback if method not available)
        try:
            balance = clob_wrapper.get_balance()
            logger.info(f"Account balance: {balance['usdc']:.2f} USDC")
        except Exception as e:
            logger.warning(f"Could not retrieve balance: {e}")
            logger.info("Continuing without balance check")
        
        # Initialize price feed (Coinbase recommended for EU)
        feed_exchange = self.config.get('price_feed', 'coinbase').lower()
        logger.info(f"Using {feed_exchange} for price feed")
        
        self.price_feed = create_price_feed(
            exchange=feed_exchange,
            on_price_update=self._on_btc_update,
            on_connection_change=self._on_connection_change,
            product_id='BTC-USD'
        )
        
        self.polymarket_ws = PolymarketWebSocket(
            on_price_update=self._on_polymarket_update
        )
        
        # Initialize circuit breakers
        cb_config = self.config.get('circuit_breakers', {})
        self.breaker_panel = CircuitBreakerPanel(
            rpc_latency_ms=cb_config.get('rpc_latency_ms', 500.0),
            ws_disconnect_seconds=cb_config.get('ws_disconnect_seconds', 5.0),
            max_consecutive_losses=cb_config.get('max_consecutive_losses', 3),
            min_win_rate=cb_config.get('min_win_rate', 0.65),
            win_rate_window=cb_config.get('win_rate_window', 100),
            max_daily_loss_pct=cb_config.get('max_daily_loss_pct', 0.05),
            slippage_warning_pct=cb_config.get('slippage_warning_pct', 0.01),
        )
        self.breaker_panel.set_bankroll(self.config.get('bankroll', 100.0))
        logger.info("Circuit breakers initialized")
        
        # Start periodic tasks
        asyncio.create_task(self._periodic_exit_check())
        asyncio.create_task(self._periodic_stats_log())
        asyncio.create_task(self._periodic_rpc_check())
        
        logger.info("Trading engine initialized")
        logger.info("=" * 60)
        
    async def _on_btc_update(self, price: float, change_30s: float):
        """Called on every BTC price update (WebSocket push)"""
        self.current_btc_price = price
        self.current_btc_change = change_30s
        
        # Heartbeat for WS health breaker
        if self.breaker_panel:
            self.breaker_panel.ws_breaker.heartbeat_price_feed()
        
        # Check if this triggers an entry signal
        threshold = self.config.get('btc_threshold', 0.005)
        if abs(change_30s) >= threshold:
            logger.info(f"BTC move detected: {change_30s:.4f} ({change_30s*100:.2f}%) | Threshold: {threshold}")
            await self._check_entry_signal(change_30s)
            
    async def _on_polymarket_update(self, token_id: str, best_bid: float, best_ask: float):
        """Called on every order book update (WebSocket push)"""
        self.poly_prices[token_id] = {
            'bid': best_bid,
            'ask': best_ask,
            'timestamp': time.time()
        }
        
        # Heartbeat for WS health breaker
        if self.breaker_panel:
            self.breaker_panel.ws_breaker.heartbeat_polymarket()
        
    async def _check_entry_signal(self, btc_change: float):
        """Check if BTC movement triggers entry"""
        
        # Check if we should trade at all
        if not await self._should_trade():
            return
            
        # Determine side based on BTC movement
        side = 'UP' if btc_change > 0 else 'DOWN'
        confidence = min(1.0, abs(btc_change) / 0.02)  # Max confidence at 2% move
        
        signal = Signal(side=side, btc_change=btc_change, confidence=confidence)
        
        logger.info(f"Signal: {side} | BTC change: {btc_change:.4f} | Confidence: {confidence:.2f}")
        
        # Execute entry
        await self._execute_entry(signal)
        
    async def _should_trade(self) -> bool:
        """Check if trading conditions are met - SINGLE TRADE MODE"""
        
        # ⛔ HARD LIMIT: Max 1 trade
        if self.has_traded or self.total_trades >= self.config.get('max_total_trades', 1):
            if not hasattr(self, '_logged_trade_limit'):
                logger.info("⛔ Trade limit reached (max 1 trade) - Engine will continue monitoring but not trade")
                self._logged_trade_limit = True
            return False
        
        # Check circuit breakers
        if self.breaker_panel:
            ok, reason = self.breaker_panel.check_all()
            if not ok:
                logger.error(f"⛔ Trade blocked by circuit breaker: {reason}")
                return False
        
        # Check daily loss limit
        daily_limit = self.config.get('daily_loss_limit', 0.05)
        if self.daily_pnl <= -self.config.get('bankroll', 100.0) * daily_limit:
            if not self.daily_loss_hit:
                logger.warning(f"Daily loss limit hit: ${self.daily_pnl:.2f}")
                self.daily_loss_hit = True
            return False
            
        # Check cooldown
        if time.time() < self.cooldown_until:
            return False
            
        # Check if max positions
        if len(self.position_manager.get_active_positions()) >= self.config.get('max_positions', 1):
            return False
            
        return True
        
    async def _execute_entry(self, signal: Signal):
        """Execute entry orders - SINGLE TRADE MODE"""
        
        # ⛔ Double-check trade limit
        if self.has_traded or self.total_trades >= self.config.get('max_total_trades', 1):
            logger.warning("Trade blocked: Max trades already reached")
            return
        
        logger.info("=" * 60)
        logger.info(f"🚀 EXECUTING ENTRY - Trade {self.total_trades + 1}/1")
        logger.info("=" * 60)
        
        # Get current window
        window = await self._get_current_window()
        if not window:
            logger.warning("No active window available")
            return
            
        window_ts = window['timestamp']
        
        # Get token IDs
        up_token = window['up_token']
        down_token = window['down_token']
        
        # Check if we already have position for this window
        if self.position_manager.get_position(window_ts):
            logger.info(f"Already have position for window {window_ts}")
            return
            
        # Check prices and edge
        up_ask = self.poly_prices.get(up_token, {}).get('ask', 0.5)
        down_ask = self.poly_prices.get(down_token, {}).get('ask', 0.5)
        
        combined = up_ask + down_ask
        if combined >= 1.0:
            logger.info(f"No edge: combined price ${combined:.2f}")
            return
            
        edge = 1.0 - combined
        min_edge = self.config.get('min_edge', 0.05)
        if edge < min_edge:
            logger.info(f"Edge too small: {edge:.4f} < {min_edge}")
            return
            
        # Calculate position size with realistic Kelly
        size = self.sizer.calculate_size(confidence=signal.confidence)
        max_size = self.config.get('max_position_dollars', 5.0)
        size = min(size, max_size)  # Hard cap
        
        if size < 1.0:
            logger.info(f"Size too small: ${size:.2f}")
            return
            
        logger.info(f"Window: {window_ts}")
        logger.info(f"Signal: {signal.side} | BTC change: {signal.btc_change:.4f}")
        logger.info(f"Position size: ${size:.2f} (max ${max_size})")
        logger.info(f"Edge: {edge:.4f}")
        logger.info(f"Trading mode: {self.trading_mode}")
        
        if self.trading_mode == 'dry_run':
            logger.info("⚠️  DRY RUN MODE - Not placing actual orders")
            logger.info("=" * 60)
            # Mark as traded even in dry run to test the flow
            self.has_traded = True
            self.total_trades += 1
            return
        
        # Create position
        position = self.position_manager.create_position(
            window_ts=window_ts,
            up_token=up_token,
            down_token=down_token,
            size=size
        )
        
        # Subscribe to order book updates for this window
        await self.polymarket_ws.subscribe(up_token)
        await self.polymarket_ws.subscribe(down_token)
        
        # Place orders
        limit_price = self.config.get('limit_price', 0.46)
        logger.info(f"Placing entry orders at ${limit_price:.2f}...")
        
        up_id, down_id = await self.order_executor.place_entry_orders(
            up_token=up_token,
            down_token=down_token,
            size=size,
            price=limit_price
        )
        
        if up_id:
            position.up_id = up_id
            logger.info(f"UP order placed: {up_id}")
        if down_id:
            position.down_id = down_id
            logger.info(f"DOWN order placed: {down_id}")
        
        # Record expected price for slippage monitoring
        if self.breaker_panel:
            self.breaker_panel.slippage_monitor.record_entry(limit_price, limit_price, size)
            
        # Create short-window exit manager for this position
        exit_mgr = ShortWindowExitManager(
            entry_price=limit_price,
            side=signal.side,
            profit_target_pct=self.config.get('assumed_gross_profit_pct', 0.05)
            - self.config.get('slippage', {}).get('entry_slippage', 0.01)
            - self.config.get('slippage', {}).get('exit_slippage', 0.01),
            stop_loss_pct=self.config.get('stop_loss_pct', 0.10)
        )
        self._exit_managers[window_ts] = exit_mgr
            
        # Update tracking
        self.has_traded = True
        self.total_trades += 1
        self.window_trades[window_ts] = self.window_trades.get(window_ts, 0) + 1
        self.last_trade_time = time.time()
        
        logger.info(f"✅ Entry complete - Trade {self.total_trades}/1 executed")
        logger.info("=" * 60)
        
        # Save state
        await self.state_manager.update_position(position)
        
    async def _periodic_exit_check(self):
        """Periodically check all positions for exit conditions"""
        while self.running:
            try:
                await self._check_exits()
            except Exception as e:
                logger.error(f"Error in exit check: {e}")
            await asyncio.sleep(0.1)  # 100ms
            
    async def _check_exits(self):
        """Check exit conditions for all active positions"""
        
        for position in self.position_manager.get_active_positions():
            await self._check_position_exit(position)
            
    async def _check_position_exit(self, position: Position):
        """Check exit conditions for a single position using short-window logic."""
        
        # Get current prices
        up_bid = self.poly_prices.get(position.up_token, {}).get('bid', 0)
        down_bid = self.poly_prices.get(position.down_token, {}).get('bid', 0)
        
        # Calculate time remaining in window
        window_end = position.window_ts + 300  # 5 minute window
        time_remaining = window_end - time.time()
        
        exit_side = None
        exit_price = 0.0
        exit_reason = None
        
        # Use short-window exit manager if available
        exit_mgr = self._exit_managers.get(position.window_ts)
        if exit_mgr and position.up_filled and not position.up_exited:
            result = exit_mgr.check_exit(up_bid, time_remaining)
            if result.should_exit:
                exit_side = 'UP'
                exit_price = result.exit_price
                exit_reason = result.reason
                
        if exit_mgr and position.down_filled and not position.down_exited and not exit_side:
            result = exit_mgr.check_exit(down_bid, time_remaining)
            if result.should_exit:
                exit_side = 'DOWN'
                exit_price = result.exit_price
                exit_reason = result.reason
                
        # One-sided stop loss fallback (if no exit manager)
        if not exit_side:
            if position.up_filled and not position.down_filled and not position.up_exited:
                stop_price = self.position_manager.calculate_exit_price(position, time_remaining)[0]
                if up_bid <= stop_price:
                    exit_side = 'UP'
                    exit_price = up_bid
                    exit_reason = f"Stop loss: ${up_bid:.2f} <= ${stop_price:.2f}"
                    
            elif position.down_filled and not position.up_filled and not position.down_exited:
                stop_price = self.position_manager.calculate_exit_price(position, time_remaining)[1]
                if down_bid >= stop_price:
                    exit_side = 'DOWN'
                    exit_price = down_bid
                    exit_reason = f"Stop loss: ${down_bid:.2f} <= ${stop_price:.2f}"
                    
        # Execute exit if triggered
        if exit_side and exit_reason:
            await self._execute_exit(position, exit_side, exit_price, exit_reason)
            
    async def _execute_exit(self, position: Position, side: str, price: float, reason: str):
        """Execute exit order"""
        
        logger.info(f"EXIT: Window {position.window_ts} {side} @ ${price:.2f} | {reason}")
        
        token_id = position.up_token if side == 'UP' else position.down_token
        
        # Place exit order
        order_id = await self.order_executor.place_exit_order(
            token_id=token_id,
            size=position.size,
            price=price
        )
        
        if order_id:
            # Update position
            self.position_manager.update_exit(position.window_ts, side, price)
            
            # Calculate P&L
            pnl = self.position_manager.calculate_realized_pnl(position)
            self.daily_pnl += pnl
            self.sizer.update_trade(pnl)
            
            # Feed circuit breakers
            if self.breaker_panel:
                self.breaker_panel.record_trade(pnl)
            
            # Record slippage on exit
            if self.breaker_panel:
                self.breaker_panel.slippage_monitor.record_exit(exit_price, exit_price, position.size)
            
            logger.info(f"Exit complete: P&L ${pnl:+.2f} | Daily: ${self.daily_pnl:+.2f}")
            
            # Save state
            await self.state_manager.update_position(position)
            
            # Set cooldown after loss
            if pnl < 0:
                self.cooldown_until = time.time() + 10  # 10s cooldown
            
            # Clean up exit manager
            if position.window_ts in self._exit_managers:
                del self._exit_managers[position.window_ts]
                
    async def _periodic_stats_log(self):
        """Log periodic statistics"""
        while self.running:
            await asyncio.sleep(60)  # Every minute
            
            stats = self.sizer.get_stats()
            active_count = len(self.position_manager.get_active_positions())
            breaker_status = self.breaker_panel.get_status() if self.breaker_panel else {}
            tripped = [k for k, v in breaker_status.items() if v.get('tripped')]
            
            logger.info(
                f"Stats: Daily P&L=${self.daily_pnl:+.2f} | "
                f"Win Rate={stats.get('win_rate', 0):.1%} | "
                f"Active Positions={active_count}"
            )
            if tripped:
                logger.warning(f"Tripped breakers: {', '.join(tripped)}")
            
    async def _periodic_rpc_check(self):
        """Periodic RPC latency check for circuit breaker."""
        while self.running:
            await asyncio.sleep(5)
            if not self.breaker_panel:
                continue
            try:
                # Simple latency proxy: measure time to get current window
                start = time.time()
                if self.gamma_client:
                    await self.gamma_client.get_current_window()
                latency_ms = (time.time() - start) * 1000
                self.breaker_panel.rpc_breaker.record_latency(latency_ms)
                self.breaker_panel.rpc_breaker.check()
            except Exception as e:
                logger.warning(f"RPC check failed: {e}")
                self.breaker_panel.rpc_breaker.record_latency(9999.0)
                self.breaker_panel.rpc_breaker.check()
            
    async def _get_current_window(self) -> Optional[dict]:
        """Get current 5-minute trading window via Gamma API"""
        if not self.gamma_client:
            logger.error("Gamma API client not initialized")
            return None
        
        try:
            window = await self.gamma_client.get_current_window()
            if not window:
                logger.debug("No active trading window found")
                return None
            
            # Log window details for debugging
            logger.info(
                f"Active window: {window.timestamp} | "
                f"Edge: {window.edge:.4f} | "
                f"Combined: ${window.combined_price:.4f}"
            )
            
            return {
                'timestamp': window.timestamp,
                'up_token': window.up_token,
                'down_token': window.down_token,
                'market_id': window.market_id,
                'end_time': window.end_time.isoformat() if hasattr(window.end_time, 'isoformat') else str(window.end_time)
            }
        except Exception as e:
            logger.error(f"Failed to get current window: {e}")
            return None
        
    def _on_connection_change(self, connected: bool):
        """Handle WebSocket connection changes"""
        if connected:
            logger.info("Binance WebSocket connected")
        else:
            logger.warning("Binance WebSocket disconnected")
            
    async def run(self):
        """Main loop - start all components"""
        logger.info("Starting trading engine...")
        self.running = True
        
        # Start WebSocket feeds
        asyncio.create_task(self.price_feed.connect())
        asyncio.create_task(self.polymarket_ws.connect())
        
        logger.info("Trading engine running")
        
        # Keep running until stopped
        while self.running:
            await asyncio.sleep(1)
            
    async def close(self):
        """Graceful shutdown"""
        logger.info("Shutting down trading engine...")
        self.running = False
        
        # Close WebSockets
        if self.price_feed:
            await self.price_feed.close()
        if self.polymarket_ws:
            await self.polymarket_ws.close()
            
        # Close executor
        if self.order_executor:
            await self.order_executor.close()
            
        # Close Gamma client
        if self.gamma_client:
            await self.gamma_client.close()
            
        # Final state flush
        if self.state_manager:
            await self.state_manager.close()
            
        logger.info("Trading engine shut down")
