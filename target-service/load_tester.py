"""Continuous load tester — sends session start requests and reports error rates."""
import asyncio
import random
import sys
import time
import uuid

import aiohttp


async def send_request(session: aiohttp.ClientSession, url: str) -> int:
    payload = {
        "session_id": str(uuid.uuid4()),
        "metadata": {"source": "load-tester", "user": f"user_{random.randint(1, 10000)}"},
    }
    try:
        async with session.post(
            f"{url}/voice-agent/session/start",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            return resp.status
    except Exception:
        return 0


async def run_load_test(url: str = "http://localhost:8080", rps: int = 10, duration: int = 120) -> dict:
    stats: dict[str, int] = {"2xx": 0, "5xx": 0, "error": 0}
    print(f"[load-tester] Targeting {url} at {rps} req/s for {duration}s")
    async with aiohttp.ClientSession() as session:
        start = time.time()
        while (elapsed := time.time() - start) < duration:
            tasks = [send_request(session, url) for _ in range(rps)]
            results = await asyncio.gather(*tasks)
            for status in results:
                if 200 <= status < 300:
                    stats["2xx"] += 1
                elif 500 <= status < 600:
                    stats["5xx"] += 1
                else:
                    stats["error"] += 1
            total_so_far = sum(stats.values())
            err_rate = (stats["5xx"] + stats["error"]) / total_so_far * 100 if total_so_far else 0
            print(f"  [{elapsed:5.0f}s] 2xx={stats['2xx']} 5xx={stats['5xx']} err_rate={err_rate:.1f}%")
            await asyncio.sleep(1)
    return stats


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    rps = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 120
    results = asyncio.run(run_load_test(url, rps, duration))
    total = sum(results.values())
    print(f"\nFinal: {results} | error_rate={((results['5xx'] + results['error']) / total * 100):.1f}%")
