"""Manifest-driven backup and restore for published Gold releases."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field

from xsmb_etl.control import ControlStatePointer, ControlStateSnapshot
from xsmb_etl.gold_keys import is_gold_table_key, legacy_snapshot_manifest_key, snapshot_manifest_key
from xsmb_etl.run_models import LatestManifest, LotteryRegion, RunManifest, RunStatus
from xsmb_etl.repository import CONSUMER_RECOVERY_MARKER_KEY
from xsmb_etl.storage import (
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    ObjectPreconditionFailedError,
    ObjectStore,
    StoredObject,
    content_type_for_key,
)


class RecoveryIntegrityError(RuntimeError):
    """Raised when source, backup, or restored bytes fail integrity checks."""


class BackupObjectReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_key: str
    backup_key: str
    size: int = Field(ge=0)
    sha256: str
    content_type: str
    cache_control: str | None = None


class BackupManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 2
    region: LotteryRegion
    boundary_id: str = Field(min_length=1)
    run_id: str
    dataset_version: str
    target_date: str
    boundary_updated_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    objects: tuple[BackupObjectReference, ...]


class RecoveryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: str
    region: LotteryRegion
    run_id: str
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    object_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    manifest_key: str


class ConsumerRecoveryMarker(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    mode: str = 'consumer_only_gold'
    region: LotteryRegion
    boundary_id: str
    run_id: str
    restored_at: datetime


def backup_latest_release(
    source: ObjectStore,
    backup: ObjectStore,
    *,
    region: LotteryRegion,
) -> RecoveryEvidence:
    """Copy one published release to a regional prefix in a backup store."""

    latest_key = 'manifests/latest.json'
    latest_bytes = source.get_bytes(latest_key)
    latest = LatestManifest.model_validate_json(latest_bytes)
    if latest.region is not region:
        raise RecoveryIntegrityError(f'latest manifest declares {latest.region.value}, expected {region.value}')

    source_keys, captured_payloads = _published_boundary_keys(source, latest)
    captured_payloads[latest_key] = latest_bytes
    boundary_id = _backup_boundary_id(latest, captured_payloads)
    boundary_updated_at = _boundary_updated_at(latest, captured_payloads)
    published_references = {reference.key: reference for reference in latest.objects}
    snapshot_key = (
        snapshot_manifest_key(latest.target_date, latest.run_id)
        if latest.schema_version >= 2
        else legacy_snapshot_manifest_key(latest.target_date)
    )
    references: list[BackupObjectReference] = []
    source_payloads: dict[str, bytes] = {}
    for source_key in source_keys:
        payload = captured_payloads.get(source_key)
        if payload is None:
            payload = source.get_bytes(source_key)
        source_payloads[source_key] = payload
        published_reference = published_references.get(source_key)
        if published_reference is not None:
            _require_payload_integrity(
                source_key,
                payload,
                published_reference.size,
                published_reference.sha256,
            )
            content_type = published_reference.content_type
            cache_control = published_reference.cache_control
        else:
            source_object = source.head(source_key)
            _require_payload_integrity(source_key, payload, source_object.size, source_object.sha256)
            content_type = source_object.content_type
            cache_control = source_object.cache_control
        if source_key in {snapshot_key, latest_key} and payload != latest_bytes:
            raise RecoveryIntegrityError(f'{source_key} does not match {latest_key}')
        if source_key == f'manifests/runs/run-id={latest.run_id}.json':
            run_manifest = RunManifest.model_validate_json(payload)
            run_gold = tuple(reference for reference in run_manifest.objects if is_gold_table_key(reference.key))
            if (
                run_manifest.run_id != latest.run_id
                or run_manifest.region is not region
                or run_manifest.target_date != latest.target_date
                or run_manifest.status is not RunStatus.SUCCESS
                or not run_manifest.quality_passed
                or sorted(_reference_signature(reference) for reference in run_gold)
                != sorted(_reference_signature(reference) for reference in latest.objects)
            ):
                raise RecoveryIntegrityError(f'{source_key} is not the successful published run')
        backup_key = _backup_object_key(region, boundary_id, source_key)
        stored = _put_immutable_or_verify(
            backup,
            backup_key,
            payload,
            content_type=content_type,
            cache_control=cache_control,
            metadata={
                'source-key': source_key,
                'run-id': latest.run_id,
                'boundary-id': boundary_id,
                'region': region.value,
            },
        )
        references.append(
            BackupObjectReference(
                source_key=source_key,
                backup_key=backup_key,
                size=stored.size,
                sha256=stored.sha256,
                content_type=stored.content_type,
                cache_control=stored.cache_control,
            )
        )

    if source.get_bytes(latest_key) != latest_bytes:
        raise RecoveryIntegrityError(f'{latest_key} changed during backup')
    control_payload = source_payloads.get('control/latest.json')
    if control_payload is not None and source.get_bytes('control/latest.json') != control_payload:
        raise RecoveryIntegrityError('control/latest.json changed during backup')
    if control_payload is None and source.exists('control/latest.json'):
        raise RecoveryIntegrityError('control/latest.json appeared during backup')
    for source_key, payload in captured_payloads.items():
        if source_key.startswith('control/versions/') and source.get_bytes(source_key) != payload:
            raise RecoveryIntegrityError(f'{source_key} changed during backup')

    manifest = BackupManifest(
        region=region,
        boundary_id=boundary_id,
        run_id=latest.run_id,
        dataset_version=latest.dataset_version,
        target_date=latest.target_date.isoformat(),
        boundary_updated_at=boundary_updated_at,
        created_at=boundary_updated_at,
        objects=tuple(references),
    )
    immutable_manifest_key = _backup_manifest_key(region, boundary_id)
    manifest_bytes = _json_bytes(manifest)
    _put_immutable_or_verify(
        backup,
        immutable_manifest_key,
        manifest_bytes,
        content_type=content_type_for_key(immutable_manifest_key),
        cache_control='no-cache',
        metadata={'run-id': latest.run_id, 'boundary-id': boundary_id, 'region': region.value},
    )
    _verify_backup_objects(backup, manifest)
    pointer_key = _backup_pointer_key(region)
    _publish_backup_pointer(backup, pointer_key, manifest, manifest_bytes)
    return RecoveryEvidence(
        operation='backup',
        region=region,
        run_id=latest.run_id,
        object_count=len(references),
        total_size_bytes=sum(reference.size for reference in references),
        manifest_key=immutable_manifest_key,
    )


def restore_latest_release(
    backup: ObjectStore,
    destination: ObjectStore,
    *,
    region: LotteryRegion,
) -> RecoveryEvidence:
    """Restore and verify a published boundary, swapping latest.json last."""

    pointer_key = _backup_pointer_key(region)
    pointer_payload = backup.get_bytes(pointer_key)
    manifest = BackupManifest.model_validate_json(pointer_payload)
    if manifest.region is not region:
        raise RecoveryIntegrityError(f'backup manifest declares {manifest.region.value}, expected {region.value}')
    immutable_manifest_key = _backup_manifest_key(region, manifest.boundary_id)
    if backup.get_bytes(immutable_manifest_key) != pointer_payload:
        raise RecoveryIntegrityError(f'{pointer_key} does not match {immutable_manifest_key}')
    _verify_backup_objects(backup, manifest)

    pointer_references: list[BackupObjectReference] = []
    for reference in manifest.objects:
        if reference.source_key in {'manifests/latest.json', 'control/latest.json'}:
            pointer_references.append(reference)
            continue
        _restore_reference(backup, destination, reference, overwrite=False)

    marker_payload = _json_bytes(
        ConsumerRecoveryMarker(
            region=region,
            boundary_id=manifest.boundary_id,
            run_id=manifest.run_id,
            restored_at=manifest.boundary_updated_at,
        )
    )
    destination.put_bytes(
        CONSUMER_RECOVERY_MARKER_KEY,
        marker_payload,
        content_type=content_type_for_key(CONSUMER_RECOVERY_MARKER_KEY),
        cache_control='no-cache',
        metadata={'run-id': manifest.run_id, 'boundary-id': manifest.boundary_id},
    )

    # ControlState and the consumer publication boundary are mutable pointers.
    # Publish them only after all immutable dependencies have been restored.
    for source_key in ('control/latest.json', 'manifests/latest.json'):
        reference = next((item for item in pointer_references if item.source_key == source_key), None)
        if reference is not None:
            _restore_reference(backup, destination, reference, overwrite=True)

    for reference in manifest.objects:
        restored = destination.get_bytes(reference.source_key)
        _require_payload_integrity(reference.source_key, restored, reference.size, reference.sha256)
        _require_stored_metadata(destination.head(reference.source_key), reference)

    return RecoveryEvidence(
        operation='restore',
        region=region,
        run_id=manifest.run_id,
        object_count=len(manifest.objects),
        total_size_bytes=sum(reference.size for reference in manifest.objects),
        manifest_key=pointer_key,
    )


def _published_boundary_keys(
    source: ObjectStore,
    latest: LatestManifest,
) -> tuple[tuple[str, ...], dict[str, bytes]]:
    run_manifest_key = f'manifests/runs/run-id={latest.run_id}.json'
    snapshot_key = (
        snapshot_manifest_key(latest.target_date, latest.run_id)
        if latest.schema_version >= 2
        else legacy_snapshot_manifest_key(latest.target_date)
    )
    keys = [reference.key for reference in latest.objects]
    keys.extend([run_manifest_key, snapshot_key, 'manifests/latest.json'])
    captured_payloads: dict[str, bytes] = {}

    if source.exists('control/latest.json'):
        control_pointer_bytes = source.get_bytes('control/latest.json')
        try:
            control_pointer = ControlStatePointer.model_validate_json(control_pointer_bytes)
        except (ValueError, TypeError) as exc:
            raise RecoveryIntegrityError('control/latest.json is invalid') from exc
        if control_pointer.region is not latest.region:
            raise RecoveryIntegrityError('control/latest.json declares the wrong region')
        if not source.exists(control_pointer.version_key):
            raise RecoveryIntegrityError(f'{control_pointer.version_key} is missing')
        version_payload = source.get_bytes(control_pointer.version_key)
        _require_payload_integrity(
            control_pointer.version_key,
            version_payload,
            control_pointer.size,
            control_pointer.sha256,
        )
        try:
            snapshot = ControlStateSnapshot.model_validate_json(version_payload)
        except (ValueError, TypeError) as exc:
            raise RecoveryIntegrityError(f'{control_pointer.version_key} is invalid') from exc
        if (
            snapshot.region is not latest.region
            or snapshot.revision != control_pointer.revision
            or len(snapshot.records) != control_pointer.record_count
            or snapshot.created_at != control_pointer.updated_at
        ):
            raise RecoveryIntegrityError(f'{control_pointer.version_key} does not match control/latest.json')
        if control_pointer.updated_at < latest.published_at:
            raise RecoveryIntegrityError(
                'control/latest.json has not caught up with the published Gold boundary; retry the backup'
            )
        keys.append('control/latest.json')
        keys.append(control_pointer.version_key)
        captured_payloads['control/latest.json'] = control_pointer_bytes
        captured_payloads[control_pointer.version_key] = version_payload
    return tuple(dict.fromkeys(keys)), captured_payloads


def _restore_reference(
    backup: ObjectStore,
    destination: ObjectStore,
    reference: BackupObjectReference,
    *,
    overwrite: bool,
) -> None:
    payload = backup.get_bytes(reference.backup_key)
    _require_payload_integrity(reference.backup_key, payload, reference.size, reference.sha256)
    if overwrite:
        destination.put_bytes(
            reference.source_key,
            payload,
            content_type=reference.content_type,
            cache_control=reference.cache_control,
            metadata={'restored-from': reference.backup_key},
        )
        return
    _put_immutable_or_verify(
        destination,
        reference.source_key,
        payload,
        content_type=reference.content_type,
        cache_control=reference.cache_control,
        metadata={'restored-from': reference.backup_key},
    )


def _verify_backup_objects(backup: ObjectStore, manifest: BackupManifest) -> None:
    for reference in manifest.objects:
        payload = backup.get_bytes(reference.backup_key)
        _require_payload_integrity(reference.backup_key, payload, reference.size, reference.sha256)


def _put_immutable_or_verify(
    store: ObjectStore,
    key: str,
    payload: bytes,
    *,
    content_type: str,
    cache_control: str | None,
    metadata: dict[str, str],
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
    except ObjectAlreadyExistsError:
        existing = store.get_bytes(key)
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        _require_payload_integrity(key, existing, len(payload), expected_sha256)
        stored = store.head(key)
        if (
            stored.content_type == content_type
            and stored.cache_control == cache_control
            and stored.sha256 == expected_sha256
        ):
            return stored
        return store.put_bytes(
            key,
            payload,
            content_type=content_type,
            cache_control=cache_control,
            metadata=metadata,
        )


def _require_payload_integrity(key: str, payload: bytes, expected_size: int, expected_sha256: str) -> None:
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if len(payload) != expected_size or (expected_sha256 and actual_sha256 != expected_sha256):
        raise RecoveryIntegrityError(f'integrity check failed for {key}')


def _reference_signature(reference) -> tuple[str, int, str, str, str | None]:
    return (
        reference.key,
        reference.size,
        reference.sha256,
        reference.content_type,
        reference.cache_control,
    )


def _require_stored_metadata(stored: StoredObject, expected: BackupObjectReference) -> None:
    if (
        stored.size != expected.size
        or stored.sha256 != expected.sha256
        or stored.content_type != expected.content_type
        or stored.cache_control != expected.cache_control
    ):
        raise RecoveryIntegrityError(f'metadata check failed for {expected.source_key}')


def _backup_boundary_id(latest: LatestManifest, captured_payloads: dict[str, bytes]) -> str:
    """Identify the exact mutable publication boundary, not only its Gold run."""

    digest = hashlib.sha256()
    for key in sorted(captured_payloads):
        payload = captured_payloads[key]
        digest.update(len(key).to_bytes(4, 'big'))
        digest.update(key.encode())
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)
    return f'{latest.run_id}-{digest.hexdigest()}'


def _boundary_updated_at(latest: LatestManifest, captured_payloads: dict[str, bytes]) -> datetime:
    pointer_payload = captured_payloads.get('control/latest.json')
    if pointer_payload is None:
        return latest.published_at
    pointer = ControlStatePointer.model_validate_json(pointer_payload)
    return max(latest.published_at, pointer.updated_at)


def _publish_backup_pointer(
    store: ObjectStore,
    key: str,
    manifest: BackupManifest,
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
                    metadata={
                        'run-id': manifest.run_id,
                        'boundary-id': manifest.boundary_id,
                        'region': manifest.region.value,
                    },
                    overwrite=False,
                )
            except ObjectAlreadyExistsError:
                continue
        else:
            if current_payload == payload:
                return store.head(key)
            current_manifest = BackupManifest.model_validate_json(current_payload)
            if current_manifest.region is not manifest.region:
                raise RecoveryIntegrityError(f'{key} declares the wrong region')
            current_order = (date.fromisoformat(current_manifest.target_date), current_manifest.boundary_updated_at)
            requested_order = (date.fromisoformat(manifest.target_date), manifest.boundary_updated_at)
            if current_order >= requested_order:
                raise RecoveryIntegrityError(
                    f'refusing to replace backup boundary {current_manifest.boundary_id} '
                    f'with non-newer boundary {manifest.boundary_id}'
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
                    metadata={
                        'run-id': manifest.run_id,
                        'boundary-id': manifest.boundary_id,
                        'region': manifest.region.value,
                    },
                    if_match=current.etag,
                )
            except ObjectPreconditionFailedError:
                continue
    raise RecoveryIntegrityError(f'{key} changed repeatedly; retry the backup')


def _backup_object_key(region: LotteryRegion, boundary_id: str, source_key: str) -> str:
    return f'regions/{region.value}/backups/boundary-id={boundary_id}/objects/{source_key}'


def _backup_manifest_key(region: LotteryRegion, boundary_id: str) -> str:
    return f'regions/{region.value}/backups/boundary-id={boundary_id}/manifest.json'


def _backup_pointer_key(region: LotteryRegion) -> str:
    return f'regions/{region.value}/backups/latest.json'


def _json_bytes(model: BaseModel) -> bytes:
    return f'{model.model_dump_json(indent=2)}\n'.encode()
