#!/usr/bin/env python3
"""
Web Search Benchmark Script

Loads credentials from .env file (OPENWEBUI_ADMIN_EMAIL, OPENWEBU_ADMIN_PASSWORD).

Usage:
    python scripts/benchmark_web_search.py --label baseline

With explicit token:
    python scripts/benchmark_web_search.py --token <jwt>

Results are automatically saved to scripts/output/ with timestamp.
"""

import argparse
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Representative test queries
TEST_QUERIES = [
    "latest news about artificial intelligence",
    "python asyncio tutorial",
    "best restaurants in amsterdam",
]

# Output directory for benchmark results
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "output"


def login(base_url: str, email: str, password: str) -> str:
    """Login and return JWT token."""
    url = f"{base_url}/api/v1/auths/signin"
    response = requests.post(
        url,
        json={"email": email, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if response.status_code != 200:
        raise ValueError(f"Login failed: {response.status_code} - {response.text[:200]}")
    data = response.json()
    return data.get("token")


@dataclass
class BenchmarkResult:
    query: str
    duration_ms: float
    status_code: int
    loaded_count: int
    error: Optional[str] = None


@dataclass
class BenchmarkSummary:
    timestamp: str
    base_url: str
    iterations: int
    total_queries: int
    successful: int
    failed: int
    avg_time_ms: float
    median_time_ms: float
    min_time_ms: float
    max_time_ms: float
    std_dev_ms: Optional[float]
    results: list[dict]


def run_single_benchmark(
    base_url: str,
    query: str,
    headers: dict,
    timeout: int = 120,
) -> BenchmarkResult:
    """Run a single web search and measure time."""
    url = f"{base_url}/api/v1/retrieval/process/web/search"
    payload = {"queries": [query]}

    start = time.perf_counter()
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        duration_ms = (time.perf_counter() - start) * 1000

        if response.status_code == 200:
            data = response.json()
            return BenchmarkResult(
                query=query,
                duration_ms=duration_ms,
                status_code=response.status_code,
                loaded_count=data.get("loaded_count", 0),
            )
        else:
            return BenchmarkResult(
                query=query,
                duration_ms=duration_ms,
                status_code=response.status_code,
                loaded_count=0,
                error=response.text[:200],
            )
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        return BenchmarkResult(
            query=query,
            duration_ms=duration_ms,
            status_code=0,
            loaded_count=0,
            error=str(e),
        )


def save_summary(summary: BenchmarkSummary, label: Optional[str] = None) -> Path:
    """Save benchmark summary to scripts/output/ directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate filename with timestamp and optional label
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if label:
        filename = f"benchmark_{timestamp}_{label}.json"
    else:
        filename = f"benchmark_{timestamp}.json"

    output_path = OUTPUT_DIR / filename

    with open(output_path, "w") as f:
        json.dump(asdict(summary), f, indent=2)

    return output_path


def run_benchmark_suite(
    base_url: str,
    token: Optional[str] = None,
    api_key: Optional[str] = None,
    iterations: int = 1,
    label: Optional[str] = None,
) -> BenchmarkSummary:
    """Run full benchmark suite."""
    headers = {"Content-Type": "application/json"}

    # Determine authentication method
    if token:
        headers["Authorization"] = f"Bearer {token}"
        print("Auth: Using provided token")
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        print("Auth: Using API key")
    else:
        # Try to login with env vars
        email = os.environ.get("OPENWEBUI_ADMIN_EMAIL")
        password = os.environ.get("OPENWEBUI_ADMIN_PASSWORD")
        if email and password:
            print(f"Auth: Logging in as {email}...")
            token = login(base_url, email, password)
            headers["Authorization"] = f"Bearer {token}"
            print("Auth: Login successful")
        else:
            raise ValueError(
                "No authentication provided. Either set OPENWEBUI_ADMIN_EMAIL and "
                "OPENWEBUI_ADMIN_PASSWORD env vars, or use --token or --api-key"
            )

    timestamp = datetime.now().isoformat()

    print("=" * 70)
    print("WEB SEARCH BENCHMARK")
    print("=" * 70)
    print(f"Base URL: {base_url}")
    print(f"Iterations per query: {iterations}")
    print(f"Timestamp: {timestamp}")
    print("=" * 70)

    all_results: list[BenchmarkResult] = []

    for query in TEST_QUERIES:
        print(f"\nQuery: '{query}'")
        query_times = []

        for i in range(iterations):
            result = run_single_benchmark(base_url, query, headers)
            all_results.append(result)
            query_times.append(result.duration_ms)

            status = "OK" if result.status_code == 200 else f"ERR:{result.status_code}"
            print(
                f"  Run {i+1}: {result.duration_ms:,.0f}ms "
                f"({status}, {result.loaded_count} pages)"
            )

            if result.error:
                print(f"    Error: {result.error}")

        if iterations > 1:
            print(f"  Avg: {statistics.mean(query_times):,.0f}ms")
            print(f"  Min: {min(query_times):,.0f}ms")
            print(f"  Max: {max(query_times):,.0f}ms")

    # Build summary
    successful = [r for r in all_results if r.status_code == 200]
    times = [r.duration_ms for r in successful] if successful else [0]

    summary = BenchmarkSummary(
        timestamp=timestamp,
        base_url=base_url,
        iterations=iterations,
        total_queries=len(all_results),
        successful=len(successful),
        failed=len(all_results) - len(successful),
        avg_time_ms=statistics.mean(times) if successful else 0,
        median_time_ms=statistics.median(times) if successful else 0,
        min_time_ms=min(times) if successful else 0,
        max_time_ms=max(times) if successful else 0,
        std_dev_ms=statistics.stdev(times) if len(times) > 1 else None,
        results=[asdict(r) for r in all_results],
    )

    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total queries: {summary.total_queries}")
    print(f"Successful: {summary.successful}")
    print(f"Failed: {summary.failed}")
    if successful:
        print(f"Average time: {summary.avg_time_ms:,.0f}ms")
        print(f"Median time: {summary.median_time_ms:,.0f}ms")
        print(f"Min time: {summary.min_time_ms:,.0f}ms")
        print(f"Max time: {summary.max_time_ms:,.0f}ms")
        if summary.std_dev_ms is not None:
            print(f"Std dev: {summary.std_dev_ms:,.0f}ms")
    print("=" * 70)

    # Save summary to file
    output_path = save_summary(summary, label)
    print(f"\nResults saved to: {output_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Benchmark Open WebUI web search")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="Open WebUI base URL",
    )
    parser.add_argument("--token", help="JWT token for authentication")
    parser.add_argument("--api-key", help="API key (sk-xxx) for authentication")
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Iterations per query (default: 1)",
    )
    parser.add_argument(
        "--label",
        help="Optional label for output file (e.g., 'baseline', 'after-optimization')",
    )

    args = parser.parse_args()

    run_benchmark_suite(
        base_url=args.base_url,
        token=args.token,
        api_key=args.api_key,
        iterations=args.iterations,
        label=args.label,
    )


if __name__ == "__main__":
    main()
