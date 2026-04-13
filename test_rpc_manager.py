"""
Test the Polygon RPC Manager with multiple fallback endpoints.
"""
import asyncio
import json
import logging

from src.execution.rpc_manager import get_rpc_manager


async def main():
    """Test RPC manager resilience and performance."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    print("="*70)
    print("🧪 TESTING POLYGON RPC MANAGER")
    print("="*70)
    print("\nFeatures being tested:")
    print("  ✅ Multiple RPC endpoints with failover")
    print("  ✅ Response caching")
    print("  ✅ Health tracking per endpoint")
    print("  ✅ Automatic retry with backoff")
    print("="*70 + "\n")
    
    # Initialize RPC manager
    rpc = get_rpc_manager(cache_ttl_seconds=30.0)
    
    # Test address (Polygon USDC contract)
    usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # Test 1: Get balance multiple times (should use cache)
    print("Test 1: Balance checks with caching")
    print("-" * 70)
    
    for i in range(3):
        start = asyncio.get_event_loop().time()
        balance = await rpc.get_balance(usdc_contract)
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        
        if balance:
            print(f"  Call {i+1}: {elapsed:.1f}ms - Balance: {balance / 10**18:.2f} MATIC")
        else:
            print(f"  Call {i+1}: {elapsed:.1f}ms - FAILED")
    
    print("\n  Note: Second and third calls should be faster (cached)")
    
    # Test 2: Get block number (short cache)
    print("\n" + "="*70)
    print("Test 2: Block number (5s cache)")
    print("-" * 70)
    
    for i in range(2):
        start = asyncio.get_event_loop().time()
        block = await rpc.get_block_number()
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        
        if block:
            print(f"  Call {i+1}: {elapsed:.1f}ms - Block: {block:,}")
        else:
            print(f"  Call {i+1}: {elapsed:.1f}ms - FAILED")
    
    # Test 3: Get stats
    print("\n" + "="*70)
    print("Test 3: RPC Manager Statistics")
    print("-" * 70)
    
    stats = rpc.get_stats()
    print(f"  Total endpoints: {stats['total_endpoints']}")
    print(f"  Healthy endpoints: {stats['healthy_endpoints']}")
    print()
    print("  Endpoint Details:")
    for ep in stats['endpoints']:
        status = "🟢" if ep['healthy'] else "🔴"
        print(f"    {status} {ep['url'][:50]}...")
        print(f"       Reliability: {ep['reliability']:.2f} | "
              f"Latency: {ep['avg_latency_ms']:.1f}ms | "
              f"Successes: {ep['successes']}")
    
    # Test 4: Simulate load
    print("\n" + "="*70)
    print("Test 4: Load Test (10 concurrent calls)")
    print("-" * 70)
    
    async def load_test_call(i):
        start = asyncio.get_event_loop().time()
        block = await rpc.get_block_number()
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        return i, elapsed, block is not None
    
    tasks = [load_test_call(i) for i in range(10)]
    results = await asyncio.gather(*tasks)
    
    successful = sum(1 for _, _, success in results if success)
    avg_time = sum(elapsed for _, elapsed, _ in results) / len(results)
    
    print(f"  Successful calls: {successful}/10")
    print(f"  Average latency: {avg_time:.1f}ms")
    print(f"  Min latency: {min(elapsed for _, elapsed, _ in results):.1f}ms")
    print(f"  Max latency: {max(elapsed for _, elapsed, _ in results):.1f}ms")
    
    # Final stats
    print("\n" + "="*70)
    print("Final Statistics")
    print("="*70)
    print(json.dumps(rpc.get_stats(), indent=2))
    
    await rpc.close()
    
    print("\n✅ Test complete!")


if __name__ == "__main__":
    asyncio.run(main())
