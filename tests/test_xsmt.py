from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xsmb_etl.config import Settings
from xsmb_etl.extract import RequestedDateMismatchError
from xsmb_etl.quality import require_quality
from xsmb_etl.repository import CentralDataLakeRepository
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.xsmt_extract import (
    CentralExtractedResult,
    CentralResultExtractor,
    parse_central_result_page,
)
from xsmb_etl.xsmt_marts import build_central_gold_tables
from xsmb_etl.xsmt_models import CentralDailyResult
from xsmb_etl.xsmt_pipeline import CentralPipeline
from xsmb_etl.xsmt_quality import build_central_quality_report
from xsmb_etl.xsmt_transform import central_draw_results_frame, central_loto_daily_frame


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmt-result-page.html'
TARGET_DATE = date(2026, 7, 18)
SOURCE_URL = 'https://xoso.com.vn/xsmt-18-07-2026.html'


def _extracted() -> CentralExtractedResult:
    raw = FIXTURE.read_bytes()
    result = parse_central_result_page(raw, selected_date=TARGET_DATE, source_url=SOURCE_URL)
    return CentralExtractedResult(raw_response=raw, result=result)


def _one_station_result(draw_date: date, station_code: str) -> CentralDailyResult:
    return _station_set_result(draw_date, station_code)


def _station_set_result(draw_date: date, *station_codes: str) -> CentralDailyResult:
    source_url = f'https://xoso.com.vn/xsmt-{draw_date:%d-%m-%Y}.html'
    base_stations = _extracted().result.stations
    stations = tuple(
        base_stations[index % len(base_stations)].model_copy(
            update={
                'draw_date': draw_date,
                'station_code': station_code,
                'station_name': station_code,
                'station_url': f'https://xoso.com.vn/xs{station_code.lower()}-p1.html',
                'source_url': source_url,
            }
        )
        for index, station_code in enumerate(station_codes)
    )
    return CentralDailyResult(draw_date=draw_date, source_url=source_url, stations=stations)


def _station_count_check(results: list[CentralDailyResult]):
    draw = central_draw_results_frame(results, 'run-xsmt')
    loto = central_loto_daily_frame(draw, run_id='run-xsmt')
    report = build_central_quality_report(
        results,
        draw,
        loto,
        run_id='run-xsmt',
        today=date(2021, 8, 18),
    )
    return next(check for check in report.checks if check.name == 'station-count-per-day')


def test_central_extractor_uses_xsmt_source_profiles() -> None:
    extractor = CentralResultExtractor(Settings(_env_file=None))

    assert extractor.build_source_url(TARGET_DATE) == SOURCE_URL
    assert extractor.build_fallback_source_url(TARGET_DATE) == 'https://xskt.com.vn/xsmt/ngay-18-7-2026'


def test_parse_central_page_extracts_three_complete_stations() -> None:
    result = _extracted().result

    assert result.draw_date == TARGET_DATE
    assert [station.station_code for station in result.stations] == ['DNA', 'QNG', 'DNO']
    assert [len(station.prizes) for station in result.stations] == [18, 18, 18]
    assert result.stations[0].prizes[-1].formatted_number == '874942'

    with pytest.raises(RequestedDateMismatchError):
        parse_central_result_page(
            FIXTURE.read_bytes(),
            selected_date=date(2026, 7, 17),
            source_url='https://xoso.com.vn/xsmt-17-07-2026.html',
        )


def test_central_transform_and_quality_use_exact_station_calendar() -> None:
    result = _extracted().result
    draw = central_draw_results_frame([result], 'run-xsmt')
    loto = central_loto_daily_frame(draw, run_id='run-xsmt')
    gold = build_central_gold_tables(draw, run_id='run-xsmt')
    report = build_central_quality_report(
        [result],
        draw,
        loto,
        run_id='run-xsmt',
        gold_tables=gold,
        today=TARGET_DATE,
    )

    require_quality(report)
    assert draw.shape == (54, 15)
    assert loto.shape == (300, 13)
    assert gold['dim-station'].shape[0] == 3
    assert next(check for check in report.checks if check.name == 'station-count-per-day').passed


def test_central_quality_accepts_only_documented_2021_partial_draws() -> None:
    results = [
        _one_station_result(date(2021, 7, 27), 'QNA'),
        _one_station_result(date(2021, 8, 3), 'QNA'),
        _one_station_result(date(2021, 8, 6), 'GL'),
        _one_station_result(date(2021, 8, 18), 'KH'),
        _station_set_result(date(2021, 8, 21), 'DNO', 'QNG'),
        _station_set_result(date(2021, 9, 4), 'DNA', 'DNO'),
    ]

    check = _station_count_check(results)

    assert check.passed
    assert check.details['documented_partial_draw_dates'] == [
        '2021-07-27',
        '2021-08-03',
        '2021-08-06',
        '2021-08-18',
        '2021-08-21',
        '2021-09-04',
    ]


@pytest.mark.parametrize(
    ('draw_date', 'station_code'),
    [
        (date(2021, 7, 28), 'QNA'),
        (date(2021, 7, 27), 'DNA'),
    ],
)
def test_central_quality_rejects_undocumented_or_wrong_single_station(
    draw_date: date,
    station_code: str,
) -> None:
    assert not _station_count_check([_one_station_result(draw_date, station_code)]).passed


def test_central_quality_rejects_wrong_station_set_with_the_right_count() -> None:
    result = _station_set_result(TARGET_DATE, 'DNA', 'QNG', 'KH')

    check = _station_count_check([result])

    assert not check.passed
    assert check.details['station_set_mismatches'] == [
        {
            'draw_date': TARGET_DATE.isoformat(),
            'expected': ['DNA', 'DNO', 'QNG'],
            'actual': ['DNA', 'KH', 'QNG'],
        }
    ]


def test_central_pipeline_publishes_an_independent_manifest(tmp_path) -> None:
    extracted = _extracted()

    class FixtureExtractor:
        def extract(self, selected_date: date) -> CentralExtractedResult:
            assert selected_date == TARGET_DATE
            return extracted

    repository = CentralDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    result = CentralPipeline(repository, FixtureExtractor()).run(TARGET_DATE)

    assert result.region is LotteryRegion.XSMT
    assert result.status == 'success'
    assert repository.latest_manifest().region is LotteryRegion.XSMT
    assert (tmp_path / 'gold/latest/dim-station.parquet').is_file()


def test_central_pipeline_accepts_documented_partial_draw(tmp_path) -> None:
    target_date = date(2021, 7, 27)
    result = _one_station_result(target_date, 'QNA')

    class FixtureExtractor:
        def extract(self, selected_date: date) -> CentralExtractedResult:
            assert selected_date == target_date
            return CentralExtractedResult(raw_response=b'documented partial draw', result=result)

    repository = CentralDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')

    run = CentralPipeline(repository, FixtureExtractor()).run(target_date)

    assert run.status == 'success'
    assert repository.latest_manifest().target_date == target_date
