# Vietnam Lottery XSMB/XSMN Data Lakes

An educational ETL and analytics project for Northern (`XSMB`) and Southern (`XSMN`) Vietnam lottery results. It extracts a selected date, preserves source lineage, validates official number widths, builds BI-friendly datasets, and publishes independently versioned objects to local storage or Cloudflare R2.

> This project performs descriptive analysis only. Historical lottery results do not reliably predict future draws and must not be presented as a betting system.

The original XSMB project was created by [Khiem Doan](https://github.com/khiemdoan) and remains available under the MIT license.

Hướng dẫn triển khai đầy đủ bằng tiếng Việt: [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md).

Dashboard chạy local: [frontend/README.md](frontend/README.md).

## Independent data lakes

XSMB and XSMN never share object-store state:

| Region | Default local root | R2 bucket setting | Fact grain |
|---|---|---|---|
| XSMB | `output/` | `R2_BUCKET_NAME` | date + prize group + prize order |
| XSMN | `output-xsmn/` | `R2_XSMN_BUCKET_NAME` | date + station + prize group + prize order |

Each root or bucket owns its own `bronze/`, `silver/`, `gold/`, `quality/`, and `manifests/` tree. A failure, backfill, or publication in one region therefore cannot update the other region's `manifests/latest.json`.

```text
XSMB source -> XSMB bucket -> Bronze -> Silver -> Gold -> XSMB consumers
XSMN source -> XSMN bucket -> Bronze -> Silver -> Gold -> XSMN consumers
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
```

The legacy `xsmb-etl` executable remains an alias of `lottery-etl`.

## Commands

Use `--region xsmb`, `--region xsmn`, or `--region all`. Omitting it preserves the original XSMB behavior.

```bash
uv run lottery-etl run --region xsmn --target-date 2026-07-16
uv run lottery-etl run --region all --target-date 2026-07-16
uv run lottery-etl backfill --region xsmn --from 2026-07-01 --to 2026-07-16
uv run lottery-etl build-gold --region xsmn
uv run lottery-etl validate --region xsmn
uv run lottery-etl download-gold --region all --download-output downloads/
uv run lottery-etl no-draw --region xsmn --target-date 2026-07-16 --detail "Officially confirmed no draw"
```

When `--region all` and `--output custom-root/` are combined, local objects are written to `custom-root/xsmb/` and `custom-root/xsmn/`. Successful dates are skipped unless `--force` is supplied. During backfill, an explicit source notice containing “không mở thưởng” is recorded as `no_draw`; unexpected network/parser failures are recorded as `failed`. Both outcomes are returned in the result list and processing continues with the next date. A missing table by itself is never sufficient to claim `no_draw`.

## Region-specific validation

- XSMB requires 27 prize results and 100 loto rows per date; loto frequency sums to 27.
- XSMN dynamically accepts the three or four stations shown on the requested page. Each station requires 18 prize results, including prize 8, and 100 station-level loto rows whose frequency sums to 18.
- Leading zeros are retained in `formatted_number` for every official width, including six-digit XSMN special prizes.
- XSMN station identity is derived from the station link and stored as `station_code`, `station_name`, `station_url`, and `station_order`.

## Historical XSMB migration

The repository's legacy JSON is XSMB-only and remains backward compatible:

```bash
uv run lottery-etl migrate-legacy --input data/xsmb.json
```

Historical rows use `legacy_repository_dataset` lineage. XSMN history is acquired with `backfill --region xsmn`; it is never written into the XSMB lake.

## Cloudflare R2

Create two buckets, for example:

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket list
```

Required shared S3 settings are `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY`. Set both bucket names:

- `R2_BUCKET_NAME=xsmb-data-lake`
- `R2_XSMN_BUCKET_NAME=xsmn-data-lake`

A single bucket-scoped token may be authorized for both buckets. For stricter isolation, the optional `R2_XSMN_ACCOUNT_ID`, `R2_XSMN_ACCESS_KEY_ID`, `R2_XSMN_SECRET_ACCESS_KEY`, and `R2_XSMN_ENDPOINT_URL` override the shared connection only for XSMN.

Keep Bronze and Silver private; expose only curated `gold/latest/*.csv` and Parquet objects through controlled custom-domain routes. See [R2 setup](docs/r2-setup.md) for the activation checklist and credential troubleshooting.

## Analytics and BI

- [Data dictionary](docs/data-dictionary.md)
- [Power BI](docs/power-bi.md)
- [Tableau](docs/tableau.md)
- [Operations runbook](docs/operations.md)
- [DuckDB analysis examples](sql/analysis-examples.sql)
- [SQL quality checks](sql/data-quality-checks.sql)
- [XSMN SQL quality checks](sql/xsmn-data-quality-checks.sql)
- [Python public-Gold example](examples/read_gold.py)

XSMN Gold adds `dim-station` and station fields to all facts. Gold CSV files are intended for Power BI and Tableau; Parquet files support Python and DuckDB. Credentials and presigned URLs are never committed.

## Automation

- `ci.yml` runs Ruff, pytest, and both offline fixture pipelines without production secrets.
- `daily-etl.yml` runs both independent R2 lakes at 18:35 Vietnam time and supports manual region, target-date, and force inputs.
- `xsmn-backfill.yml` runs resumable yearly XSMN batches from 2010 onward, one year at a time, and publishes Gold once per batch.
- The daily workflow has read-only repository permissions and writes generated data to R2 rather than committing it to Git.

## Legacy analysis

The original wide local XSMB dataset and plotting script remain available for compatibility. Running `uv run src/analyze.py` writes its report to `output/legacy-analysis.md` and charts to `images/generated/`; it does not rewrite this README or tracked images.
