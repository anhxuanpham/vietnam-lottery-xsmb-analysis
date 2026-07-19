# Tableau exercise

The initial supported workflow uses curated Gold CSV or Parquet files. A Web Data Connector or Hyper extract is intentionally not required.

## Download

Public custom domain:

```bash
curl -O https://data.example.com/xsmb/gold/latest/fact-loto-daily.csv
curl -O https://data.example.com/xsmb/gold/latest/dim-number.csv
curl -O https://data.example.com/xsmb/gold/latest/dim-date.csv

# XSMN/XSMT are separate bucket routes and add dim-station.
curl -O https://data.example.com/xsmn/gold/latest/fact-loto-daily.csv
curl -O https://data.example.com/xsmn/gold/latest/dim-station.csv
curl -O https://data.example.com/xsmt/gold/latest/fact-loto-daily.csv
curl -O https://data.example.com/xsmt/gold/latest/dim-station.csv
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
