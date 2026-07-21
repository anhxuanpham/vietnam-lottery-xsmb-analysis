"""On-demand CSV exports decoupled from the daily Parquet publication."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import PurePosixPath
from typing import Protocol

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from xsmb_etl.gold_keys import gold_filename, gold_release_prefix, is_gold_table_key
from xsmb_etl.run_models import DataObjectReference, LatestManifest, LotteryRegion
from xsmb_etl.storage import (
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    ObjectPreconditionFailedError,
    ObjectStore,
    StoredObject,
    content_type_for_key,
)


class CsvExportIntegrityError(RuntimeError):
    pass


class CsvExportManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1)
    region: LotteryRegion
    run_id: str
    dataset_version: str
    target_date: date
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    objects: tuple[DataObjectReference, ...]

    @model_validator(mode='after')
    def validate_export(self) -> CsvExportManifest:
        if self.schema_version != 1:
            raise ValueError(f'unsupported CSV export schema_version: {self.schema_version}')
        if self.dataset_version != self.run_id:
            raise ValueError('CSV dataset_version must equal run_id')
        expected_prefix = f'exports/csv/run-id={self.run_id}/'
        keys = [reference.key for reference in self.objects]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError('CSV export must contain non-duplicate objects')
        if any(not key.startswith(expected_prefix) or not key.endswith('.csv') for key in keys):
            raise ValueError('CSV export objects must belong to the declared run')
        return self


class GoldRepository(Protocol):
    store: ObjectStore
    region: LotteryRegion
    gold_cache_control: str

    def latest_manifest(self) -> LatestManifest | None: ...


def export_latest_gold_csv(repository: GoldRepository) -> CsvExportManifest:
    """Materialize verified CSV files for the current immutable Gold release."""

    latest = repository.latest_manifest()
    if latest is None:
        raise ValueError(f'{repository.region.value.upper()} has no published Gold release')
    if latest.region is not repository.region:
        raise CsvExportIntegrityError(
            f'latest manifest declares {latest.region.value}, expected {repository.region.value}'
        )
    if latest.dataset_version != latest.run_id:
        raise CsvExportIntegrityError('latest manifest dataset_version does not match run_id')
    if latest.schema_version >= 2:
        expected_prefix = gold_release_prefix(latest.run_id)
        if latest.release_prefix != expected_prefix:
            raise CsvExportIntegrityError('latest manifest release_prefix does not match run_id')
        if any(not reference.key.startswith(expected_prefix) for reference in latest.objects):
            raise CsvExportIntegrityError('latest manifest mixes objects outside its immutable release')
    if any(not is_gold_table_key(reference.key) for reference in latest.objects):
        raise CsvExportIntegrityError('latest manifest references a non-Gold object')

    objects: list[StoredObject] = []
    output_filenames: set[str] = set()
    for reference in latest.objects:
        if not reference.key.endswith('.parquet'):
            continue
        payload = repository.store.get_bytes(reference.key)
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != reference.size or digest != reference.sha256:
            raise CsvExportIntegrityError(f'manifest integrity check failed for {reference.key}')
        dataframe = pd.read_parquet(BytesIO(payload))
        logical_filename = gold_filename(reference.key)
        if logical_filename is None:
            raise CsvExportIntegrityError(f'invalid Gold object key: {reference.key}')
        filename = f'{PurePosixPath(logical_filename).stem}.csv'
        if filename in output_filenames:
            raise CsvExportIntegrityError(f'duplicate CSV output filename: {filename}')
        output_filenames.add(filename)
        key = f'exports/csv/run-id={latest.run_id}/{filename}'
        csv_payload = dataframe.to_csv(
            index=False,
            date_format='%Y-%m-%d',
            lineterminator='\n',
        ).encode('utf-8')
        objects.append(
            _put_immutable_or_verify(
                repository.store,
                key,
                csv_payload,
                cache_control=repository.gold_cache_control,
                metadata={
                    'run-id': latest.run_id,
                    'dataset-version': latest.dataset_version,
                    'source-key': reference.key,
                },
            )
        )

    if not objects:
        raise ValueError('published Gold release contains no Parquet tables')
    manifest = CsvExportManifest(
        region=repository.region,
        run_id=latest.run_id,
        dataset_version=latest.dataset_version,
        target_date=latest.target_date,
        published_at=latest.published_at,
        objects=tuple(DataObjectReference.from_stored(item) for item in objects),
    )
    manifest_bytes = f'{manifest.model_dump_json(indent=2)}\n'.encode()
    immutable_manifest_key = f'exports/csv/run-id={latest.run_id}/manifest.json'
    _put_immutable_or_verify(
        repository.store,
        immutable_manifest_key,
        manifest_bytes,
        cache_control='no-cache',
        metadata={'run-id': latest.run_id},
    )
    confirmed_latest = repository.latest_manifest()
    if confirmed_latest != latest:
        raise CsvExportIntegrityError('Gold latest changed during CSV export; retry against the new release')
    latest_key = 'exports/csv/latest.json'
    _publish_csv_pointer(repository.store, latest_key, manifest, manifest_bytes)
    return manifest


def _put_immutable_or_verify(
    store: ObjectStore,
    key: str,
    payload: bytes,
    *,
    cache_control: str,
    metadata: dict[str, str],
) -> StoredObject:
    try:
        return store.put_bytes(
            key,
            payload,
            content_type=content_type_for_key(key),
            cache_control=cache_control,
            metadata=metadata,
            overwrite=False,
        )
    except ObjectAlreadyExistsError as exc:
        if store.get_bytes(key) != payload:
            raise CsvExportIntegrityError(f'immutable CSV export differs: {key}') from exc
        return store.head(key)


def _publish_csv_pointer(
    store: ObjectStore,
    key: str,
    manifest: CsvExportManifest,
    payload: bytes,
) -> StoredObject:
    for _attempt in range(8):
        try:
            current_payload = store.get_bytes(key)
        except ObjectNotFoundError:
            try:
                return store.put_bytes(
                    key,
                    payload,
                    content_type=content_type_for_key(key),
                    cache_control='no-cache',
                    metadata={'run-id': manifest.run_id},
                    overwrite=False,
                )
            except ObjectAlreadyExistsError:
                continue
        else:
            if current_payload == payload:
                return store.head(key)
            current_manifest = CsvExportManifest.model_validate_json(current_payload)
            current_order = (current_manifest.target_date, current_manifest.published_at)
            requested_order = (manifest.target_date, manifest.published_at)
            if requested_order <= current_order:
                raise CsvExportIntegrityError(
                    f'refusing to replace CSV export {current_manifest.run_id} with non-newer {manifest.run_id}'
                )
            current = store.head(key)
            if current.sha256 != hashlib.sha256(current_payload).hexdigest() or current.etag is None:
                continue
            try:
                return store.put_bytes(
                    key,
                    payload,
                    content_type=content_type_for_key(key),
                    cache_control='no-cache',
                    metadata={'run-id': manifest.run_id},
                    if_match=current.etag,
                )
            except ObjectPreconditionFailedError:
                continue
    raise CsvExportIntegrityError(f'{key} changed repeatedly; retry the export')
