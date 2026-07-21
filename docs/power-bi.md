# Power BI exercise

Power BI should resolve the weekly CSV export manifest from a stable Cloudflare custom-domain URL. Choose XSMB,
XSMN, or XSMT explicitly; every route targets a different bucket. Bronze, Silver, credentials, presigned URLs, and
the private daily Parquet release are not Power BI data sources.

## Load `fact-loto-daily`

Replace the host/path with the public custom-domain route configured for your bucket:

```powerquery
let
    BaseUrl = "https://data.example.com/xsmb/",
    ExportManifest = Json.Document(Web.Contents(BaseUrl & "exports/csv/latest.json")),
    FactReference = List.First(
        List.Select(
            ExportManifest[objects],
            each Text.EndsWith(_[key], "/fact-loto-daily.csv")
        )
    ),
    Source = Csv.Document(
        Web.Contents(BaseUrl & FactReference[key]),
        [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Types = Table.TransformColumnTypes(
        Headers,
        {
            {"draw_date", type date},
            {"number_2d", type text},
            {"frequency", Int64.Type},
            {"appeared", type logical},
            {"draws_since_previous", Int64.Type},
            {"calendar_days_since_previous", Int64.Type},
            {"rolling_7_frequency", Int64.Type},
            {"rolling_30_frequency", Int64.Type},
            {"rolling_90_frequency", Int64.Type},
            {"run_id", type text}
        }
    )
in
    Types
```

Keep `number_2d` as text so values such as `00` and `09` retain their leading zeros.

For XSMN/XSMT, change the URL path to the selected region and add `station_code` and `station_name` as text columns in the `Types` step. Relate `station_code` to `dim-station.station_code`; every loto measure must remain at station grain unless an intentional regional aggregate is clearly labeled.

## Suggested exercises

1. Join `fact-loto-daily.number_2d` to `dim-number.number_2d`.
2. Join fact dates to `dim-date.date`.
3. Build a `10 × 10` frequency heatmap using tens and ones digits.
4. Compare rolling 7, 30, and 90 draw frequencies.
5. Build a pipeline-health page using `draw_status`.
6. For XSMN/XSMT, add a station selector and compare only like-for-like station histories.

Label every frequency or waiting-time view as descriptive. It is not evidence that a future lottery result can be predicted reliably.

## Refresh check

Before enabling scheduled refresh, open `exports/csv/latest.json`, verify `region`, `dataset_version`, and the expected
CSV reference, then verify its run is no newer than the region's `manifests/latest.json`. Refresh the manifest URL,
not a hard-coded run path or expiring signed URL. CSV updates weekly; daily dashboard data uses Results API v2.
