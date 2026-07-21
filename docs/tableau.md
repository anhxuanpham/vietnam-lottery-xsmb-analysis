# Tableau exercise

The supported file workflow uses the manifest-addressed weekly CSV export or a locally downloaded current Parquet
release. A Web Data Connector or Hyper extract is intentionally not required.

## Download

Resolve `exports/csv/latest.json`, copy the run ID, then download only referenced files. Example for one resolved run:

```bash
XSMB_RUN_ID='<resolved-xsmb-run-id>'
XSMN_RUN_ID='<resolved-xsmn-run-id>'
XSMT_RUN_ID='<resolved-xsmt-run-id>'
curl -O "https://data.example.com/xsmb/exports/csv/run-id=${XSMB_RUN_ID}/fact-loto-daily.csv"
curl -O "https://data.example.com/xsmb/exports/csv/run-id=${XSMB_RUN_ID}/dim-number.csv"
curl -O "https://data.example.com/xsmb/exports/csv/run-id=${XSMB_RUN_ID}/dim-date.csv"

# XSMN/XSMT are separate bucket routes and add dim-station.
curl -O "https://data.example.com/xsmn/exports/csv/run-id=${XSMN_RUN_ID}/fact-loto-daily.csv"
curl -O "https://data.example.com/xsmn/exports/csv/run-id=${XSMN_RUN_ID}/dim-station.csv"
curl -O "https://data.example.com/xsmt/exports/csv/run-id=${XSMT_RUN_ID}/fact-loto-daily.csv"
curl -O "https://data.example.com/xsmt/exports/csv/run-id=${XSMT_RUN_ID}/dim-station.csv"
```

Private/local object store:

```bash
uv run lottery-etl download-gold --region xsmb --download-output downloads/xsmb/
uv run lottery-etl download-gold --region xsmn --download-output downloads/xsmn/
```

## Model

1. Open `fact-loto-daily.csv` in Tableau Desktop.
2. Relate `number_2d` to `dim-number.number_2d` as text.
3. Relate `draw_date` to `dim-date.date` as a date.
4. For XSMN/XSMT, relate `station_code` to `dim-station.station_code` and retain it in fact relationships.
5. Keep nullable waiting-time fields as null; do not replace “never seen” with zero.
6. Save Tableau workbooks outside generated ETL object paths.

Suggested sheets include frequency heatmaps, draw-count versus calendar-day gaps, weekday comparisons, and pipeline status. All results are descriptive, not predictive.
