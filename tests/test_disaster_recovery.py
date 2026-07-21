from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest

from xsmb_etl.disaster_recovery import (
    BackupManifest,
    RecoveryIntegrityError,
    backup_latest_release,
    restore_latest_release,
)
from xsmb_etl.control import ControlStatePointer, ControlStateSnapshot, DrawStateRecord, DrawStatus
from xsmb_etl.gold_keys import gold_object_key, gold_release_prefix, snapshot_manifest_key
from xsmb_etl.repository import ConsumerRecoveryLakeError, DataLakeRepository
from xsmb_etl.run_models import (
    DataObjectReference,
    LatestManifest,
    LotteryRegion,
    RunManifest,
    RunStatus,
    SourceLineage,
)
from xsmb_etl.storage import LocalObjectStore


def _publish_release(
    source: LocalObjectStore,
    *,
    run_id: str,
    target_date: date,
    payload: bytes,
    published_at: datetime | None = None,
) -> None:
    gold_key = gold_object_key(run_id, 'fact-draw-result')
    gold = source.put_bytes(
        gold_key,
        payload,
        content_type='application/vnd.apache.parquet',
        cache_control='public, max-age=300',
        overwrite=False,
    )
    now = published_at or datetime(2026, 7, 21, tzinfo=UTC)
    run = RunManifest(
        run_id=run_id,
        target_date=target_date,
        status=RunStatus.SUCCESS,
        source_lineage=SourceLineage.DERIVED_REBUILD,
        started_at=now,
        completed_at=now,
        quality_passed=True,
        objects=(DataObjectReference.from_stored(gold),),
    )
    latest = LatestManifest(
        schema_version=2,
        run_id=run_id,
        dataset_version=run_id,
        target_date=target_date,
        release_prefix=gold_release_prefix(run_id),
        published_at=now,
        objects=(DataObjectReference.from_stored(gold),),
    )
    run_key = f'manifests/runs/run-id={run_id}.json'
    snapshot_key = snapshot_manifest_key(target_date, run_id)
    source.put_bytes(run_key, f'{run.model_dump_json(indent=2)}\n'.encode(), content_type='application/json')
    source.put_bytes(snapshot_key, f'{latest.model_dump_json(indent=2)}\n'.encode(), content_type='application/json')
    source.put_bytes(
        'manifests/latest.json', f'{latest.model_dump_json(indent=2)}\n'.encode(), content_type='application/json'
    )


def _published_source(tmp_path) -> LocalObjectStore:
    source = LocalObjectStore(tmp_path / 'source')
    _publish_release(
        source,
        run_id='run-1',
        target_date=date(2026, 7, 21),
        payload=b'parquet bytes',
    )
    return source


def _publish_control(
    source: LocalObjectStore,
    *,
    revision: str,
    target_date: date,
    status: DrawStatus,
    updated_at: datetime,
) -> None:
    snapshot = ControlStateSnapshot(
        region=LotteryRegion.XSMB,
        revision=revision,
        created_at=updated_at,
        records=(
            DrawStateRecord(
                draw_date=target_date,
                status=status,
                run_id=f'{revision}-run',
                updated_at=updated_at,
            ),
        ),
    )
    version_key = f'control/versions/{revision}.json'
    payload = f'{snapshot.model_dump_json(indent=2)}\n'.encode()
    source.put_bytes(version_key, payload, content_type='application/json')
    pointer = ControlStatePointer(
        region=LotteryRegion.XSMB,
        revision=revision,
        version_key=version_key,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        record_count=1,
        updated_at=updated_at,
    )
    source.put_bytes(
        'control/latest.json',
        f'{pointer.model_dump_json(indent=2)}\n'.encode(),
        content_type='application/json',
    )


def test_backup_and_restore_latest_release_with_checksum_evidence(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    recovered = LocalObjectStore(tmp_path / 'recovered')

    backup_evidence = backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    repeated_backup = backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    restore_evidence = restore_latest_release(backup, recovered, region=LotteryRegion.XSMB)

    assert backup_evidence.operation == 'backup'
    assert repeated_backup.run_id == backup_evidence.run_id
    assert restore_evidence.operation == 'restore'
    assert backup_evidence.object_count == restore_evidence.object_count == 4
    assert recovered.get_bytes('manifests/latest.json') == source.get_bytes('manifests/latest.json')
    assert recovered.get_bytes(gold_object_key('run-1', 'fact-draw-result')) == b'parquet bytes'


def test_restore_rejects_corrupt_backup_object(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    manifest = BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json'))
    gold_reference = next(
        reference
        for reference in manifest.objects
        if reference.source_key == gold_object_key('run-1', 'fact-draw-result')
    )
    backup.put_bytes(
        gold_reference.backup_key,
        b'corrupt',
        content_type='application/vnd.apache.parquet',
    )

    with pytest.raises(RecoveryIntegrityError):
        restore_latest_release(backup, LocalObjectStore(tmp_path / 'recovered'), region=LotteryRegion.XSMB)


def test_backup_advances_without_overwriting_the_previous_release(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    first_manifest = BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json'))
    first_release_key = next(
        reference.backup_key
        for reference in first_manifest.objects
        if reference.source_key == gold_object_key('run-1', 'fact-draw-result')
    )
    first_payload = backup.get_bytes(first_release_key)

    _publish_release(
        source,
        run_id='run-2',
        target_date=date(2026, 7, 22),
        payload=b'next parquet bytes',
    )
    evidence = backup_latest_release(source, backup, region=LotteryRegion.XSMB)

    assert evidence.run_id == 'run-2'
    assert backup.get_bytes(first_release_key) == first_payload


def test_backup_boundary_advances_when_control_changes_without_a_new_gold_run(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    _publish_control(
        source,
        revision='revision-1',
        target_date=date(2026, 7, 21),
        status=DrawStatus.SUCCESS,
        updated_at=datetime(2026, 7, 21, 0, tzinfo=UTC),
    )

    first = backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    first_manifest = BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json'))
    _publish_control(
        source,
        revision='revision-2',
        target_date=date(2026, 7, 22),
        status=DrawStatus.NO_DRAW,
        updated_at=datetime(2026, 7, 21, 1, tzinfo=UTC),
    )
    second = backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    second_manifest = BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json'))

    assert first.run_id == second.run_id == 'run-1'
    assert first.manifest_key != second.manifest_key
    assert first_manifest.boundary_id != second_manifest.boundary_id
    assert backup.exists(first.manifest_key)
    assert backup.exists(second.manifest_key)


def test_backup_rejects_transient_gold_control_boundary_then_accepts_aligned_boundary(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    first_published_at = datetime(2026, 7, 21, tzinfo=UTC)
    second_published_at = datetime(2026, 7, 22, tzinfo=UTC)
    _publish_control(
        source,
        revision='revision-1',
        target_date=date(2026, 7, 21),
        status=DrawStatus.SUCCESS,
        updated_at=first_published_at,
    )
    first = backup_latest_release(source, backup, region=LotteryRegion.XSMB)

    _publish_release(
        source,
        run_id='run-2',
        target_date=date(2026, 7, 22),
        payload=b'next parquet bytes',
        published_at=second_published_at,
    )
    with pytest.raises(RecoveryIntegrityError, match='has not caught up'):
        backup_latest_release(source, backup, region=LotteryRegion.XSMB)

    assert (
        BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json')).boundary_id
        in first.manifest_key
    )

    _publish_control(
        source,
        revision='revision-2',
        target_date=date(2026, 7, 22),
        status=DrawStatus.SUCCESS,
        updated_at=second_published_at,
    )
    aligned = backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    latest_backup = BackupManifest.model_validate_json(backup.get_bytes('regions/xsmb/backups/latest.json'))

    assert aligned.run_id == 'run-2'
    assert latest_backup.boundary_updated_at == second_published_at
    assert latest_backup.boundary_id in aligned.manifest_key


def test_backup_rejects_gold_bytes_that_no_longer_match_publication_manifest(tmp_path) -> None:
    source = _published_source(tmp_path)
    source.put_bytes(
        gold_object_key('run-1', 'fact-draw-result'),
        b'changed after publication',
        content_type='application/vnd.apache.parquet',
    )

    with pytest.raises(RecoveryIntegrityError):
        backup_latest_release(source, LocalObjectStore(tmp_path / 'backup'), region=LotteryRegion.XSMB)


def test_backup_rejects_a_malformed_control_pointer(tmp_path) -> None:
    source = _published_source(tmp_path)
    source.put_bytes('control/latest.json', b'{}', content_type='application/json')

    with pytest.raises(RecoveryIntegrityError, match='control/latest.json is invalid'):
        backup_latest_release(source, LocalObjectStore(tmp_path / 'backup'), region=LotteryRegion.XSMB)


def test_restore_requires_backup_pointer_to_match_immutable_manifest(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    pointer_key = 'regions/xsmb/backups/latest.json'
    backup.put_bytes(
        pointer_key,
        backup.get_bytes(pointer_key) + b'\n',
        content_type='application/json',
    )

    with pytest.raises(RecoveryIntegrityError, match='does not match'):
        restore_latest_release(backup, LocalObjectStore(tmp_path / 'recovered'), region=LotteryRegion.XSMB)


def test_restore_repairs_metadata_for_matching_preexisting_bytes(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    recovered = LocalObjectStore(tmp_path / 'recovered')
    backup_latest_release(source, backup, region=LotteryRegion.XSMB)
    gold_key = gold_object_key('run-1', 'fact-draw-result')
    recovered.put_bytes(gold_key, b'parquet bytes', content_type='application/octet-stream')

    restore_latest_release(backup, recovered, region=LotteryRegion.XSMB)

    restored = recovered.head(gold_key)
    assert restored.content_type == 'application/vnd.apache.parquet'
    assert restored.cache_control == 'public, max-age=300'


def test_restored_consumer_boundary_blocks_etl_until_full_lake_recovery(tmp_path) -> None:
    source = _published_source(tmp_path)
    backup = LocalObjectStore(tmp_path / 'backup')
    recovered = LocalObjectStore(tmp_path / 'recovered')
    backup_latest_release(source, backup, region=LotteryRegion.XSMB)

    restore_latest_release(backup, recovered, region=LotteryRegion.XSMB)

    repository = DataLakeRepository(recovered, gold_cache_control='no-cache')
    with pytest.raises(ConsumerRecoveryLakeError, match='consumer-only Gold recovery'):
        repository.require_etl_writable()
