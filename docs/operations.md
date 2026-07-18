# Operations runbook

Every operation targets one independent lake with `--region xsmb` or `--region xsmn`. `--region all` executes both and returns a region-tagged result for each lake.

## Daily success

1. The source page matches the requested date.
2. XSMB contains 27 values, or each of the three/four XSMN stations contains 18 values.
3. Critical quality checks pass at the correct date/station grain.
4. Bronze is written once; monthly Silver and all Gold latest objects are updated in that region's lake.
5. A run manifest and snapshot manifest are written.
6. That lake's `manifests/latest.json` is written last.

## Failure and backfill

A source page that explicitly states “không mở thưởng” writes a `no_draw` manifest. An HTTP failure, unexpected missing table, or parser/layout error writes a `failed` manifest instead. During a range backfill, neither outcome stops later dates: every date receives a result entry and processing advances. Failed dates remain eligible for a future backfill; `no_draw` dates are skipped unless `--force` is used.

XSMN range backfill validates and upserts Bronze/Silver one date at a time, then rebuilds Silver Loto and publishes Gold once after the batch. Until that final publication succeeds, the previous `manifests/latest.json` remains the consumer boundary. An interrupted run is safe to repeat because completed Bronze objects and Silver business keys are reused.

Some historical primary pages omit the first digit of all station special prizes. Only this exact five-digit-width failure activates the independent XSMN fallback source. The result is accepted only when station codes, all 17 non-special prizes, and the five-digit special suffix agree. Reconciled Bronze retains `response.html`, `fallback-response.html`, both source URLs and hashes, and `reconciliation=full_station_prize_comparison`. A disagreement remains `failed`; the pipeline never guesses or zero-pads the missing digit.

Bronze uses an atomic conditional PUT. When an interrupted or concurrent run already created the same object, the repository verifies the stored payload and safely resumes. A different payload remains an immutable-Bronze conflict, is reported as `failed`, and does not stop later dates. Failure reporting does not make another manifest-list request, so a secondary R2 read cannot break the continuation path.

```bash
uv run lottery-etl backfill --region xsmn --from 2026-07-14 --to 2026-07-16
uv run lottery-etl backfill --region xsmb --from 2026-07-14 --to 2026-07-16
```

For the complete six-digit XSMN history, dispatch `xsmn-backfill.yml` with `from_year=2010`. It runs one year at a time with `max-parallel: 1` and shares the `vietnam-lottery-xsmn` concurrency group with the daily XSMN job.

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

- Confirm the selected R2 bucket name before running a destructive `--force` operation.
- Inspect that region's most recent run manifest and quality report.
- Confirm `manifests/latest.json` still points to the last complete run after a failure.
- Compare object SHA-256 metadata to manifest values.
- Re-run only unresolved dates; do not infer completeness from the maximum stored date.
- Rotate the relevant bucket token immediately if credentials may have been exposed.

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
```
