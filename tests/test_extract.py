from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from xsmb_etl.config import Settings
from xsmb_etl.extract import (
    HttpStatusError,
    NoDrawSourcePageError,
    PrizeCountError,
    RequestedDateMismatchError,
    ResultExtractor,
    parse_result_page,
)


FIXTURES = Path(__file__).parent / 'fixtures'
TARGET_DATE = date(2026, 7, 16)


@dataclass(frozen=True)
class FakeResponse:
    status_code: int
    content: bytes = b''


class FakeHttpClient:
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_parse_valid_fixture_returns_raw_canonical_result(valid_result_page: bytes) -> None:
    result = parse_result_page(
        valid_result_page,
        selected_date=TARGET_DATE,
        source_url='https://xoso.com.vn/xsmb-16-07-2026.html',
    )

    assert result.draw_date == TARGET_DATE
    assert result.source_url.endswith('/xsmb-16-07-2026.html')
    assert len(result.prizes) == 27


@pytest.mark.parametrize('fixture_name', ['invalid-result-page.html', 'changed-layout-page.html'])
def test_parse_rejects_incomplete_or_changed_prize_layout(fixture_name: str) -> None:
    with pytest.raises(PrizeCountError):
        parse_result_page(
            (FIXTURES / fixture_name).read_bytes(),
            selected_date=TARGET_DATE,
            source_url='https://xoso.com.vn/xsmb-16-07-2026.html',
        )


def test_parse_rejects_page_for_a_different_date(valid_result_page: bytes) -> None:
    with pytest.raises(RequestedDateMismatchError, match='2026-07-15.*2026-07-16'):
        parse_result_page(
            valid_result_page,
            selected_date=date(2026, 7, 15),
            source_url='https://xoso.com.vn/xsmb-15-07-2026.html',
        )


def test_parse_classifies_explicit_no_draw_notice() -> None:
    raw = b"""
        <section id="kqngay_01042020">
          <h1>XSMB 01/04/2020</h1>
          <p>Ket qua xo so mien Bac ngay 01/04/2020
             kh\xc3\xb4ng m\xe1\xbb\x9f th\xc6\xb0\xe1\xbb\x9fng.</p>
        </section>
    """

    with pytest.raises(NoDrawSourcePageError, match='explicitly reports no draw') as raised:
        parse_result_page(
            raw,
            selected_date=date(2020, 4, 1),
            source_url='https://xoso.com.vn/xsmb-01-04-2020.html',
        )

    assert raised.value.draw_date == date(2020, 4, 1)
    assert 'không mở thưởng' in raised.value.notice


def test_extractor_applies_timeout_and_retries_transient_status(valid_result_page: bytes) -> None:
    client = FakeHttpClient([FakeResponse(503), FakeResponse(200, valid_result_page)])
    settings = Settings(
        _env_file=None,
        http_timeout_seconds=12.5,
        http_max_retries=2,
        http_retry_backoff_seconds=0,
    )
    extractor = ResultExtractor(settings=settings, http_client=client)

    extracted = extractor.extract(TARGET_DATE)

    assert extracted.raw_response == valid_result_page
    assert extracted.result.draw_date == TARGET_DATE
    assert len(client.calls) == 2
    assert [timeout for _, timeout in client.calls] == [12.5, 12.5]


def test_extractor_does_not_retry_permanent_http_error() -> None:
    client = FakeHttpClient([FakeResponse(404)])
    settings = Settings(_env_file=None, http_max_retries=3, http_retry_backoff_seconds=0)

    with pytest.raises(HttpStatusError, match='404'):
        ResultExtractor(settings=settings, http_client=client).extract(TARGET_DATE)

    assert len(client.calls) == 1
