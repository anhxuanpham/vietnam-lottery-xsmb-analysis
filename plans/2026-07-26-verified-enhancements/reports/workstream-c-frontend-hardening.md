# Workstream C — Frontend hardening

## Files changed

- `frontend/app/page.tsx` — memo hardening, hoisted formatters, Model Lab / Prize Lab fallback UI, heatmap colors via helper
- `frontend/app/error.tsx` — new app-router error boundary
- `frontend/app/globals.css` — additions only (new notice classes, disabled download style, legend swatch overrides)
- `frontend/dashboard-data.ts` — named constant for the 455 station-history target
- `frontend/heat-color.ts` — new WCAG-AA heat ramp helper
- `frontend/tests/heat-color.test.mjs` — new contrast/monotonicity/clamp tests
- `frontend/tests/rendered-html.test.mjs` — NOT changed (all its page-source assertions still match; verified by running it)

## 1 — Render-phase throws no longer blank the page

### (a) Insufficient history for backtest

In the `analysis` useMemo, `backtest` throws when `draws.length <= window`. The memo now computes
`benchmarkAvailable = draws.length > activeWindow` (comment in code states the invariant: backtest
needs a full training window plus at least one evaluation draw) and skips the three model
benchmarks entirely when false: `models: []`, `evaluationCount: 0` (via the existing
`models[0]?.benchmark.evaluationCount ?? 0`), plus `requiredDraws: activeWindow + 1` and
`availableDraws: draws.length` on the memo result. Counts, heatmap, hot/cold, momentum, and
prizeLab still compute from the available draws.

Model Lab renders a Vietnamese notice instead of the model-card grid when
`benchmarkAvailable` is false, citing both counts ("cần ít nhất {requiredDraws} kỳ nhưng đài này
mới có {availableDraws} kỳ") and pointing at the smaller-window escape hatch. The
"Tải benchmark JSON" button gets `disabled={!analysis.benchmarkAvailable}` plus an early return
inside `downloadBenchmarkReport` as a second guard.

Consumers of `analysis.models` / `analysis.evaluationCount` audited (grep): the metrics tile
(shows `0 kỳ`, already null-safe), the model grid (now behind the `benchmarkAvailable` ternary),
and `downloadBenchmarkReport` (guarded). No other consumers exist.

### (b) PrizeAnalyticsError on contract drift

`analyzePrizeWindow(analysisDraws)` is wrapped in try/catch inside the memo. `PrizeAnalyticsError`
sets `prizeLab = null`; any other error is rethrown (and now lands in the new error boundary
instead of a white screen). When `prizeLab` is null the Prize Lab section keeps its heading and
renders a short Vietnamese data-quality notice (`.prize-lab-empty`) instead of the KPI/anatomy
panels; the heading copy and "Tải Prize Lab JSON" button are hidden (the handler also
early-returns). The rest of the page stays mounted.

Null-safety fixes in consumers:
- `topSpecialTails`, `topSpecialDigitSums`, `specialPositionLeaders` are guarded by a
  `const prizeLab = analysis.prizeLab` local (`[]` when null) that also gives TS narrowing for
  the whole section.
- `openExplorerEvidence` used `analysis.prizeLab.dateRange` for its query range; it now falls
  back to `analysis.analysisDraws[0/last].date`. These values are identical when prizeLab exists
  (analyzePrizeWindow's range is the first/last date of the same ascending-sorted window), so
  behavior is unchanged in the normal path.

### (c) Last-resort error boundary

New `frontend/app/error.tsx`: `"use client"` component reusing the existing
`loading-shell error-shell` / `loading-mark` classes, Vietnamese copy, and a "Thử lại" button
calling `reset()`.

## 2 — Intl formatters hoisted to module scope

`dateFormatter`, `timestampFormatter`, and `runTimeFormatter` are now module constants next to the
existing `numberFormatter`/`percentFormatter`. `formatDate` and `formatTimestamp` keep their exact
signatures; `runModels` uses `runTimeFormatter`. No per-render `Intl.DateTimeFormat` allocations
remain (`grep 'new Intl'` in page.tsx shows only module scope).

## 3 — Heatmap WCAG AA contrast

New `frontend/heat-color.ts` exports `heatCellColors(intensity)`, used by the heatmap cell
`style`. Ramp design was **computed** (scratch script at
`.../scratchpad/heat-ramp-design.mjs`), not guessed:

- `i ∈ [0, 0.55]`: unchanged current look — `rgba(224, 58, 36, 0.12 + i*0.88)` with `#171714`
  text. Worst case (alpha 0.604 composited over `#fffdf7`) = **7.12:1**.
- `i ∈ (0.55, 1]`: light text `#fffdf7` over a solid ramp from `#c83420` (**5.20:1**) to
  `#8f2013` (**8.66:1**). Both channelwise-decreasing, so effective luminance is monotonically
  non-increasing across the whole ramp (the step down at 0.55 reads hotter, never lighter).
- Out-of-range/NaN intensities clamp to [0, 1].

Note on the task brief: my computation gives white `#fffdf7` on pure `#a8291b` = **6.89:1**
(passes); the "~4.36:1 — NOT enough" figure in the brief matches `#e03a24` (4.30:1), not
`#a8291b`. I still darkened the high end toward/below `#a8291b` as instructed — the hottest cell
is `#8f2013` — but the lightest white-text anchor `#c83420` is chosen from computation with a
0.7 margin above 4.5, keeping the fixed cells visually closer to today's red. Verification
source: the WCAG relative-luminance implementation in `tests/heat-color.test.mjs` and the
scratch script.

`tests/heat-color.test.mjs` implements the WCAG formula (0.03928/12.92/2.4 constants),
composites rgba over `#fffdf7`, and asserts:
- contrast ≥ 4.5 for i = 0 → 1 in 0.01 steps (101 points),
- monotonically non-increasing effective background luminance over the same sweep,
- light-end look preserved exactly at i=0 (`rgba(224, 58, 36, 0.12)`, `#171714`), text switch
  after 0.55, and clamping of −1 / 2 / NaN.

Also appended two `.heat-legend` swatch overrides in globals.css so the legend's hot end matches
the new ramp (`#c83420`, `#8f2013`).

## 4 — Named constant for 455

`frontend/dashboard-data.ts`: `const STATION_HISTORY_TARGET_DRAWS = 455` with a one-line comment
("365-draw max analysis window plus the 90-draw evaluation limit; must match
DEFAULT_RECENT_DRAWS_PER_STATION in scripts/export_serving_data.py" — confirmed that constant is
455 at scripts/export_serving_data.py:37). Used at both sites (the pagination loop condition and
the slice).

## globals.css additions (append-only)

`.model-empty-notice`, `.benchmark-download:disabled` (+ `:hover` neutralizer),
`.prize-lab-empty`, and the two `.heat-legend` swatch overrides. No existing line was modified.

## Verification

From `frontend/`:

- `npx tsc --noEmit` — clean, no output.
- `npm run lint` — clean, no findings.
- `node --test tests/*.test.mjs` — **75 pass, 0 fail** (includes the 3 new heat-color tests and
  the untouched rendered-html suite, whose page-source assertions all still match).
- `npm run build` intentionally not run per instructions (verify phase owns it). Note
  `tests/rendered-html.test.mjs` SSR assertions ran against the pre-existing `dist/` build.

## Constraints compliance

- No git checkout/restore/stash/reset/commit was run; other workstreams' uncommitted changes
  (worker/, src/, docs) were left untouched.
- Frontend style: double quotes, no AI/plan references in comments, comments only for the two
  non-obvious invariants (backtest minimum history; 455 provenance; AA ramp contract).
- No predictive/betting framing added; all new UI copy is Vietnamese and descriptive
  ("Chưa đủ lịch sử để backtest…", "Prize Lab tạm ẩn…").

Status: DONE
Summary: All four verified fixes implemented — benchmark-skip + Prize Lab degradation + error boundary remove the white-screen paths, Intl formatters are hoisted, the heatmap now passes WCAG AA at every intensity with a computed and test-enforced ramp, and the 455 magic number is a documented named constant. tsc, eslint, and all 75 node tests pass.
Concerns: The task brief's "#a8291b vs #fffdf7 ≈ 4.36:1" premise is incorrect per computation (6.89:1; 4.30:1 belongs to #e03a24) — the implemented ramp still ends below #a8291b and every point is ≥ 5.2:1, so the intent is met either way. The "Backtest gần nhất" metric tile shows "0 kỳ" when benchmarks are skipped (per spec evaluationCount: 0); if a dash is preferred there, it is a one-line change.
