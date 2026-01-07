# Playwright Web Search Speed Optimization Plan

## Overview

Optimize the `SafePlaywrightURLLoader` class to dramatically improve web search response times by implementing resource blocking, faster wait strategies, and parallel URL fetching. Expected total improvement: **3-5x faster** (from ~50s to ~12-15s for 5 URLs).

Based on research: `thoughts/shared/research/2026-01-07-web-search-speed-optimization.md`

## Current State Analysis

**File**: `backend/open_webui/retrieval/web/utils.py:516-545`

Current `alazy_load()` issues:
1. **Sequential processing**: URLs fetched one-by-one in a for loop
2. **No resource blocking**: Loads all images, CSS, fonts, media
3. **Default wait strategy**: Uses implicit `load` event (waits for everything)
4. **Memory leak**: Pages are never closed with `await page.close()`

**Current time for 5 URLs**: ~50 seconds worst case (10s timeout × 5 sequential)

## Desired End State

After implementation, `SafePlaywrightURLLoader.alazy_load()` will:
1. Block non-essential resources (images, fonts, stylesheets, media)
2. Use `domcontentloaded` wait strategy
3. Fetch URLs in parallel with configurable concurrency
4. Properly close pages to prevent memory leaks

**Target time for 5 URLs**: ~12-15 seconds (parallel fetch + faster page loads)

### Verification:
- Run benchmark script: `python scripts/benchmark_web_search.py`
- Expected 3-5x improvement in `playwright_fetch_time`

## What We're NOT Doing

- ❌ Adding new config options (use existing `WEB_LOADER_CONCURRENT_REQUESTS`)
- ❌ Implementing context pooling (complexity not worth it)
- ❌ Changing SearXNG timeouts
- ❌ Adding `BYPASS_WEB_SEARCH_WEB_LOADER` config changes
- ❌ Frontend changes

## Implementation Approach

Three phases, each building on the previous, with incremental verification:

| Phase | Optimization | Expected Improvement | Risk |
|-------|--------------|---------------------|------|
| 1 | Resource blocking | 50-70% per page | None |
| 2 | `domcontentloaded` | 30-50% per page | Low (some SPAs) |
| 3 | Parallel URL fetch | 3-5x total | None |

---

## Phase 1: Resource Blocking

### Overview
Add route interception to block non-essential resources (images, fonts, stylesheets, media) before navigation. This has the biggest per-page impact with zero quality tradeoff for text extraction.

### Changes Required:

#### 1. Add resource blocking helper method
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Add new method to `SafePlaywrightURLLoader` class (after line 485)

```python
    async def _setup_resource_blocking(self, page) -> None:
        """Block non-essential resources to speed up page loading.

        Blocks images, media, fonts, and stylesheets since we only need text content.
        """
        await page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type
                in ["image", "media", "font", "stylesheet"]
                else route.continue_()
            ),
        )
```

#### 2. Update alazy_load() to use resource blocking
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Modify `alazy_load()` method, after `page = await browser.new_page()` (line 532)

**Current** (line 532-533):
```python
                    page = await browser.new_page()
                    response = await page.goto(url, timeout=self.playwright_timeout)
```

**Change to**:
```python
                    page = await browser.new_page()
                    await self._setup_resource_blocking(page)
                    response = await page.goto(url, timeout=self.playwright_timeout)
```

#### 3. Update lazy_load() for consistency
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Modify `lazy_load()` method similarly (around line 501)

Add sync version of resource blocking:
```python
    def _setup_resource_blocking_sync(self, page) -> None:
        """Sync version of resource blocking setup."""
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type
                in ["image", "media", "font", "stylesheet"]
                else route.continue_()
            ),
        )
```

And call it after `page = browser.new_page()` in `lazy_load()`.

### Success Criteria:

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing frontend errors unrelated to Python changes)
- [x] Backend linting passes: `npm run lint:backend` (module imports successfully, no new errors)
- [x] Unit tests pass (if any exist for this loader) (no dedicated tests exist)

#### Manual Verification:
- [x] Web search still returns text content correctly
- [x] Page load times improved (check browser network tab)
- [x] No regressions in search quality

**Implementation Note**: After completing this phase and all automated verification passes, run a test web search to confirm text extraction still works before proceeding.

---

## Phase 2: Wait Strategy Change

### Overview
Change from default `load` event (waits for all resources) to `domcontentloaded` (waits only for HTML parsing). Combined with resource blocking, this significantly reduces wait time.

### Changes Required:

#### 1. Update alazy_load() wait strategy
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Modify `page.goto()` call in `alazy_load()` (line ~534 after Phase 1)

**Current**:
```python
                    response = await page.goto(url, timeout=self.playwright_timeout)
```

**Change to**:
```python
                    response = await page.goto(
                        url,
                        timeout=self.playwright_timeout,
                        wait_until="domcontentloaded",
                    )
```

#### 2. Update lazy_load() for consistency
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Similarly update `page.goto()` in `lazy_load()` method

```python
                response = page.goto(
                    url,
                    timeout=self.playwright_timeout,
                    wait_until="domcontentloaded",
                )
```

### Success Criteria:

#### Automated Verification:
- [x] Type checking passes: `npm run check` (pre-existing frontend errors unrelated to Python changes)
- [x] Backend linting passes: `npm run lint:backend` (module imports successfully, no new errors)

#### Manual Verification:
- [x] REVERTED - domcontentloaded caused quality degradation (incomplete content on many sites)

**Implementation Note**: Phase 2 was implemented but reverted due to quality issues. The `domcontentloaded` strategy didn't wait long enough for content to load on many sites. Proceeding directly to Phase 3 (parallel fetching) which provides the biggest speedup without quality tradeoffs.

---

## Phase 3: Parallel URL Fetching

### Overview
Refactor `alazy_load()` to fetch multiple URLs in parallel using `asyncio.gather()` with a semaphore for concurrency control. This is the largest overall speedup (5 URLs in ~10s instead of ~50s).

### Changes Required:

#### 1. Add imports
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Add to imports section (around line 1-20)

```python
from typing import List, Tuple, Optional
```

(Note: `asyncio` is likely already imported, verify)

#### 2. Add parallel fetch helper method
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Add to `SafePlaywrightURLLoader` class (after `_setup_resource_blocking`)

```python
    async def _fetch_single_url(
        self,
        browser,
        url: str,
        semaphore: asyncio.Semaphore,
    ) -> Tuple[Optional[Document], Optional[Exception]]:
        """Fetch a single URL with semaphore-controlled concurrency.

        Returns a tuple of (Document, None) on success or (None, Exception) on failure.
        """
        async with semaphore:
            page = None
            try:
                await self._safe_process_url(url)
                page = await browser.new_page()
                await self._setup_resource_blocking(page)
                response = await page.goto(
                    url,
                    timeout=self.playwright_timeout,
                    wait_until="domcontentloaded",
                )
                if response is None:
                    raise ValueError(f"page.goto() returned None for url {url}")

                text = await self.evaluator.evaluate_async(page, browser, response)
                metadata = {"source": url}
                return Document(page_content=text, metadata=metadata), None
            except Exception as e:
                return None, e
            finally:
                if page:
                    await page.close()
```

#### 3. Completely rewrite alazy_load() method
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Replace entire `alazy_load()` method (lines 516-545)

```python
    async def alazy_load(self) -> AsyncIterator[Document]:
        """Safely load URLs asynchronously with parallel fetching.

        Uses asyncio.gather for concurrent URL fetching with semaphore-based
        concurrency control. Resource blocking and domcontentloaded wait strategy
        are applied for faster page loads.
        """
        from playwright.async_api import async_playwright

        # Default to 5 concurrent requests if not using rate limiting
        max_concurrent = 5
        if self.requests_per_second and self.requests_per_second > 0:
            # If rate limiting is set, use it as concurrency limit
            max_concurrent = max(1, int(self.requests_per_second))

        async with async_playwright() as p:
            # Use remote browser if ws_endpoint is provided, otherwise use local browser
            if self.playwright_ws_url:
                browser = await p.chromium.connect(self.playwright_ws_url)
            else:
                browser = await p.chromium.launch(
                    headless=self.headless, proxy=self.proxy
                )

            try:
                semaphore = asyncio.Semaphore(max_concurrent)

                # Fetch all URLs in parallel
                tasks = [
                    self._fetch_single_url(browser, url, semaphore)
                    for url in self.urls
                ]
                results = await asyncio.gather(*tasks)

                # Yield successful results, handle failures
                for url, (doc, error) in zip(self.urls, results):
                    if error is not None:
                        if self.continue_on_failure:
                            log.exception(f"Error loading {url}: {error}")
                            continue
                        raise error
                    if doc is not None:
                        yield doc
            finally:
                await browser.close()
```

#### 4. Fix page closure in lazy_load() (memory leak fix)
**File**: `backend/open_webui/retrieval/web/utils.py`
**Location**: Update `lazy_load()` method to close pages

Add `page.close()` after extracting text in the sync version:
```python
                    text = self.evaluator.evaluate(page, browser, response)
                    metadata = {"source": url}
                    page.close()  # ADD THIS LINE
                    yield Document(page_content=text, metadata=metadata)
```

### Success Criteria:

#### Automated Verification:
- [ ] Type checking passes: `npm run check`
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] No import errors when starting backend: `open-webui dev`

#### Manual Verification:
- [ ] Web search completes in ~12-15s for 5 results (down from ~50s)
- [ ] All 5 results are returned (no dropped URLs)
- [ ] Memory usage stable after multiple searches (no leak)
- [ ] Rate limiting still works when `requests_per_second` is set

**Implementation Note**: This is the final phase. After completion, run the benchmark script to verify the full 3-5x improvement.

---

## Testing Strategy

### Manual Testing Steps:
1. Start the application: `open-webui dev`
2. Navigate to chat, enable web search
3. Ask a question that triggers web search (e.g., "What happened in tech news today?")
4. Measure time from query to first response token
5. Verify search results are complete and accurate

### Benchmark Script:
Use `scripts/benchmark_web_search.py` to measure:
- SearXNG query time
- Playwright fetch time (per URL and total)
- Total end-to-end time

### Edge Cases to Test:
- Sites with heavy JavaScript (SPAs) - may have less content
- Sites behind Cloudflare/bot protection - should timeout gracefully
- Invalid/dead URLs - should continue to next URL
- Very slow sites - should respect timeout

## Performance Considerations

**Memory**: With proper page closure, memory usage should remain stable. Each page context uses ~100-300MB, and with 5 concurrent pages we need ~1.5GB.

**CPU**: Parallel fetching increases CPU usage during fetch phase. With 8-12GB allocation and 1-3 CPU cores (per k8s config), this is well within limits.

**Network**: Parallel requests may trigger rate limiting on some sites. The semaphore prevents overwhelming a single origin.

## References

- Research document: `thoughts/shared/research/2026-01-07-web-search-speed-optimization.md`
- SafePlaywrightURLLoader: `backend/open_webui/retrieval/web/utils.py:432-545`
- Similar pattern (MistralLoader): `backend/open_webui/retrieval/loaders/mistral.py:702-769`
- Web search endpoint: `backend/open_webui/routers/retrieval.py:2118-2151`
- Playwright config: `backend/open_webui/config.py:3288-3298`
