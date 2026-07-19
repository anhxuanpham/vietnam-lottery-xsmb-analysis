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


def test_central_extractor_uses_xsmt_source_profiles() -> None:
    extractor = CentralResultExtractor(Settings(_env_file=None))

    assert extractor.build_source_url(TARGET_DATE) == SOURCE_URL
    assert extractor.build_fallback_source_url(TARGET_DATE) == 'https://xskt.com.vn/xsmt/ngay-18-7-2026'


def test_parse_central_page_extracts_two_complete_stations() -> None:
    result = _extracted().result

    assert result.draw_date == TARGET_DATE
    assert [station.station_code for station in result.stations] == ['DNA', 'QNG']
    assert [len(station.prizes) for station in result.stations] == [18, 18]
    assert result.stations[0].prizes[-1].formatted_number == '874942'

    with pytest.raises(RequestedDateMismatchError):
        parse_central_result_page(
            FIXTURE.read_bytes(),
            selected_date=date(2026, 7, 17),
            source_url='https://xoso.com.vn/xsmt-17-07-2026.html',
        )


def test_central_transform_and_quality_use_two_or_three_station_rule() -> None:
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
    assert draw.shape == (36, 15)
    assert loto.shape == (200, 13)
    assert gold['dim-station'].shape[0] == 2
    assert next(check for check in report.checks if check.name == 'station-count-per-day').passed


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
