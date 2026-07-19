"""Export one published regional Gold snapshot as compact dashboard JSON.

The exporter treats ``manifests/latest.json`` as the publication boundary and
reads only the Gold Parquet objects referenced by that manifest.  It never
rebuilds Gold and never writes credentials into the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence

import pandas as pd

from xsmb_etl.config import Settings
from xsmb_etl.models import PRIZE_SPECS
from xsmb_etl.r2 import R2ObjectStore
from xsmb_etl.repository import CentralDataLakeRepository, DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import DataObjectReference, LotteryRegion
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.xsmn_models import SOUTHERN_PRIZE_SPECS


SCHEMA_VERSION = 1
# The dashboard supports a 365-draw training window plus a 90-draw
# walk-forward evaluation. Keep enough station-grain history for both.
DEFAULT_RECENT_DRAWS_PER_STATION = 455
MANIFEST_KEY = 'manifests/latest.json'
FACT_DRAW_RESULT_KEY = 'gold/latest/fact-draw-result.parquet'
DIM_STATION_KEY = 'gold/latest/dim-station.parquet'


class ServingDataError(RuntimeError):
    """Raised when a published snapshot cannot safely be exported."""


def build_serving_payload(
    repository: DataLakeRepository,
    region: LotteryRegion,
    *,
    recent_draws_per_station: int = DEFAULT_RECENT_DRAWS_PER_STATION,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build a dashboard payload from an already-published Gold snapshot."""

    if recent_draws_per_station < 1:
        raise ValueError('recent_draws_per_station must be at least 1')

    manifest = repository.latest_manifest()
    if manifest is None:
        raise ServingDataError(f'{region.value.upper()} has no published {MANIFEST_KEY}')
    if manifest.region is not region:
        raise ServingDataError(f'manifest region is {manifest.region.value}, expected {region.value}')

    references = {item.key: item for item in manifest.objects}
    fact = _read_parquet(repository, references, FACT_DRAW_RESULT_KEY)
    _validate_fact(fact, region, manifest.run_id)

    station_dimension = None
    if region in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
        station_dimension = _read_parquet(repository, references, DIM_STATION_KEY)
        _require_columns(
            station_dimension,
            DIM_STATION_KEY,
            {'station_code', 'station_name', 'station_url', 'first_draw_date', 'latest_draw_date'},
        )

    normalized = fact.copy()
    normalized['draw_date'] = pd.to_datetime(normalized['draw_date']).dt.normalize()
    normalized['formatted_number'] = normalized['formatted_number'].astype(str)
    normalized['loto_2d'] = normalized['loto_2d'].astype(str).str.zfill(2)
    if region is LotteryRegion.XSMB:
        normalized['station_code'] = region.value
        normalized['station_name'] = 'Miền Bắc'
    else:
        normalized['station_code'] = normalized['station_code'].astype(str)
        normalized['station_name'] = normalized['station_name'].astype(str)

    prize_order = _prize_group_order(region)
    normalized['_group_order'] = normalized['prize_group'].astype(str).map(prize_order)
    if normalized['_group_order'].isna().any():
        unknown = sorted(set(normalized.loc[normalized['_group_order'].isna(), 'prize_group'].astype(str)))
        raise ServingDataError(f'{FACT_DRAW_RESULT_KEY} contains unknown prize groups: {unknown}')
    normalized = normalized.sort_values(
        ['draw_date', 'station_code', '_group_order', 'prize_order'],
        kind='stable',
    ).reset_index(drop=True)

    station_metadata = _station_metadata(normalized, station_dimension, region)
    station_payloads: list[dict[str, object]] = []
    recent_frames: list[pd.DataFrame] = []
    for station in station_metadata:
        station_rows = normalized.loc[normalized['station_code'].eq(station['code'])]
        station_draw_dates = station_rows['draw_date'].drop_duplicates().sort_values()
        recent_dates = set(station_draw_dates.tail(recent_draws_per_station))
        recent_frames.append(station_rows.loc[station_rows['draw_date'].isin(recent_dates)])
        station_payloads.append(
            {
                **station,
                'drawCount': int(station_draw_dates.size),
                'resultCount': int(len(station_rows)),
                'fullFrequency': _frequency_map(station_rows['loto_2d']),
            }
        )

    recent = pd.concat(recent_frames, ignore_index=True).sort_values(
        ['draw_date', 'station_code', '_group_order', 'prize_order'],
        kind='stable',
    )
    draws = _draw_records(recent)
    minimum_date = pd.Timestamp(normalized['draw_date'].min()).date().isoformat()
    maximum_date = pd.Timestamp(normalized['draw_date'].max()).date().isoformat()
    latest_results = [draw for draw in draws if draw['date'] == maximum_date]
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)

    return {
        'schemaVersion': SCHEMA_VERSION,
        'region': region.value,
        'generatedAt': generated.isoformat().replace('+00:00', 'Z'),
        'manifest': {
            'key': MANIFEST_KEY,
            'runId': manifest.run_id,
            'datasetVersion': manifest.dataset_version,
            'targetDate': manifest.target_date.isoformat(),
            'publishedAt': manifest.published_at.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
        },
        'freshness': {
            'latestDrawDate': maximum_date,
            'manifestTargetDate': manifest.target_date.isoformat(),
            'matchesManifestTarget': maximum_date == manifest.target_date.isoformat(),
        },
        'range': {'from': minimum_date, 'to': maximum_date},
        'drawCount': int(normalized[['draw_date', 'station_code']].drop_duplicates().shape[0]),
        'resultCount': int(len(normalized)),
        'latest': {'date': maximum_date, 'results': latest_results},
        'fullFrequency': _frequency_map(normalized['loto_2d']),
        'draws': draws,
        'stations': station_payloads,
    }


def write_serving_payload(payload: dict[str, object], output: Path) -> None:
    """Atomically write compact, stable-key JSON suitable for an R2 object."""

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    with NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
        temporary.write(encoded)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, output)


def _repository(
    settings: Settings,
    storage: str,
    region: LotteryRegion,
    *,
    lake_root: Path | None,
) -> DataLakeRepository:
    if storage == 'r2':
        if lake_root is not None:
            raise ValueError('--lake-root is only valid with --storage local')
        store = R2ObjectStore(settings, region=region)
    else:
        defaults = {
            LotteryRegion.XSMB: settings.local_output_dir,
            LotteryRegion.XSMN: settings.local_xsmn_output_dir,
            LotteryRegion.XSMT: settings.local_xsmt_output_dir,
        }
        store = LocalObjectStore(lake_root or defaults[region])

    if region is LotteryRegion.XSMT:
        return CentralDataLakeRepository(store, gold_cache_control=settings.gold_cache_control)
    if region is LotteryRegion.XSMN:
        return SouthernDataLakeRepository(store, gold_cache_control=settings.gold_cache_control)
    return DataLakeRepository(store, gold_cache_control=settings.gold_cache_control)


def _read_parquet(
    repository: DataLakeRepository,
    references: dict[str, DataObjectReference],
    key: str,
) -> pd.DataFrame:
    reference = references.get(key)
    if reference is None:
        raise ServingDataError(f'published manifest does not reference required object: {key}')
    payload = repository.store.get_bytes(key)
    if len(payload) != reference.size:
        raise ServingDataError(
            f'published object size mismatch for {key}: expected {reference.size}, got {len(payload)}'
        )
    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != reference.sha256:
        raise ServingDataError(
            f'published object checksum mismatch for {key}: expected {reference.sha256}, got {checksum}'
        )
    try:
        return pd.read_parquet(BytesIO(payload))
    except Exception as exc:
        raise ServingDataError(f'cannot read published Parquet object: {key}') from exc


def _validate_fact(fact: pd.DataFrame, region: LotteryRegion, run_id: str) -> None:
    required = {
        'draw_date',
        'prize_group',
        'prize_order',
        'formatted_number',
        'loto_2d',
        'run_id',
    }
    if region in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
        required.update({'station_code', 'station_name'})
    _require_columns(fact, FACT_DRAW_RESULT_KEY, required)
    if fact.empty:
        raise ServingDataError(f'{FACT_DRAW_RESULT_KEY} is empty')
    fact_run_ids = set(fact['run_id'].dropna().astype(str))
    if fact_run_ids != {run_id}:
        raise ServingDataError(
            f'{FACT_DRAW_RESULT_KEY} run_id values {sorted(fact_run_ids)} do not match manifest {run_id}'
        )


def _require_columns(dataframe: pd.DataFrame, key: str, required: set[str]) -> None:
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ServingDataError(f'{key} is missing required columns: {missing}')


def _prize_group_order(region: LotteryRegion) -> dict[str, int]:
    groups = PRIZE_SPECS if region is LotteryRegion.XSMB else SOUTHERN_PRIZE_SPECS
    return {group.value: position for position, group in enumerate(groups)}


def _station_metadata(
    fact: pd.DataFrame,
    station_dimension: pd.DataFrame | None,
    region: LotteryRegion,
) -> list[dict[str, object]]:
    if region is LotteryRegion.XSMB:
        return [
            {
                'code': region.value,
                'name': 'Miền Bắc',
                'url': None,
                'range': {
                    'from': pd.Timestamp(fact['draw_date'].min()).date().isoformat(),
                    'to': pd.Timestamp(fact['draw_date'].max()).date().isoformat(),
                },
            }
        ]

    assert station_dimension is not None
    station_rows = station_dimension.copy()
    station_rows['station_code'] = station_rows['station_code'].astype(str)
    fact_codes = set(fact['station_code'].astype(str))
    dimension_codes = set(station_rows['station_code'])
    if fact_codes != dimension_codes:
        raise ServingDataError(
            f'{DIM_STATION_KEY} station codes do not match facts: '
            f'missing={sorted(fact_codes - dimension_codes)}, extra={sorted(dimension_codes - fact_codes)}'
        )

    output = []
    for row in station_rows.sort_values('station_code', kind='stable').itertuples(index=False):
        output.append(
            {
                'code': str(row.station_code),
                'name': str(row.station_name),
                'url': str(row.station_url),
                'range': {
                    'from': pd.Timestamp(row.first_draw_date).date().isoformat(),
                    'to': pd.Timestamp(row.latest_draw_date).date().isoformat(),
                },
            }
        )
    return output


def _frequency_map(values: pd.Series) -> dict[str, int]:
    frequencies = values.astype(str).str.zfill(2).value_counts().to_dict()
    return {f'{number:02d}': int(frequencies.get(f'{number:02d}', 0)) for number in range(100)}


def _draw_records(dataframe: pd.DataFrame) -> list[dict[str, object]]:
    output = []
    groups = dataframe.groupby(['draw_date', 'station_code'], sort=True)
    for (draw_date, station_code), rows in groups:
        rows = rows.sort_values(['_group_order', 'prize_order'], kind='stable')
        prizes: dict[str, list[str]] = {}
        for prize_group, group_rows in rows.groupby('prize_group', sort=False):
            prizes[str(prize_group)] = (
                group_rows.sort_values('prize_order', kind='stable')['formatted_number'].astype(str).tolist()
            )
        special_values = prizes.get('special', [])
        if len(special_values) != 1:
            raise ServingDataError(
                f'{FACT_DRAW_RESULT_KEY} expected one special prize for '
                f'{pd.Timestamp(draw_date).date().isoformat()} {station_code}'
            )
        special_prize = special_values[0]
        special_tail = special_prize[-2:]
        loto_numbers = rows['loto_2d'].astype(str).str.zfill(2).tolist()
        # Keep the special prize first for a region-independent display contract.
        special_index = next(
            index for index, prize_group in enumerate(rows['prize_group'].astype(str)) if prize_group == 'special'
        )
        numbers = [loto_numbers[special_index], *loto_numbers[:special_index], *loto_numbers[special_index + 1 :]]
        output.append(
            {
                'date': pd.Timestamp(draw_date).date().isoformat(),
                'stationCode': str(station_code),
                'stationName': str(rows['station_name'].iloc[0]),
                'specialPrize': special_prize,
                'specialTail': special_tail,
                'numbers': numbers,
                'prizes': prizes,
            }
        )
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Export one published XSMB/XSMN/XSMT Gold snapshot as dashboard JSON')
    parser.add_argument('--storage', choices=('local', 'r2'), default='local')
    parser.add_argument('--region', choices=tuple(region.value for region in LotteryRegion), required=True)
    parser.add_argument('--output', type=Path, required=True, help='destination JSON file')
    parser.add_argument('--lake-root', type=Path, help='local object-store root; local storage only')
    parser.add_argument(
        '--recent-draws-per-station',
        type=int,
        default=DEFAULT_RECENT_DRAWS_PER_STATION,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.recent_draws_per_station < 1:
        raise SystemExit('--recent-draws-per-station must be at least 1')
    region = LotteryRegion(args.region)
    settings = Settings()
    repository = _repository(settings, args.storage, region, lake_root=args.lake_root)
    payload = build_serving_payload(
        repository,
        region,
        recent_draws_per_station=args.recent_draws_per_station,
    )
    write_serving_payload(payload, args.output)
    print(
        json.dumps(
            {
                'region': region.value,
                'output': str(args.output),
                'datasetVersion': payload['manifest']['datasetVersion'],
                'drawCount': payload['drawCount'],
                'resultCount': payload['resultCount'],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
