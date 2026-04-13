"""Tests for MarketStateLogger."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from analysis.market_state_logger import MarketStateLogger


class DummyEngine:
    """Minimal engine stub for logger tests."""

    def __init__(self):
        self.current_btc_price = 65000.0
        self.current_btc_change = 0.008
        self.daily_pnl = -2.5
        self.total_trades = 3
        self.has_traded = True
        self.breaker_panel = None
        self.position_manager = self
        self._current_window = {
            "timestamp": 1713000000,
            "up_ask": 0.48,
            "down_ask": 0.48,
            "combined": 0.96,
            "edge": 0.04,
        }

    def get_active_positions(self):
        return []


class DummySignal:
    def __init__(self):
        self.side = "UP"
        self.confidence = 0.85
        self.btc_change = 0.008


@pytest.fixture
def temp_log_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.anyio
async def test_initialize_creates_file(temp_log_file):
    logger = MarketStateLogger(filepath=temp_log_file, flush_interval=5.0)
    await logger.initialize()
    assert Path(temp_log_file).exists()
    await logger.close()


@pytest.mark.anyio
async def test_log_snapshot_writes_valid_jsonl(temp_log_file):
    logger = MarketStateLogger(filepath=temp_log_file, flush_interval=5.0)
    await logger.initialize()

    engine = DummyEngine()
    await logger.log_snapshot(engine)
    await logger.flush(force=True)

    lines = Path(temp_log_file).read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "snapshot"
    assert data["btc_price"] == 65000.0
    assert data["btc_change_30s"] == 0.008
    assert data["active_window_ts"] == 1713000000
    assert data["up_ask"] == 0.48
    assert data["down_ask"] == 0.48
    assert data["combined"] == 0.96
    assert data["edge"] == 0.04
    assert data["daily_pnl"] == -2.5
    assert data["total_trades"] == 3
    assert data["has_traded"] is True
    assert data["active_positions"] == 0

    await logger.close()


@pytest.mark.anyio
async def test_log_signal_writes_enriched_snapshot(temp_log_file):
    logger = MarketStateLogger(filepath=temp_log_file, flush_interval=5.0)
    await logger.initialize()

    engine = DummyEngine()
    signal = DummySignal()
    await logger.log_signal(engine, signal)
    await logger.flush(force=True)

    lines = Path(temp_log_file).read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "signal"
    assert data["signal_side"] == "UP"
    assert data["signal_confidence"] == 0.85
    assert data["signal_btc_change"] == 0.008
    assert data["btc_price"] == 65000.0

    await logger.close()


@pytest.mark.anyio
async def test_periodic_flush_writes_data(temp_log_file):
    logger = MarketStateLogger(filepath=temp_log_file, flush_interval=0.1)
    await logger.initialize()

    engine = DummyEngine()
    await logger.log_snapshot(engine)
    # Wait for periodic flush
    await asyncio.sleep(0.25)

    lines = Path(temp_log_file).read_text().strip().split("\n")
    assert len(lines) >= 1
    data = json.loads(lines[0])
    assert data["type"] == "snapshot"

    await logger.close()


@pytest.mark.anyio
async def test_close_performs_final_flush(temp_log_file):
    logger = MarketStateLogger(filepath=temp_log_file, flush_interval=60.0)
    await logger.initialize()

    engine = DummyEngine()
    await logger.log_snapshot(engine)
    await logger.close()

    lines = Path(temp_log_file).read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "snapshot"
