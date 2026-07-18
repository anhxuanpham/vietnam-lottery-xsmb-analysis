from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from xsmb_etl.config import Settings
from xsmb_etl.extract import NoDrawSourcePageError, PrizeCountError, RequestedDateMismatchError, SourcePageError
from xsmb_etl.xsmn_extract import (
    SourceReconciliationError,
    SouthernResultExtractor,
    TruncatedSpecialPrizeError,
    parse_southern_result_page,
)
from xsmb_etl.xsmn_models import SouthernDailyResult, SouthernPrizeGroup


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'
HISTORICAL_SPECIALS = (
    (date(2010, 2, 24), ('188006', '885932', '282400')),
    (date(2010, 2, 25), ('249438', '050445', '839549')),
    (date(2010, 2, 26), ('795996', '018085', '694363')),
    (date(2010, 11, 29), ('104685', '510431', '854791')),
    (date(2010, 11, 30), ('893651', '036777', '952997')),
    (date(2010, 12, 1), ('106921', '489263', '757702')),
)


class StubResponse:
    status_code = 200

    def __init__(self, content: bytes) -> None:
        self.content = content


class RecordingHttpClient:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> StubResponse:
        self.calls.append(url)
        return StubResponse(self.responses[url])


def _historical_primary_page(
    selected_date: date,
    specials: tuple[str, str, str],
    *,
    truncate_specials: bool,
) -> bytes:
    page = FIXTURE.read_text()
    page = page.replace('16/07/2026', f'{selected_date:%d/%m/%Y}')
    page = page.replace('16/7/2026', f'{selected_date.day}/{selected_date.month}/{selected_date.year}')
    page = page.replace('16072026', f'{selected_date:%d%m%Y}')
    page = page.replace('16-07-2026', f'{selected_date:%d-%m-%Y}')
    for original, replacement in zip(('005113', '830834', '623836'), specials, strict=True):
        page = page.replace(original, replacement[-5:] if truncate_specials else replacement)
    return page.encode()


def _fallback_page(result: SouthernDailyResult) -> bytes:
    labels = {
        SouthernPrizeGroup.PRIZE8: 'G.8',
        SouthernPrizeGroup.PRIZE7: 'G.7',
        SouthernPrizeGroup.PRIZE6: 'G.6',
        SouthernPrizeGroup.PRIZE5: 'G.5',
        SouthernPrizeGroup.PRIZE4: 'G.4',
        SouthernPrizeGroup.PRIZE3: 'G.3',
        SouthernPrizeGroup.PRIZE2: 'G.2',
        SouthernPrizeGroup.PRIZE1: 'G.1',
        SouthernPrizeGroup.SPECIAL: 'ĐB',
    }
    headers = ''.join(
        f'<th><a href="/xs{station.station_code.lower()}">{station.station_name}</a></th>'
        for station in result.stations
    )
    rows = []
    for group in SouthernPrizeGroup:
        cells = ''.join(
            f'<td>{"<br>".join(prize.formatted_number for prize in station.prizes_for(group))}</td>'
            for station in result.stations
        )
        rows.append(f'<tr><td>{labels[group]}</td>{cells}</tr>')
    return (
        f'<html><head><title>Kết quả xổ số toàn quốc ngày {result.draw_date:%d/%m/%Y}</title></head>'
        f'<body><table class="tbl-xsmn"><tr><th>{result.draw_date:%d/%m}</th>{headers}</tr>'
        f'{"".join(rows)}</table></body></html>'
    ).encode()


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


def test_parse_southern_page_classifies_five_digit_special_as_reconcilable() -> None:
    raw = _historical_primary_page(date(2010, 2, 24), HISTORICAL_SPECIALS[0][1], truncate_specials=True)

    with pytest.raises(TruncatedSpecialPrizeError, match='special value must contain exactly 6 digits'):
        parse_southern_result_page(
            raw,
            selected_date=date(2010, 2, 24),
            source_url='https://primary.test/xsmn-24-02-2010.html',
        )


@pytest.mark.parametrize(('selected_date', 'specials'), HISTORICAL_SPECIALS)
def test_southern_extractor_reconciles_known_historical_special_prefixes(
    selected_date: date,
    specials: tuple[str, str, str],
) -> None:
    primary_url = f'https://primary.test/xsmn-{selected_date:%d-%m-%Y}.html'
    fallback_url = f'https://fallback.test/ngay/{selected_date:%d-%m-%Y}'
    complete_primary = _historical_primary_page(selected_date, specials, truncate_specials=False)
    complete_result = parse_southern_result_page(
        complete_primary,
        selected_date=selected_date,
        source_url=primary_url,
    )
    primary = _historical_primary_page(selected_date, specials, truncate_specials=True)
    fallback = _fallback_page(complete_result)
    client = RecordingHttpClient({primary_url: primary, fallback_url: fallback})
    extractor = SouthernResultExtractor(
        Settings(source_base_url='https://primary.test', xsmn_fallback_base_url='https://fallback.test'),
        client,
    )

    extracted = extractor.extract(selected_date)

    assert client.calls == [primary_url, fallback_url]
    assert extracted.raw_response == primary
    assert extracted.fallback_response == fallback
    assert extracted.fallback_url == fallback_url
    assert [
        station.prizes_for(SouthernPrizeGroup.SPECIAL)[0].formatted_number for station in extracted.result.stations
    ] == list(specials)


def test_southern_extractor_rejects_fallback_value_mismatch() -> None:
    selected_date, specials = HISTORICAL_SPECIALS[0]
    primary_url = f'https://primary.test/xsmn-{selected_date:%d-%m-%Y}.html'
    fallback_url = f'https://fallback.test/ngay/{selected_date:%d-%m-%Y}'
    primary = _historical_primary_page(selected_date, specials, truncate_specials=True)
    mismatched = (specials[0][:-1] + '7', *specials[1:])
    fallback_result = parse_southern_result_page(
        _historical_primary_page(selected_date, mismatched, truncate_specials=False),
        selected_date=selected_date,
        source_url=primary_url,
    )
    client = RecordingHttpClient({primary_url: primary, fallback_url: _fallback_page(fallback_result)})
    extractor = SouthernResultExtractor(
        Settings(source_base_url='https://primary.test', xsmn_fallback_base_url='https://fallback.test'),
        client,
    )

    with pytest.raises(SourceReconciliationError, match='TN special'):
        extractor.extract(selected_date)


def test_southern_extractor_accepts_fallback_station_alias_url() -> None:
    selected_date, specials = HISTORICAL_SPECIALS[0]
    primary_url = f'https://primary.test/xsmn-{selected_date:%d-%m-%Y}.html'
    fallback_url = f'https://fallback.test/ngay/{selected_date:%d-%m-%Y}'
    complete_result = parse_southern_result_page(
        _historical_primary_page(selected_date, specials, truncate_specials=False),
        selected_date=selected_date,
        source_url=primary_url,
    )
    fallback = _fallback_page(complete_result).replace(b'/xstn', b'/xstn-alias')
    client = RecordingHttpClient(
        {
            primary_url: _historical_primary_page(selected_date, specials, truncate_specials=True),
            fallback_url: fallback,
        }
    )
    extractor = SouthernResultExtractor(
        Settings(source_base_url='https://primary.test', xsmn_fallback_base_url='https://fallback.test'),
        client,
    )

    extracted = extractor.extract(selected_date)

    assert extracted.result.stations[0].station_code == 'TN'


def test_southern_extractor_matches_station_name_when_source_codes_differ() -> None:
    selected_date, specials = HISTORICAL_SPECIALS[0]
    primary_url = f'https://primary.test/xsmn-{selected_date:%d-%m-%Y}.html'
    fallback_url = f'https://fallback.test/ngay/{selected_date:%d-%m-%Y}'
    complete_result = parse_southern_result_page(
        _historical_primary_page(selected_date, specials, truncate_specials=False),
        selected_date=selected_date,
        source_url=primary_url,
    )
    fallback = _fallback_page(complete_result).replace(b'/xstn', b'/xsother')
    client = RecordingHttpClient(
        {
            primary_url: _historical_primary_page(selected_date, specials, truncate_specials=True),
            fallback_url: fallback,
        }
    )
    extractor = SouthernResultExtractor(
        Settings(source_base_url='https://primary.test', xsmn_fallback_base_url='https://fallback.test'),
        client,
    )

    extracted = extractor.extract(selected_date)

    assert extracted.result.stations[0].station_code == 'TN'


def test_southern_extractor_does_not_fallback_for_other_quality_errors() -> None:
    selected_date = date(2026, 7, 16)
    primary_url = 'https://primary.test/xsmn-16-07-2026.html'
    primary = FIXTURE.read_text().replace('<span class="xs_prize1">42815</span>', '').encode()
    client = RecordingHttpClient({primary_url: primary})
    extractor = SouthernResultExtractor(
        Settings(source_base_url='https://primary.test', xsmn_fallback_base_url='https://fallback.test'),
        client,
    )

    with pytest.raises(PrizeCountError, match='prize4 expected 7'):
        extractor.extract(selected_date)

    assert client.calls == [primary_url]


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
