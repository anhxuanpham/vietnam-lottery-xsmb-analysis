-- Run against the independent XSMT Gold Parquet files.

-- Must return no rows: station draws without 18 prize rows.
SELECT draw_date, station_code, COUNT(*) AS row_count
FROM read_parquet('gold/latest/fact-draw-result.parquet')
GROUP BY draw_date, station_code
HAVING COUNT(*) <> 18;

-- Must return no rows: station draws without 100 loto rows or frequency sum 18.
SELECT
    draw_date,
    station_code,
    COUNT(*) AS row_count,
    SUM(frequency) AS frequency_sum
FROM read_parquet('gold/latest/fact-loto-daily.parquet')
GROUP BY draw_date, station_code
HAVING COUNT(*) <> 100 OR SUM(frequency) <> 18;

-- Must return no rows: duplicate XSMT draw business keys.
SELECT draw_date, station_code, prize_group, prize_order, COUNT(*) AS duplicate_count
FROM read_parquet('gold/latest/fact-draw-result.parquet')
GROUP BY draw_date, station_code, prize_group, prize_order
HAVING COUNT(*) > 1;

-- Must return no rows: source dates outside the normal two/three-station rule
-- or the exact documented partial-draw station set during the 2021 closures.
WITH documented_partial_draws(draw_date, station_code) AS (
    VALUES
        (DATE '2021-07-27', 'QNA'),
        (DATE '2021-08-03', 'QNA'),
        (DATE '2021-08-06', 'GL'),
        (DATE '2021-08-18', 'KH')
),
actual AS (
    SELECT
        draw_date,
        COUNT(DISTINCT station_code) AS station_count,
        MIN(station_code) AS single_station_code
    FROM read_parquet('gold/latest/fact-draw-result.parquet')
    GROUP BY draw_date
)
SELECT actual.draw_date, actual.station_count, actual.single_station_code
FROM actual
LEFT JOIN documented_partial_draws USING (draw_date)
WHERE
    (documented_partial_draws.draw_date IS NULL AND actual.station_count NOT IN (2, 3))
    OR (
        documented_partial_draws.draw_date IS NOT NULL
        AND (actual.station_count <> 1 OR actual.single_station_code <> documented_partial_draws.station_code)
    );
