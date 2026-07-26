# Verified enhancements — 2026-07-26

Status: complete — all four workstreams implemented, adversarially reviewed, and verified (212 Python tests, 82 frontend tests, ruff/eslint/tsc/build clean). Post-review fix round closed the three minor findings: explorer auto-continue over empty capped pages, worker metadata re-read before rejecting a newer-release cursor, and `cli.py` now reuses the precomputed loto frame.

Findings were produced by two deep-analysis passes and adversarially verified by independent agents before implementation.

## Phases

1. **WS-A Python perf + observability** — vectorize `loto_daily_frame` per-row loop (`src/xsmb_etl/transform.py:106-132`, `src/xsmb_etl/xsmn_transform.py:134-158`), reuse the precomputed loto frame in `build_gold_tables` (`src/xsmb_etl/marts.py:94` recomputes it), add structured logging (currently zero log calls in `src/`), warn in the 7 `except Exception: pass` blocks, delete dead `src/fetch.py`.
2. **WS-B Worker caching** — cache validated v2 metadata pointer and immutable year shards in `frontend/worker/lottery-v2.ts` (today: sequential un-cached scan of up to 22 shards × 2MB per request).
3. **WS-C Frontend hardening** — guard render-phase `backtest`/`analyzePrizeWindow` throws, add `app/error.tsx`, hoist `Intl` formatters, fix heatmap WCAG AA contrast for intensity > 0.55 cells, name the `455` constant in `dashboard-data.ts`.
4. **WS-D Prize Lab 6-digit exploitation** — extend `frontend/prize-analytics.ts` with head-3 frequency, digit presence ("chạm" 0–9), tail-3 recency; render them in the Prize Lab section; bump analytics version to v2.

## Acceptance criteria

- All existing Python tests pass (`uv run pytest`), ruff check + format clean.
- Frontend: `tsc --noEmit`, eslint, `node --test tests/*.test.mjs`, `npm run build` all clean.
- Vectorization proven equivalent to the old loop on synthetic data and benchmarked at real scale.
- Heat-cell text contrast computationally verified ≥ 4.5:1 at every intensity.
- No commits; working tree only (pre-existing uncommitted user changes must be preserved untouched).

Deferred (separate future refactors): splitting `app/page.tsx` into components; consolidating XSMB/XSMN pipeline orchestration (86–93% measured duplication).

Reports: `reports/`
