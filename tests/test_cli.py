from __future__ import annotations

import json

from xsmb_etl.cli import main
from xsmb_etl.run_models import RunManifest


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
