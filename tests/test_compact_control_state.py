from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from xsmb_etl.control import ControlStatePointer, ControlStateSnapshot, DrawStatus
from xsmb_etl.repository import ControlStateIntegrityError, DataLakeRepository
from xsmb_etl.run_models import LotteryRegion, RunManifest, RunStatus, SourceLineage
from xsmb_etl.storage import LocalObjectStore


def _repository(store: LocalObjectStore, *, region: LotteryRegion = LotteryRegion.XSMB) -> DataLakeRepository:
    return DataLakeRepository(store, gold_cache_control='no-cache', region=region)


def _manifest(
    run_id: str,
    target_date: date,
    status: RunStatus,
    completed_at: datetime,
    *,
    region: LotteryRegion = LotteryRegion.XSMB,
    covered_dates: tuple[date, ...] = (),
    forced: bool = False,
    error_message: str | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        region=region,
        target_date=target_date,
        status=status,
        source_lineage=SourceLineage.LIVE_SOURCE,
        started_at=completed_at - timedelta(minutes=1),
        completed_at=completed_at,
        forced=forced,
        quality_passed=status is RunStatus.SUCCESS,
        covered_dates=covered_dates,
        error_message=error_message,
    )


def _write_legacy_manifest(store: LocalObjectStore, manifest: RunManifest) -> None:
    key = f'manifests/runs/run-id={manifest.run_id}.json'
    store.put_bytes(
        key,
        f'{manifest.model_dump_json(indent=2)}\n'.encode(),
        content_type='application/json; charset=utf-8',
    )


def _pointer(store: LocalObjectStore) -> ControlStatePointer:
    return ControlStatePointer.model_validate_json(store.get_bytes('control/latest.json'))


def test_bootstrap_materializes_legacy_manifests_once_with_latest_wins(tmp_path, monkeypatch) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    started = datetime(2026, 7, 20, tzinfo=UTC)
    first = date(2026, 7, 17)
    second = date(2026, 7, 18)
    third = date(2026, 7, 19)

    _write_legacy_manifest(
        store,
        _manifest(
            'legacy-success',
            third,
            RunStatus.SUCCESS,
            started,
            covered_dates=(first, second, third),
        ),
    )
    _write_legacy_manifest(
        store,
        _manifest('legacy-failed', second, RunStatus.FAILED, started + timedelta(hours=1), error_message='retry me'),
    )
    _write_legacy_manifest(
        store,
        _manifest('legacy-no-draw', third, RunStatus.NO_DRAW, started + timedelta(hours=2)),
    )
    _write_legacy_manifest(
        store,
        _manifest('legacy-forced', second, RunStatus.SUCCESS, started + timedelta(hours=3), forced=True),
    )

    state = repository.control_state()

    assert state.status_for(first) is DrawStatus.SUCCESS
    assert state.status_for(second) is DrawStatus.SUCCESS
    assert state.record_for(second).run_id == 'legacy-forced'
    assert state.status_for(third) is DrawStatus.NO_DRAW
    assert state.status_for(date(2026, 7, 16)) is DrawStatus.MISSING
    assert state.pending_dates(first, third) == []
    assert state.pending_dates(first, third, force=True) == [first, second, third]

    pointer = _pointer(store)
    snapshot = ControlStateSnapshot.model_validate_json(store.get_bytes(pointer.version_key))
    assert snapshot.parent_revision is None
    assert pointer.record_count == 3

    reads: list[str] = []
    original_get_bytes = store.get_bytes

    def tracked_get_bytes(key: str) -> bytes:
        reads.append(key)
        return original_get_bytes(key)

    def no_listing(_prefix: str = '') -> list[str]:
        raise AssertionError('steady-state control_state must not list run manifests')

    monkeypatch.setattr(store, 'get_bytes', tracked_get_bytes)
    monkeypatch.setattr(store, 'list_keys', no_listing)

    reloaded = repository.control_state()

    assert reloaded.records == state.records
    assert reads == ['control/latest.json', pointer.version_key]


def test_manifest_writes_publish_immutable_versions_and_preserve_latest_event(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    target = date(2026, 7, 20)
    started = datetime(2026, 7, 20, tzinfo=UTC)

    repository.write_run_manifest(_manifest('failed', target, RunStatus.FAILED, started, error_message='source down'))
    failed_pointer = _pointer(store)
    failed_payload = store.get_bytes(failed_pointer.version_key)

    repository.write_run_manifest(
        _manifest('forced-success', target, RunStatus.SUCCESS, started + timedelta(hours=2), forced=True)
    )
    success_pointer = _pointer(store)

    assert success_pointer.revision != failed_pointer.revision
    assert store.get_bytes(failed_pointer.version_key) == failed_payload
    assert (
        ControlStateSnapshot.model_validate_json(store.get_bytes(success_pointer.version_key)).parent_revision
        == failed_pointer.revision
    )
    assert repository.control_state().status_for(target) is DrawStatus.SUCCESS

    repository.write_run_manifest(_manifest('older-no-draw', target, RunStatus.NO_DRAW, started + timedelta(hours=1)))

    state = repository.control_state()
    assert state.status_for(target) is DrawStatus.SUCCESS
    assert state.record_for(target).run_id == 'forced-success'


def test_success_manifest_updates_every_covered_date_in_one_snapshot(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    covered_dates = (date(2026, 7, 17), date(2026, 7, 18), date(2026, 7, 19))
    completed_at = datetime(2026, 7, 20, tzinfo=UTC)

    repository.write_run_manifest(
        _manifest(
            'historical-success',
            covered_dates[-1],
            RunStatus.SUCCESS,
            completed_at,
            covered_dates=covered_dates,
        )
    )

    state = repository.control_state()
    assert tuple(record.draw_date for record in state.records) == covered_dates
    assert all(record.status is DrawStatus.SUCCESS for record in state.records)
    assert all(record.run_id == 'historical-success' for record in state.records)


class PointerFailingStore(LocalObjectStore):
    def __init__(self, root) -> None:
        super().__init__(root)
        self.fail_pointer_write = False

    def put_bytes(self, key, data, **kwargs):
        if key == 'control/latest.json' and self.fail_pointer_write:
            raise RuntimeError('injected pointer write failure')
        return super().put_bytes(key, data, **kwargs)


def test_crash_before_pointer_publish_leaves_previous_state_usable(tmp_path) -> None:
    store = PointerFailingStore(tmp_path)
    repository = _repository(store)
    target = date(2026, 7, 20)
    started = datetime(2026, 7, 20, tzinfo=UTC)
    repository.write_run_manifest(_manifest('success', target, RunStatus.SUCCESS, started))
    previous_pointer_payload = store.get_bytes('control/latest.json')
    previous_versions = store.list_keys('control/versions/')

    store.fail_pointer_write = True
    with pytest.raises(RuntimeError, match='injected pointer write failure'):
        repository.write_run_manifest(
            _manifest('newer-failed', target, RunStatus.FAILED, started + timedelta(hours=1), error_message='boom')
        )
    store.fail_pointer_write = False

    assert store.get_bytes('control/latest.json') == previous_pointer_payload
    assert repository.control_state().status_for(target) is DrawStatus.SUCCESS
    assert len(store.list_keys('control/versions/')) == len(previous_versions) + 1

    repository.write_run_manifest(
        _manifest('newer-failed', target, RunStatus.FAILED, started + timedelta(hours=1), error_message='boom')
    )
    assert repository.control_state().status_for(target) is DrawStatus.FAILED


def test_control_state_rejects_pointer_to_corrupt_snapshot(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    repository.control_state()
    pointer = _pointer(store)
    store.put_bytes(
        pointer.version_key,
        b'{}',
        content_type='application/json; charset=utf-8',
    )

    with pytest.raises(ControlStateIntegrityError, match='size'):
        repository.control_state()


def test_manifest_region_must_match_repository_region(tmp_path) -> None:
    repository = _repository(LocalObjectStore(tmp_path), region=LotteryRegion.XSMT)
    manifest = _manifest(
        'wrong-region',
        date(2026, 7, 20),
        RunStatus.SUCCESS,
        datetime(2026, 7, 20, tzinfo=UTC),
        region=LotteryRegion.XSMB,
    )

    with pytest.raises(ValueError, match='does not match repository region xsmt'):
        repository.write_run_manifest(manifest)

    assert not repository.store.exists('manifests/runs/run-id=wrong-region.json')
    assert not repository.store.exists('control/latest.json')


def test_control_pointer_json_is_small_and_contains_no_draw_records(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    repository.write_run_manifest(
        _manifest(
            'no-draw',
            date(2026, 7, 20),
            RunStatus.NO_DRAW,
            datetime(2026, 7, 20, tzinfo=UTC),
        )
    )

    pointer_document = json.loads(store.get_bytes('control/latest.json'))
    assert 'records' not in pointer_document
    assert pointer_document['record_count'] == 1
    assert len(store.get_bytes('control/latest.json')) < 1_000


def test_concurrent_control_writers_retry_and_preserve_both_updates(tmp_path) -> None:
    class RacingStore(LocalObjectStore):
        injected = False
        callback = None

        def put_bytes(self, key, data, **kwargs):
            if key == 'control/latest.json' and kwargs.get('if_match') and not self.injected:
                self.injected = True
                assert self.callback is not None
                self.callback()
            return super().put_bytes(key, data, **kwargs)

    store = RacingStore(tmp_path)
    first_repository = _repository(store)
    second_repository = _repository(store)
    first_repository.control_state()
    started = datetime(2026, 7, 20, tzinfo=UTC)
    second_manifest = _manifest('second', date(2026, 7, 21), RunStatus.SUCCESS, started + timedelta(minutes=2))
    store.callback = lambda: second_repository.write_run_manifest(second_manifest)

    first_repository.write_run_manifest(
        _manifest('first', date(2026, 7, 20), RunStatus.SUCCESS, started + timedelta(minutes=1))
    )

    state = first_repository.control_state()
    assert state.status_for(date(2026, 7, 20)) is DrawStatus.SUCCESS
    assert state.status_for(date(2026, 7, 21)) is DrawStatus.SUCCESS
    assert _pointer(store).updated_at > second_manifest.completed_at


def test_stale_manifest_cannot_move_control_pointer_updated_at_backwards(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    repository = _repository(store)
    newer_completed_at = datetime(2026, 7, 22, tzinfo=UTC)
    repository.write_run_manifest(_manifest('newer', date(2026, 7, 22), RunStatus.SUCCESS, newer_completed_at))
    newer_pointer = _pointer(store)

    repository.write_run_manifest(
        _manifest(
            'stale-but-new-date',
            date(2026, 7, 21),
            RunStatus.SUCCESS,
            newer_completed_at - timedelta(days=1),
        )
    )
    advanced_pointer = _pointer(store)

    assert advanced_pointer.revision != newer_pointer.revision
    assert advanced_pointer.updated_at > newer_pointer.updated_at

    repository.write_run_manifest(
        _manifest(
            'stale-no-op',
            date(2026, 7, 22),
            RunStatus.FAILED,
            newer_completed_at - timedelta(days=2),
            error_message='older failure',
        )
    )

    assert _pointer(store) == advanced_pointer
