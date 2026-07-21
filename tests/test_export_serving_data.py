from __future__ import annotations

from datetime import UTC, date, datetime
import importlib.util
import json
from pathlib import Path

import pytest

from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult
from xsmb_etl.repository import CentralDataLakeRepository, DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.transform import draw_results_frame
from xsmb_etl.xsmn_extract import parse_southern_result_page
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_transform import southern_draw_results_frame


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    'export_serving_data', PROJECT_ROOT / 'scripts' / 'export_serving_data.py'
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(EXPORTER)
FACT_DRAW_RESULT_KEY = EXPORTER.FACT_DRAW_RESULT_KEY
ServingDataError = EXPORTER.ServingDataError
build_serving_payload = EXPORTER.build_serving_payload
build_serving_v2_bundle = EXPORTER.build_serving_v2_bundle
main = EXPORTER.main
serving_v2_shard_key = EXPORTER.serving_v2_shard_key
write_serving_v2_bundle = EXPORTER.write_serving_v2_bundle

FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'
GENERATED_AT = datetime(2026, 7, 19, 6, 30, tzinfo=UTC)


def _publish(repository, tables, *, run_id: str, target_date: date) -> None:
    objects = repository.write_gold_tables(tables, run_id=run_id)
    repository.publish_snapshot_and_latest(
        run_id=run_id,
        target_date=target_date,
        gold_objects=objects,
    )


def _xsmb_repository(tmp_path, grouped_prize_values) -> DataLakeRepository:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    first = LotteryResult.from_prize_groups(
        date(2026, 7, 18),
        'https://example.test/18',
        grouped_prize_values,
    )
    second = first.model_copy(update={'draw_date': date(2026, 7, 19), 'source_url': 'https://example.test/19'})
    fact = draw_results_frame([first, second], 'run-xsmb')
    _publish(
        repository,
        build_gold_tables(fact, run_id='run-xsmb'),
        run_id='run-xsmb',
        target_date=second.draw_date,
    )
    return repository


def test_build_serving_payload_exports_xsmb_without_rebuilding_gold(tmp_path, grouped_prize_values) -> None:
    repository = _xsmb_repository(tmp_path, grouped_prize_values)

    payload = build_serving_payload(
        repository,
        LotteryRegion.XSMB,
        recent_draws_per_station=1,
        generated_at=GENERATED_AT,
    )

    assert payload['schemaVersion'] == 1
    assert payload['region'] == 'xsmb'
    assert payload['generatedAt'] == '2026-07-19T06:30:00Z'
    assert payload['manifest']['runId'] == 'run-xsmb'
    assert payload['freshness']['matchesManifestTarget'] is True
    assert payload['range'] == {'from': '2026-07-18', 'to': '2026-07-19'}
    assert payload['drawCount'] == 2
    assert payload['resultCount'] == 54
    assert len(payload['draws']) == 1
    assert payload['latest']['results'][0]['specialPrize'] == '96763'
    assert payload['latest']['results'][0]['specialTail'] == '63'
    assert sum(payload['fullFrequency'].values()) == 54
    assert payload['stations'][0]['code'] == 'xsmb'
    assert sum(payload['stations'][0]['fullFrequency'].values()) == 54


@pytest.mark.parametrize(
    ('region', 'repository_class'),
    [
        (LotteryRegion.XSMN, SouthernDataLakeRepository),
        (LotteryRegion.XSMT, CentralDataLakeRepository),
    ],
)
def test_build_serving_payload_exports_station_metadata_and_recent_draws(tmp_path, region, repository_class) -> None:
    repository = repository_class(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    result = parse_southern_result_page(
        FIXTURE.read_bytes(),
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )
    fact = southern_draw_results_frame([result], 'run-xsmn')
    _publish(
        repository,
        build_southern_gold_tables(fact, run_id='run-xsmn'),
        run_id='run-xsmn',
        target_date=result.draw_date,
    )

    payload = build_serving_payload(
        repository,
        region,
        generated_at=GENERATED_AT,
    )

    assert payload['region'] == region.value
    assert payload['drawCount'] == 3
    assert payload['resultCount'] == 54
    assert len(payload['draws']) == 3
    assert len(payload['latest']['results']) == 3
    assert all(draw['numbers'][0] == draw['specialTail'] for draw in payload['draws'])
    assert [station['code'] for station in payload['stations']] == ['AG', 'BTH', 'TN']
    assert all(station['url'].startswith('https://') for station in payload['stations'])
    assert all(station['drawCount'] == 1 for station in payload['stations'])
    assert sum(payload['fullFrequency'].values()) == 54


def test_export_rejects_gold_object_that_no_longer_matches_manifest(tmp_path, grouped_prize_values) -> None:
    repository = _xsmb_repository(tmp_path, grouped_prize_values)
    fact_key = next(
        reference.key
        for reference in repository.latest_manifest().objects
        if reference.key.endswith(f'/{FACT_DRAW_RESULT_KEY}')
    )
    repository.store.put_bytes(
        fact_key,
        b'not the published object',
        content_type='application/vnd.apache.parquet',
    )

    with pytest.raises(ServingDataError, match='published object size mismatch'):
        build_serving_payload(repository, LotteryRegion.XSMB, generated_at=GENERATED_AT)


def test_main_writes_requested_local_output(tmp_path, grouped_prize_values, monkeypatch, capsys) -> None:
    lake_root = tmp_path / 'lake'
    destination = tmp_path / 'serving' / 'xsmb.json'
    _xsmb_repository(lake_root, grouped_prize_values)
    monkeypatch.setenv('ETL_ENV', 'test')

    exit_code = main(
        [
            '--storage',
            'local',
            '--region',
            'xsmb',
            '--lake-root',
            str(lake_root),
            '--output',
            str(destination),
        ]
    )

    payload = json.loads(destination.read_text(encoding='utf-8'))
    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload['region'] == 'xsmb'
    assert summary['output'] == str(destination)
    assert summary['datasetVersion'] == 'run-xsmb'


def test_build_serving_v2_bundle_uses_immutable_station_year_shards(tmp_path, grouped_prize_values) -> None:
    repository = _xsmb_repository(tmp_path, grouped_prize_values)

    bundle = build_serving_v2_bundle(repository, LotteryRegion.XSMB, generated_at=GENERATED_AT)

    assert bundle.metadata_key == 'v2/regions/xsmb/latest.json'
    assert bundle.metadata['schemaVersion'] == 2
    assert bundle.metadata['releaseId'] == 'run-xsmb'
    assert bundle.metadata['source'] == 'r2'
    assert bundle.metadata['stations'][0]['years'] == [2026]
    assert len(json.dumps(bundle.metadata).encode()) < 100 * 1024

    expected_key = serving_v2_shard_key('run-xsmb', LotteryRegion.XSMB, 'xsmb', 2026)
    assert set(bundle.shards) == {expected_key}
    shard = bundle.shards[expected_key]
    assert shard['releaseId'] == 'run-xsmb'
    assert shard['station'] == {'code': 'xsmb', 'name': 'Miền Bắc'}
    assert shard['range'] == {'from': '2026-07-18', 'to': '2026-07-19'}
    assert shard['drawCount'] == 2
    assert all(draw['numbers'][0] == draw['specialTail'] for draw in shard['draws'])


def test_write_serving_v2_bundle_writes_latest_metadata_after_shards(tmp_path, grouped_prize_values) -> None:
    repository = _xsmb_repository(tmp_path / 'lake', grouped_prize_values)
    bundle = build_serving_v2_bundle(repository, LotteryRegion.XSMB, generated_at=GENERATED_AT)

    write_serving_v2_bundle(bundle, tmp_path / 'serving')

    metadata_path = tmp_path / 'serving' / bundle.metadata_key
    assert json.loads(metadata_path.read_text())['releaseId'] == 'run-xsmb'
    for key, expected in bundle.shards.items():
        assert json.loads((tmp_path / 'serving' / key).read_text()) == expected


def test_main_can_export_only_the_v2_bundle(tmp_path, grouped_prize_values, monkeypatch, capsys) -> None:
    lake_root = tmp_path / 'lake'
    destination = tmp_path / 'serving'
    _xsmb_repository(lake_root, grouped_prize_values)
    monkeypatch.setenv('ETL_ENV', 'test')

    assert (
        main(
            [
                '--storage',
                'local',
                '--region',
                'xsmb',
                '--lake-root',
                str(lake_root),
                '--v2-output-dir',
                str(destination),
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary['v2MetadataKey'] == 'v2/regions/xsmb/latest.json'
    assert summary['v2ObjectCount'] == 2
    assert summary['datasetVersion'] == 'run-xsmb'


def test_main_aborts_combined_export_if_publication_changes_mid_build(
    tmp_path,
    grouped_prize_values,
    monkeypatch,
) -> None:
    lake_root = tmp_path / 'lake'
    destination = tmp_path / 'serving' / 'xsmb.json'
    v2_destination = tmp_path / 'serving-v2'
    repository = _xsmb_repository(lake_root, grouped_prize_values)
    next_result = LotteryResult.from_prize_groups(
        date(2026, 7, 20),
        'https://example.test/20',
        grouped_prize_values,
    )
    next_fact = draw_results_frame([next_result], 'run-xsmb-next')
    next_objects = repository.write_gold_tables(
        build_gold_tables(next_fact, run_id='run-xsmb-next'),
        run_id='run-xsmb-next',
    )
    original_build = EXPORTER.build_serving_payload

    def build_then_advance(*args, **kwargs):
        payload = original_build(*args, **kwargs)
        repository.publish_snapshot_and_latest(
            run_id='run-xsmb-next',
            target_date=next_result.draw_date,
            gold_objects=next_objects,
            published_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        return payload

    monkeypatch.setattr(EXPORTER, 'build_serving_payload', build_then_advance)
    monkeypatch.setenv('ETL_ENV', 'test')

    with pytest.raises(ServingDataError, match='changed during export; no serving data was written'):
        main(
            [
                '--storage',
                'local',
                '--region',
                'xsmb',
                '--lake-root',
                str(lake_root),
                '--output',
                str(destination),
                '--v2-output-dir',
                str(v2_destination),
            ]
        )

    assert not destination.exists()
    assert not v2_destination.exists()
