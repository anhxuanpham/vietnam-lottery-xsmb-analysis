"""Bronze, Silver, Gold, quality, and manifest repository operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd

from xsmb_etl.control import (
    ControlState,
    ControlStatePointer,
    ControlStateSnapshot,
    DrawStateRecord,
    DrawStatus,
)
from xsmb_etl.extract import ExtractedResult
from xsmb_etl.gold_keys import (
    LEGACY_GOLD_PREFIX,
    gold_filename,
    gold_object_key,
    gold_release_prefix,
    legacy_snapshot_manifest_key,
    snapshot_manifest_key,
)
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
    ObjectNotFoundError,
    ObjectPreconditionFailedError,
    ObjectStore,
    StoredObject,
    content_type_for_key,
)
from xsmb_etl.xsmn_extract import SouthernExtractedResult
from xsmb_etl.xsmn_models import SouthernDailyResult


class ImmutableObjectConflictError(RuntimeError):
    pass


class BronzeConflictError(ImmutableObjectConflictError):
    pass


class ControlStateIntegrityError(RuntimeError):
    pass


class LatestPublicationConflictError(RuntimeError):
    pass


class ConsumerRecoveryLakeError(RuntimeError):
    pass


CONSUMER_RECOVERY_MARKER_KEY = 'recovery/consumer-only.json'


def _put_immutable_object(
    store: ObjectStore,
    key: str,
    payload: bytes,
    *,
    content_type: str,
    cache_control: str | None,
    metadata: dict[str, str],
    conflict_message: str,
) -> StoredObject:
    try:
        return store.put_bytes(
            key,
            payload,
            content_type=content_type,
            cache_control=cache_control,
            metadata=metadata,
            overwrite=False,
        )
    except ObjectAlreadyExistsError as exc:
        if store.get_bytes(key) != payload:
            raise ImmutableObjectConflictError(conflict_message) from exc
        return store.head(key)


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

    def require_etl_writable(self) -> None:
        if self.store.exists(CONSUMER_RECOVERY_MARKER_KEY):
            raise ConsumerRecoveryLakeError(
                'this is a consumer-only Gold recovery lake; restore Bronze and Silver from a full source backup '
                'before running ETL'
            )

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

    def upsert_silver_loto_daily(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        """Update only the touched monthly Loto partitions for a daily run."""

        return self._write_partitioned_parquet(
            dataframe,
            dataset='loto-daily',
            business_key=['draw_date', 'number_2d'],
            merge_existing=True,
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

    def write_gold_tables(
        self,
        tables: dict[str, pd.DataFrame],
        *,
        run_id: str,
        formats: tuple[str, ...] = ('parquet', 'csv'),
    ) -> list[StoredObject]:
        """Write one immutable Gold release.

        Daily pipelines request Parquet only to keep transfer volume small. CSV
        remains available as an explicit export format for compatibility jobs.
        """

        invalid_formats = set(formats) - {'parquet', 'csv'}
        if invalid_formats or len(formats) != len(set(formats)):
            raise ValueError('Gold formats must be unique values from: parquet, csv')
        if not formats:
            raise ValueError('at least one Gold format is required')

        objects: list[StoredObject] = []
        for table_name, dataframe in tables.items():
            metadata = {'run-id': run_id, 'dataset-version': run_id}
            payloads = {
                'parquet': lambda: _parquet_bytes(dataframe),
                'csv': lambda: _csv_bytes(dataframe),
            }
            for output_format in formats:
                key = gold_object_key(run_id, table_name, output_format)
                payload = payloads[output_format]()
                objects.append(
                    _put_immutable_object(
                        self.store,
                        key,
                        payload,
                        content_type=content_type_for_key(key),
                        cache_control=self.gold_cache_control,
                        metadata=metadata,
                        conflict_message=f'immutable Gold release object differs: {key}',
                    )
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

    def write_run_manifest(self, manifest: RunManifest, *, update_control: bool = True) -> StoredObject:
        if manifest.region is not self.region:
            raise ValueError(
                f'run manifest region {manifest.region.value} does not match repository region {self.region.value}'
            )

        key = f'manifests/runs/run-id={manifest.run_id}.json'
        stored = _put_immutable_object(
            self.store,
            key,
            _model_json_bytes(manifest),
            content_type=content_type_for_key(key),
            cache_control='no-cache',
            metadata={'run-id': manifest.run_id, 'status': manifest.status.value},
            conflict_message=f'run manifest already exists with different content: {key}',
        )
        if update_control:
            self.publish_manifest_control_state(manifest)
        return stored

    def publish_manifest_control_state(self, manifest: RunManifest) -> ControlStatePointer:
        """Publish manifest outcomes after their consumer boundary is durable."""

        if manifest.region is not self.region:
            raise ValueError(
                f'run manifest region {manifest.region.value} does not match repository region {self.region.value}'
            )
        records = self._records_for_manifest(manifest)
        for _attempt in range(8):
            current_state, current_pointer = self._load_or_bootstrap_control_state()
            updated_state = current_state.with_records(records)
            if updated_state.records == current_state.records:
                return current_pointer
            pointer_updated_at = max(
                manifest.completed_at,
                current_pointer.updated_at + timedelta(microseconds=1),
            )
            try:
                return self._publish_control_state(
                    updated_state,
                    parent_revision=current_pointer.revision,
                    created_at=pointer_updated_at,
                    expected_pointer=current_pointer,
                )
            except ObjectPreconditionFailedError:
                continue
        raise ControlStateIntegrityError('control-state pointer changed repeatedly; retry the operation')

    def publish_snapshot_and_latest(
        self,
        *,
        run_id: str,
        target_date: date,
        gold_objects: list[StoredObject],
        published_at: datetime | None = None,
    ) -> tuple[StoredObject, StoredObject]:
        references = tuple(DataObjectReference.from_stored(item) for item in gold_objects)
        if not references:
            raise ValueError('latest manifest must reference at least one Gold table object')

        release_prefix = gold_release_prefix(run_id)
        is_versioned_release = all(reference.key.startswith(release_prefix) for reference in references)
        is_legacy_release = all(reference.key.startswith(LEGACY_GOLD_PREFIX) for reference in references)
        if not is_versioned_release and not is_legacy_release:
            raise ValueError(
                'latest manifest references must all belong to the current immutable release '
                f'{release_prefix} or all use the legacy {LEGACY_GOLD_PREFIX} prefix'
            )
        manifest = LatestManifest(
            schema_version=2 if is_versioned_release else 1,
            run_id=run_id,
            region=self.region,
            dataset_version=run_id,
            target_date=target_date,
            published_at=published_at or datetime.now(UTC),
            release_prefix=release_prefix if is_versioned_release else None,
            objects=references,
        )
        snapshot_key = (
            snapshot_manifest_key(target_date, run_id)
            if is_versioned_release
            else legacy_snapshot_manifest_key(target_date)
        )
        try:
            snapshot = _put_immutable_object(
                self.store,
                snapshot_key,
                _model_json_bytes(manifest),
                content_type=content_type_for_key(snapshot_key),
                cache_control='no-cache',
                metadata={'run-id': run_id},
                conflict_message=f'immutable publication snapshot differs: {snapshot_key}',
            )
        except ImmutableObjectConflictError:
            existing = LatestManifest.model_validate_json(self.store.get_bytes(snapshot_key))
            if _publication_identity(existing) != _publication_identity(manifest):
                raise
            manifest = existing
            snapshot = self.store.head(snapshot_key)
        latest_key = 'manifests/latest.json'
        latest_payload = _model_json_bytes(manifest)
        latest: StoredObject | None = None
        for _attempt in range(8):
            try:
                current_payload = self.store.get_bytes(latest_key)
            except ObjectNotFoundError:
                try:
                    latest = self.store.put_bytes(
                        latest_key,
                        latest_payload,
                        content_type=content_type_for_key(latest_key),
                        cache_control='no-cache',
                        metadata={'run-id': run_id},
                        overwrite=False,
                    )
                    break
                except ObjectAlreadyExistsError:
                    continue
                except Exception as publish_error:
                    try:
                        committed = self.store.get_bytes(latest_key) == latest_payload
                        latest = self.store.head(latest_key) if committed else None
                    except Exception:
                        committed = False
                    if committed and latest is not None:
                        break
                    raise publish_error

            if current_payload == latest_payload:
                latest = self.store.head(latest_key)
                break
            current_manifest = LatestManifest.model_validate_json(current_payload)
            current_order = (current_manifest.target_date, current_manifest.published_at)
            requested_order = (manifest.target_date, manifest.published_at)
            if requested_order < current_order or (
                requested_order == current_order and current_manifest.run_id != manifest.run_id
            ):
                raise LatestPublicationConflictError(
                    f'refusing to replace newer publication {current_manifest.run_id} '
                    f'({current_manifest.target_date}) with {manifest.run_id} ({manifest.target_date})'
                )
            current = self.store.head(latest_key)
            if current.sha256 != hashlib.sha256(current_payload).hexdigest() or current.etag is None:
                continue
            try:
                latest = self.store.put_bytes(
                    latest_key,
                    latest_payload,
                    content_type=content_type_for_key(latest_key),
                    cache_control='no-cache',
                    metadata={'run-id': run_id},
                    if_match=current.etag,
                )
                break
            except ObjectPreconditionFailedError:
                continue
            except Exception as publish_error:
                try:
                    committed = self.store.get_bytes(latest_key) == latest_payload
                    latest = self.store.head(latest_key) if committed else None
                except Exception:
                    committed = False
                if committed and latest is not None:
                    break
                raise publish_error
        if latest is None:
            raise LatestPublicationConflictError('latest publication pointer changed repeatedly; retry the operation')
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
        state, _ = self._load_or_bootstrap_control_state()
        return state

    def _load_or_bootstrap_control_state(self) -> tuple[ControlState, ControlStatePointer]:
        latest_key = 'control/latest.json'
        try:
            pointer_payload = self.store.get_bytes(latest_key)
        except ObjectNotFoundError:
            return self._bootstrap_control_state()

        try:
            pointer = ControlStatePointer.model_validate_json(pointer_payload)
        except (ValueError, TypeError) as exc:
            raise ControlStateIntegrityError(f'{latest_key} is invalid') from exc
        if pointer.region is not self.region:
            raise ControlStateIntegrityError(
                f'{latest_key} declares region {pointer.region.value}, expected {self.region.value}'
            )

        try:
            version_payload = self.store.get_bytes(pointer.version_key)
        except ObjectNotFoundError as exc:
            raise ControlStateIntegrityError(f'{pointer.version_key} is missing') from exc
        if len(version_payload) != pointer.size:
            raise ControlStateIntegrityError(
                f'{pointer.version_key} size {len(version_payload)} does not match pointer {pointer.size}'
            )
        checksum = hashlib.sha256(version_payload).hexdigest()
        if checksum != pointer.sha256:
            raise ControlStateIntegrityError(f'{pointer.version_key} SHA-256 does not match control/latest.json')

        try:
            snapshot = ControlStateSnapshot.model_validate_json(version_payload)
        except (ValueError, TypeError) as exc:
            raise ControlStateIntegrityError(f'{pointer.version_key} is invalid') from exc
        if snapshot.region is not self.region:
            raise ControlStateIntegrityError(
                f'{pointer.version_key} declares region {snapshot.region.value}, expected {self.region.value}'
            )
        if snapshot.revision != pointer.revision:
            raise ControlStateIntegrityError(f'{pointer.version_key} revision does not match control/latest.json')
        if len(snapshot.records) != pointer.record_count:
            raise ControlStateIntegrityError(f'{pointer.version_key} record count does not match control/latest.json')
        return snapshot.to_state(), pointer

    def _bootstrap_control_state(self) -> tuple[ControlState, ControlStatePointer]:
        state = self._legacy_control_state()
        created_at = max(
            (record.updated_at for record in state.records),
            default=datetime.now(UTC),
        )
        try:
            pointer = self._publish_control_state(
                state,
                parent_revision=None,
                created_at=created_at,
                create_pointer=True,
            )
            return state, pointer
        except ObjectAlreadyExistsError:
            # Another writer completed the one-time bootstrap first.
            return self._load_or_bootstrap_control_state()

    def _legacy_control_state(self) -> ControlState:
        records = [record for manifest in self.run_manifests() for record in self._records_for_manifest(manifest)]
        return ControlState(records)

    def _publish_control_state(
        self,
        state: ControlState,
        *,
        parent_revision: str | None,
        created_at: datetime,
        expected_pointer: ControlStatePointer | None = None,
        create_pointer: bool = False,
    ) -> ControlStatePointer:
        revision = str(uuid4())
        version_key = f'control/versions/{revision}.json'
        snapshot = ControlStateSnapshot(
            region=self.region,
            revision=revision,
            parent_revision=parent_revision,
            created_at=created_at,
            records=state.records,
        )
        payload = _model_json_bytes(snapshot)
        try:
            version = self.store.put_bytes(
                version_key,
                payload,
                content_type=content_type_for_key(version_key),
                cache_control='no-cache',
                metadata={'revision': revision, 'region': self.region.value},
                overwrite=False,
            )
        except ObjectAlreadyExistsError as exc:
            existing = self.store.get_bytes(version_key)
            if existing != payload:
                raise ControlStateIntegrityError(f'control-state revision collision: {revision}') from exc
            version = self.store.head(version_key)

        pointer = ControlStatePointer(
            region=self.region,
            revision=revision,
            version_key=version_key,
            size=version.size,
            sha256=version.sha256,
            record_count=len(state.records),
            updated_at=created_at,
        )
        latest_key = 'control/latest.json'
        pointer_payload = _model_json_bytes(pointer)
        expected_etag: str | None = None
        if expected_pointer is not None:
            expected_payload = _model_json_bytes(expected_pointer)
            current = self.store.head(latest_key)
            if current.sha256 != hashlib.sha256(expected_payload).hexdigest() or current.etag is None:
                raise ObjectPreconditionFailedError(f'object precondition failed: {latest_key}')
            expected_etag = current.etag
        try:
            self.store.put_bytes(
                latest_key,
                pointer_payload,
                content_type=content_type_for_key(latest_key),
                cache_control='no-cache',
                metadata={'revision': revision, 'region': self.region.value},
                overwrite=not create_pointer,
                if_match=expected_etag,
            )
        except Exception as publish_error:
            try:
                committed = self.store.get_bytes(latest_key) == pointer_payload
            except Exception:
                committed = False
            if not committed:
                raise publish_error
        return pointer

    @staticmethod
    def _records_for_manifest(manifest: RunManifest) -> tuple[DrawStateRecord, ...]:
        status_map = {
            RunStatus.SUCCESS: DrawStatus.SUCCESS,
            RunStatus.FAILED: DrawStatus.FAILED,
            RunStatus.NO_DRAW: DrawStatus.NO_DRAW,
        }
        covered_dates = (
            manifest.covered_dates
            if manifest.status is RunStatus.SUCCESS and manifest.covered_dates
            else (manifest.target_date,)
        )
        return tuple(
            DrawStateRecord(
                draw_date=draw_date,
                status=status_map[manifest.status],
                run_id=manifest.run_id,
                updated_at=manifest.completed_at,
                detail=manifest.error_message,
            )
            for draw_date in covered_dates
        )

    def download_gold(self, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        latest = self.latest_manifest()
        if latest is None:
            return []
        paths: list[Path] = []
        seen_filenames: set[str] = set()
        for reference in latest.objects:
            filename = gold_filename(reference.key)
            if filename is None:
                continue
            if filename in seen_filenames:
                raise ValueError(f'latest manifest contains duplicate Gold filename: {filename}')
            seen_filenames.add(filename)
            path = output_dir / filename
            path.write_bytes(self.store.get_bytes(reference.key))
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
    """Station-grain XSMN/XSMT repository using an independent store."""

    def __init__(
        self,
        store: ObjectStore,
        *,
        gold_cache_control: str,
        region: LotteryRegion = LotteryRegion.XSMN,
    ) -> None:
        if region not in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
            raise ValueError('station-grain repository region must be xsmn or xsmt')
        super().__init__(store, gold_cache_control=gold_cache_control, region=region)

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
        recovery_candidate_raw_sha256 = hashlib.sha256(extracted.raw_response).hexdigest()
        partial_recovery = False
        if not force:
            extracted, partial_recovery = self._recover_equivalent_partial_bronze(extracted)

        draw_date = extracted.result.draw_date
        prefix = self._bronze_prefix(draw_date)
        raw_sha256 = hashlib.sha256(extracted.raw_response).hexdigest()
        fallback_raw_sha256 = (
            hashlib.sha256(extracted.fallback_response).hexdigest() if extracted.fallback_response is not None else None
        )
        station_codes = [station.station_code for station in extracted.result.stations]
        observation_time = fetched_at or datetime.now(UTC)
        metadata = {
            'run_id': run_id,
            'region': self.region.value,
            'draw_date': draw_date.isoformat(),
            'source_url': extracted.result.source_url,
            'source_lineage': source_lineage,
            'fetched_at': None if partial_recovery else observation_time.isoformat(),
            'recovered_at': observation_time.isoformat() if partial_recovery else None,
            'partial_recovery': 'canonical_result_match' if partial_recovery else None,
            'recovery_candidate_raw_sha256': recovery_candidate_raw_sha256 if partial_recovery else None,
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
            'partial_recovery': 'canonical_result_match' if partial_recovery else None,
            'recovery_candidate_raw_sha256': recovery_candidate_raw_sha256 if partial_recovery else None,
        }
        return [
            _put_immutable_bronze_object(
                self.store,
                key,
                payload,
                object_metadata=object_metadata,
                force=force,
                stable_json_fields=stable_metadata if key.endswith('/metadata.json') else None,
                conflict_message=(
                    f'{self.region.value.upper()} Bronze object differs for {draw_date}: '
                    f'{key}; use --force to replace it'
                ),
            )
            for key, payload in zip(keys, payloads, strict=True)
        ]

    def _recover_equivalent_partial_bronze(
        self,
        extracted: SouthernExtractedResult,
    ) -> tuple[SouthernExtractedResult, bool]:
        """Reuse immutable partial artifacts only when their canonical result matches."""

        prefix = self._bronze_prefix(extracted.result.draw_date)
        response_key = f'{prefix}/response.html'
        parsed_key = f'{prefix}/parsed-results.json'
        metadata_key = f'{prefix}/metadata.json'
        if self.store.exists(metadata_key) or not all(self.store.exists(key) for key in (response_key, parsed_key)):
            return extracted, False

        try:
            existing_result = SouthernDailyResult.model_validate_json(self.store.get_bytes(parsed_key))
        except ValueError:
            return extracted, False
        if existing_result != extracted.result:
            return extracted, False

        fallback_key = f'{prefix}/fallback-response.html'
        existing_fallback = self.store.exists(fallback_key)
        if existing_fallback and extracted.fallback_response is None:
            return extracted, False
        fallback_response = self.store.get_bytes(fallback_key) if existing_fallback else extracted.fallback_response
        return (
            SouthernExtractedResult(
                raw_response=self.store.get_bytes(response_key),
                result=existing_result,
                fallback_response=fallback_response,
                fallback_url=extracted.fallback_url,
            ),
            True,
        )

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

    def upsert_silver_loto_daily(self, dataframe: pd.DataFrame) -> list[StoredObject]:
        return self._write_partitioned_parquet(
            dataframe,
            dataset='loto-daily',
            business_key=['draw_date', 'station_code', 'number_2d'],
            merge_existing=True,
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


class CentralDataLakeRepository(SouthernDataLakeRepository):
    """XSMT repository with its own object store and publication manifest."""

    def __init__(self, store: ObjectStore, *, gold_cache_control: str) -> None:
        super().__init__(
            store,
            gold_cache_control=gold_cache_control,
            region=LotteryRegion.XSMT,
        )


def _parquet_bytes(dataframe: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    dataframe.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _parquet_from_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(BytesIO(data))


def _csv_bytes(dataframe: pd.DataFrame) -> bytes:
    return dataframe.to_csv(index=False, date_format='%Y-%m-%d', lineterminator='\n').encode('utf-8')


def _publication_identity(manifest: LatestManifest) -> tuple[object, ...]:
    return (
        manifest.schema_version,
        manifest.run_id,
        manifest.region,
        manifest.dataset_version,
        manifest.target_date,
        manifest.release_prefix,
        manifest.objects,
    )


def _model_json_bytes(model) -> bytes:
    return f'{model.model_dump_json(indent=2)}\n'.encode('utf-8')


def _json_bytes(values: dict[str, object]) -> bytes:
    return f'{json.dumps(values, indent=2, sort_keys=True)}\n'.encode('utf-8')
