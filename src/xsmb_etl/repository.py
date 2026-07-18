"""Bronze, Silver, Gold, quality, and manifest repository operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

from xsmb_etl.control import ControlState, DrawStateRecord, DrawStatus
from xsmb_etl.extract import ExtractedResult
from xsmb_etl.models import LotteryResult
from xsmb_etl.quality import QualityReport
from xsmb_etl.run_models import (
    DataObjectReference,
    LatestManifest,
    LotteryRegion,
    MigrationReport,
    RunManifest,
    RunStatus,
)
from xsmb_etl.storage import (
    ObjectAlreadyExistsError,
    ObjectStore,
    StoredObject,
    content_type_for_key,
)
from xsmb_etl.xsmn_extract import SouthernExtractedResult
from xsmb_etl.xsmn_models import SouthernDailyResult


class BronzeConflictError(RuntimeError):
    pass


def _put_immutable_bronze_object(
    store: ObjectStore,
    key: str,
    payload: bytes,
    *,
    object_metadata: dict[str, str],
    force: bool,
    stable_json_fields: dict[str, object] | None = None,
    conflict_message: str,
) -> StoredObject:
    """Create immutable Bronze or safely reuse an equivalent concurrent write."""

    try:
        return store.put_bytes(
            key,
            payload,
            content_type=content_type_for_key(key),
            metadata=object_metadata,
            overwrite=force,
        )
    except ObjectAlreadyExistsError as exc:
        existing_payload = store.get_bytes(key)
        payload_matches = existing_payload == payload
        if stable_json_fields is not None:
            try:
                existing_json = json.loads(existing_payload)
                payload_matches = isinstance(existing_json, dict) and all(
                    existing_json.get(field) == value for field, value in stable_json_fields.items()
                )
            except (json.JSONDecodeError, AttributeError):
                payload_matches = False
        if not payload_matches:
            raise BronzeConflictError(conflict_message) from exc
        return store.head(key)


class DataLakeRepository:
    def __init__(
        self,
        store: ObjectStore,
        *,
        gold_cache_control: str,
        region: LotteryRegion = LotteryRegion.XSMB,
    ) -> None:
        self.store = store
        self.gold_cache_control = gold_cache_control
        self.region = region

    def bronze_complete(self, draw_date: date) -> bool:
        prefix = self._bronze_prefix(draw_date)
        return all(
            self.store.exists(f'{prefix}/{filename}')
            for filename in ('response.html', 'parsed-result.json', 'metadata.json')
        )

    def bronze_objects(self, draw_date: date) -> list[StoredObject]:
        prefix = self._bronze_prefix(draw_date)
        return [
            self.store.head(f'{prefix}/{filename}')
            for filename in ('response.html', 'parsed-result.json', 'metadata.json')
        ]

    def load_bronze(self, draw_date: date) -> ExtractedResult:
        prefix = self._bronze_prefix(draw_date)
        raw_response = self.store.get_bytes(f'{prefix}/response.html')
        result = LotteryResult.model_validate_json(self.store.get_bytes(f'{prefix}/parsed-result.json'))
        return ExtractedResult(raw_response=raw_response, result=result)

    def write_bronze(
        self,
        extracted: ExtractedResult,
        *,
        run_id: str,
        force: bool = False,
        fetched_at: datetime | None = None,
        source_lineage: str = 'live_source',
    ) -> list[StoredObject]:
        draw_date = extracted.result.draw_date
        prefix = self._bronze_prefix(draw_date)
        keys = [f'{prefix}/response.html', f'{prefix}/parsed-result.json', f'{prefix}/metadata.json']
        raw_sha256 = hashlib.sha256(extracted.raw_response).hexdigest()
        metadata = {
            'run_id': run_id,
            'draw_date': draw_date.isoformat(),
            'source_url': extracted.result.source_url,
            'source_lineage': source_lineage,
            'fetched_at': (fetched_at or datetime.now(UTC)).isoformat(),
            'raw_sha256': raw_sha256,
        }
        payloads = [
            extracted.raw_response,
            _model_json_bytes(extracted.result),
            _json_bytes(metadata),
        ]
        object_metadata = {'run-id': run_id, 'draw-date': draw_date.isoformat()}
        stable_metadata = {
            'draw_date': draw_date.isoformat(),
            'source_url': extracted.result.source_url,
            'source_lineage': source_lineage,
            'raw_sha256': raw_sha256,
        }
        return [
            _put_immutable_bronze_object(
                self.store,
                key,
                payload,
                object_metadata=object_metadata,
                force=force,
                stable_json_fields=stable_metadata if key.endswith('/metadata.json') else None,
                conflict_message=f'Bronze object differs for {draw_date}: {key}; use --force to replace it',
            )
            for key, payload in zip(keys, payloads, strict=True)
        ]

    def upsert_silver_draw_results(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='draw-results',
            business_key=['draw_date', 'prize_group', 'prize_order'],
            merge_existing=True,
        )

    def replace_silver_draw_results(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='draw-results',
            business_key=['draw_date', 'prize_group', 'prize_order'],
            merge_existing=False,
        )

    def replace_silver_loto_daily(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='loto-daily',
            business_key=['draw_date', 'number_2d'],
            merge_existing=False,
        )

    def read_all_silver_draw_results(self) -> pd.DataFrame:
        keys = [key for key in self.store.list_keys('silver/draw-results/') if key.endswith('/draw-results.parquet')]
        if not keys:
            return pd.DataFrame()
        frames = [_parquet_from_bytes(self.store.get_bytes(key)) for key in keys]
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(['draw_date', 'prize_group', 'prize_order'], kind='stable')
            .reset_index(drop=True)
        )

    def write_gold_tables(self, tables: dict[str, pd.DataFrame], *, run_id: str) -> list[StoredObject]:
        objects: list[StoredObject] = []
        for table_name, dataframe in tables.items():
            parquet_key = f'gold/latest/{table_name}.parquet'
            csv_key = f'gold/latest/{table_name}.csv'
            metadata = {'run-id': run_id, 'dataset-version': run_id}
            objects.extend(
                [
                    self.store.put_bytes(
                        parquet_key,
                        _parquet_bytes(dataframe),
                        content_type=content_type_for_key(parquet_key),
                        cache_control=self.gold_cache_control,
                        metadata=metadata,
                    ),
                    self.store.put_bytes(
                        csv_key,
                        _csv_bytes(dataframe),
                        content_type=content_type_for_key(csv_key),
                        cache_control=self.gold_cache_control,
                        metadata=metadata,
                    ),
                ]
            )
        return objects

    def write_quality_report(self, report: QualityReport, draw_date: date) -> StoredObject:
        key = f'quality/year={draw_date:%Y}/month={draw_date:%m}/date={draw_date.isoformat()}/report.json'
        return self.store.put_bytes(
            key,
            _model_json_bytes(report),
            content_type=content_type_for_key(key),
            metadata={'run-id': report.run_id, 'passed': str(report.passed).lower()},
        )

    def write_migration_report(self, report: MigrationReport) -> StoredObject:
        key = f'quality/migrations/run-id={report.run_id}/report.json'
        return self.store.put_bytes(
            key,
            _model_json_bytes(report),
            content_type=content_type_for_key(key),
            metadata={'run-id': report.run_id, 'passed': str(report.passed).lower()},
        )

    def write_run_manifest(self, manifest: RunManifest) -> StoredObject:
        key = f'manifests/runs/run-id={manifest.run_id}.json'
        return self.store.put_bytes(
            key,
            _model_json_bytes(manifest),
            content_type=content_type_for_key(key),
            metadata={'run-id': manifest.run_id, 'status': manifest.status.value},
        )

    def publish_snapshot_and_latest(
        self,
        *,
        run_id: str,
        target_date: date,
        gold_objects: list[StoredObject],
    ) -> tuple[StoredObject, StoredObject]:
        references = tuple(DataObjectReference.from_stored(item) for item in gold_objects)
        manifest = LatestManifest(
            run_id=run_id,
            region=self.region,
            dataset_version=run_id,
            target_date=target_date,
            objects=references,
        )
        snapshot_key = f'gold/snapshots/as-of={target_date.isoformat()}/manifest.json'
        snapshot = self.store.put_bytes(
            snapshot_key,
            _model_json_bytes(manifest),
            content_type=content_type_for_key(snapshot_key),
            cache_control='no-cache',
            metadata={'run-id': run_id},
        )
        latest_key = 'manifests/latest.json'
        latest = self.store.put_bytes(
            latest_key,
            _model_json_bytes(manifest),
            content_type=content_type_for_key(latest_key),
            cache_control='no-cache',
            metadata={'run-id': run_id},
        )
        return snapshot, latest

    def latest_manifest(self) -> LatestManifest | None:
        key = 'manifests/latest.json'
        if not self.store.exists(key):
            return None
        return LatestManifest.model_validate_json(self.store.get_bytes(key))

    def run_manifests(self) -> list[RunManifest]:
        manifests = []
        for key in self.store.list_keys('manifests/runs/'):
            if key.endswith('.json'):
                manifests.append(RunManifest.model_validate_json(self.store.get_bytes(key)))
        return sorted(manifests, key=lambda manifest: manifest.completed_at)

    def control_state(self) -> ControlState:
        status_map = {
            RunStatus.SUCCESS: DrawStatus.SUCCESS,
            RunStatus.FAILED: DrawStatus.FAILED,
            RunStatus.NO_DRAW: DrawStatus.NO_DRAW,
        }
        records = []
        for manifest in self.run_manifests():
            covered_dates = (
                manifest.covered_dates
                if manifest.status is RunStatus.SUCCESS and manifest.covered_dates
                else (manifest.target_date,)
            )
            records.extend(
                DrawStateRecord(
                    draw_date=draw_date,
                    status=status_map[manifest.status],
                    run_id=manifest.run_id,
                    updated_at=manifest.completed_at,
                    detail=manifest.error_message,
                )
                for draw_date in covered_dates
            )
        return ControlState(records)

    def download_gold(self, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for key in self.store.list_keys('gold/latest/'):
            filename = key.removeprefix('gold/latest/')
            if not filename or '/' in filename:
                continue
            path = output_dir / filename
            path.write_bytes(self.store.get_bytes(key))
            paths.append(path)
        return sorted(paths)

    def _write_partitioned_parquet(
        self,
        dataframe: pd.DataFrame,
        *,
        dataset: str,
        business_key: list[str],
        merge_existing: bool,
    ) -> list[StoredObject]:
        if dataframe.empty:
            return []
        working = dataframe.copy()
        working['draw_date'] = pd.to_datetime(working['draw_date'])
        objects = []
        for (year, month), partition in working.groupby(
            [working['draw_date'].dt.year, working['draw_date'].dt.month], sort=True
        ):
            key = f'silver/{dataset}/year={year:04d}/month={month:02d}/{dataset}.parquet'
            merged = partition.copy()
            if merge_existing and self.store.exists(key):
                existing = _parquet_from_bytes(self.store.get_bytes(key))
                merged = pd.concat([existing, merged], ignore_index=True)
            merged = (
                merged.drop_duplicates(business_key, keep='last')
                .sort_values(business_key, kind='stable')
                .reset_index(drop=True)
            )
            objects.append(
                self.store.put_bytes(
                    key,
                    _parquet_bytes(merged),
                    content_type=content_type_for_key(key),
                    metadata={'dataset': dataset, 'year': str(year), 'month': f'{month:02d}'},
                )
            )
        return objects

    @staticmethod
    def _bronze_prefix(draw_date: date) -> str:
        return f'bronze/source=xoso/year={draw_date:%Y}/month={draw_date:%m}/date={draw_date.isoformat()}'


class SouthernDataLakeRepository(DataLakeRepository):
    """XSMN repository using the same lake contract in an independent store."""

    def __init__(self, store: ObjectStore, *, gold_cache_control: str) -> None:
        super().__init__(store, gold_cache_control=gold_cache_control, region=LotteryRegion.XSMN)

    def bronze_complete(self, draw_date: date) -> bool:
        prefix = self._bronze_prefix(draw_date)
        return all(
            self.store.exists(f'{prefix}/{filename}')
            for filename in ('response.html', 'parsed-results.json', 'metadata.json')
        )

    def bronze_objects(self, draw_date: date) -> list[StoredObject]:
        prefix = self._bronze_prefix(draw_date)
        filenames = ['response.html', 'parsed-results.json']
        fallback_key = f'{prefix}/fallback-response.html'
        if self.store.exists(fallback_key):
            filenames.append('fallback-response.html')
        filenames.append('metadata.json')
        return [self.store.head(f'{prefix}/{filename}') for filename in filenames]

    def load_bronze(self, draw_date: date) -> SouthernExtractedResult:
        prefix = self._bronze_prefix(draw_date)
        raw_response = self.store.get_bytes(f'{prefix}/response.html')
        result = SouthernDailyResult.model_validate_json(self.store.get_bytes(f'{prefix}/parsed-results.json'))
        metadata = json.loads(self.store.get_bytes(f'{prefix}/metadata.json'))
        fallback_url = metadata.get('fallback_source_url')
        fallback_response = (
            self.store.get_bytes(f'{prefix}/fallback-response.html') if isinstance(fallback_url, str) else None
        )
        return SouthernExtractedResult(
            raw_response=raw_response,
            result=result,
            fallback_response=fallback_response,
            fallback_url=fallback_url if isinstance(fallback_url, str) else None,
        )

    def write_bronze(
        self,
        extracted: SouthernExtractedResult,
        *,
        run_id: str,
        force: bool = False,
        fetched_at: datetime | None = None,
        source_lineage: str = 'live_source',
    ) -> list[StoredObject]:
        draw_date = extracted.result.draw_date
        prefix = self._bronze_prefix(draw_date)
        raw_sha256 = hashlib.sha256(extracted.raw_response).hexdigest()
        fallback_raw_sha256 = (
            hashlib.sha256(extracted.fallback_response).hexdigest() if extracted.fallback_response is not None else None
        )
        station_codes = [station.station_code for station in extracted.result.stations]
        metadata = {
            'run_id': run_id,
            'region': self.region.value,
            'draw_date': draw_date.isoformat(),
            'source_url': extracted.result.source_url,
            'source_lineage': source_lineage,
            'fetched_at': (fetched_at or datetime.now(UTC)).isoformat(),
            'raw_sha256': raw_sha256,
            'fallback_source_url': extracted.fallback_url,
            'fallback_raw_sha256': fallback_raw_sha256,
            'reconciliation': 'full_station_prize_comparison' if extracted.fallback_response is not None else None,
            'station_count': len(station_codes),
            'station_codes': station_codes,
        }
        keys = [f'{prefix}/response.html', f'{prefix}/parsed-results.json']
        payloads = [extracted.raw_response, _model_json_bytes(extracted.result)]
        if extracted.fallback_response is not None:
            keys.append(f'{prefix}/fallback-response.html')
            payloads.append(extracted.fallback_response)
        keys.append(f'{prefix}/metadata.json')
        payloads.append(_json_bytes(metadata))
        object_metadata = {
            'run-id': run_id,
            'region': self.region.value,
            'draw-date': draw_date.isoformat(),
        }
        stable_metadata = {
            'region': self.region.value,
            'draw_date': draw_date.isoformat(),
            'source_url': extracted.result.source_url,
            'source_lineage': source_lineage,
            'raw_sha256': raw_sha256,
            'fallback_source_url': extracted.fallback_url,
            'fallback_raw_sha256': fallback_raw_sha256,
            'station_codes': station_codes,
        }
        return [
            _put_immutable_bronze_object(
                self.store,
                key,
                payload,
                object_metadata=object_metadata,
                force=force,
                stable_json_fields=stable_metadata if key.endswith('/metadata.json') else None,
                conflict_message=f'XSMN Bronze object differs for {draw_date}: {key}; use --force to replace it',
            )
            for key, payload in zip(keys, payloads, strict=True)
        ]

    def upsert_silver_draw_results(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='draw-results',
            business_key=['draw_date', 'station_code', 'prize_group', 'prize_order'],
            merge_existing=True,
        )

    def replace_silver_draw_results(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='draw-results',
            business_key=['draw_date', 'station_code', 'prize_group', 'prize_order'],
            merge_existing=False,
        )

    def replace_silver_loto_daily(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='loto-daily',
            business_key=['draw_date', 'station_code', 'number_2d'],
            merge_existing=False,
        )

    def read_all_silver_draw_results(self) -> pd.DataFrame:
        keys = [key for key in self.store.list_keys('silver/draw-results/') if key.endswith('/draw-results.parquet')]
        if not keys:
            return pd.DataFrame()
        frames = [_parquet_from_bytes(self.store.get_bytes(key)) for key in keys]
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(['draw_date', 'station_code', 'prize_group', 'prize_order'], kind='stable')
            .reset_index(drop=True)
        )


def _parquet_bytes(dataframe: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    dataframe.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _parquet_from_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(BytesIO(data))


def _csv_bytes(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False, date_format='%Y-%m-%d', lineterminator='\n').encode('utf-8')


def _model_json_bytes(model) -> bytes:
    return f'{model.model_dump_json(indent=2)}\n'.encode('utf-8')


def _json_bytes(values: dict[str, object]) -> bytes:
    return f'{json.dumps(values, indent=2, sort_keys=True)}\n'.encode('utf-8')
