# WS-A Report: Python Performance + Observability

Date: 2026-07-26

## Task 1 — Vectorized loto recency computation

### What changed

- `src/xsmb_etl/transform.py` (`loto_daily_frame`): replaced the per-row Python loop (previously lines 106-132, ~1.52M `.at` calls at real scale) with vectorized pandas operations.
- `src/xsmb_etl/xsmn_transform.py` (`southern_loto_daily_frame`): same replacement for the station-grain variant, grouped by `station_code` + `number_2d`.

### Vectorized recipe

- `draw_position = groupby(keys, sort=False).cumcount()` reproduces the loop's 0-based within-group position (row order within each group is already draw-date order in both frames).
- `appeared_position = draw_position.where(appeared)` and `appeared_date = draw_date.where(appeared)` keep values only at appearance rows.
- `groupby(keys).shift(1)` followed by `groupby(keys).ffill()` yields, for each row, the last appeared position/date strictly before it — matching the loop semantics where the previous pointer updates only after the current row and only on appeared rows.
- `draws_since_previous = (draw_position - previous_position).astype('Int64')`, `calendar_days_since_previous = (draw_date - previous_date).dt.days.astype('Int64')`, and `previous_appearance_status = previous_position.notna().map({True: 'seen_before', False: 'never_seen'}).astype('string')`.
- Column order (`LOTO_DAILY_COLUMNS` / `SOUTHERN_LOTO_DAILY_COLUMNS`), dtypes (`Int64` nullable, `string`), and the final `sort_values` calls are unchanged.

### Equivalence proof

Harness: `/private/tmp/claude-501/-Users-william-Developer-vietnam-lottery-xsmb-analysis/4232e3cd-911c-4d2f-9912-3682d80fc908/scratchpad/recency_equivalence_harness.py`

It contains verbatim copies of both OLD loop implementations and asserts `pandas.testing.assert_frame_equal` (values, dtypes, index, column order) across 10 synthetic cases:

- XSMB: date gaps + duplicate frequencies + never-seen numbers; `run_id=None` fallback path; single date; heavy duplicates (same number 5x/day); randomized 300 dates seed=7 (restricted range → many never-seen); randomized 120 dates seed=21 (full 00-99 range).
- XSMN: intermittent station schedules (stations missing on some dates); `run_id=None` fallback; randomized 200 dates seed=11 with 4 stations and per-date station subsets; single date multi-station.

Results (both runs — standalone new implementation before editing the modules, and `--module` against the shipped `xsmb_etl` functions after editing): all 10 cases frame-identical.

### Benchmark (7600-date XSMB scale, `--bench`)

- Input: 7600 dates, 205,200 draw rows → 760,000 output rows.
- Old loop: 35.55s. Vectorized: 1.57s. Speedup: **22.6x** (requirement: >= 10x). The residual 1.57s is dominated by the pre-existing row-dict frame construction, not the recency computation.
- The benchmark run also asserts `assert_frame_equal` between old and new output at that scale.

## Task 2 — Reuse the precomputed loto frame in gold builders

- `src/xsmb_etl/marts.py` `build_gold_tables` and `src/xsmb_etl/xsmn_marts.py` `build_southern_gold_tables`: added keyword-only `loto_daily: pd.DataFrame | None = None`. When provided, the builder copies the frame (caller's frame is never mutated) and restamps `run_id` exactly as `loto_daily_frame(..., run_id=run_id)` would (assign then `astype('string')`, mirroring the `fact_draw_result` stamping). When omitted, behavior is byte-identical to before.
- Call sites updated to pass the already-computed frame:
  - `src/xsmb_etl/pipeline.py`: `run` (current + full-history), `_ingest_backfill_date`, `build_gold`.
  - `src/xsmb_etl/xsmn_pipeline.py`: `run` (current + full-history), `_ingest_backfill_date`, `build_gold` (this class also serves XSMT).
  - `src/xsmb_etl/migration.py`: `migrate` (same verified redundancy — `loto` was computed two lines above the builder call; migration.py is in this workstream's file list).
- `xsmt_marts.py` aliases `build_southern_gold_tables`, so XSMT inherits the parameter with no change.
- `cli.py` calls `build_gold_tables`/`build_southern_gold_tables` without a precomputed frame; it is outside this workstream's file list and its behavior is unchanged by the optional parameter.
- New test `tests/test_gold_precomputed_loto.py` (addition only): reused-vs-recomputed tables are frame-identical for XSMB and XSMN, the provided frame is not mutated, and a stale `run_id` on the provided frame is restamped in the output without touching the caller's copy.

## Task 3 — Structured logging

Module-level `logger = logging.getLogger(__name__)` added to `pipeline.py`, `xsmn_pipeline.py`, `migration.py`, `extract.py`, `xsmn_extract.py`. No logging added to per-row hot paths; no secrets or credentialed URLs are logged.

- `pipeline.py` / `xsmn_pipeline.py` INFO points: run start (region label + target date + run_id) in `run`, `_ingest_backfill_date`, and `build_gold`; bronze reuse vs fresh extract; quality report passed; gold objects written (count); snapshot + latest published; no-draw recorded (both the `NoDrawSourcePageError` handlers and `record_no_draw`).
- All 7 best-effort failure-manifest blocks (`pipeline.py` x3, `xsmn_pipeline.py` x3, `migration.py` x1) now emit `logger.warning('failed to write failure manifest for %s', target_date, exc_info=True)` inside the existing `except Exception:` and the original re-raise control flow is preserved exactly (the outer exception still propagates; a manifest-write failure still never masks it).
- `extract.py`: `before_sleep=before_sleep_log(logger, logging.WARNING)` added to the existing tenacity `Retrying` in `_get_with_retry` (inherited by the XSMN/XSMT extractors), plus INFO on successful extraction with the date. `xsmn_extract.py`: INFO on successful extraction (normal and fallback-reconciliation paths) with region label and date.

## Task 4 — Deleted dead code

- Deleted `src/fetch.py`. Re-verified zero references repo-wide before deletion (only mention is in the plan document).

## Acceptance criteria results

- `uv run pytest -q`: **212 passed** (209 baseline + 3 new tests), 0 failures.
- `uv run ruff check .`: All checks passed.
- `uv run ruff format --check .`: 74 files already formatted.
- Equivalence harness: all 10 cases identical, in both standalone and `--module` modes.
- Benchmark: 22.6x speedup at 7600-date scale (>= 10x required).

Files changed: `src/xsmb_etl/transform.py`, `src/xsmb_etl/xsmn_transform.py`, `src/xsmb_etl/marts.py`, `src/xsmb_etl/xsmn_marts.py`, `src/xsmb_etl/pipeline.py`, `src/xsmb_etl/xsmn_pipeline.py`, `src/xsmb_etl/migration.py`, `src/xsmb_etl/extract.py`, `src/xsmb_etl/xsmn_extract.py`, `tests/test_gold_precomputed_loto.py` (new), `src/fetch.py` (deleted).

Status: DONE
Summary: Vectorized both loto recency loops with proven frame-identical output (22.6x at 7600-date scale), eliminated the duplicate loto computation in all gold builders via an optional precomputed-frame parameter, added structured logging across pipelines/extractors including retry visibility and exc_info warnings in all 7 silent failure-manifest blocks, and deleted dead src/fetch.py. All 212 tests, ruff check, and ruff format --check pass.
Concerns: None material. Migration.py's build_gold_tables call site was also updated to pass its precomputed loto frame (same verified redundancy pattern; the file was in this workstream's list). cli.py still recomputes the loto frame inside its gold-builder calls but was outside this workstream's file list.
