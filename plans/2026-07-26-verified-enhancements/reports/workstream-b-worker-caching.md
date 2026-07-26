# Workstream B — Cloudflare Worker caching

## Files changed

- `frontend/worker/lottery-v2.ts` — in-worker Cache API caching for the v2 read path plus a per-request shard scan cap.
- `frontend/worker/index.ts` — the two v2 GET routes now pass `{ waitUntil }` so cache writes ride `ctx.waitUntil` instead of blocking the response (the only index.ts change; ctx was already available at these call sites).
- `frontend/tests/lottery-v2.test.mjs` — additions only: a TTL-aware fake cache double, an R2-get-counting env helper, a multi-year fixture builder, and 6 new tests. All pre-existing tests are untouched and still pass.

## Task 1 — Cache design

### Seam

`handleLotteryV2Metadata` and `handleLotteryV2Results` accept an optional 4th parameter:

```ts
export type LotteryV2CacheContext = {
  cache?: LotteryV2EdgeCache;              // match(url) / put(url, response)
  waitUntil?: (promise: Promise<unknown>) => void;
};
```

`resolveCacheContext` prefers an injected cache (tests), otherwise feature-detects the Workers runtime default cache via `(globalThis as { caches?: { default?: ... } }).caches?.default`. The `globalThis` cast avoids depending on how the global `caches` symbol is typed (the tsconfig loads both `dom` and `@cloudflare/workers-types`, which disagree about `CacheStorage.default`). In Node tests and local dev there is no global `caches`, so the resolved context is `undefined` and every cache branch is skipped — existing tests run byte-identical code paths.

### Keys and TTLs

- Metadata pointer: `https://lottery-cache.internal/v2/regions/<region>/latest`, `Cache-Control: public, max-age=60`. Staleness is bounded to 60s, well inside the 300s public response `cache-control` the endpoints already advertise.
- Year shards: `https://lottery-cache.internal/v2/releases/<releaseId>/regions/<region>/stations/<stationCode>/years/<year>`, `max-age=86400`. The long TTL is safe because shards are immutable per release and the releaseId is part of the key, so a new publish can never be served old shard bytes. Note: the task's example key omitted the station segment; I added it because XSMN/XSMT have multiple stations per region and shard identity is (release, region, station, year).
- Negative results (missing metadata) are never cached.

### Validation-on-hit decision

What is cached is the exact R2 body text that already passed `isLotteryV2ReleaseMetadata` / `isLotteryV2Shard` (I switched `object.json()` to `object.text()` + `JSON.parse` so the cached bytes are precisely the validated bytes, with no re-serialization cost). Skipping validation on hit would therefore be admissible per the task's criterion. I chose to re-validate on hit anyway, with a fall-through to R2 when parse or validation fails, because:

1. It makes a corrupted/foreign cache entry behave as a miss instead of a 503 or wrong data — the request can never be *worse* off than the uncached path.
2. The cost is CPU-only and identical to what the R2 path already pays per request today, so hits are still strictly cheaper (no R2 round trip) and never slower than before.
3. `caches.default` is zone-shared; validation removes any reliance on the synthetic-origin namespace being exclusively ours.

### Write failure isolation

`writeCachedJson` wraps `cache.put` in an inner async IIFE whose `catch` swallows all errors, so the promise handed to `waitUntil` can never reject (no unhandled rejection) and the awaited fallback path (no `waitUntil`, e.g. tests) cannot throw into the request. Cache `match` errors are treated as misses inside `readCachedJson`.

### What is deliberately NOT cached

The ingest/publication path reads (`readCurrentMetadataForPublication`, `validateDeclaredShards`, `activateV2HealthIfReady`) call `readMetadata`/`readShard` without a cache context: publish-time validation must observe real R2 state, never a cached view. No public API shape, error code, or response header changed.

## Task 2 — Per-request shard scan cap (implemented)

Finding: the existing cursor CAN cleanly express a partial page resuming at a year boundary, so the cap was implemented (`MAX_SHARDS_PER_REQUEST = 8`).

Reasoning, verified against source:

- Cursor payload semantics are `beforeDate` = "return draws strictly before this date", bound to releaseId + query fingerprint (`decodeCursor` validates only shape + `validDate`; it never requires `beforeDate` to be an item date). After scanning shards for years `Y_newest..Y_old`, every draw dated `>= Y_old-01-01` has been considered, so `beforeDate = "<Y_old>-01-01"` is an exact, loss-free resume point. On resume the `year > beforeYear` skip re-reads exactly one already-scanned shard (year `Y_old`, which then yields zero draws because all its dates are `>= beforeDate`); that is the price of not changing the cursor shape, costs one shard read per continuation, and is cache-hot after Task 1. Progress of at least `cap - 1 = 7` new shards per request guarantees termination.
- Response contract: an empty/partial page carrying a non-null `nextCursor` already validates against `isLotteryV2ResultsPage` (`returned: 0` ≤ limit, empty `items.every(...)` is true).
- Client compatibility, verified in source: `fetchStationHistory` (dashboard-data.ts:224-233) loops `while (cursor && ...)`; `completeExplorerRequest` (explorer-state.ts:141-164) appends empty pages and keeps the cursor; the load-more button (app/page.tsx:937) renders on `explorerState.cursor` regardless of status, so a filtered search whose first 8 years match nothing shows "empty so far" plus load-more — accurate and actionable.

`readResultPage` now returns `{ items, resumeBeforeDate }`: item-date cursor when the page filled early (identical to previous behavior, including the exact `beforeDate` values, confirmed by the untouched pre-existing pagination tests), year-boundary cursor when the cap left shards unscanned, `null` when the scan completed. The previously dead `?? effectiveFrom` cursor fallback (unreachable: `hasMore` implied a non-empty page) disappeared with the restructure.

Bound achieved: a never-matching filter over XSMB 2005-2026 (22 shards) now costs at most 1 metadata + 8 shard reads per request across 3 requests, instead of 22 shard reads in one request — and with Task 1, repeat scans are R2-free.

## Task 3 — Tests (all additions to `frontend/tests/lottery-v2.test.mjs`)

Fake cache double: TTL-aware (`max-age` parsed from the stored response, injectable clock), records `matches`/`puts`, can fail puts or matches on demand.

- (a) "v2 metadata cache serves repeat reads within the TTL and expires after it" — second read performs zero R2 gets; advancing the clock past 60s triggers exactly one more R2 get.
- (b) "v2 shard reads hit the cache on repeat requests for the same release" — second identical request performs zero R2 gets and returns a deep-equal page.
- (c) "v2 cached shards are release-scoped so a new release bypasses them" — with release-A metadata + shard cached, the pointer moves to release B (metadata TTL elapsed, shard TTL not); the request serves release B, fetches the B shard from R2, and the cache was probed under a B-scoped URL (asserted via `cache.matches`), proving old entries cannot collide.
- (d) "v2 requests succeed when the cache cannot be read or written" — failing `put`, failing `match`, and the `waitUntil` path all return 200 with correct items; the deferred write promises settle without rejecting.
- Cap test 1 — 10-year fixture, filter matching only the oldest year: request 1 reads exactly 9 R2 objects (metadata + 8 shards), returns 0 items with a cursor whose decoded `beforeDate` is the year-boundary `<years[2]>-01-01`; request 2 reads 4 more objects and returns the match with a null cursor.
- Cap test 2 — never-matching filter pages to completion in exactly 2 requests, ≤ 9 R2 gets per request.

## Verification

- `cd frontend && node --test tests/lottery-v2.test.mjs` → 20 tests, 20 pass, 0 fail (14 pre-existing + 6 new). The single stderr JSON line during the run is the expected log from the pre-existing release-completeness 503 test.
- `npx tsc --noEmit` (no other process was running it) → only errors are `app/page.tsx` JSX syntax errors (TS17008/TS17015/TS1382), a file owned by a concurrent workstream and untouched here; zero errors in `worker/lottery-v2.ts`, `worker/index.ts`.
- Did not run `npm run build` or the full suite per workstream constraints.

Status: DONE
Summary: Added feature-detected, injectable Cache API caching (release-scoped shard keys with 24h TTL, 60s metadata pointer TTL, best-effort writes via ctx.waitUntil) and an 8-shard-per-request scan cap expressed through the existing beforeDate cursor semantics, with 6 new tests; 20/20 tests pass and my files type-check cleanly.
Concerns: Continuation pages re-read one boundary shard by design (cache-hot, documented above); the final verify phase should re-run full tsc/build once the sibling page.tsx edits land.
