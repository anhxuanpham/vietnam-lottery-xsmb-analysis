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

-- Must return no rows: station codes differ from the exact versioned XSMT calendar.
WITH actual AS (
    SELECT
        draw_date,
        string_agg(DISTINCT station_code, ',' ORDER BY station_code) AS actual_station_codes
    FROM read_parquet('gold/latest/fact-draw-result.parquet')
    GROUP BY draw_date
),
expected AS (
    SELECT
        draw_date,
        CASE
            WHEN draw_date IN (DATE '2021-07-27', DATE '2021-08-03') THEN 'QNA'
            WHEN draw_date = DATE '2021-08-06' THEN 'GL'
            WHEN draw_date = DATE '2021-08-18' THEN 'KH'
            WHEN draw_date = DATE '2021-08-21' THEN 'DNO,QNG'
            WHEN draw_date = DATE '2021-09-04' THEN 'DNA,DNO'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 0 AND draw_date < DATE '2022-01-02'
                THEN 'KH,KT'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 0 THEN 'KH,KT,TTH'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 1 THEN 'PY,TTH'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 2 THEN 'DLK,QNA'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 3 THEN 'DNA,KH'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 4 THEN 'BDI,QB,QT'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 5 THEN 'GL,NT'
            WHEN CAST(strftime(draw_date, '%w') AS INTEGER) = 6 THEN 'DNA,DNO,QNG'
        END AS expected_station_codes
    FROM actual
)
SELECT actual.draw_date, expected.expected_station_codes, actual.actual_station_codes
FROM actual
JOIN expected USING (draw_date)
WHERE actual.actual_station_codes <> expected.expected_station_codes;
