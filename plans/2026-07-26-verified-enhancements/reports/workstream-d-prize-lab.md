# Workstream D — Prize Lab 6-digit exploitation

Deeper descriptive exploitation of the full special-prize number (6 digits XSMN/XSMT, 5 digits XSMB) in
`frontend/prize-analytics.ts` and the Prize Lab UI. All computation stays pure, deterministic, and
input-order-independent; no predictive or betting framing anywhere.

## Analytics (`frontend/prize-analytics.ts`)

- `PRIZE_ANALYTICS_VERSION` bumped `prize-descriptive-v1` → `prize-descriptive-v2`. A repo-wide grep confirmed the
  literal `prize-descriptive-v1` appeared only in `prize-analytics.ts` itself; every other consumer (page.tsx, tests)
  references the exported constant, so no other pin needed updating.
- New `digitPresence: DigitFrequency[]` on `PrizeNumberMetrics` (every group): for each digit 0-9 in fixed order, the
  count/rate of observations containing that digit at least once anywhere in the formatted value ("chạm"). Implemented
  with `new Set(formattedNumber)` per value so a digit occurring multiple times in one value counts once per
  observation. Rates are `count / observations`; the array always has ten entries.
- New `head3Frequency: Head3Frequency[]` on `PrizeNumberMetrics`: mirror of `tail3Frequency` over the FIRST three
  digits, computed only when `officialWidth >= 4` (empty array otherwise, so a width-3 group never duplicates its
  tail3 view). Same sorted-by-key (`localeCompare`) ordering as `tail3Frequency`. New exported type `Head3Frequency`.
- New `tail3Recency: Tail3Recency[]` on `SpecialPrizeAnatomy` only (the special group has exactly one value per draw,
  so per-draw dates are well defined). Each entry: `{ tail3, count, lastSeenDate, drawsSinceLastSeen }` where
  `drawsSinceLastSeen` counts draws after the last occurrence in the prepared ascending window (0 = latest draw).
  Ordering is count desc, then tail3 asc. Empty when `officialWidth < 3`. Computed by `specialTail3Recency()` from
  `prepared.draws`, which `preparePrizeWindow` already sorts ascending and de-duplicates, so the metric is independent
  of input order. New exported type `Tail3Recency`.
- No `Date.now`/`Math.random`; all new metrics derive solely from the validated window.

## UI (`frontend/app/page.tsx`)

Built on top of workstream C's current state (re-read before editing; `prizeLab` may be null and all additions live
inside the existing `prizeLab === null ? … : (<>…</>)` conditional — the null notice branch is untouched).

- "Chạm 0–9 trong giải đặc biệt" strip in the Special Prize Anatomy panel: ten non-interactive bars (digit, red bar on
  paper-deep track, `count kỳ · percent` label) styled like the existing rank/momentum bars. A digit is not an
  explorer-searchable value, so the rows are plain divs with a descriptive `title`, not buttons.
- "Đầu 3 số lặp lại" column added alongside "Đuôi 3 số lặp lại" in `prize-pattern-columns` (the third column, "Tổng
  chữ số phổ biến", wraps to the second grid row — no container CSS change needed). Same slicing style as the tails
  column: `count > 1`, count desc then key asc, top 8. Entries are NON-interactive `pattern-list` divs:
  `frontend/lottery-contract.ts` defines `LOTTERY_PRIZE_MATCHES = ["exact", "suffix"]` — no prefix match kind exists,
  so per the brief no click-through was wired and neither the contract nor the worker was touched.
- Recency on the existing top tail3 buttons: the stats span now appends `· vừa về kỳ mới nhất` (drawsSinceLastSeen 0)
  or `· về cách đây N kỳ`, read from a `Map` built from `tail3Recency` (empty map when `prizeLab` is null; the append
  is guarded for a missing key even though tail3Frequency/tail3Recency are built from the same values).
- New derived consts `topSpecialHeads` and `specialTailRecency` follow the existing `prizeLab === null ? [] : …`
  pattern next to `topSpecialTails`.

## CSS (`frontend/app/globals.css`, append-only)

`.digit-presence`, `.digit-presence-grid` (2 columns, 1 column under 720px via an appended media query),
`.digit-presence-row` (16px digit / bar / 104px label grid, 4px bar, `--paper-deep` track, `--red` fill, mono fonts)
— matching the `.rank-row` conventions. Workstream C's appended rules were preserved byte-for-byte.

## Tests (`frontend/tests/prize-analytics.test.mjs`)

Five new tests (suite went 5 → 10 tests in this file, 75 → 80 overall):

- version pin: `PRIZE_ANALYTICS_VERSION === "prize-descriptive-v2"` (existing tests assert payloads carry the
  exported constant, so they follow automatically).
- digitPresence: full 10-entry deepEqual on a 3-draw 6-digit window, explicitly covering a digit occurring twice in
  one value ("1" twice in 005113, three times in 123114) counted once per observation; plus reversed-input equality.
- head3: 6-digit window deepEqual, 5-digit XSMB special and prize1 deepEqual, width-3 (prize6) empty while its
  tail3Frequency is non-empty, width-2 (prize7) empty; count-sum invariant equals observations.
- tail3Recency: ordering count desc/tail asc, `drawsSinceLastSeen === 0` for a latest-draw occurrence, never-repeated
  tail present with count 1 and correct gap, a tie-on-count case, and reversed-input equality.
- width guard: a synthetic 2-digit special window yields empty `tail3Recency` and `tail3Frequency`.

The existing determinism test (`deepEqual(anatomy, analyzeSpecialPrizeAnatomy(chronological))`) and the purity test
(`structuredClone` input comparison) now cover the new fields automatically since they are part of the same objects.

## Docs (`frontend/README.md`)

One short Vietnamese paragraph after the feature bullet list documenting chạm 0–9, đầu 3 số (width ≥ 4 rule), and
special-prize tail3 recency, closing with the descriptive-only framing.

## Verification (all from `frontend/`)

- `npx tsc --noEmit` — exit 0, no output.
- `npm run lint` — exit 0, no findings.
- `node --test tests/*.test.mjs` — 80 pass, 0 fail (run twice: before and after the rebuild).
- `npm run build` — exit 0; dist/ regenerated, then the full test suite re-run against the fresh dist (80 pass,
  including the rendered-html SSR assertions).

## Files changed

- `frontend/prize-analytics.ts`
- `frontend/app/page.tsx`
- `frontend/app/globals.css`
- `frontend/tests/prize-analytics.test.mjs`
- `frontend/README.md`

Status: DONE
Summary: Prize Lab v2 adds digit presence (chạm 0–9), head3 frequency (width ≥ 4), and special-prize tail3 recency —
computed purely in prize-analytics.ts, surfaced in Vietnamese descriptive UI inside the existing null-guarded panel,
fully tested, with typecheck/lint/tests/build all green.
Concerns: None blocking. Head3 entries are intentionally non-interactive because the contract has no prefix match
kind; if one is ever added end-to-end, `openExplorerEvidence(head3, "prefix", "special")` is the single wiring point.
