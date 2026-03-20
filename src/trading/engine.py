"""
Trading Engine - Core event-driven trading logic
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Callable
from dataclasses import dataclass

from ..feeds.binance_ws import BinanceWebSocket
from ..feeds.polymarket_ws import PolymarketWebSocket
from ..execution.orders import OrderExecutor
from ..execution.state import StateManager
from .position import PositionManager, Position
from .sizing import KellySizer

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
    
    Reacts to price updates via callbacks - no polling
    Target latency: 80-120ms from BTC move to order placement
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # Components (initialized in initialize())
        self.binance_ws: Optional[BinanceWebSocket] = None
        self.polymarket_ws: Optional[PolymarketWebSocket] = None
        self.order_executor: Optional[OrderExecutor] = None
        self.state_manager: Optional[StateManager] = None
        self.position_manager = PositionManager()
        self.sizer: Optional[KellySizer] = None
        
        # Trading state
        self.daily_pnl = 0.0
        self.daily_loss_hit = False
        self.last_trade_time = 0
        self.cooldown_until = 0
        self.window_trades: Dict[int, int] = {}  # window_ts -> trade count
        
        # Current market data
        self.current_btc_price = 0.0
        self.current_btc_change = 0.0
        self.poly_prices: Dict[str, dict] = {}  # token_id -> {bid, ask}
        
        # Running flag
        self.running = False
        
    async def initialize(self):
        """Initialize all components"""
        logger.info("Initializing trading engine...")
        
        # Initialize Kelly sizer
        self.sizer = KellySizer(
            bankroll=self.config.get('bankroll', 100.0),
            kelly_fraction=self.config.get('kelly_fraction', 0.5)
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
        
        # Initialize order executor
        # TODO: Pass actual ClobClient instance
        self.order_executor = OrderExecutor(clob_client=None)
        await self.order_executor.initialize()
        
        # Initialize WebSocket feeds
        self.binance_ws = BinanceWebSocket(
            on_price_update=self._on_btc_update,
            on_connection_change=self._on_connection_change
        )
        
        self.polymarket_ws = PolymarketWebSocket(
            on_price_update=self._on_polymarket_update
        )
        
        # Start periodic tasks
        asyncio.create_task(self._periodic_exit_check())
        asyncio.create_task(self._periodic_stats_log())
        
        logger.info("Trading engine initialized")
        
    async def _on_btc_update(self, price: float, change_30s: float):
        """Called on every BTC price update (WebSocket push)"""
        self.current_btc_price = price
        self.current_btc_change = change_30s
        
        # Check if this triggers an entry signal
        if abs(change_30s) >= self.config.get('btc_threshold', 0.005):
            await self._check_entry_signal(change_30s)
            
    async def _on_polymarket_update(self, token_id: str, best_bid: float, best_ask: float):
        """Called on every order book update (WebSocket push)"""
        self.poly_prices[token_id] = {
            'bid': best_bid,
            'ask': best_ask,
            'timestamp': time.time()
        }
        
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
        """Check if trading conditions are met"""
        
        # Check daily loss limit
        daily_limit = self.config.get('daily_loss_limit', 0.10)
        if self.daily_pnl <= -self.config.get('bankroll', 100.0) * daily_limit:
            if not self.daily_loss_hit:
                logger.warning(f"Daily loss limit hit: ${self.daily_pnl:.2f}")
                self.daily_loss_hit = True
            return False
            
        # Check cooldown
        if time.time() < self.cooldown_until:
            return False
            
        # Check if max positions
        if len(self.position_manager.get_active_positions()) >= self.config.get('max_positions', 2):
            return False
            
        return True
        
    async def _execute_entry(self, signal: Signal):
        """Execute entry orders"""
        
        # Get current window
        window = await self._get_current_window()
        if not window:
            logger.warning("No active window available")
            return
            
        window_ts = window['timestamp']
        
        # Check max trades per window
        max_trades = self.config.get('max_trades_per_window', 2)
        if self.window_trades.get(window_ts, 0) >= max_trades:
            logger.info(f"Max trades reached for window {window_ts}")
            return
            
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
            
        # Calculate position size
        size = self.sizer.calculate_size(confidence=signal.confidence)
        if size < 1.0:
            logger.info(f"Size too small: ${size:.2f}")
            return
            
        logger.info(f"Entering window {window_ts} | Size: ${size:.2f} | Edge: {edge:.4f}")
        
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
        
        # Place orders concurrently
        limit_price = self.config.get('limit_price', 0.46)
        up_id, down_id = await self.order_executor.place_entry_orders(
            up_token=up_token,
            down_token=down_token,
            size=size,
            price=limit_price
        )
        
        if up_id:
            position.up_id = up_id
        if down_id:
            position.down_id = down_id
            
        # Update tracking
        self.window_trades[window_ts] = self.window_trades.get(window_ts, 0) + 1
        self.last_trade_time = time.time()
        
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
        """Check exit conditions for a single position"""
        
        # Get current prices
        up_bid = self.poly_prices.get(position.up_token, {}).get('bid', 0)
        down_bid = self.poly_prices.get(position.down_token, {}).get('bid', 0)
        
        # Update position with current prices
        self.position_manager.update_prices(position.window_ts, up_bid, down_bid)
        
        # Calculate time remaining
        window_end = position.window_ts + 300  # 5 minute window
        time_remaining = window_end - time.time()
        
        exit_reason = None
        exit_side = None
        exit_price = 0.0
        
        # Check various exit conditions
        if position.both_filled() and not position.both_exited():
            # Both filled - exit at targets
            if up_bid >= self.config.get('winner_target', 0.90):
                exit_side = 'UP'
                exit_price = up_bid
                exit_reason = f"Winner target hit: ${up_bid:.2f}"
            elif down_bid >= self.config.get('winner_target', 0.90):
                exit_side = 'DOWN'
                exit_price = down_bid
                exit_reason = f"Winner target hit: ${down_bid:.2f}"
                
        elif position.up_filled and not position.down_filled and not position.up_exited:
            # One-sided UP position
            stop_price = self.position_manager.calculate_exit_price(position, time_remaining)
            if up_bid <= stop_price:
                exit_side = 'UP'
                exit_price = up_bid
                exit_reason = f"Stop loss: ${up_bid:.2f} <= ${stop_price:.2f}"
                
        elif position.down_filled and not position.up_filled and not position.down_exited:
            # One-sided DOWN position
            stop_price = self.position_manager.calculate_exit_price(position, time_remaining)
            if down_bid <= stop_price:
                exit_side = 'DOWN'
                exit_price = down_bid
                exit_reason = f"Stop loss: ${down_bid:.2f} <= ${stop_price:.2f}"
                
        # Time-based exit
        time_held = time.time() - position.entry_time
        max_hold = self.config.get('max_hold_time', 60)
        if time_held > max_hold and not exit_side:
            # Exit if profitable or down 10%
            pnl = self.position_manager.calculate_pnl(position)
            if pnl > 0:
                # Exit profitable side(s)
                if position.up_filled and not position.up_exited and up_bid > position.entry_price:
                    exit_side = 'UP'
                    exit_price = up_bid
                    exit_reason = "Time exit (profit)"
                elif position.down_filled and not position.down_exited and down_bid > position.entry_price:
                    exit_side = 'DOWN'
                    exit_price = down_bid
                    exit_reason = "Time exit (profit)"
                    
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
            
            logger.info(f"Exit complete: P&L ${pnl:+.2f} | Daily: ${self.daily_pnl:+.2f}")
            
            # Save state
            await self.state_manager.update_position(position)
            
            # Set cooldown after loss
            if pnl < 0:
                self.cooldown_until = time.time() + 10  # 10s cooldown
                
    async def _periodic_stats_log(self):
        """Log periodic statistics"""
        while self.running:
            await asyncio.sleep(60)  # Every minute
            
            stats = self.sizer.get_stats()
            active_count = len(self.position_manager.get_active_positions())
            
            logger.info(
                f"Stats: Daily P&L=${self.daily_pnl:+.2f} | "
                f"Win Rate={stats.get('win_rate', 0):.1%} | "
                f"Active Positions={active_count}"
            )
            
    async def _get_current_window(self) -> Optional[dict]:
        """Get current 5-minute trading window"""
        # TODO: Implement window discovery via Gamma API
        # For now, return mock window
        now = int(time.time())
        window_ts = (now // 300) * 300
        
        return {
            'timestamp': window_ts,
            'up_token': 'mock_up_token',
            'down_token': 'mock_down_token'
        }
        
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
        asyncio.create_task(self.binance_ws.connect())
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
        if self.binance_ws:
            await self.binance_ws.close()
        if self.polymarket_ws:
            await self.polymarket_ws.close()
            
        # Close executor
        if self.order_executor:
            await self.order_executor.close()
            
        # Final state flush
        if self.state_manager:
            await self.state_manager.close()
            
        logger.info("Trading engine shut down")
