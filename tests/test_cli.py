from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from xsmb_etl import cli
from xsmb_etl.cli import main
from xsmb_etl.repository import CentralDataLakeRepository, DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import DataObjectReference, LotteryRegion, RunManifest, RunStatus, SourceLineage
from xsmb_etl.storage import LocalObjectStore


def test_cli_runs_fixture_pipeline_and_skips_repeat(tmp_path, capsys) -> None:
    fixture = 'tests/fixtures/valid-result-page.html'
    arguments = [
        'run',
        '--target-date',
        '2026-07-16',
        '--fixture',
        fixture,
        '--output',
        str(tmp_path),
    ]

    assert main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'success'
    manifest = RunManifest.model_validate_json(next((tmp_path / 'manifests/runs').glob('run-id=*.json')).read_bytes())
    assert manifest.source_lineage.value == 'test_fixture'
    assert main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['skipped'] is True


def test_cli_validates_fixture_without_creating_lake(tmp_path, capsys) -> None:
    assert (
        main(
            [
                'validate',
                '--target-date',
                '2026-07-16',
                '--fixture',
                'tests/fixtures/valid-result-page.html',
                '--output',
                str(tmp_path),
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert all(check['passed'] for check in report['checks'] if check['severity'] == 'critical')
    assert not list(tmp_path.iterdir())


def test_cli_backs_up_and_restores_published_release(tmp_path, capsys) -> None:
    source = tmp_path / 'source'
    backup = tmp_path / 'backup'
    recovered = tmp_path / 'recovered'
    assert (
        main(
            [
                'run',
                '--target-date',
                '2026-07-16',
                '--fixture',
                'tests/fixtures/valid-result-page.html',
                '--output',
                str(source),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                'backup-release',
                '--storage',
                'local',
                '--output',
                str(source),
                '--backup-output',
                str(backup),
            ]
        )
        == 0
    )
    backup_evidence = json.loads(capsys.readouterr().out)
    assert backup_evidence['operation'] == 'backup'

    assert (
        main(
            [
                'restore-release',
                '--storage',
                'local',
                '--output',
                str(recovered),
                '--backup-output',
                str(backup),
            ]
        )
        == 0
    )
    restore_evidence = json.loads(capsys.readouterr().out)
    assert restore_evidence['operation'] == 'restore'
    assert (recovered / 'manifests/latest.json').is_file()


def test_cli_runs_xsmn_fixture_in_its_own_lake(tmp_path, capsys) -> None:
    arguments = [
        'run',
        '--region',
        'xsmn',
        '--target-date',
        '2026-07-16',
        '--fixture',
        'tests/fixtures/valid-xsmn-result-page.html',
        '--output',
        str(tmp_path),
    ]

    assert main(arguments) == 0

    result = json.loads(capsys.readouterr().out)
    manifest = RunManifest.model_validate_json(next((tmp_path / 'manifests/runs').glob('run-id=*.json')).read_bytes())
    assert result['region'] == 'xsmn'
    assert result['status'] == 'success'
    assert manifest.region.value == 'xsmn'
    latest = SouthernDataLakeRepository(
        LocalObjectStore(tmp_path),
        gold_cache_control='public, max-age=300',
    ).latest_manifest()
    assert latest is not None
    station_key = next(reference.key for reference in latest.objects if reference.key.endswith('/dim-station.parquet'))
    assert (tmp_path / station_key).is_file()


def test_cli_validates_xsmn_fixture_without_creating_lake(tmp_path, capsys) -> None:
    assert (
        main(
            [
                'validate',
                '--region',
                'xsmn',
                '--target-date',
                '2026-07-16',
                '--fixture',
                'tests/fixtures/valid-xsmn-result-page.html',
                '--output',
                str(tmp_path),
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert all(check['passed'] for check in report['checks'] if check['severity'] == 'critical')
    assert not list(tmp_path.iterdir())


def test_cli_runs_and_validates_xsmt_fixture_in_its_own_lake(tmp_path, capsys) -> None:
    arguments = [
        'run',
        '--region',
        'xsmt',
        '--target-date',
        '2026-07-18',
        '--fixture',
        'tests/fixtures/valid-xsmt-result-page.html',
        '--output',
        str(tmp_path),
    ]

    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    manifest = RunManifest.model_validate_json(next((tmp_path / 'manifests/runs').glob('run-id=*.json')).read_bytes())
    assert result['region'] == 'xsmt'
    assert manifest.region.value == 'xsmt'

    validation_output = tmp_path / 'validation'
    assert (
        main(
            [
                'validate',
                '--region',
                'xsmt',
                '--target-date',
                '2026-07-18',
                '--fixture',
                'tests/fixtures/valid-xsmt-result-page.html',
                '--output',
                str(validation_output),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert all(check['passed'] for check in report['checks'] if check['severity'] == 'critical')
    assert not validation_output.exists()


def test_cli_region_all_reads_three_separate_local_lakes(tmp_path, capsys) -> None:
    fixtures = {
        'xsmb': 'tests/fixtures/valid-result-page.html',
        'xsmn': 'tests/fixtures/valid-xsmn-result-page.html',
        'xsmt': 'tests/fixtures/valid-xsmt-result-page.html',
    }
    for region, fixture in fixtures.items():
        target_date = '2026-07-18' if region == 'xsmt' else '2026-07-16'
        assert (
            main(
                [
                    'run',
                    '--region',
                    region,
                    '--target-date',
                    target_date,
                    '--fixture',
                    fixture,
                    '--output',
                    str(tmp_path / region),
                ]
            )
            == 0
        )
        capsys.readouterr()

    assert main(['validate', '--region', 'all', '--output', str(tmp_path)]) == 0

    reports = json.loads(capsys.readouterr().out)
    assert set(reports) == {'xsmb', 'xsmn', 'xsmt'}
    assert all(report['passed'] for report in reports.values())
    assert (tmp_path / 'xsmb/manifests/latest.json').is_file()
    assert (tmp_path / 'xsmn/manifests/latest.json').is_file()
    assert (tmp_path / 'xsmt/manifests/latest.json').is_file()


def test_cli_status_checks_all_lakes_without_reading_data_objects(tmp_path, capsys, monkeypatch) -> None:
    for region in LotteryRegion:
        _publish_minimal_lake(tmp_path / region.value, region)

    original_get_bytes = LocalObjectStore.get_bytes
    read_keys: list[str] = []

    def manifest_only_get_bytes(store, key: str) -> bytes:
        read_keys.append(key)
        if key.startswith(('silver/', 'gold/latest/')):
            raise AssertionError(f'status downloaded a data object: {key}')
        return original_get_bytes(store, key)

    def no_listing(*_args, **_kwargs):
        raise AssertionError('status listed the object store')

    monkeypatch.setattr(LocalObjectStore, 'get_bytes', manifest_only_get_bytes)
    monkeypatch.setattr(LocalObjectStore, 'list_keys', no_listing)

    assert main(['status', '--storage', 'local', '--region', 'all', '--output', str(tmp_path), '--json']) == 0
    statuses = json.loads(capsys.readouterr().out)
    assert set(statuses) == {'xsmb', 'xsmn', 'xsmt'}
    assert all(status['healthy'] for status in statuses.values())
    assert all(status['verified_object_count'] == 1 for status in statuses.values())
    assert not any(key.startswith(('silver/', 'gold/latest/')) for key in read_keys)


def test_cli_status_returns_unhealthy_for_corrupt_gold_metadata(tmp_path, capsys) -> None:
    store = _publish_minimal_lake(tmp_path, LotteryRegion.XSMB)
    store.put_bytes(
        'gold/latest/example.parquet',
        b'changed',
        content_type='application/vnd.apache.parquet',
        cache_control='public, max-age=60',
    )

    assert main(['status', '--storage', 'local', '--output', str(tmp_path), '--json']) == 1
    status = json.loads(capsys.readouterr().out)
    assert status['healthy'] is False
    assert status['verified_object_count'] == 0
    assert 'SHA-256 does not match manifest' in status['issues'][0]


def test_cli_status_returns_unhealthy_for_unreadable_local_metadata(tmp_path, capsys) -> None:
    store = _publish_minimal_lake(tmp_path, LotteryRegion.XSMB)
    metadata_path = store.metadata_root / 'gold/latest/example.parquet.json'
    metadata_path.write_text('{not-json', encoding='utf-8')

    assert main(['status', '--storage', 'local', '--output', str(tmp_path), '--json']) == 1
    status = json.loads(capsys.readouterr().out)
    assert status['healthy'] is False
    assert status['verified_object_count'] == 0
    assert 'metadata cannot be read' in status['issues'][0]


def test_cli_status_requires_latest_objects_to_match_successful_run_manifest(tmp_path, capsys) -> None:
    store = _publish_minimal_lake(tmp_path, LotteryRegion.XSMB)
    run_key = 'manifests/runs/run-id=xsmb-run.json'
    run = RunManifest.model_validate_json(store.get_bytes(run_key))
    mismatched_reference = run.objects[0].model_copy(update={'sha256': '0' * 64})
    mismatched_run = run.model_copy(update={'objects': (mismatched_reference,)})
    store.put_bytes(
        run_key,
        f'{mismatched_run.model_dump_json(indent=2)}\n'.encode(),
        content_type='application/json',
    )

    assert main(['status', '--storage', 'local', '--output', str(tmp_path), '--json']) == 1
    status = json.loads(capsys.readouterr().out)
    assert status['healthy'] is False
    assert status['verified_object_count'] == 1
    assert status['issues'] == [
        'latest Gold objects do not match the successful run manifest (metadata differs: gold/latest/example.parquet)'
    ]


def test_cli_status_reports_missing_publication_in_text_mode(tmp_path, capsys) -> None:
    assert main(['status', '--storage', 'local', '--output', str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert 'XSMB  UNHEALTHY' in output
    assert 'manifests/latest.json is missing' in output


class _FakeHistoryAuditReport:
    def __init__(
        self,
        region: LotteryRegion,
        *,
        healthy: bool,
        from_date: date,
        to_date: date,
    ) -> None:
        self.region = region
        self.healthy = healthy
        self.run_id = f'{region.value}-run'
        self.dataset_version = 'gold-v1'
        self.manifest_target_date = to_date
        self.from_date = from_date
        self.to_date = to_date
        self.latest_completed_date = to_date
        self.fact_row_count = 100
        self.loto_row_count = 50
        self.station_count = 1
        self.status_counts = {'success': 2}
        self.issues = ()

    def model_dump(self, *, mode: str):
        assert mode == 'json'
        return {
            'region': self.region.value,
            'healthy': self.healthy,
            'run_id': self.run_id,
            'dataset_version': self.dataset_version,
            'manifest_target_date': self.manifest_target_date.isoformat(),
            'from_date': self.from_date.isoformat(),
            'to_date': self.to_date.isoformat(),
            'latest_completed_date': self.latest_completed_date.isoformat(),
            'fact_row_count': self.fact_row_count,
            'loto_row_count': self.loto_row_count,
            'station_count': self.station_count,
            'status_counts': self.status_counts,
            'issues': [],
        }

    def model_dump_json(self, *, indent: int) -> str:
        return json.dumps(self.model_dump(mode='json'), indent=indent)


def test_cli_audit_history_routes_all_regions_and_returns_findings(tmp_path, capsys, monkeypatch) -> None:
    repositories: list[tuple[str | None, LotteryRegion, object]] = []
    calls: list[tuple[LotteryRegion, date | None, date | None]] = []

    def fake_repository(_settings, storage, region, *, output=None):
        repositories.append((storage, region, output))
        return SimpleNamespace(region=region)

    def fake_audit_history(repository, *, from_date=None, to_date=None):
        calls.append((repository.region, from_date, to_date))
        return _FakeHistoryAuditReport(
            repository.region,
            healthy=repository.region is not LotteryRegion.XSMT,
            from_date=from_date,
            to_date=to_date,
        )

    monkeypatch.setattr(cli, '_repository', fake_repository)
    monkeypatch.setattr(cli, 'audit_history', fake_audit_history)

    assert (
        main(
            [
                'audit-history',
                '--storage',
                'local',
                '--region',
                'all',
                '--output',
                str(tmp_path),
                '--from',
                '2026-07-01',
                '--to',
                '2026-07-20',
                '--json',
            ]
        )
        == 1
    )

    reports = json.loads(capsys.readouterr().out)
    assert set(reports) == {'xsmb', 'xsmn', 'xsmt'}
    assert reports['xsmt']['healthy'] is False
    assert repositories == [
        ('local', LotteryRegion.XSMB, tmp_path / 'xsmb'),
        ('local', LotteryRegion.XSMN, tmp_path / 'xsmn'),
        ('local', LotteryRegion.XSMT, tmp_path / 'xsmt'),
    ]
    assert calls == [
        (LotteryRegion.XSMB, date(2026, 7, 1), date(2026, 7, 20)),
        (LotteryRegion.XSMN, date(2026, 7, 1), date(2026, 7, 20)),
        (LotteryRegion.XSMT, date(2026, 7, 1), date(2026, 7, 20)),
    ]


def test_cli_audit_history_emits_one_report_object_for_one_region(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, '_repository_for_args', lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        cli,
        'audit_history',
        lambda *_args, **_kwargs: _FakeHistoryAuditReport(
            LotteryRegion.XSMN,
            healthy=True,
            from_date=date(2026, 1, 1),
            to_date=date(2026, 7, 20),
        ),
    )

    assert main(['audit-history', '--region', 'xsmn', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['region'] == 'xsmn'
    assert 'xsmn' not in report


def test_cli_audit_history_prints_concise_text_findings(capsys, monkeypatch) -> None:
    report = _FakeHistoryAuditReport(
        LotteryRegion.XSMT,
        healthy=False,
        from_date=date(2021, 1, 1),
        to_date=date(2026, 7, 20),
    )
    report.issues = (
        SimpleNamespace(
            severity='critical',
            code='missing_draw_dates',
            message='2 expected draw dates are missing',
        ),
    )
    monkeypatch.setattr(cli, '_repository_for_args', lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(cli, 'audit_history', lambda *_args, **_kwargs: report)

    assert main(['audit-history', '--region', 'xsmt']) == 1
    output = capsys.readouterr().out
    assert 'XSMT  FINDINGS' in output
    assert 'range: 2021-01-01 to 2026-07-20' in output
    assert 'rows: fact=100; loto=50; stations=1' in output
    assert 'CRITICAL missing_draw_dates: 2 expected draw dates are missing' in output


def test_cli_audit_history_rejects_reversed_range_before_loading_settings(capsys, monkeypatch) -> None:
    def unexpected_settings():
        raise AssertionError('Settings must not be loaded for an invalid date range')

    monkeypatch.setattr(cli, 'Settings', unexpected_settings)

    with pytest.raises(SystemExit) as error:
        main(['audit-history', '--from', '2026-07-20', '--to', '2026-07-19'])

    assert error.value.code == 2
    assert '--to must be on or after --from' in capsys.readouterr().err


@pytest.mark.parametrize(
    ('arguments', 'invalid_region'),
    [
        (['--region', 'xsmt', '--to', '2017-12-31'], 'xsmt'),
        (['--region', 'all', '--to', '2017-12-31'], 'xsmt'),
        (['--region', 'all', '--from', '9999-12-31'], 'xsmb'),
    ],
)
def test_cli_audit_history_rejects_reversed_resolved_range_before_loading_settings(
    arguments,
    invalid_region,
    capsys,
    monkeypatch,
) -> None:
    def unexpected_settings():
        raise AssertionError('Settings must not be loaded for an invalid resolved range')

    monkeypatch.setattr(cli, 'Settings', unexpected_settings)

    with pytest.raises(SystemExit) as error:
        main(['audit-history', *arguments])

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert '--to must be on or after --from' in stderr
    assert f'for {invalid_region}' in stderr


def test_cli_audit_history_converts_core_range_error_to_argparse_exit(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, '_repository_for_args', lambda *_args, **_kwargs: SimpleNamespace())

    def reject_range(*_args, **_kwargs):
        raise ValueError('to_date must be on or after from_date')

    monkeypatch.setattr(cli, 'audit_history', reject_range)

    with pytest.raises(SystemExit) as error:
        main(['audit-history', '--from', '2026-07-01', '--to', '2026-07-20'])

    assert error.value.code == 2
    assert '--to must be on or after --from' in capsys.readouterr().err


def test_cli_audit_history_does_not_mask_unrelated_storage_errors(monkeypatch) -> None:
    monkeypatch.setattr(cli, '_repository_for_args', lambda *_args, **_kwargs: SimpleNamespace())
    storage_error = RuntimeError('storage unavailable')

    def fail_storage(*_args, **_kwargs):
        raise storage_error

    monkeypatch.setattr(cli, 'audit_history', fail_storage)

    with pytest.raises(RuntimeError) as error:
        main(['audit-history', '--from', '2026-07-01', '--to', '2026-07-20'])

    assert error.value is storage_error


def test_cli_audit_history_integrates_with_a_local_gold_publication(tmp_path, capsys) -> None:
    assert (
        main(
            [
                'run',
                '--target-date',
                '2026-07-16',
                '--fixture',
                'tests/fixtures/valid-result-page.html',
                '--output',
                str(tmp_path),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                'audit-history',
                '--storage',
                'local',
                '--output',
                str(tmp_path),
                '--from',
                '2026-07-16',
                '--to',
                '2026-07-16',
                '--json',
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report['region'] == 'xsmb'
    assert report['healthy'] is True
    assert report['fact_row_count'] == 27
    assert report['status_counts']['success'] == 1


def _publish_minimal_lake(root, region: LotteryRegion) -> LocalObjectStore:
    store = LocalObjectStore(root)
    if region is LotteryRegion.XSMT:
        repository = CentralDataLakeRepository(store, gold_cache_control='public, max-age=60')
    elif region is LotteryRegion.XSMN:
        repository = SouthernDataLakeRepository(store, gold_cache_control='public, max-age=60')
    else:
        repository = DataLakeRepository(store, gold_cache_control='public, max-age=60')

    gold = store.put_bytes(
        'gold/latest/example.parquet',
        b'healthy',
        content_type='application/vnd.apache.parquet',
        cache_control='public, max-age=60',
    )
    now = datetime(2026, 7, 19, tzinfo=UTC)
    run = RunManifest(
        run_id=f'{region.value}-run',
        region=region,
        target_date=date(2026, 7, 18),
        status=RunStatus.SUCCESS,
        source_lineage=SourceLineage.DERIVED_REBUILD,
        started_at=now,
        completed_at=now,
        quality_passed=True,
        covered_dates=(date(2026, 7, 18),),
        objects=(DataObjectReference.from_stored(gold),),
    )
    repository.write_run_manifest(run)
    repository.publish_snapshot_and_latest(
        run_id=run.run_id,
        target_date=run.target_date,
        gold_objects=[gold],
    )
    return store
