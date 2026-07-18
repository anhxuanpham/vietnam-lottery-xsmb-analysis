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

-- Must return no rows: source dates that contain neither three nor four stations.
SELECT draw_date, COUNT(DISTINCT station_code) AS station_count
FROM read_parquet('gold/latest/fact-draw-result.parquet')
GROUP BY draw_date
HAVING COUNT(DISTINCT station_code) NOT IN (3, 4);
