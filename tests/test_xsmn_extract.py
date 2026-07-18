from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xsmb_etl.extract import NoDrawSourcePageError, PrizeCountError, RequestedDateMismatchError, SourcePageError
from xsmb_etl.xsmn_extract import SouthernResultExtractor, parse_southern_result_page
from xsmb_etl.xsmn_models import SouthernPrizeGroup


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


def test_parse_southern_page_extracts_all_station_columns() -> None:
    result = parse_southern_result_page(
        FIXTURE.read_bytes(),
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )

    assert [station.station_code for station in result.stations] == ['TN', 'AG', 'BTH']
    assert [station.station_name for station in result.stations] == ['Tây Ninh', 'An Giang', 'Bình Thuận']
    assert result.stations[0].station_url == 'https://xoso.com.vn/xo-so-tay-ninh/xstn-p1.html'
    assert result.stations[0].prizes_for(SouthernPrizeGroup.SPECIAL)[0].formatted_number == '005113'


def test_parse_southern_page_validates_requested_date() -> None:
    with pytest.raises(RequestedDateMismatchError):
        parse_southern_result_page(
            FIXTURE.read_bytes(),
            selected_date=date(2026, 7, 15),
            source_url='https://example.test/xsmn-15-07-2026.html',
        )


def test_parse_southern_page_rejects_missing_prize_value() -> None:
    raw = FIXTURE.read_text().replace('<span class="xs_prize1">42815</span>', '').encode()

    with pytest.raises(PrizeCountError, match='prize4 expected 7'):
        parse_southern_result_page(
            raw,
            selected_date=date(2026, 7, 16),
            source_url='https://example.test/xsmn-16-07-2026.html',
        )


def test_parse_southern_page_rejects_missing_table() -> None:
    with pytest.raises(SourcePageError, match='result table'):
        parse_southern_result_page(
            b'<section id="mn_kqngay_16072026"></section>',
            selected_date=date(2026, 7, 16),
            source_url='https://example.test/xsmn-16-07-2026.html',
        )


def test_parse_southern_page_classifies_explicit_no_draw_notice() -> None:
    raw = """
        <section id="mn_kqngay_01042020">
          <h1>XSMN 01/04/2020</h1>
          <p>Kết quả xổ số miền Nam ngày 01/04/2020 không mở thưởng.</p>
        </section>
    """.encode()

    with pytest.raises(NoDrawSourcePageError) as raised:
        parse_southern_result_page(
            raw,
            selected_date=date(2020, 4, 1),
            source_url='https://xoso.com.vn/xsmn-01-04-2020.html',
        )

    assert raised.value.draw_date == date(2020, 4, 1)
    assert 'không mở thưởng' in raised.value.notice


def test_southern_extractor_builds_daily_url() -> None:
    assert SouthernResultExtractor().build_source_url(date(2026, 7, 16)).endswith('/xsmn-16-07-2026.html')
