# Deferred refactors — 2026-07-26

Status: complete — both refactors implemented and adversarially reviewed with zero behavior drift (212 Python tests + 82 frontend tests pass unchanged, ruff/eslint/tsc/build clean, all three offline fixture pipelines exit 0, SSR markup byte-compatible). Pipeline orchestration now lives once in `pipeline_core.py` (pipeline.py 467→86, xsmn_pipeline.py 475→113 lines). `page.tsx` 1,429→1,256 with six components extracted; LatestResultCard/ResultExplorer/ModelLab/DataHealth stay in-file because `rendered-html.test.mjs` asserts against page.tsx source text — retargeting those assertions is a deliberate future decision, not part of this change.

Follow-up to `plans/2026-07-26-verified-enhancements/` (merged as PR #14). Baseline: clean `main` at merge commit f225e70a, 212 Python tests + 82 frontend tests green.

## Phases

1. **R-A Consolidate XSMB/XSMN pipeline orchestration** — measured 86.1% byte-identical lines between `src/xsmb_etl/pipeline.py` and `src/xsmb_etl/xsmn_pipeline.py` (93.4% after name normalization); `_check` duplicated verbatim between `quality.py` and `xsmn_quality.py`; `Prize` validation ~80% copied between `models.py` and `xsmn_models.py`. Extract a shared orchestration skeleton (template-method), keeping the public Gold schema of each lake byte-identical and region-specific quality logic local. The existing XSMT-on-XSMN shim hierarchy proves the pattern.
2. **R-B Split `frontend/app/page.tsx`** (~1,450 lines post Prize Lab v2) into presentational components along the existing section boundaries, with `React.memo` + stable callbacks so explorer keystrokes stop re-rendering the heatmap/Prize Lab. Rendered markup must stay identical (`rendered-html.test.mjs` is the guard).

## Acceptance criteria

- All existing tests pass **unchanged** (they are the behavior contract): `uv run pytest`, ruff check/format, `tsc --noEmit`, eslint, `node --test`, `npm run build`.
- No Gold schema change, no HTML structure/class change, no new dependencies.
- Material size reduction: shared pipeline logic exists in exactly one place; `page.tsx` becomes an orchestrating shell.

Reports: `reports/`
