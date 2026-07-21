from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd
import pytest

from xsmb_etl.extract import ExtractedResult, NoDrawSourcePageError, SourcePageError
from xsmb_etl.models import LotteryResult
from xsmb_etl.pipeline import Pipeline, backfill_failure_result
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.storage import LocalObjectStore


class FixtureExtractor:
    def __init__(self, extracted: ExtractedResult | None = None, error: Exception | None = None) -> None:
        self.extracted = extracted
        self.error = error
        self.calls = 0

    def extract(self, selected_date: date) -> ExtractedResult:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.extracted is not None
        return self.extracted


class RecordingStore(LocalObjectStore):
    def __init__(self, root) -> None:
        super().__init__(root)
        self.writes: list[str] = []

    def put_bytes(self, key, data, **kwargs):
        stored = super().put_bytes(key, data, **kwargs)
        self.writes.append(key)
        return stored


def test_fixture_pipeline_publishes_latest_last_and_is_idempotent(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    target_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(target_date, 'https://example.test', grouped_prize_values)
    extractor = FixtureExtractor(ExtractedResult(valid_result_page, result))
    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='public, max-age=300')
    pipeline = Pipeline(repository, extractor)

    first = pipeline.run(target_date)
    second = pipeline.run(target_date)

    assert first.status == 'success'
    assert store.writes[-1] == 'manifests/latest.json'
    assert repository.latest_manifest().run_id == first.run_id
    assert second.skipped
    assert extractor.calls == 1


def test_failed_run_is_recorded_without_publishing_latest(tmp_path) -> None:
    target_date = date(2026, 7, 16)
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='public, max-age=300')
    pipeline = Pipeline(repository, FixtureExtractor(error=SourcePageError('layout changed')))

    with pytest.raises(SourcePageError):
        pipeline.run(target_date)

    assert repository.latest_manifest() is None
    assert repository.control_state().status_for(target_date).value == 'failed'


def test_explicit_no_draw_is_recorded_and_skipped_on_repeat(tmp_path) -> None:
    target_date = date(2020, 4, 1)
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    extractor = FixtureExtractor(
        error=NoDrawSourcePageError(
            target_date,
            'https://xoso.com.vn/xsmb-01-04-2020.html',
            'Kết quả xổ số miền Bắc ngày 01/04/2020 không mở thưởng.',
        )
    )
    pipeline = Pipeline(repository, extractor)

    first = pipeline.run(target_date)
    second = pipeline.run(target_date)

    assert first.status == 'no_draw'
    assert second.status == 'no_draw'
    assert second.skipped
    assert extractor.calls == 1
    assert repository.latest_manifest() is None
    assert repository.control_state().status_for(target_date).value == 'no_draw'


def test_backfill_records_each_outcome_and_continues_after_failure(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    dates = [date(2026, 7, 14), date(2026, 7, 15), date(2026, 7, 16), date(2026, 7, 17)]

    class BackfillExtractor:
        def extract(self, selected_date: date) -> ExtractedResult:
            if selected_date == dates[0]:
                raise NoDrawSourcePageError(selected_date, '', 'Source explicitly says không mở thưởng.')
            if selected_date == dates[1]:
                raise SourcePageError('unexpected layout change')
            result = LotteryResult.from_prize_groups(selected_date, 'https://example.test', grouped_prize_values)
            return ExtractedResult(valid_result_page, result)

    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='no-cache')
    pipeline = Pipeline(repository, BackfillExtractor())

    results = pipeline.backfill(dates[0], dates[-1])

    assert [result.status for result in results] == ['no_draw', 'failed', 'success', 'success']
    assert 'backfill continued' in results[1].message
    assert all('published batch dataset version' in result.message for result in results[2:])
    assert store.writes.count('manifests/latest.json') == 1
    assert repository.latest_manifest().target_date == dates[-1]
    assert [repository.control_state().status_for(value).value for value in dates] == [
        'no_draw',
        'failed',
        'success',
        'success',
    ]


def test_backfill_republishes_gold_when_only_new_outcome_is_no_draw(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    first_date = date(2026, 7, 16)
    no_draw_date = date(2026, 7, 17)
    latest_date = date(2026, 7, 18)

    class HistoricalExtractor:
        def extract(self, selected_date: date) -> ExtractedResult:
            if selected_date == no_draw_date:
                raise NoDrawSourcePageError(selected_date, '', 'Source explicitly says không mở thưởng.')
            result = LotteryResult.from_prize_groups(
                selected_date,
                'https://example.test',
                grouped_prize_values,
            )
            return ExtractedResult(valid_result_page, result)

    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='no-cache')
    pipeline = Pipeline(repository, HistoricalExtractor())
    pipeline.run(first_date)
    pipeline.run(latest_date)
    previous_run_id = repository.latest_manifest().run_id

    results = pipeline.backfill(no_draw_date, no_draw_date)

    assert [result.status for result in results] == ['no_draw']
    assert repository.latest_manifest().run_id != previous_run_id
    assert store.writes.count('manifests/latest.json') == 3
    dim_date = pd.read_parquet(BytesIO(store.get_bytes('gold/latest/dim-date.parquet')))
    status = dim_date.loc[pd.to_datetime(dim_date['date']).dt.date.eq(no_draw_date), 'draw_status'].item()
    assert status == 'no_draw'


def test_backfill_of_older_success_keeps_latest_target_and_publishes_once(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    historical_date = date(2011, 4, 26)
    latest_date = date(2026, 7, 20)

    class HistoricalExtractor:
        def extract(self, selected_date: date) -> ExtractedResult:
            result = LotteryResult.from_prize_groups(
                selected_date,
                'https://example.test',
                grouped_prize_values,
            )
            return ExtractedResult(valid_result_page, result)

    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='no-cache')
    pipeline = Pipeline(repository, HistoricalExtractor())
    pipeline.run(latest_date)

    results = pipeline.backfill(historical_date, historical_date)

    assert [result.status for result in results] == ['success']
    assert repository.latest_manifest().target_date == latest_date
    assert store.writes.count('manifests/latest.json') == 2


def test_backfill_recovers_no_draw_manifest_newer_than_gold_publication(
    tmp_path,
    valid_result_page: bytes,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    first_date = date(2026, 7, 16)
    no_draw_date = date(2026, 7, 17)
    latest_date = date(2026, 7, 18)

    class HistoricalExtractor:
        def extract(self, selected_date: date) -> ExtractedResult:
            result = LotteryResult.from_prize_groups(
                selected_date,
                'https://example.test',
                grouped_prize_values,
            )
            return ExtractedResult(valid_result_page, result)

    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='no-cache')
    pipeline = Pipeline(repository, HistoricalExtractor())
    pipeline.run(first_date)
    pipeline.run(latest_date)
    pipeline.record_no_draw(no_draw_date, detail='Source explicitly says không mở thưởng.')
    stale_run_id = repository.latest_manifest().run_id

    results = pipeline.backfill(no_draw_date, no_draw_date)

    assert results == []
    assert repository.latest_manifest().run_id != stale_run_id
    dim_date = pd.read_parquet(BytesIO(store.get_bytes('gold/latest/dim-date.parquet')))
    status = dim_date.loc[pd.to_datetime(dim_date['date']).dt.date.eq(no_draw_date), 'draw_status'].item()
    assert status == 'no_draw'


def test_backfill_failure_result_does_not_make_a_second_repository_request() -> None:
    class UnavailableRepository:
        region = LotteryRegion.XSMN

        def run_manifests(self):
            raise AssertionError('failure reporting must not query R2 again')

    result = backfill_failure_result(
        UnavailableRepository(),
        date(2021, 4, 15),
        RuntimeError('conditional write conflict'),
    )

    assert result.status == 'failed'
    assert result.region is LotteryRegion.XSMN
    assert result.run_id is None
    assert 'backfill continued' in result.message
