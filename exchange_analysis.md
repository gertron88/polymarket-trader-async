# Exchange Latency Analysis for Dublin VPS

## Current Situation
- **VPS Location**: AWS Ireland (eu-west-1)
- **Binance Latency**: ~149ms average
- **Issue**: Binance primarily serves from Asia (Tokyo/Singapore)

## Binance Server Infrastructure

Binance operates primarily from:
- **Tokyo, Japan** (ap-northeast-1) - Primary
- **Singapore** (ap-southeast-1) - Secondary
- **No known EU-specific endpoints** for futures WebSocket

The ~149ms latency is typical for EU→Asia routing.

## Better Alternatives for Dublin

### 1. Coinbase Pro / Coinbase Exchange ⭐ BEST OPTION
**Latency estimate**: 20-50ms from Dublin

**Why it's better**:
- Uses AWS with EU regions (Ireland, London, Frankfurt)
- Strong presence in eu-west-1 (same region as your VPS!)
- Professional-grade WebSocket API

**WebSocket URL**: `wss://ws-feed.exchange.coinbase.com`

**Implementation**:
```python
# Coinbase uses different message format
{
    "type": "ticker",
    "product_id": "BTC-USD",
    "price": "50000.00",
    "time": "2024-01-01T00:00:00.000000Z"
}
```

### 2. Kraken ⭐ GOOD OPTION
**Latency estimate**: 30-60ms from Dublin

**Why it's better**:
- EU-based exchange (infrastructure in EU)
- Lower latency to Dublin than Binance
- Good for BTC price correlation

**WebSocket URL**: `wss://ws.kraken.com`

### 3. Bitstamp
**Latency estimate**: 20-40ms from Dublin

**Why it's better**:
- EU-based (Luxembourg)
- Regulated EU exchange
- Good for price correlation

**WebSocket URL**: `wss://ws.bitstamp.net`

### 4. OKX
**Latency estimate**: 100-150ms (similar to Binance)

- Primary servers in Asia
- Not much improvement over Binance

## Recommended Changes

### Option A: Switch to Coinbase (Recommended)
Replace Binance WebSocket with Coinbase in your bot:

```python
# Current (Binance)
ws_url = "wss://fstream.binance.com/ws/btcusdt@markPrice@1s"
# Latency: ~149ms

# Better (Coinbase)
ws_url = "wss://ws-feed.exchange.coinbase.com"
# Subscribe to ticker channel
# Latency: ~20-50ms (4x better!)
```

### Option B: Move VPS to Singapore
Keep Binance, move infrastructure:
- **Latency improvement**: ~149ms → ~20-40ms
- **Trade-off**: Higher latency to Polymarket (~30-50ms → ~150ms)
- **Net result**: Worse overall (Polymarket is EU-based)

### Option C: Use Multiple Feeds
Use Coinbase for low-latency primary feed, Binance as backup:
```python
# Primary: Coinbase (fast from Dublin)
# Secondary: Binance (correlation check)
```

## Implementation Priority

1. **Test Coinbase WebSocket** - Verify latency
2. **Implement Coinbase adapter** - Convert message format
3. **Compare price correlation** - Ensure BTC prices match
4. **Switch to Coinbase** - Deploy with lower latency

## Expected Improvement

| Component | Before (Binance) | After (Coinbase) |
|-----------|------------------|------------------|
| Exchange→Signal | 149ms | 30ms |
| Signal→Order | 10ms | 10ms |
| Network→Polymarket | 30ms | 30ms |
| Order placement | 40ms | 40ms |
| **Total** | **229ms** | **110ms** |

**52% latency reduction!**
