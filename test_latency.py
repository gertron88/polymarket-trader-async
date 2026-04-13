"""
Binance WebSocket Latency Test

Measures latency from Binance Futures WebSocket to this VPS.
Tests the actual trading infrastructure latency component.
"""

import asyncio
import json
import time
import statistics
from collections import deque
from typing import List

import websockets


async def test_binance_latency(duration_seconds: int = 30) -> dict:
    """
    Test Binance WebSocket latency for specified duration.
    
    Args:
        duration_seconds: How long to collect data
        
    Returns:
        Dictionary with latency statistics
    """
    WS_URL = "wss://fstream.binance.com/ws/btcusdt@markPrice@1s"
    
    latencies: List[float] = []
    message_times: List[float] = []
    start_time = time.time()
    
    print(f"🔗 Connecting to Binance WebSocket...")
    print(f"⏱️  Testing for {duration_seconds} seconds...\n")
    
    try:
        async with websockets.connect(
            WS_URL,
            ping_interval=20,
            ping_timeout=10
        as websocket:
            print("✅ Connected! Collecting latency data...\n")
            
            # Collect messages for specified duration
            while time.time() - start_time < duration_seconds:
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=5.0
                    )
                    
                    receive_time = time.time()
                    data = json.loads(message)
                    
                    # Binance event time is in milliseconds
                    event_time_ms = data.get("E", int(receive_time * 1000))
                    event_time = event_time_ms / 1000.0
                    
                    # Calculate latency: receive_time - event_time
                    latency_ms = (receive_time - event_time) * 1000
                    
                    latencies.append(latency_ms)
                    message_times.append(receive_time)
                    
                    # Print progress every 10 messages
                    if len(latencies) % 10 == 0:
                        price = float(data.get("p", 0))
                        print(f"  Message {len(latencies):3d}: "
                              f"Latency={latency_ms:6.2f}ms | "
                              f"BTC=${price:,.2f}")
                    
                except asyncio.TimeoutError:
                    print("  ⚠️  Timeout waiting for message")
                    continue
                    
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"error": str(e)}
    
    if not latencies:
        return {"error": "No messages received"}
    
    # Calculate statistics
    stats = {
        "samples": len(latencies),
        "duration_seconds": duration_seconds,
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "avg_ms": statistics.mean(latencies),
        "median_ms": statistics.median(latencies),
        "stdev_ms": statistics.stdev(latencies) if len(latencies) > 1 else 0,
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95)],
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)],
        "messages_per_second": len(latencies) / duration_seconds,
    }
    
    return stats


def print_results(stats: dict) -> None:
    """Print formatted latency results."""
    if "error" in stats:
        print(f"\n❌ Test failed: {stats['error']}")
        return
    
    print("\n" + "="*60)
    print("📊 BINANCE WEBSOCKET LATENCY TEST RESULTS")
    print("="*60)
    print(f"\n📈 Samples collected: {stats['samples']}")
    print(f"⏱️  Test duration: {stats['duration_seconds']}s")
    print(f"📨 Messages/sec: {stats['messages_per_second']:.1f}")
    
    print("\n🎯 Latency Statistics (ms):")
    print(f"   Min:     {stats['min_ms']:8.2f} ms")
    print(f"   Avg:     {stats['avg_ms']:8.2f} ms")
    print(f"   Median:  {stats['median_ms']:8.2f} ms")
    print(f"   Max:     {stats['max_ms']:8.2f} ms")
    print(f"   StdDev:  {stats['stdev_ms']:8.2f} ms")
    print(f"   P95:     {stats['p95_ms']:8.2f} ms")
    print(f"   P99:     {stats['p99_ms']:8.2f} ms")
    
    print("\n📊 Assessment:")
    avg = stats['avg_ms']
    if avg < 50:
        print("   ✅ EXCELLENT: < 50ms average latency")
        print("      Trading infrastructure is highly responsive")
    elif avg < 100:
        print("   ✅ GOOD: 50-100ms average latency")
        print("      Competitive for algorithmic trading")
    elif avg < 200:
        print("   ⚠️  FAIR: 100-200ms average latency")
        print("      Acceptable but not optimal")
    else:
        print("   ❌ POOR: > 200ms average latency")
        print("      Consider network optimization or closer VPS")
    
    print("\n🎯 For Polymarket Bot:")
    print(f"   Binance→Signal: ~{avg:.0f}ms (this test)")
    print(f"   Signal→Order:    ~5-10ms (processing)")
    print(f"   Network→Polymarket: <1ms (Dublin VPS)")
    print(f"   Order placement: ~20-50ms")
    print(f"   ─────────────────────────────")
    print(f"   Total expected: ~{avg + 35:.0f}-{avg + 70:.0f}ms")
    
    print("="*60)


async def main():
    """Run the latency test."""
    print("\n" + "="*60)
    print("🚀 BINANCE WEBSOCKET LATENCY TEST")
    print("="*60)
    print(f"VPS Location: Testing from current AWS instance")
    print(f"Target: Binance Futures (wss://fstream.binance.com)")
    print("="*60 + "\n")
    
    # Run 30-second test
    stats = await test_binance_latency(duration_seconds=30)
    print_results(stats)


if __name__ == "__main__":
    asyncio.run(main())
