# R-B — Split `frontend/app/page.tsx` into components

Baseline: `main` at merge commit f225e70a. Scope: `frontend/app/page.tsx` + new files under
`frontend/app/components/`. No worker/, contract, analytics, prize-analytics, dashboard-data,
or explorer-state module was touched; `frontend/tests/rendered-html.test.mjs` is unmodified.

## Blocking constraint discovered

`tests/rendered-html.test.mjs` ("ships all three serving-schema demo datasets…") reads
`app/page.tsx` **source text** and asserts ~25 regexes against it, including exact code
expressions, not just rendered output:

- `orderedPrizeEntries\(latestDraw\.prizes\)`
- `aria-pressed=\{latestResultView === "full"\}`
- `openExplorerEvidence\(number\)`
- `model\.benchmark\.fingerprint`
- section copy: `MODEL LAB`, `RESULT EXPLORER`, `Lô tô 2 số`, `Kết quả đầy đủ`, `Khớp đuôi`,
  `Số đầy đủ`, `Nhóm giải`, `Tra kết quả`, `Tải thêm kết quả`, `Không tìm thấy kỳ quay phù hợp`,
  `95% CI`, `Tải benchmark JSON`, `12 lựa chọn model/cửa sổ`, `Watchdog gần nhất`,
  `không phải dự báo xác suất trúng`, `ANALYTICS_MODEL_VERSION`, `LOTTERY_REGIONS`

Those expressions live inside LatestResultCard, ResultExplorer, ModelLab, and DataHealth. Since
the test is the unmodifiable contract, those four components **cannot move to separate files**
without contorting the code (e.g. render-prop indirection) purely to smuggle literal strings back
into page.tsx. Resolution: they are extracted as typed, props-driven components **within
page.tsx** (three of them memoized; ResultExplorer intentionally not), and every component whose
text is not pinned to page.tsx source moved to `frontend/app/components/`.

Consequence: the "well under 500 lines" target for page.tsx is not reachable under the
unchanged-test constraint. The four pinned components account for ~497 lines of page.tsx.

## File inventory

| File | Lines | Contents |
| --- | ---: | --- |
| `frontend/app/page.tsx` | 1,256 | `Home` (all state/effects/callbacks), `DashboardLoading`, deep-link helpers, and the four test-pinned components: `LatestResultCard` (memo), `ResultExplorer`, `ModelLab` (memo), `DataHealth` (memo) |
| `frontend/app/components/format.ts` | 74 | `formatDate`, `formatTimestamp`, `numberFormatter`, `percentFormatter`, `PRIZE_NAMES`, `downloadJson`, `orderedPrizeEntries` |
| `frontend/app/components/explorer-result-list.tsx` | 83 | `ExplorerResultList` (memo) + `prizeMatchesExplorerQuery` |
| `frontend/app/components/loto-heatmap.tsx` | 46 | `LotoHeatmap` (memo), 100-cell heatmap |
| `frontend/app/components/metrics-bar.tsx` | 31 | `MetricsBar` (memo) |
| `frontend/app/components/prize-lab.tsx` | 274 | `PrizeLab` (memo); derived slices moved inside |
| `frontend/app/components/signal-stack.tsx` | 40 | `SignalStack` (memo): hot/cold + momentum |

## Line counts

- Before: `page.tsx` 1,429 lines — single default-export client component, ~20 `useState`,
  one JSX tree, all formatters/helpers module-scope in the same file.
- After: `page.tsx` 1,256 lines + 548 lines across six component files (1,804 total).
  Within page.tsx: imports/types/helpers ~145, pinned components ~497 (LatestResultCard 75,
  ResultExplorer 165, ModelLab 160, DataHealth 97), `Home` ~609.

## What changed inside page.tsx

- All state, refs, and effects stay in `Home`; no context/reducers/new dependencies.
- `runExplorer(append, overrideQuery)` split into `executeExplorerQuery(query, append, cursor)`
  (deps: `data`, `fallbackData`, `selectedStation`, `servingMode`) and a thin `runExplorer(append)`
  that builds the query from input state. Control flow, guards, abort handling, and v1
  compatibility path are line-for-line equivalent; `openExplorerEvidence` now calls
  `executeExplorerQuery(query)` (was `runExplorer(false, query)`).
- Render-body arrow handlers became `useCallback`s hoisted above the early returns, with
  equivalent null-guards (`!data || !analysis`) replacing the guarantees the early returns used
  to provide: `openExplorerEvidence`, `chooseRegion`, `chooseStation`, `runModels`,
  `downloadBenchmarkReport`, `downloadPrizeLabReport`, `resetExplorer`.
- PrizeLab derivations (`topSpecialTails`, `topSpecialHeads`, `specialTailRecency`,
  `topSpecialDigitSums`, `specialPositionLeaders`) moved from Home's render body into a
  `useMemo` keyed on `[prizeLab]` inside the `PrizeLab` component, so they recompute only when
  the prize-window analysis changes. The digit-presence, head3, and recency blocks render there.
- DataHealth derivations (`regionalHealth`, `unhealthyRegions`, watchdog label/dot,
  `lineageHealthy`) moved inside `DataHealth`, fed by primitive props.

## DOM-identity verification

Compared each extracted JSX block against the original at f225e70a: same elements, class names,
aria attributes, keys, inline styles, and text; only identifier substitutions (prop names for the
identical expressions, e.g. `analysis.counts` → `counts`, `data.stations` → `stations`,
`setLatestResultView` → `onViewChange` where `onViewChange={setLatestResultView}`). The
PrizeLab section wrapper and heading remain in page.tsx; the component returns exactly the
original null-branch paragraph or the original fragment. No globals.css class was renamed.

## Re-render argument (keystroke in an explorer input)

A keystroke fires `resetExplorer()` + one input setter (e.g. `setExplorerValue`).

- `resetExplorer` writes `""` / `INITIAL_EXPLORER_STATE` (module constant); React bails out when
  the state is already at those values, otherwise clearing stale results is pre-existing behavior.
- `analysis` is a `useMemo` on `[activeWindow, data, draws, region, selectedStation]` — none of
  these change on a keystroke, so every data slice handed to memoized siblings keeps its identity.
- Every callback passed to a memoized component has a dependency array free of explorer input
  state: `openExplorerEvidence` `[analysis, executeExplorerQuery, region, resetExplorer,
  selectedStation]`; `executeExplorerQuery` `[data, fallbackData, selectedStation, servingMode]`;
  `chooseRegion` `[region, resetExplorer]`; `chooseStation` `[resetExplorer]`; `runModels`
  `[selectedWindow]`; `downloadBenchmarkReport`/`downloadPrizeLabReport`
  `[activeWindow, analysis, data, region]`; `setLatestResultView`/`setSelectedWindow` are stable
  setters. All identities survive the keystroke.
- The only callback that changes identity per keystroke is `runExplorer` (it closes over the
  input fields). It is passed exclusively to the un-memoized `ResultExplorer`, which must
  re-render anyway because its `explorerValue` prop changed. This is precisely why
  `executeExplorerQuery` was split out — `openExplorerEvidence` no longer inherits input deps.
- Net: a keystroke re-renders `Home` + `ResultExplorer` only. `LatestResultCard`, `MetricsBar`,
  `ModelLab`, `LotoHeatmap` (100 cells), `SignalStack`, `PrizeLab`, and `DataHealth` are all
  `React.memo` with unchanged prop identities and skip. `ExplorerResultList` (memo) re-renders
  only when `explorerState` identity actually changes (first keystroke after a completed search,
  which clears results — original behavior).

## Gates (all run from `frontend/`)

| Gate | Result |
| --- | --- |
| `npx tsc --noEmit` | pass |
| `npm run lint` (eslint, incl. react-hooks) | pass |
| `node --test tests/*.test.mjs` | 82/82 pass |
| `npm run build` | pass |
| `node --test tests/rendered-html.test.mjs` (fresh dist) | 5/5 pass |

Zero test files modified (`git status`: only `frontend/app/page.tsx` changed plus new
`frontend/app/components/`).

Status: DONE_WITH_CONCERNS
Summary: page.tsx reduced 1,429 → 1,256 lines with six new component files; heavy sections are
memoized with stable callbacks so explorer keystrokes re-render only Home + ResultExplorer; all
gates green with tests unchanged.
Concerns: `rendered-html.test.mjs` asserts page.tsx source regexes that pin LatestResultCard,
ResultExplorer, ModelLab, and DataHealth to page.tsx, so the "well under 500 lines" target is
unreachable without editing that test; if the source-text assertions are ever relaxed to target
the components, those four blocks can move under `components/` mechanically.
