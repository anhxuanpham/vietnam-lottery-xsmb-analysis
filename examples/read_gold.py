"""Query the manifest-selected public Gold release with DuckDB."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request

import duckdb


region = os.environ.get('LOTTERY_REGION', 'xsmb').lower()
if region not in {'xsmb', 'xsmn', 'xsmt'}:
    raise SystemExit('LOTTERY_REGION must be xsmb, xsmn, or xsmt')
variable_by_region = {
    'xsmb': 'R2_PUBLIC_BASE_URL',
    'xsmn': 'R2_XSMN_PUBLIC_BASE_URL',
    'xsmt': 'R2_XSMT_PUBLIC_BASE_URL',
}
variable = variable_by_region[region]
public_base_url = os.environ.get(variable)
if not public_base_url:
    raise SystemExit(f'Set {variable} to the public Gold base URL')

base_url = public_base_url.rstrip('/')
with urllib.request.urlopen(f'{base_url}/manifests/latest.json', timeout=30) as response:
    manifest = json.load(response)

if manifest.get('region') != region:
    raise SystemExit(f'Latest manifest region is {manifest.get("region")!r}, expected {region!r}')
fact_reference = next(
    (item for item in manifest.get('objects', []) if item['key'].endswith('/fact-loto-daily.parquet')),
    None,
)
if fact_reference is None:
    raise SystemExit('Latest manifest does not reference fact-loto-daily.parquet')
fact_url = f'{base_url}/{fact_reference["key"]}'

# Verify the exact immutable object before querying it. This keeps the example
# aligned with the publication boundary instead of trusting a mutable URL.
with urllib.request.urlopen(fact_url, timeout=60) as response:
    fact_payload = response.read()
if len(fact_payload) != fact_reference['size'] or hashlib.sha256(fact_payload).hexdigest() != fact_reference['sha256']:
    raise SystemExit('Published fact-loto-daily.parquet failed manifest integrity verification')

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
