# ⚠️ DEPRECATION NOTICE — DO NOT DEPLOY CAPITAL

**Status:** ARCHIVED / MATHEMATICALLY INVALID  
**Date:** 2026-04-13  
**Reason:** Core strategy has negative expectancy under realistic market conditions.

---

## Why This Bot Was Archived

### 1. Negative Kelly Criterion
The original strategy assumed:
- 70% win rate
- 8% net profit per trade

**Reality:**
- At realistic slippage (2-5% net profit), required win rate is **85-95%**
- The Kelly criterion was **negative** — the bet had negative expectancy
- Position sizing logic recommended trades that were mathematically guaranteed to lose money over time

### 2. Impossible Timing Assumptions
The "intra-window exit" strategy assumed 200-500ms holds.

**Reality:**
- Polygon block time is **2.3 seconds minimum**
- Sub-second exits are **impossible** on-chain
- The entire micro-arbitrage thesis was built on false latency assumptions

### 3. Not True Arbitrage
This bot was **directional speculation**, not arbitrage:
- It guessed BTC price direction based on Coinbase spot moves
- It bought binary options on Polymarket and hoped they moved favorably
- There was **no locked-in profit** at entry — exposure to price drift remained

### 4. Risk Register Findings
See `memory/polymarket-trading-bot-risks.md` for full analysis.

---

## What Was Fixed Before Archival

To prevent accidental deployment, the following safety fixes were applied:

1. **Kelly sizing corrected** — negative Kelly now returns 0.0 (no trade)
2. **Slippage model added** — configurable entry/exit slippage
3. **P&L uses actual fill prices** — no more hardcoded assumptions
4. **Short-window exits** — replaced impossible intra-window logic with 2.5s minimum holds
5. **Circuit breakers** — RPC latency, WS health, consecutive losses, win rate, daily loss limits
6. **64 passing tests** — math validation, safety guards, engine tests

**These fixes make the bot SAFE, but they do not make it PROFITABLE.**
The underlying strategy (BTC directional speculation on binary options) still lacks a verifiable edge.

---

## What Replaces This

**New project:** `polymarket-kalshi-arb/` (cross-platform prediction market arbitrage)

**Key differences:**
- True arbitrage: profit is **locked in at entry** by exploiting price discrepancies
- Multi-platform: Polymarket ↔ Kalshi
- Non-crypto markets: politics, sports, macro events
- No directional guessing — we buy YES on one platform and NO on the other when `price_YES + price_NO < 1.0 - fees`

---

## If You Still Want to Experiment

- **Paper trade ONLY** with fake money
- **Validate a >78% win rate** over 500+ trades before considering real capital
- Expect the strategy to be **outcompeted by market makers** within 2-6 weeks if any edge exists

---

**Do not deploy capital to this bot.**
