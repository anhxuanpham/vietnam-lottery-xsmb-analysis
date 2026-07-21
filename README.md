# Vietnam Lottery XSMB/XSMN/XSMT Data Lakes

An educational ETL and analytics project for Northern (`XSMB`), Southern (`XSMN`), and Central (`XSMT`) Vietnam lottery results. It extracts a selected date, preserves source lineage, validates official number widths, builds BI-friendly datasets, and publishes independently versioned objects to local storage or Cloudflare R2.

> This project performs descriptive analysis only. Historical lottery results do not reliably predict future draws and must not be presented as a betting system.

The original XSMB project was created by [Khiem Doan](https://github.com/khiemdoan) and remains available under the MIT license.

Hướng dẫn triển khai đầy đủ bằng tiếng Việt: [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md).

Dashboard chạy local: [frontend/README.md](frontend/README.md).

## Independent data lakes

XSMB, XSMN, and XSMT never share object-store state:

| Region | Default local root | R2 bucket setting | Fact grain |
|---|---|---|---|
| XSMB | `output/` | `R2_BUCKET_NAME` | date + prize group + prize order |
| XSMN | `output-xsmn/` | `R2_XSMN_BUCKET_NAME` | date + station + prize group + prize order |
| XSMT | `output-xsmt/` | `R2_XSMT_BUCKET_NAME` | date + station + prize group + prize order |

Each root or bucket owns its own `bronze/`, `silver/`, `gold/`, `quality/`, and `manifests/` tree. A failure, backfill, or publication in one region therefore cannot update the other region's `manifests/latest.json`.

```text
XSMB source -> XSMB bucket -> Bronze -> Silver -> Gold -> XSMB consumers
XSMN source -> XSMN bucket -> Bronze -> Silver -> Gold -> XSMN consumers
XSMT source -> XSMT bucket -> Bronze -> Silver -> Gold -> XSMT consumers
```

`manifests/latest.json` is the publication boundary inside each lake. It is updated only after the complete Gold dataset and snapshot manifest have been written.

## Local setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

No Cloudflare credentials are required for local mode. Fixture commands default to local storage unless `--storage r2` is explicit. Run deterministic offline fixtures:

```bash
# XSMB; --region xsmb is the backward-compatible default
uv run lottery-etl run \
  --region xsmb \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-result-page.html

# XSMN; extracts all station columns represented by the page
uv run lottery-etl run \
  --region xsmn \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-xsmn-result-page.html

# XSMT; uses the same station grain and its exact versioned station calendar
uv run lottery-etl run \
  --region xsmt \
  --target-date 2026-07-18 \
  --fixture tests/fixtures/valid-xsmt-result-page.html
```

The legacy `xsmb-etl` executable remains an alias of `lottery-etl`.

## Commands

Use `--region xsmb`, `--region xsmn`, `--region xsmt`, or `--region all`. Omitting it preserves the original XSMB behavior.

```bash
uv run lottery-etl run --region xsmn --target-date 2026-07-16
uv run lottery-etl run --region all --target-date 2026-07-16
uv run lottery-etl backfill --region xsmn --from 2026-07-01 --to 2026-07-16
uv run lottery-etl build-gold --region xsmn
uv run lottery-etl validate --region xsmn
uv run lottery-etl audit-history --storage r2 --region all
uv run lottery-etl download-gold --region all --download-output downloads/
uv run lottery-etl no-draw --region xsmn --target-date 2026-07-16 --detail "Officially confirmed no draw"
```

When `--region all` and `--output custom-root/` are combined, local objects are written to `custom-root/xsmb/`, `custom-root/xsmn/`, and `custom-root/xsmt/`. Successful dates are skipped unless `--force` is supplied. During backfill, an explicit source notice containing “không mở thưởng” is recorded as `no_draw`; unexpected network/parser failures are recorded as `failed`. Both outcomes are returned in the result list and processing continues with the next date. A missing table by itself is never sufficient to claim `no_draw`.

## Region-specific validation

- XSMB requires 27 prize results and 100 loto rows per date; loto frequency sums to 27.
- XSMN must match the exact weekday station schedule (three stations Monday–Friday and Sunday, four on Saturday). Each station requires 18 prize results, including prize 8, and 100 station-level loto rows whose frequency sums to 18.
- XSMT must match its exact weekday station schedule, the Sunday schedule change effective `2022-01-02`, or one of six versioned source-observed partial sets in July–September 2021. Each station uses the same 18-result schema.
- Leading zeros are retained in `formatted_number` for every official width, including six-digit regional special prizes.
- XSMN/XSMT station identity is derived from the station link and stored as `station_code`, `station_name`, `station_url`, and `station_order`.

## Historical XSMB migration

The repository's legacy JSON is XSMB-only and remains backward compatible:

```bash
uv run lottery-etl migrate-legacy --input data/xsmb.json
```

Historical rows use `legacy_repository_dataset` lineage. XSMN history is acquired with `backfill --region xsmn`; it is never written into the XSMB lake.

## Cloudflare R2

Create three buckets, for example:

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket create xsmt-data-lake
npx wrangler r2 bucket list
```

Required shared S3 settings are `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY`. Set all three bucket names:

- `R2_BUCKET_NAME=xsmb-data-lake`
- `R2_XSMN_BUCKET_NAME=xsmn-data-lake`
- `R2_XSMT_BUCKET_NAME=xsmt-data-lake`

A single bucket-scoped token may be authorized for all three buckets. For stricter isolation, optional `R2_XSMN_*` and `R2_XSMT_*` settings override the shared connection only for that region.

`XSMN_FALLBACK_BASE_URL` and `XSMT_FALLBACK_BASE_URL` are optional and default to `https://xskt.com.vn`. They are contacted only for strictly recognizable historical corruption: a five-digit special prize, explicit `...` placeholders, or an exact prize-8/prize-7 transposition. The extractor repairs only the affected values after the station set and every unaffected prize match the independent source; arbitrary mismatches still fail. Both raw responses and hashes are retained in Bronze.

The XSMB parser also recognizes one narrow historical source defect: the complete prize-5 and prize-6 values are present but their CSS classes are exchanged. It repairs the groups only when every other prize group is complete and both exchanged groups match the opposite group's exact count and digit widths; near-matches still fail.

Keep Bronze and Silver private; expose only curated `gold/latest/*.csv` and Parquet objects through controlled custom-domain routes. See [R2 setup](docs/r2-setup.md) for the activation checklist and credential troubleshooting.

## Analytics and BI

- [Data dictionary](docs/data-dictionary.md)
- [Power BI](docs/power-bi.md)
- [Tableau](docs/tableau.md)
- [Operations runbook](docs/operations.md)
- [DuckDB analysis examples](sql/analysis-examples.sql)
- [SQL quality checks](sql/data-quality-checks.sql)
- [XSMN SQL quality checks](sql/xsmn-data-quality-checks.sql)
- [XSMT SQL quality checks](sql/xsmt-data-quality-checks.sql)
- [Python public-Gold example](examples/read_gold.py)

XSMN and XSMT Gold add `dim-station` and station fields to all facts. Gold CSV files are intended for Power BI and Tableau; Parquet files support Python and DuckDB. Credentials and presigned URLs are never committed.

Audit the complete published history after a backfill with:

```bash
uv run lottery-etl audit-history --storage r2 --region all
uv run lottery-etl audit-history --storage r2 --region all --json
```

The audit starts at the first supported date for each lake (XSMB `2005-10-01`, XSMN `2010-01-01`, XSMT `2018-01-01`) and ends at the latest draw whose regional cutoff has passed. It verifies manifest-bound Gold checksums, calendar coverage, fact/loto grains, and the exact XSMN/XSMT station schedule. Exit code `0` means all requested lakes are clean; exit code `1` means the structured report contains findings. Use `--from` and `--to` to inspect a smaller inclusive range.

## Automation

- `ci.yml` runs Ruff, pytest, all three offline fixture pipelines, and frontend lint/type/build/API tests without production secrets.
- `daily-etl.yml` queues at 18:17 Vietnam time, waits until the safe 18:35 draw cutoff, then runs all three independent R2 lakes. Manual region, target-date, and force inputs remain available without the scheduled wait.
- `dashboard-publish.yml` runs at 19:47 Vietnam time, away from GitHub's top-of-hour scheduling hotspot. It validates healthy manifests against the 18:35 draw cutoff, exports compact station-grain JSON, and updates the private Sites serving bucket.
- `xsmn-backfill.yml` runs resumable yearly XSMN batches from 2010 onward, one year at a time, and publishes Gold once per batch. The current-year batch stops at yesterday because the daily ETL owns today's draw after publication time.
- `xsmt-backfill.yml` does the same for XSMT from 2018 onward using its own concurrency group and bucket.
- The daily workflow has read-only repository permissions and writes generated data to R2 rather than committing it to Git.

## Legacy analysis

The original wide local XSMB dataset and plotting script remain available for compatibility. Running `uv run src/analyze.py` writes its report to `output/legacy-analysis.md` and charts to `images/generated/`; it does not rewrite this README or tracked images.
