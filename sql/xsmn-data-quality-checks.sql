-- Run against the independent XSMN Gold Parquet files.

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

-- Must return no rows: duplicate XSMN draw business keys.
SELECT draw_date, station_code, prize_group, prize_order, COUNT(*) AS duplicate_count
FROM read_parquet('gold/latest/fact-draw-result.parquet')
GROUP BY draw_date, station_code, prize_group, prize_order
HAVING COUNT(*) > 1;

-- Must return no rows: station codes differ from the exact XSMN weekday calendar.
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
        CASE CAST(strftime(draw_date, '%w') AS INTEGER)
            WHEN 0 THEN 'DL,KG,TG'
            WHEN 1 THEN 'CM,DT,HCM'
            WHEN 2 THEN 'BL,BTR,VT'
            WHEN 3 THEN 'CT,DN,ST'
            WHEN 4 THEN 'AG,BTH,TN'
            WHEN 5 THEN 'BD,TV,VL'
            WHEN 6 THEN 'BP,HCM,HG,LA'
        END AS expected_station_codes
    FROM actual
)
SELECT actual.draw_date, expected.expected_station_codes, actual.actual_station_codes
FROM actual
JOIN expected USING (draw_date)
WHERE actual.actual_station_codes <> expected.expected_station_codes;
