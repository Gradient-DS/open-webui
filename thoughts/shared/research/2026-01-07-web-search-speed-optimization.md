---
date: 2026-01-07T17:30:00+01:00
researcher: Claude
git_commit: 86886d81c6671321588f28813e4b494337c0c27b
branch: feat/web-fast
repository: open-webui
topic: "Web Search Speed Optimization with 8-12GB Resources"
tags: [research, codebase, web-search, playwright, searxng, performance, optimization]
status: complete
last_updated: 2026-01-07
last_updated_by: Claude
---

# Research: Web Search Speed Optimization with 8-12GB Resources

**Date**: 2026-01-07T17:30:00+01:00
**Researcher**: Claude
**Git Commit**: 86886d81c6671321588f28813e4b494337c0c27b
**Branch**: feat/web-fast
**Repository**: open-webui

## Research Question

How to make web search as fast as possible with 8-12GB allocated resources? What affects speed, how to reduce time-to-answer, and what are the quality tradeoffs?

## Summary

The current implementation has **significant optimization opportunities** in the Playwright loader:

| Issue | Current State | Potential Speedup |
|-------|---------------|-------------------|
| Wait strategy | `load` (default) | `domcontentloaded` = **30-50% faster** |
| Resource blocking | None | Block images/fonts/CSS = **50-70% faster** |
| URL processing | Sequential | Parallel with semaphore = **3-5x faster** |
| Page cleanup | Pages not closed | Prevent memory leaks |
| Context reuse | New page per URL | Context pooling = **10-20% faster** |

**Recommended priority order:**
1. **Resource blocking** - Biggest win, no quality impact for text extraction
2. **`wait_until: domcontentloaded`** - Major win for most pages
3. **Parallel URL fetching** - Dramatic speedup for multiple results
4. **SearXNG timeout tuning** - Already reasonable, minor gains possible
5. **Bypass options** - Skip page loading entirely for fastest results

## Detailed Findings

### Current Architecture Flow

```
1. User search query
      ↓
2. SearXNG query (3s timeout per engine) → returns URLs + snippets
      ↓
3. Playwright fetches each URL SEQUENTIALLY (10s timeout each)
   └── No resource blocking
   └── Waits for full page load
   └── Creates new page per URL (not closed)
      ↓
4. Text extraction via BeautifulSoup
      ↓
5. Optional: Embedding + vector DB storage
```

**Time breakdown for 5 search results:**
- SearXNG: 3-6s (parallel across engines)
- Playwright: 10s × 5 = **50s worst case** (sequential)
- Text extraction: <1s
- Embedding: 2-5s

**Total: 56-62 seconds** for 5 results (Playwright is the bottleneck)

---

### Performance Bottleneck #1: Playwright Sequential Processing

**Location**: `backend/open_webui/retrieval/web/utils.py:516-545`

```python
# Current implementation - SEQUENTIAL
async with async_playwright() as p:
    browser = await p.chromium.connect(self.playwright_ws_url)
    for url in self.urls:  # ← Sequential loop
        page = await browser.new_page()
        response = await page.goto(url, timeout=self.playwright_timeout)
        # ... extract text
        # NOTE: page.close() is NEVER called - memory leak!
    await browser.close()
```

**Recommendation**: Parallel fetching with semaphore

```python
async def scrape_urls_parallel(self, max_concurrent: int = 5):
    async with async_playwright() as p:
        browser = await p.chromium.connect(self.playwright_ws_url)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_one(url: str):
            async with semaphore:
                context = await browser.new_context()
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                   timeout=self.playwright_timeout)
                    content = await page.content()
                    return Document(page_content=content, ...)
                finally:
                    await context.close()  # Proper cleanup

        results = await asyncio.gather(*[fetch_one(url) for url in self.urls])
        await browser.close()
        return results
```

**Expected improvement**: 5 URLs in parallel = **5x faster** (10s instead of 50s)

---

### Performance Bottleneck #2: No Resource Blocking

**Location**: `backend/open_webui/retrieval/web/utils.py:529-538`

Current code loads ALL resources (images, fonts, CSS, JS):

```python
response = await page.goto(url, timeout=self.playwright_timeout)
```

**Recommendation**: Block non-essential resources

```python
async def fetch_with_blocking(page, url, timeout):
    # Block heavy resources
    await page.route("**/*", lambda route:
        route.abort() if route.request.resource_type in
        ["image", "media", "font", "stylesheet"]
        else route.continue_()
    )

    response = await page.goto(url,
                                wait_until="domcontentloaded",
                                timeout=timeout)
    return await page.content()
```

**Expected improvement**: **50-70% faster page loads**, no quality impact for text extraction

---

### Performance Bottleneck #3: Wait Strategy

**Location**: `backend/open_webui/retrieval/web/utils.py:533`

```python
response = await page.goto(url, timeout=self.playwright_timeout)
# Default wait_until="load" - waits for EVERYTHING
```

**Wait strategy comparison:**

| Strategy | Wait For | Speed | Use Case |
|----------|----------|-------|----------|
| `domcontentloaded` | HTML parsed, deferred scripts starting | Fast | Text extraction |
| `load` | Full page + images + stylesheets | Medium | Need images |
| `networkidle` | No network activity for 500ms | Slow | SPAs, dynamic content |

**Recommendation**: Use `domcontentloaded` for text extraction

```python
response = await page.goto(url,
                           wait_until="domcontentloaded",
                           timeout=self.playwright_timeout)
```

**Expected improvement**: **30-50% faster** for most pages

---

### Performance Bottleneck #4: SearXNG Timeout Settings

**Location**: `searxng/settings.yml:44-50`

Current settings are reasonable:
```yaml
outgoing:
  request_timeout: 3.0        # Good - fail fast
  max_request_timeout: 10.0   # Could reduce to 6.0
  pool_connections: 100       # Good
  pool_maxsize: 20            # Good
  enable_http2: true          # Good
```

**Recommendation**: Slightly more aggressive timeouts

```yaml
outgoing:
  request_timeout: 2.0        # Reduced from 3.0
  max_request_timeout: 6.0    # Reduced from 10.0
```

**Expected improvement**: 1-2s savings on slow engines, may lose some results

---

### Configuration Quick Wins

#### Option 1: Skip Page Loading Entirely (Fastest, Quality Tradeoff)

Set `BYPASS_WEB_SEARCH_WEB_LOADER=True` in `k8s/open-webui.yaml`:

```yaml
BYPASS_WEB_SEARCH_WEB_LOADER: "true"
```

**Effect**: Uses search snippets instead of full page content
- **Pro**: 10-50x faster (no Playwright)
- **Con**: Less context for RAG, lower answer quality

**Location**: `backend/open_webui/routers/retrieval.py:2178-2195`

#### Option 2: Skip Embedding (Faster, Less Reusability)

Set `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=True`:

```yaml
BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: "true"
```

**Effect**: Injects full content as context without vector DB
- **Pro**: No embedding latency (2-5s savings)
- **Con**: No caching, repeated searches re-fetch everything

#### Option 3: Reduce Result Count

Current: `WEB_SEARCH_RESULT_COUNT: "5"` (k8s config)

```yaml
WEB_SEARCH_RESULT_COUNT: "3"  # Reduced
```

**Effect**: Fewer pages to scrape
- **Pro**: Linear speedup (3 pages vs 5 = 40% faster)
- **Con**: Less comprehensive search results

#### Option 4: Increase Playwright Timeout Granularity

Current: `PLAYWRIGHT_TIMEOUT: "10000"` (10s per page)

Consider reducing to 5000ms for faster fail-fast:

```yaml
PLAYWRIGHT_TIMEOUT: "5000"
```

---

### Kubernetes Resource Analysis

**Current allocation** (`k8s/playwright.yaml:20-26`):
```yaml
resources:
  requests:
    memory: "8Gi"
    cpu: "1000m"
  limits:
    memory: "12Gi"
    cpu: "3000m"
```

**Analysis**: 8-12GB is generous for Playwright. Each browser context uses ~100-300MB.
- With 5 concurrent contexts: 0.5-1.5GB
- Chromium base: ~500MB
- Total: ~2GB needed, **6-10GB overhead available**

**Recommendation**: Can safely run 10-20 concurrent page fetches with current allocation

---

### Implementation Priority Matrix

| Optimization | Effort | Impact | Quality Risk |
|--------------|--------|--------|--------------|
| Resource blocking | Low | High (50-70%) | None |
| `domcontentloaded` | Low | High (30-50%) | Low (some SPAs) |
| Parallel URL fetch | Medium | Very High (3-5x) | None |
| Page/context cleanup | Low | Medium (memory) | None |
| Reduce timeouts | Low | Low (1-2s) | Medium (lost results) |
| `BYPASS_WEB_LOADER` | None (config) | Very High | High |
| Reduce result count | None (config) | Medium | Medium |

---

### Recommended Implementation

**Phase 1 - Quick Config Wins (No Code Changes)**
1. Set `PLAYWRIGHT_TIMEOUT: "5000"` (5s instead of 10s)
2. Set `WEB_SEARCH_RESULT_COUNT: "3"` (if acceptable)
3. Consider `BYPASS_WEB_SEARCH_WEB_LOADER: "true"` for speed-critical use

**Phase 2 - Playwright Loader Optimization**
1. Add resource blocking in `SafePlaywrightURLLoader`
2. Change wait strategy to `domcontentloaded`
3. Add `page.close()` / `context.close()` calls

**Phase 3 - Parallel Fetching**
1. Refactor `alazy_load()` to use `asyncio.gather()` with semaphore
2. Add `PLAYWRIGHT_MAX_CONCURRENT` config option
3. Implement context pooling for reuse

---

## Code References

| File | Line | Purpose |
|------|------|---------|
| `backend/open_webui/retrieval/web/utils.py` | 432-545 | SafePlaywrightURLLoader class |
| `backend/open_webui/retrieval/web/utils.py` | 653-725 | get_web_loader() factory |
| `backend/open_webui/routers/retrieval.py` | 2105-2259 | Web search endpoint |
| `backend/open_webui/config.py` | 3288-3298 | Playwright config |
| `k8s/playwright.yaml` | 1-42 | Playwright k8s deployment |
| `k8s/searxng.yaml` | 1-88 | SearXNG k8s deployment |
| `searxng/settings.yml` | 44-50 | SearXNG timeout settings |
| `k8s/open-webui.yaml` | 115-126 | Open WebUI web search config |

## Architecture Insights

1. **Separation of concerns**: Search (SearXNG) vs Scraping (Playwright) are well-separated
2. **Factory pattern**: `get_web_loader()` allows easy engine switching
3. **Config-driven**: Most settings are environment-variable driven
4. **Mixin pattern**: `RateLimitMixin` provides reusable rate limiting

## Quality vs Speed Tradeoffs

| Setting | Speed Gain | Quality Impact |
|---------|------------|----------------|
| `BYPASS_WEB_SEARCH_WEB_LOADER` | 10-50x | Significant - only snippets |
| `domcontentloaded` | 30-50% | Minor - some SPAs may have incomplete content |
| Resource blocking | 50-70% | None for text extraction |
| Shorter timeouts | 10-30% | May lose slow-loading pages |
| Fewer results | Linear | Less comprehensive answers |

**Recommended balance for 8-12GB allocation:**
- Keep full page scraping (quality)
- Use `domcontentloaded` + resource blocking (speed)
- Parallel fetch with 5 concurrent (speed)
- 5s timeout (balanced)

**Expected result**: 5 URLs in ~12-15s instead of 50s+ (**3-4x improvement**)

## Open Questions

1. Should we add a `PLAYWRIGHT_WAIT_STRATEGY` config option?
2. Should resource blocking be configurable per-request?
3. Is context pooling worth the complexity for persistent browser connections?
4. Should we implement a caching layer for recently-scraped URLs?
