"""Query public Gold Parquet with DuckDB; no credentials are required."""

from __future__ import annotations

import os

import duckdb


region = os.environ.get('LOTTERY_REGION', 'xsmb').lower()
if region not in {'xsmb', 'xsmn'}:
    raise SystemExit('LOTTERY_REGION must be xsmb or xsmn')
public_base_url = os.environ.get('R2_XSMN_PUBLIC_BASE_URL' if region == 'xsmn' else 'R2_PUBLIC_BASE_URL')
if not public_base_url:
    variable = 'R2_XSMN_PUBLIC_BASE_URL' if region == 'xsmn' else 'R2_PUBLIC_BASE_URL'
    raise SystemExit(f'Set {variable} to the public Gold base URL')

fact_url = f'{public_base_url.rstrip("/")}/gold/latest/fact-loto-daily.parquet'
with duckdb.connect() as connection:
    rows = connection.execute(
        """
        SELECT number_2d, SUM(frequency) AS total_frequency
        FROM read_parquet(?)
        GROUP BY number_2d
        ORDER BY total_frequency DESC, number_2d
        LIMIT 10
        """,
        [fact_url],
    ).fetchall()

for number_2d, total_frequency in rows:
    print(f'{number_2d}: {total_frequency}')
