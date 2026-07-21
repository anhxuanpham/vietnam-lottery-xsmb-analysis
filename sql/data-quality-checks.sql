-- Resolve manifests/latest.json, verify its checksums, and set this to the
-- downloaded or R2-hosted immutable release directory.
SET VARIABLE gold_root = 'gold/releases/run-id=<RUN_ID>';

-- Must return no rows: successful draw dates without 27 prize rows.
SELECT draw_date, COUNT(*) AS row_count
FROM read_parquet(getvariable('gold_root') || '/fact-draw-result.parquet')
GROUP BY draw_date
HAVING COUNT(*) <> 27;

-- Must return no rows: dates without 100 loto rows or a frequency sum of 27.
SELECT
    draw_date,
    COUNT(*) AS row_count,
    SUM(frequency) AS frequency_sum
FROM read_parquet(getvariable('gold_root') || '/fact-loto-daily.parquet')
GROUP BY draw_date
HAVING COUNT(*) <> 100 OR SUM(frequency) <> 27;

-- Must return no rows: duplicate draw business keys.
SELECT draw_date, prize_group, prize_order, COUNT(*) AS duplicate_count
FROM read_parquet(getvariable('gold_root') || '/fact-draw-result.parquet')
GROUP BY draw_date, prize_group, prize_order
HAVING COUNT(*) > 1;

-- Inspect unresolved dates without treating them as proof of a source problem.
SELECT date, draw_status
FROM read_parquet(getvariable('gold_root') || '/dim-date.parquet')
WHERE draw_status IN ('missing', 'failed')
ORDER BY date;
