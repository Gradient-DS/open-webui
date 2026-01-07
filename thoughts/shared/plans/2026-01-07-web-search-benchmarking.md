# Web Search Benchmarking Plan

## Overview

Before implementing the Playwright optimizations from the research document, we need a reliable way to measure the current baseline and track improvements after each optimization step.

## Current State Analysis

From `thoughts/shared/research/2026-01-07-web-search-speed-optimization.md`:
- **Current bottleneck**: Playwright sequential fetching (50s+ for 5 results)
- **Expected improvement**: 3-4x faster after all optimizations (~12-15s for 5 URLs)
- **No existing timing** in web search flow (only debug logs for URLs/results)

## Desired End State

A simple, repeatable benchmarking setup that:
1. Measures end-to-end web search latency
2. Breaks down timing by component (SearXNG, Playwright, embedding)
3. Can be run via CLI before/after each optimization
4. Outputs results in a format easy to compare

## What We're NOT Doing

- No complex dashboards or Grafana/Prometheus setup
- No persistent metrics storage
- No production monitoring infrastructure
- No changes to the optimization code itself (that's a separate plan)

## Implementation Approach

Two-phase approach:
1. **Phase 1**: CLI benchmark script (external, no code changes)
2. **Phase 2**: Add internal timing logs to web search flow (minimal code changes)

---

## Phase 1: CLI Benchmark Script

### Overview
Create a standalone Python script that calls the web search API and measures response time.

### Test Queries

Three representative queries covering different scenarios:

| Query | Purpose | Expected Behavior |
|-------|---------|-------------------|
| `"latest news about artificial intelligence"` | News/dynamic content | Multiple results, varied page sizes |
| `"python asyncio tutorial"` | Technical docs | Documentation sites, code blocks |
| `"best restaurants in amsterdam"` | Local/commercial | Rich media pages, slower loads |

### Changes Required:

#### 1. Benchmark Script
**File**: `scripts/benchmark_web_search.py`

```python
#!/usr/bin/env python3
"""
Web Search Benchmark Script

Usage:
    python scripts/benchmark_web_search.py --base-url http://localhost:8080 --token <jwt>

Or with API key:
    python scripts/benchmark_web_search.py --base-url http://localhost:8080 --api-key sk-xxx
"""

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from typing import Optional

import requests

# Representative test queries
TEST_QUERIES = [
    "latest news about artificial intelligence",
    "python asyncio tutorial",
    "best restaurants in amsterdam",
]


@dataclass
class BenchmarkResult:
    query: str
    duration_ms: float
    status_code: int
    loaded_count: int
    error: Optional[str] = None


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


def run_benchmark_suite(
    base_url: str,
    token: Optional[str] = None,
    api_key: Optional[str] = None,
    iterations: int = 1,
) -> None:
    """Run full benchmark suite."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        raise ValueError("Either --token or --api-key required")

    print("=" * 70)
    print("WEB SEARCH BENCHMARK")
    print("=" * 70)
    print(f"Base URL: {base_url}")
    print(f"Iterations per query: {iterations}")
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
            print(f"  Run {i+1}: {result.duration_ms:,.0f}ms ({status}, {result.loaded_count} pages)")

            if result.error:
                print(f"    Error: {result.error}")

        if iterations > 1:
            print(f"  Avg: {statistics.mean(query_times):,.0f}ms")
            print(f"  Min: {min(query_times):,.0f}ms")
            print(f"  Max: {max(query_times):,.0f}ms")

    # Summary
    successful = [r for r in all_results if r.status_code == 200]
    if successful:
        times = [r.duration_ms for r in successful]
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total queries: {len(all_results)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(all_results) - len(successful)}")
        print(f"Average time: {statistics.mean(times):,.0f}ms")
        print(f"Median time: {statistics.median(times):,.0f}ms")
        print(f"Min time: {min(times):,.0f}ms")
        print(f"Max time: {max(times):,.0f}ms")
        if len(times) > 1:
            print(f"Std dev: {statistics.stdev(times):,.0f}ms")
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Benchmark Open WebUI web search")
    parser.add_argument("--base-url", default="http://localhost:8080", help="Open WebUI base URL")
    parser.add_argument("--token", help="JWT token for authentication")
    parser.add_argument("--api-key", help="API key (sk-xxx) for authentication")
    parser.add_argument("--iterations", type=int, default=1, help="Iterations per query")
    parser.add_argument("--output-json", help="Output results to JSON file")

    args = parser.parse_args()

    run_benchmark_suite(
        base_url=args.base_url,
        token=args.token,
        api_key=args.api_key,
        iterations=args.iterations,
    )


if __name__ == "__main__":
    main()
```

### Success Criteria:

#### Automated Verification:
- [x] Script runs without syntax errors: `python -c "import scripts.benchmark_web_search"`
- [x] Help text displays: `python scripts/benchmark_web_search.py --help`

#### Manual Verification:
- [ ] Script connects to local Open WebUI instance
- [ ] All 3 test queries return results
- [ ] Timing output is displayed in readable format
- [ ] Results are consistent across multiple runs (within 20% variance)

---

## Phase 2: Internal Timing Logs

### Overview
Add timing instrumentation inside the web search flow to understand where time is spent.

### Changes Required:

#### 1. Add Timing to Web Search Endpoint
**File**: `backend/open_webui/routers/retrieval.py`
**Location**: Around line 2105 (`process_web_search` function)

Add timing for each phase:
1. SearXNG query time
2. Playwright fetch time (total)
3. Embedding/storage time

```python
# At start of process_web_search function (after line ~2110)
import time
search_start = time.perf_counter()

# After search_web completes (around line 2151)
search_time = (time.perf_counter() - search_start) * 1000
log.info(f"[BENCHMARK] SearXNG query took {search_time:.0f}ms, found {len(all_results)} results")

# Before web loader (around line 2177)
loader_start = time.perf_counter()

# After loader.aload() completes (around line 2210)
loader_time = (time.perf_counter() - loader_start) * 1000
log.info(f"[BENCHMARK] Web loader fetched {len(docs)} pages in {loader_time:.0f}ms ({loader_time/len(docs):.0f}ms/page)")

# Before embedding (around line 2227)
embed_start = time.perf_counter()

# After save_docs_to_vector_db (around line 2243)
embed_time = (time.perf_counter() - embed_start) * 1000
log.info(f"[BENCHMARK] Embedding/storage took {embed_time:.0f}ms")

# At end of function
total_time = (time.perf_counter() - search_start) * 1000
log.info(f"[BENCHMARK] Total web search: {total_time:.0f}ms (search={search_time:.0f}, loader={loader_time:.0f}, embed={embed_time:.0f})")
```

#### 2. Add Timing to Playwright Loader
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: `SafePlaywrightURLLoader.alazy_load()` around line 516

```python
# Inside the URL loop (around line 529)
url_start = time.perf_counter()

# After page.goto completes (around line 538)
url_time = (time.perf_counter() - url_start) * 1000
log.debug(f"[BENCHMARK] Fetched {url} in {url_time:.0f}ms")
```

### Success Criteria:

#### Automated Verification:
- [ ] No syntax errors: `python -c "from open_webui.routers import retrieval"`
- [ ] Linting passes: `npm run lint:backend`

#### Manual Verification:
- [ ] `[BENCHMARK]` log lines appear in Open WebUI logs during web search
- [ ] Breakdown shows reasonable distribution (SearXNG ~3s, loader ~50s, embed ~5s baseline)
- [ ] Per-URL timing visible in debug logs

---

## Usage Instructions

### Running the Benchmark

1. **Start local environment** (docker-compose or k8s):
```bash
# Docker Compose
docker compose -f docker-compose.websearch.yaml up -d
docker compose up -d

# Or Kubernetes
kubectl apply -f k8s/
```

2. **Get authentication token**:
```bash
# Option A: Create API key in Open WebUI Settings > Account > API Keys
# Option B: Extract JWT from browser cookies
```

3. **Run baseline benchmark**:
```bash
python scripts/benchmark_web_search.py \
  --base-url http://localhost:8080 \
  --api-key sk-your-key \
  --iterations 3
```

4. **Save baseline results**:
```bash
python scripts/benchmark_web_search.py \
  --base-url http://localhost:8080 \
  --api-key sk-your-key \
  --iterations 3 > benchmark_baseline.txt
```

5. **After each optimization**, re-run and compare:
```bash
python scripts/benchmark_web_search.py ... > benchmark_after_phase1.txt
diff benchmark_baseline.txt benchmark_after_phase1.txt
```

### Viewing Internal Timing

```bash
# Docker
docker logs -f open-webui 2>&1 | grep BENCHMARK

# Kubernetes
kubectl logs -f -n open-webui deployment/open-webui | grep BENCHMARK
```

---

## Expected Baseline Results

Based on the research document, expect:

| Metric | Baseline (5 results) | After Optimization |
|--------|---------------------|-------------------|
| Total time | 50-60s | 12-15s |
| SearXNG | 3-6s | 3-6s (unchanged) |
| Playwright | 40-50s | 8-12s |
| Embedding | 2-5s | 2-5s (unchanged) |

---

## References

- Research: `thoughts/shared/research/2026-01-07-web-search-speed-optimization.md`
- Web search endpoint: `backend/open_webui/routers/retrieval.py:2105-2259`
- Playwright loader: `backend/open_webui/retrieval/web/utils.py:432-545`
