from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xsmb_etl.extract import NoDrawSourcePageError, SourcePageError
from xsmb_etl.repository import SouthernDataLakeRepository
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.station_calendar import expected_station_codes
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.xsmn_extract import SouthernExtractedResult, parse_southern_result_page
from xsmb_etl.xsmn_pipeline import SouthernPipeline


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


class FixtureSouthernExtractor:
    def __init__(self, extracted: SouthernExtractedResult | None = None, error: Exception | None = None) -> None:
        self.extracted = extracted
        self.error = error
        self.calls = 0

    def extract(self, selected_date: date) -> SouthernExtractedResult:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.extracted is not None
        assert selected_date == self.extracted.result.draw_date
        return self.extracted


def _extracted() -> SouthernExtractedResult:
    raw = FIXTURE.read_bytes()
    result = parse_southern_result_page(
        raw,
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )
    return SouthernExtractedResult(raw_response=raw, result=result)


def _extracted_for(target_date: date) -> SouthernExtractedResult:
    extracted = _extracted()
    source_url = f'https://xoso.com.vn/xsmn-{target_date:%d-%m-%Y}.html'
    station_codes = sorted(expected_station_codes(LotteryRegion.XSMN, target_date))
    stations = tuple(
        extracted.result.stations[index % len(extracted.result.stations)].model_copy(
            update={
                'draw_date': target_date,
                'station_code': station_code,
                'station_name': station_code,
                'station_url': f'https://xoso.com.vn/xs{station_code.lower()}-p1.html',
                'source_url': source_url,
            }
        )
        for index, station_code in enumerate(station_codes)
    )
    result = extracted.result.model_copy(
        update={
            'draw_date': target_date,
            'source_url': source_url,
            'stations': stations,
        }
    )
    return SouthernExtractedResult(raw_response=extracted.raw_response, result=result)


def test_southern_pipeline_publishes_independent_latest_and_skips_repeat(tmp_path) -> None:
    extracted = _extracted()
    extractor = FixtureSouthernExtractor(extracted)
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='public, max-age=300')
    pipeline = SouthernPipeline(repository, extractor)

    first = pipeline.run(extracted.result.draw_date)
    second = pipeline.run(extracted.result.draw_date)

    assert first.region is LotteryRegion.XSMN
    assert first.status == 'success'
    assert repository.latest_manifest().region is LotteryRegion.XSMN
    assert len(repository.latest_manifest().objects) == 6
    assert all(reference.key.startswith('gold/releases/run-id=') for reference in repository.latest_manifest().objects)
    assert second.skipped
    assert extractor.calls == 1


def test_southern_failed_run_does_not_publish_latest(tmp_path) -> None:
    target_date = date(2026, 7, 16)
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    pipeline = SouthernPipeline(repository, FixtureSouthernExtractor(error=SourcePageError('layout changed')))

    with pytest.raises(SourcePageError):
        pipeline.run(target_date)

    assert repository.latest_manifest() is None
    manifest = repository.run_manifests()[0]
    assert manifest.region is LotteryRegion.XSMN
    assert manifest.status.value == 'failed'


def test_southern_backfill_marks_no_draw_and_continues_after_failure(tmp_path) -> None:
    dates = [date(2026, 7, 14), date(2026, 7, 15), date(2026, 7, 16)]
    extracted = _extracted()

    class BackfillExtractor:
        def extract(self, selected_date: date) -> SouthernExtractedResult:
            if selected_date == dates[0]:
                raise NoDrawSourcePageError(selected_date, '', 'Source explicitly says không mở thưởng.')
            if selected_date == dates[1]:
                raise SourcePageError('unexpected layout change')
            return extracted

    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    pipeline = SouthernPipeline(repository, BackfillExtractor())

    results = pipeline.backfill(dates[0], dates[-1])

    assert [result.status for result in results] == ['no_draw', 'failed', 'success']
    assert 'backfill continued' in results[1].message
    assert repository.latest_manifest().target_date == dates[-1]
    assert [repository.control_state().status_for(value).value for value in dates] == [
        'no_draw',
        'failed',
        'success',
    ]


def test_southern_backfill_publishes_gold_once_after_ingesting_the_batch(tmp_path) -> None:
    dates = [date(2026, 7, 14), date(2026, 7, 15), date(2026, 7, 16)]

    class BackfillExtractor:
        def __init__(self) -> None:
            self.calls = 0

        def extract(self, selected_date: date) -> SouthernExtractedResult:
            self.calls += 1
            return _extracted_for(selected_date)

    class RecordingStore(LocalObjectStore):
        def __init__(self, root) -> None:
            super().__init__(root)
            self.writes: list[str] = []

        def put_bytes(self, key, data, **kwargs):
            stored = super().put_bytes(key, data, **kwargs)
            self.writes.append(key)
            return stored

    extractor = BackfillExtractor()
    store = RecordingStore(tmp_path)
    repository = SouthernDataLakeRepository(store, gold_cache_control='no-cache')
    pipeline = SouthernPipeline(repository, extractor)

    results = pipeline.backfill(dates[0], dates[-1])

    assert [result.status for result in results] == ['success', 'success', 'success']
    assert all('published batch dataset version' in result.message for result in results)
    assert extractor.calls == 3
    assert store.writes.count('manifests/latest.json') == 1
    assert len([key for key in store.writes if key.startswith('gold/releases/')]) == 6
    assert repository.read_all_silver_draw_results().shape == (162, 15)
    assert [repository.control_state().status_for(value).value for value in dates] == [
        'success',
        'success',
        'success',
    ]

    writes_after_first_run = list(store.writes)
    assert pipeline.backfill(dates[0], dates[-1]) == []
    assert store.writes == writes_after_first_run
