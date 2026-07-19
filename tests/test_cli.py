from __future__ import annotations

import json
from datetime import UTC, date, datetime

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
    assert (tmp_path / 'gold/latest/dim-station.parquet').is_file()


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
