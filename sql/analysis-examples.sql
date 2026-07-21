-- DuckDB examples for descriptive analysis only.
-- Replace placeholders at runtime; never commit real credentials.

INSTALL httpfs;
LOAD httpfs;

CREATE OR REPLACE SECRET xsmb_r2 (
    TYPE r2,
    KEY_ID '<R2_ACCESS_KEY_ID>',
    SECRET '<R2_SECRET_ACCESS_KEY>',
    ACCOUNT_ID '<R2_ACCOUNT_ID>'
);

-- Resolve manifests/latest.json first and copy its run_id values here. Every
-- query below then reads one immutable, checksum-verifiable release.
SET VARIABLE xsmb_gold_root = 'r2://xsmb-data-lake/gold/releases/run-id=<XSMB_RUN_ID>';
SET VARIABLE xsmn_gold_root = 'r2://xsmn-data-lake/gold/releases/run-id=<XSMN_RUN_ID>';
SET VARIABLE xsmt_gold_root = 'r2://xsmt-data-lake/gold/releases/run-id=<XSMT_RUN_ID>';

-- Total observed frequency by two-digit number.
SELECT
    number_2d,
    SUM(frequency) AS total_frequency
FROM read_parquet(
    getvariable('xsmb_gold_root') || '/fact-loto-daily.parquet'
)
GROUP BY number_2d
ORDER BY total_frequency DESC, number_2d;

-- Special-prize tails by weekday.
SELECT
    d.day_name,
    s.tail_2d,
    COUNT(*) AS observations
FROM read_parquet(
    getvariable('xsmb_gold_root') || '/fact-special-prize.parquet'
) AS s
JOIN read_parquet(
    getvariable('xsmb_gold_root') || '/dim-date.parquet'
) AS d
    ON s.draw_date = d.date
GROUP BY d.day_name, s.tail_2d
ORDER BY observations DESC;

-- XSMN is a different bucket and station-grain dataset.
SELECT
    station_code,
    number_2d,
    SUM(frequency) AS total_frequency
FROM read_parquet(
    getvariable('xsmn_gold_root') || '/fact-loto-daily.parquet'
)
GROUP BY station_code, number_2d
ORDER BY station_code, total_frequency DESC, number_2d;

-- XSMT has the same station-grain model in its own immutable release.
SELECT
    station_code,
    number_2d,
    SUM(frequency) AS total_frequency
FROM read_parquet(
    getvariable('xsmt_gold_root') || '/fact-loto-daily.parquet'
)
GROUP BY station_code, number_2d
ORDER BY station_code, total_frequency DESC, number_2d;
