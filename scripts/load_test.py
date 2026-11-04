import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError
from urllib.request import urlopen


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def measure_request(url):
    started_at = time.perf_counter()
    try:
        with urlopen(url, timeout=10) as response:
            status = response.status
    except (OSError, URLError, TimeoutError):
        status = None
    return status, time.perf_counter() - started_at


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser(
        description="Measure latency of the database-backed Django health endpoint."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000/healthz/")
    parser.add_argument("--requests", type=positive_integer, default=200)
    parser.add_argument("--concurrency", type=positive_integer, default=10)
    options = parser.parse_args()

    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=options.concurrency) as executor:
        results = list(
            executor.map(
                measure_request,
                [options.url] * options.requests,
            )
        )
    elapsed = time.perf_counter() - started_at
    latencies = [duration for _, duration in results]
    successful = sum(status == 200 for status, _ in results)
    report = {
        "endpoint": options.url,
        "workload": {
            "requests": options.requests,
            "concurrency": options.concurrency,
        },
        "successful": successful,
        "failed": options.requests - successful,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_per_second": round(successful / elapsed, 2),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50) * 1000, 2),
            "p95": round(percentile(latencies, 0.95) * 1000, 2),
            "p99": round(percentile(latencies, 0.99) * 1000, 2),
            "max": round(max(latencies) * 1000, 2),
        },
    }
    print(json.dumps(report, indent=2))
    return 0 if successful == options.requests else 1


if __name__ == "__main__":
    raise SystemExit(main())
