# Refactor A — Consolidate XSMB/XSMN Pipeline Orchestration

## What Changed

The duplicated orchestration in `pipeline.py` and `xsmn_pipeline.py` (86.1% byte-identical) is now a single
template-method skeleton in a new shared module, with each lake reduced to a thin specialization.

### New module

- `src/xsmb_etl/pipeline_core.py` (520 lines)
  - `BasePipeline`: owns `run()`, `backfill()`, `_ingest_backfill_date()`, `record_no_draw()`, `build_gold()`,
    plus the shared internals they were both duplicating: bronze reuse/extract branch (`_acquire_bronze`),
    pre-write quality gate (`_validate_current_draw`), NO_DRAW manifest+result handling (`_no_draw_outcome`),
    failure-manifest scaffolding (`_record_failure` / `_write_failure_manifest`), and the
    `publication_committed` re-raise guard.
  - Region-specific behavior goes through 11 hook methods: five builders
    (`_draw_results_frame`, `_loto_daily_frame`, `_gold_tables`, `_quality_report`, `_canonical_results`),
    `_boundary_date`, four message hooks (`_skipped_message`, `_published_message`, `_rebuilt_message`,
    `_empty_silver_message`), and `_publication_region_kwargs`.
  - `_safe_error_message` (previously verbatim in both files) and `backfill_failure_result` moved here;
    `_vietnam_today()` centralizes the Asia/Ho_Chi_Minh "today" computation.
  - `DrawExtractor` protocol describes the one-method extractor contract both lakes already used.

### Rewritten as specializations

- `src/xsmb_etl/pipeline.py`: `Pipeline(BasePipeline)` — XSMB frame/gold/quality builders and XSMB wording.
  Re-exports `backfill_failure_result` (tests import it from here; `__all__` keeps ruff clean).
- `src/xsmb_etl/xsmn_pipeline.py`: `SouthernPipeline(BasePipeline)` — station-grain builders, the
  XSMN/XSMT repository guard, `documented_partial_draws` class attribute, region-labeled wording.
  `pd_timestamp_date` stays public in this module (now using a module-level pandas import; pandas was already
  transitively imported at module load via `xsmn_transform`).
- `src/xsmb_etl/xsmt_pipeline.py`: untouched thin shim (`CentralPipeline(SouthernPipeline)`, 1 attribute).

### Quality consolidation

- `xsmn_quality.py` dropped its verbatim `_check` copy and imports `_check` from `quality.py`
  (which already exported `QualityCheck`/`QualityReport`/`QualitySeverity` to it). `quality.py` unchanged.
- `build_quality_report` and `build_southern_quality_report` keep their different signatures
  (`region=` / `documented_partial_draws=` only exist on the southern one); the base class calls them through
  the `_quality_report` hook rather than unifying them.

### Models consolidation

- `models.py` gained three shared helpers parameterized by spec table / group enum / prize class:
  `validate_prize_against_spec`, `validate_group_completeness`, `prizes_from_groups`.
- `Prize.validate_against_group_spec`, `LotteryResult.validate_complete_draw`, and
  `LotteryResult.from_prize_groups` now delegate to them; `xsmn_models.py` imports the same helpers for
  `SouthernPrize` / `SouthernStationResult`. Every error string is byte-identical to before; each model keeps
  its own spec table, width bound (`le=5` vs `le=6` Field), and public API. Pydantic model shapes unchanged.

## Deltas Deliberately Preserved

- **Log events byte-identical incl. logger names**: the CLI log format includes `%(name)s`, so `BasePipeline`
  logs through a `_logger` class attribute that each subclass binds to its own module logger.
  XSMB still logs as `xsmb_etl.pipeline`; XSMN and XSMT still log as `xsmb_etl.xsmn_pipeline` (verified in
  fixture runs below).
- **XSMB publication records omit `region=`**: the `run()` success manifest, both `build_gold()` manifests,
  and the `build_gold()` result historically relied on the models' XSMB default while XSMN always passes
  `region=`. Preserved via the `_publication_region_kwargs()` hook (`{}` for XSMB,
  `{'region': self.region}` for southern). This keeps the existing failure mode where a mis-paired
  non-XSMB repository would reject an XSMB-default publication manifest.
- **`.date()` vs `pd_timestamp_date`**: preserved via the `_boundary_date` hook.
- **All message strings**: XSMB messages without region label ("published dataset version …",
  "no Silver draw results are available", skip message without " in XSMB") vs region-labeled southern
  messages — all verified byte-identical (see verification).
- **XSMB Gold public contract**: no station columns — verified against the published release parquets.

## Line Counts

| File | Before | After | Delta |
|---|---|---|---|
| `src/xsmb_etl/pipeline.py` | 467 | 86 | -381 |
| `src/xsmb_etl/xsmn_pipeline.py` | 475 | 113 | -362 |
| `src/xsmb_etl/xsmt_pipeline.py` | 13 | 13 | 0 (untouched) |
| `src/xsmb_etl/pipeline_core.py` | 0 | 520 | +520 (new) |
| `src/xsmb_etl/quality.py` | 177 | 177 | 0 (untouched — already `_check`'s home) |
| `src/xsmb_etl/xsmn_quality.py` | 178 | 160 | -18 |
| `src/xsmb_etl/models.py` | 201 | 223 | +22 |
| `src/xsmb_etl/xsmn_models.py` | 171 | 139 | -32 |
| **Total** | **1682** | **1431** | **-251** |

Duplicated-logic reduction: the two pipeline modules previously carried 942 lines of ~86%-identical
orchestration written twice; orchestration now exists once (520 shared lines) with 199 lines of
region-specific hooks. The verbatim `_check` (17 lines) and the ~110 lines of copy-pasted prize
validation/parsing across the two model modules are each down to one definition.
Git diff over modified files: +202/-973, plus the 520-line new module.

## Verification (all gates run locally)

- `uv run pytest -q` — **212 passed**, zero test files changed.
- `uv run ruff check .` — clean.
- `uv run ruff format --check .` — clean (75 files).
- Offline fixture pipelines exactly as README documents, all **exit 0**:
  - `lottery-etl run --region xsmb --target-date 2026-07-16 --fixture tests/fixtures/valid-result-page.html`
    → `"message": "published dataset version …"`, logger `xsmb_etl.pipeline`, manifest region `xsmb`.
  - `lottery-etl run --region xsmn --target-date 2026-07-16 --fixture tests/fixtures/valid-xsmn-result-page.html`
    → `"message": "published XSMN dataset version …"`, logger `xsmb_etl.xsmn_pipeline`.
  - `lottery-etl run --region xsmt --target-date 2026-07-18 --fixture tests/fixtures/valid-xsmt-result-page.html`
    → `"message": "published XSMT dataset version …"`, logger `xsmb_etl.xsmn_pipeline`.
- Repeat-run skip messages verified byte-identical:
  XSMB `…classified as success; use --force…` vs XSMN `…classified as success in XSMN; use --force…`.
- Empty-Silver `build_gold()` errors verified per lake:
  `no Silver draw results are available` / `no XSMN Silver …` / `no XSMT Silver …`, and the
  `SouthernPipeline requires an XSMN or XSMT repository` guard still raises.
- XSMB release parquets inspected: `dim-date`, `dim-number`, `fact-draw-result`, `fact-loto-daily`,
  `fact-special-prize` — no station columns.
- Generated fixture output directories were removed after verification (they are gitignored).

## Status

Status: DONE
Summary: Cross-region pipeline orchestration, the quality `_check` helper, and the prize validation/parsing
copy-paste are consolidated behind a template-method base class and shared model helpers; all 212 tests pass
unchanged, ruff is clean, and the three README fixture pipelines exit 0 with byte-identical messages,
manifests, and logger names.
Concerns: `frontend/app/components/*` files appeared from a concurrent workstream during this session and
were not touched. `pd_timestamp_date` now uses a module-level pandas import instead of a function-local one
(pandas was already imported transitively at module load; no observable change).
