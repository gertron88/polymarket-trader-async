# Project Structure and Tasks

## Component Breakdown

### 1. Binance WebSocket Feed (`src/feeds/binance_ws.py`)
**Priority:** P0 - Critical path
**Model:** kimi-coding/k2p5
**Task:** WebSocket client for Binance Futures BTC price
- Connect to wss://fstream.binance.com/ws/btcusdt@markPrice@1s
- Auto-reconnect with exponential backoff
- Callback for price updates
- Track 30s price change
- Latency target: 15-30ms

### 2. Polymarket WebSocket Feed (`src/feeds/polymarket_ws.py`)
**Priority:** P0 - Critical path
**Model:** kimi-coding/k2p5
**Task:** WebSocket client for Polymarket CLOB
- Connect to wss://clob.polymarket.com/ws/market
- Subscribe/unsubscribe to token order books
- Callback for bid/ask updates
- Auto-reconnect
- Latency target: 20-40ms

### 3. Trading Engine (`src/trading/engine.py`)
**Priority:** P0 - Critical path
**Model:** kimi-coding/k2p5
**Task:** Main event-driven trading logic
- React to price updates (no polling)
- Signal detection (BTC move > 0.5%)
- Entry/exit decision logic
- Concurrent position monitoring
- Latency target: 10-20ms processing

### 4. Position Manager (`src/trading/position.py`)
**Priority:** P1 - Important
**Model:** kimi-coding/k2p5
**Task:** Position tracking and exits
- Position dataclass
- Fill tracking
- Dynamic exit logic (time-based stops)
- P&L calculation

### 5. Kelly Sizing (`src/trading/sizing.py`)
**Priority:** P1 - Important
**Model:** kimi-coding/k2p5
**Task:** Position size calculation
- Kelly criterion implementation
- Half-Kelly default
- Size based on confidence
- Bankroll tracking

### 6. Order Execution (`src/execution/orders.py`)
**Priority:** P0 - Critical path
**Model:** kimi-coding/k2p5
**Task:** Async order placement
- aiohttp session with connection pooling
- Concurrent order placement (UP + DOWN)
- Error handling and retries
- Fill confirmation

### 7. State Manager (`src/execution/state.py`)
**Priority:** P1 - Important
**Model:** kimi-coding/k2p5
**Task:** Batched state persistence
- In-memory state with async flush
- Batch writes every 5 seconds
- No blocking I/O in hot path
- JSONL append-only log

### 8. Main Orchestrator (`src/main.py`)
**Priority:** P0 - Critical path
**Model:** kimi-coding/k2p5
**Task:** Startup and coordination
- Initialize all components
- Start WebSocket feeds
- Run trading engine
- Graceful shutdown

### 9. Configuration (`config/settings.yaml`)
**Priority:** P2 - Nice to have
**Model:** (no model needed)
**Task:** YAML config file
- Bankroll settings
- Risk limits
- API endpoints

### 10. Requirements (`requirements.txt`)
**Priority:** P1 - Important
**Model:** (no model needed)
**Task:** Dependency list
- aiohttp
- websockets
- pyyaml
- etc.

## Dependency Graph

```
main.py
├── binance_ws.py
├── polymarket_ws.py
├── engine.py
│   ├── position.py
│   ├── sizing.py
│   ├── orders.py
│   └── state.py
```

## Execution Order

1. Spawn 1+2 (feeds) in parallel
2. Spawn 6+7 (execution) in parallel
3. Spawn 4+5 (trading) in parallel
4. Spawn 3 (engine) - depends on all above
5. Spawn 8 (main) - depends on all above
6. Spawn 9+10 (config) anytime

## Git Strategy

- Each subagent commits their component
- I review and integrate
- Final integration test
- Push to GitHub
