"""Reliable HTTP extraction and parsing for one XSMB draw date."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from bs4 import BeautifulSoup, Tag
from cloudscraper import CloudScraper
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from xsmb_etl.config import Settings
from xsmb_etl.models import LotteryResult, PRIZE_SPECS, PrizeGroup


TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
SECTION_ID_PATTERN = re.compile(r'^kqngay_(\d{8})$')
CANONICAL_DATE_PATTERN = re.compile(r'/xsmb-(\d{2})-(\d{2})-(\d{4})\.html')
DISPLAY_DATE_PATTERN = re.compile(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b')
NO_DRAW_PATTERN = re.compile(r'không\s+mở\s+thưởng', re.IGNORECASE)


class ExtractionError(RuntimeError):
    """Base class for expected extraction failures."""


class TransientExtractionError(ExtractionError):
    """An extraction error that can safely be retried."""


class HttpStatusError(ExtractionError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        super().__init__(f'source returned HTTP {status_code} for {url}')


class TransientHttpStatusError(TransientExtractionError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        super().__init__(f'source temporarily returned HTTP {status_code} for {url}')


class SourcePageError(ExtractionError):
    """The response is not a parseable result page."""


class NoDrawSourcePageError(SourcePageError):
    """The dated source page explicitly states that no drawing took place."""

    def __init__(self, draw_date: date, source_url: str, notice: str) -> None:
        self.draw_date = draw_date
        self.source_url = source_url
        self.notice = notice
        super().__init__(f'{draw_date.isoformat()} source explicitly reports no draw: {notice}')


class RequestedDateMismatchError(SourcePageError):
    """The source page represents a different date than requested."""


class PrizeCountError(SourcePageError):
    """A prize group contains an unexpected number of values."""


class HttpResponse(Protocol):
    status_code: int
    content: bytes


class HttpClient(Protocol):
    def get(self, url: str, *, timeout: float) -> HttpResponse: ...


@dataclass(frozen=True)
class ExtractedResult:
    raw_response: bytes
    result: LotteryResult


class ResultExtractor:
    def __init__(self, settings: Settings | None = None, http_client: HttpClient | None = None) -> None:
        self.settings = settings or Settings()
        self.http_client = http_client or CloudScraper()

    def extract(self, selected_date: date) -> ExtractedResult:
        url = self.build_source_url(selected_date)
        response = self._get_with_retry(url)
        raw_response = bytes(response.content)
        if not raw_response:
            raise SourcePageError(f'source returned an empty response for {url}')
        result = parse_result_page(raw_response, selected_date=selected_date, source_url=url)
        return ExtractedResult(raw_response=raw_response, result=result)

    def build_source_url(self, selected_date: date) -> str:
        base_url = str(self.settings.source_base_url).rstrip('/')
        return f'{base_url}/xsmb-{selected_date:%d-%m-%Y}.html'

    def _get_with_retry(self, url: str) -> HttpResponse:
        retrying = Retrying(
            stop=stop_after_attempt(self.settings.http_max_retries + 1),
            wait=wait_exponential(multiplier=self.settings.http_retry_backoff_seconds, max=30),
            retry=retry_if_exception_type(TransientExtractionError),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._request_once(url)
        raise AssertionError('retry loop ended without returning or raising')

    def _request_once(self, url: str) -> HttpResponse:
        try:
            response = self.http_client.get(url, timeout=self.settings.http_timeout_seconds)
        except (Timeout, RequestsConnectionError) as exc:
            raise TransientExtractionError(f'transient source request failure for {url}') from exc
        except RequestException as exc:
            raise ExtractionError(f'source request failed for {url}') from exc

        if response.status_code in TRANSIENT_HTTP_STATUS_CODES:
            raise TransientHttpStatusError(response.status_code, url)
        if response.status_code != 200:
            raise HttpStatusError(response.status_code, url)
        return response


def parse_result_page(raw_response: bytes, selected_date: date, source_url: str) -> LotteryResult:
    """Validate a source page and return one complete canonical result."""

    soup = BeautifulSoup(raw_response, 'lxml')
    represented_date, result_container = _represented_date_and_container(soup)
    if represented_date is None:
        raise SourcePageError('could not determine the draw date represented by the source page')
    if represented_date != selected_date:
        raise RequestedDateMismatchError(
            f'requested {selected_date.isoformat()} but page represents {represented_date.isoformat()}'
        )

    no_draw_notice = explicit_no_draw_notice(soup)
    if no_draw_notice is not None:
        raise NoDrawSourcePageError(selected_date, source_url, no_draw_notice)

    raw_groups: dict[PrizeGroup, list[str]] = {}
    container = result_container or soup
    for group, spec in PRIZE_SPECS.items():
        css_class = 'special-prize' if group is PrizeGroup.SPECIAL else group.value
        raw_groups[group] = [element.get_text(strip=True) for element in container.select(f'.{css_class}')]

    _repair_exact_prize5_prize6_transposition(raw_groups)
    groups: dict[str, list[str]] = {}
    for group, spec in PRIZE_SPECS.items():
        values = raw_groups[group]
        if len(values) != spec.count:
            raise PrizeCountError(f'{group.value} expected {spec.count} values, got {len(values)}')
        groups[group.value] = values

    try:
        return LotteryResult.from_prize_groups(selected_date, source_url, groups)
    except ValueError as exc:
        raise SourcePageError(f'invalid prize value: {exc}') from exc


def _represented_date_and_container(soup: BeautifulSoup) -> tuple[date | None, Tag | None]:
    section = soup.find('section', id=SECTION_ID_PATTERN)
    if isinstance(section, Tag):
        section_id = section.get('id')
        if isinstance(section_id, str):
            match = SECTION_ID_PATTERN.fullmatch(section_id)
            if match:
                return datetime.strptime(match.group(1), '%d%m%Y').date(), section

    canonical = soup.select_one('link[rel~="canonical"]')
    if isinstance(canonical, Tag):
        href = canonical.get('href')
        if isinstance(href, str):
            match = CANONICAL_DATE_PATTERN.search(href)
            if match:
                day, month, year = (int(value) for value in match.groups())
                return date(year, month, day), None

    for element_name in ('h1', 'title'):
        element = soup.find(element_name)
        if element is None:
            continue
        match = DISPLAY_DATE_PATTERN.search(element.get_text(' ', strip=True))
        if match:
            day, month, year = (int(value) for value in match.groups())
            return date(year, month, day), None
    return None, None


def explicit_no_draw_notice(soup: BeautifulSoup) -> str | None:
    """Return a normalized source notice only when it explicitly says no draw."""

    text_node = soup.find(string=NO_DRAW_PATTERN)
    if text_node is None:
        return None
    parent = text_node.parent
    notice = parent.get_text(' ', strip=True) if isinstance(parent, Tag) else str(text_node).strip()
    return re.sub(r'\s+', ' ', notice)[:500]


def _repair_exact_prize5_prize6_transposition(raw_groups: dict[PrizeGroup, list[str]]) -> None:
    """Repair the recognizable historical page where prize-5/6 CSS classes are exchanged."""

    prize5 = raw_groups[PrizeGroup.PRIZE5]
    prize6 = raw_groups[PrizeGroup.PRIZE6]
    other_groups_are_complete = all(
        len(raw_groups[group]) == spec.count
        for group, spec in PRIZE_SPECS.items()
        if group not in {PrizeGroup.PRIZE5, PrizeGroup.PRIZE6}
    )
    prize5_looks_like_prize6 = len(prize5) == PRIZE_SPECS[PrizeGroup.PRIZE6].count and all(
        _is_ascii_width(value, PRIZE_SPECS[PrizeGroup.PRIZE6].width) for value in prize5
    )
    prize6_looks_like_prize5 = len(prize6) == PRIZE_SPECS[PrizeGroup.PRIZE5].count and all(
        _is_ascii_width(value, PRIZE_SPECS[PrizeGroup.PRIZE5].width) for value in prize6
    )
    if other_groups_are_complete and prize5_looks_like_prize6 and prize6_looks_like_prize5:
        raw_groups[PrizeGroup.PRIZE5], raw_groups[PrizeGroup.PRIZE6] = prize6, prize5


def _is_ascii_width(value: str, width: int) -> bool:
    return len(value) == width and value.isascii() and value.isdigit()
