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

-- Total observed frequency by two-digit number.
SELECT
    number_2d,
    SUM(frequency) AS total_frequency
FROM read_parquet(
    'r2://xsmb-data-lake/gold/latest/fact-loto-daily.parquet'
)
GROUP BY number_2d
ORDER BY total_frequency DESC, number_2d;

-- Special-prize tails by weekday.
SELECT
    d.day_name,
    s.tail_2d,
    COUNT(*) AS observations
FROM read_parquet(
    'r2://xsmb-data-lake/gold/latest/fact-special-prize.parquet'
) AS s
JOIN read_parquet(
    'r2://xsmb-data-lake/gold/latest/dim-date.parquet'
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
    'r2://xsmn-data-lake/gold/latest/fact-loto-daily.parquet'
)
GROUP BY station_code, number_2d
ORDER BY station_code, total_frequency DESC, number_2d;
