from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from xsmb_etl.extract import ExtractedResult
from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult
from xsmb_etl.quality import build_quality_report
from xsmb_etl.repository import BronzeConflictError, DataLakeRepository
from xsmb_etl.run_models import RunManifest, RunStatus, SourceLineage
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.transform import draw_results_frame, loto_daily_frame


def _repository(tmp_path) -> DataLakeRepository:
    return DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='public, max-age=300')


def test_repository_writes_immutable_bronze_and_monthly_silver(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    repository = _repository(tmp_path)
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), 'https://example.test', grouped_prize_values)
    extracted = ExtractedResult(raw_response=valid_result_page, result=result)

    bronze = repository.write_bronze(extracted, run_id='run-1')
    draw = draw_results_frame([result], 'run-1')
    silver = repository.upsert_silver_draw_results(draw)

    assert len(bronze) == 3
    assert repository.load_bronze(result.draw_date).result == result
    assert silver[0].key == 'silver/draw-results/year=2026/month=07/draw-results.parquet'
    assert repository.read_all_silver_draw_results().shape == (27, 11)
    with pytest.raises(BronzeConflictError):
        repository.write_bronze(ExtractedResult(raw_response=b'changed', result=result), run_id='run-2')


def test_repository_resumes_matching_partial_bronze(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    repository = _repository(tmp_path)
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), 'https://example.test', grouped_prize_values)
    extracted = ExtractedResult(raw_response=valid_result_page, result=result)
    prefix = repository._bronze_prefix(result.draw_date)
    repository.store.put_bytes(
        f'{prefix}/response.html',
        valid_result_page,
        content_type='text/html; charset=utf-8',
        overwrite=False,
    )

    objects = repository.write_bronze(extracted, run_id='run-1')

    assert len(objects) == 3
    assert repository.bronze_complete(result.draw_date)


def test_repository_publishes_latest_after_gold_and_loads_control_state(
    tmp_path,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    repository = _repository(tmp_path)
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    draw = draw_results_frame([result], 'run-1')
    loto = loto_daily_frame(draw)
    tables = build_gold_tables(draw, run_id='run-1')
    report = build_quality_report([result], draw, loto, run_id='run-1', gold_tables=tables, today=date(2026, 7, 16))
    quality_object = repository.write_quality_report(report, result.draw_date)
    gold_objects = repository.write_gold_tables(tables, run_id='run-1')
    now = datetime(2026, 7, 16, tzinfo=UTC)
    repository.write_run_manifest(
        RunManifest(
            run_id='run-1',
            target_date=result.draw_date,
            status=RunStatus.SUCCESS,
            source_lineage=SourceLineage.LIVE_SOURCE,
            started_at=now,
            completed_at=now,
            quality_passed=True,
        )
    )
    repository.publish_snapshot_and_latest(run_id='run-1', target_date=result.draw_date, gold_objects=gold_objects)

    assert quality_object.key.endswith('/report.json')
    assert len(gold_objects) == 10
    assert repository.latest_manifest().run_id == 'run-1'
    assert repository.control_state().status_for(result.draw_date).value == 'success'
