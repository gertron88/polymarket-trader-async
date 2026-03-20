# Polymarket Async Trading Bot

High-frequency trading bot for Polymarket with sub-100ms latency.

## Architecture

- **Language:** Python 3.11+ with asyncio
- **Latency Target:** 80-120ms (down from 700ms)
- **Data Feeds:** WebSocket (Binance + Polymarket)
- **Strategy:** BTC price arbitrage on 5-minute markets

## Components

```
├── src/
│   ├── feeds/
│   │   ├── binance_ws.py      # Binance Futures WebSocket
│   │   └── polymarket_ws.py   # Polymarket CLOB WebSocket
│   ├── trading/
│   │   ├── engine.py          # Main trading loop
│   │   ├── position.py        # Position management
│   │   └── sizing.py          # Kelly criterion sizing
│   ├── execution/
│   │   ├── orders.py          # Order placement
│   │   └── state.py           # Batched state management
│   └── main.py                # Entry point
├── config/
│   └── settings.yaml          # Configuration
├── tests/
└── requirements.txt
```

## Key Features

- ✅ WebSocket feeds (no polling)
- ✅ Concurrent API calls
- ✅ Batched state updates
- ✅ Kelly position sizing
- ✅ Dynamic exit strategies
- ✅ Risk management (daily limits)

## Quick Start

```bash
pip install -r requirements.txt
python src/main.py
```

## Configuration

Edit `config/settings.yaml`:
```yaml
bankroll: 100.0
kelly_fraction: 0.5
daily_loss_limit: 0.10
max_trades_per_window: 2
```

## Monitoring

- Discord alerts for trades
- JSONL logs for analysis
- Real-time P&L tracking
