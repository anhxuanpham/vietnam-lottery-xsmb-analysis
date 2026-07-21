# Operations runbook

Every operation targets one independent lake with `--region xsmb`, `--region xsmn`, or `--region xsmt`. `--region all` executes all three and returns a region-tagged result for each lake.

## Daily success

1. The source page matches the requested date.
2. XSMB contains 27 values; every XSMN/XSMT station contains 18 values. XSMN and XSMT must match their exact weekday station calendars, not only the station count. XSMT Sunday uses `KH, KT` through 2021 and `KH, KT, TTH` from `2022-01-02`. Six exact source-observed partial sets are versioned for 2021: `2021-07-27 QNA`, `2021-08-03 QNA`, `2021-08-06 GL`, `2021-08-18 KH`, `2021-08-21 DNO/QNG`, and `2021-09-04 DNA/DNO`; arbitrary omissions remain a critical failure.
3. Critical quality checks pass at the correct date/station grain.
4. Bronze is written once; monthly Silver and all Gold latest objects are updated in that region's lake.
5. A run manifest and snapshot manifest are written.
6. That lake's `manifests/latest.json` is written last.

## Failure and backfill

A source page that explicitly states “không mở thưởng” writes a `no_draw` manifest. An HTTP failure, unexpected missing table, or parser/layout error writes a `failed` manifest instead. During a range backfill, neither outcome stops later dates: every date receives a result entry and processing advances. Failed dates remain eligible for a future backfill; `no_draw` dates are skipped unless `--force` is used.

XSMB, XSMN, and XSMT range backfills validate and upsert Bronze/Silver one date at a time, then rebuild Silver Loto and publish Gold once after the batch. Until that final publication succeeds, the previous `manifests/latest.json` remains the pointer, and consumers must keep verifying its object checksums because `gold/latest/*` objects are mutable during publication. An interrupted run is safe to repeat because completed Bronze objects and Silver business keys are reused. XSMB also detects a control manifest newer than the current publication, so rerunning the same range completes Gold after an interruption that already recorded `no_draw`.

Some historical primary pages omit the first digit of station special prizes. Only strictly reconcilable corruption activates the independent XSMN/XSMT fallback source. The result is accepted only when station identity and all unaffected prizes agree. Reconciled Bronze retains `response.html`, `fallback-response.html`, both source URLs and hashes, and `reconciliation=full_station_prize_comparison`. A disagreement remains `failed`; the pipeline never guesses or zero-pads the missing digit.

One XSMB historical layout defect exchanges the prize-5 and prize-6 CSS classes while retaining all 27 values. The parser swaps them only when all other groups are complete and the two affected groups exactly match the opposite count and width. The original HTML remains in Bronze; any incomplete or ambiguous variant remains `failed`.

Bronze uses an atomic conditional PUT. When an interrupted or concurrent run already created the same object, the repository verifies the stored payload and safely resumes. A different payload remains an immutable-Bronze conflict, is reported as `failed`, and does not stop later dates. Failure reporting does not make another manifest-list request, so a secondary R2 read cannot break the continuation path.

```bash
uv run lottery-etl backfill --region xsmn --from 2026-07-14 --to 2026-07-16
uv run lottery-etl backfill --region xsmt --from 2026-07-14 --to 2026-07-16
uv run lottery-etl backfill --region xsmb --from 2026-07-14 --to 2026-07-16
```

For the complete six-digit XSMN history, dispatch `xsmn-backfill.yml` with `from_year=2010`. It runs one year at a time with `max-parallel: 1` and shares the `vietnam-lottery-xsmn` concurrency group with the daily XSMN job.

For XSMT, dispatch `xsmt-backfill.yml` with `from_year=2018`. It uses the same resumable yearly pattern but owns the separate `vietnam-lottery-xsmt` group and XSMT bucket.

Historical backfill never requests the current Vietnam date. A current-year batch ends at yesterday, while `daily-etl.yml` owns today's result after the scheduled draw publication window. On January 1, the current-year historical batch exits successfully with no dates instead of requesting an incomplete draw.

For a bounded XSMB repair, dispatch `xsmb-gap-repair.yml`. It shares `vietnam-lottery-xsmb` concurrency with Daily, rejects the current/future Vietnam date, never passes `--force`, publishes once, audits the requested range, and uploads the repair/audit JSON for 30 days. The optional legacy classification is limited to four 2006–2007 dates whose absence is corroborated by two independent historical archives; it is not described as an official source notice.

If the source officially confirms there was no draw, record it explicitly in the affected lake:

```bash
uv run lottery-etl no-draw \
  --region xsmn \
  --target-date 2026-07-16 \
  --detail "Official source confirmed no draw"
```

## Source correction

Bronze is immutable by default. Inspect the stored raw response and source correction, then use `--force` only when replacement is intentional:

An interrupted XSMN Bronze write can be completed without `--force` when the stored canonical JSON exactly matches a fresh extraction. The original raw HTML remains immutable and the completed metadata records `partial_recovery=canonical_result_match`. Any canonical difference remains a hard conflict.

```bash
uv run lottery-etl run --region xsmn --target-date 2026-07-16 --force
```

The command cannot change XSMB because it opens only the XSMN repository/bucket.

## Recovery checks

- Start with the lightweight publication check. It reads only `manifests/latest.json`, its exact run/snapshot
  manifests, and HEAD metadata for the referenced Gold objects. It does not list or download Silver partitions,
  download Gold payloads, or rebuild Gold:

```bash
uv run lottery-etl status --storage r2 --region all
uv run lottery-etl status --storage r2 --region all --json
```

The command exits `0` only when every requested lake has a successful, quality-passed run manifest, a matching
snapshot manifest, and matching size/SHA-256/content metadata for every published Gold object. It exits `1` for an
unpublished lake, missing manifest/object, or metadata mismatch, so the JSON form is suitable for a GitHub Actions
health gate. `--storage local --output data/lake` checks local lakes; with `--region all`, the command resolves
`data/lake/xsmb`, `data/lake/xsmn`, and `data/lake/xsmt` independently.

- After a historical backfill, run the deeper read-only audit. Unlike `status`, this downloads the manifest-bound
  Gold Parquet objects and scans the requested history:

```bash
uv run lottery-etl audit-history --storage r2 --region all
uv run lottery-etl audit-history --storage r2 --region all --json
uv run lottery-etl audit-history --storage r2 --region xsmt --from 2021-07-01 --to 2021-09-30
```

By default the range starts at XSMB `2005-10-01`, XSMN `2010-01-01`, or XSMT `2018-01-01`, and ends at the latest
completed regional cutoff (16:35 XSMN, 17:35 XSMT, 18:35 XSMB, Vietnam time). The audit verifies exact manifest
size/SHA-256 before parsing, calendar statuses, fact and loto business keys/cardinality, number formatting, station
dimension coverage, and exact weekday station sets. It returns `0` only with no findings and `1` otherwise. Findings
are evidence to investigate; the command never writes data or reclassifies an unexplained gap as `no_draw`.

- Confirm the selected R2 bucket name before running a destructive `--force` operation.
- Inspect that region's most recent run manifest and quality report.
- Confirm `manifests/latest.json` still points to the last complete run after a failure.
- Compare object SHA-256 metadata to manifest values.
- Re-run only unresolved dates; do not infer completeness from the maximum stored date.
- Rotate the relevant bucket token immediately if credentials may have been exposed.

## Dashboard publication

`Publish Lottery Dashboard Data` runs daily at 19:47 Vietnam time, away from GitHub's top-of-hour scheduling hotspot,
after the 18:35 Daily ETL window, and can also be dispatched manually. It first runs the metadata-only three-lake
health gate and requires all three publication dates
to match the expected draw date (yesterday before 18:35, today from 18:35 onward). It then runs the complete-history
audit for all three lakes through that same date. Any checksum, calendar, fact-grain, loto-grain, station-dimension, or
station-schedule finding blocks publication. JSON reports produced by the gates are retained as a workflow artifact
for 30 days.
If a gate fails before it can emit valid JSON, the artifact retains the report from any earlier completed gate.
After both gates pass, the workflow downloads only the published
`fact-draw-result.parquet` plus `dim-station.parquet` where applicable, exports 455 recent draws per station, and
uploads compact JSON to the Sites Worker. The three source lakes stay private; the browser only reads the separate
`LOTTERY_DATA` serving bucket.

GitHub Actions configuration (one entry per line):

```text
DASHBOARD_INGEST_URL (variable) = https://<site>/api/admin/lottery
DASHBOARD_INGEST_TOKEN (secret) = same value as the Sites runtime secret
DASHBOARD_SITES_BYPASS_TOKEN (secret) = Sites owner-only bypass token
```

If the URL or ingest token is absent, the automatic workflow records a skipped publication instead of touching the
serving bucket. A configured publish must return success for all three regions; otherwise the workflow fails. The
Worker validates the region, schema, content type, token, and 8 MiB body limit before replacing
`regions/<region>.json`.

## Local verification

```bash
uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest

uv run lottery-etl validate \
  --region xsmb \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-result-page.html

uv run lottery-etl validate \
  --region xsmn \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-xsmn-result-page.html

uv run lottery-etl validate \
  --region xsmt \
  --target-date 2026-07-18 \
  --fixture tests/fixtures/valid-xsmt-result-page.html
```
