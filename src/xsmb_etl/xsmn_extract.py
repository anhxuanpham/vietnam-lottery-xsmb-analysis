"""Reliable extraction and parsing for one XSMN result date."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from xsmb_etl.extract import (
    DISPLAY_DATE_PATTERN,
    NoDrawSourcePageError,
    PrizeCountError,
    RequestedDateMismatchError,
    ResultExtractor,
    SourcePageError,
    explicit_no_draw_notice,
)
from xsmb_etl.xsmn_models import SouthernDailyResult, SouthernPrizeGroup, SouthernStationResult


XSMN_SECTION_ID_PATTERN = re.compile(r'^mn_kqngay_(\d{8})$')
XSMN_CANONICAL_DATE_PATTERN = re.compile(r'/xsmn-(\d{2})-(\d{2})-(\d{4})\.html')
STATION_CODE_PATTERN = re.compile(r'/xs(?P<code>[a-z0-9]+)-p\d+\.html$', re.IGNORECASE)
ROW_GROUPS = {
    '8': SouthernPrizeGroup.PRIZE8,
    '7': SouthernPrizeGroup.PRIZE7,
    '6': SouthernPrizeGroup.PRIZE6,
    '5': SouthernPrizeGroup.PRIZE5,
    '4': SouthernPrizeGroup.PRIZE4,
    '3': SouthernPrizeGroup.PRIZE3,
    '2': SouthernPrizeGroup.PRIZE2,
    '1': SouthernPrizeGroup.PRIZE1,
    'ĐB': SouthernPrizeGroup.SPECIAL,
    'DB': SouthernPrizeGroup.SPECIAL,
}


@dataclass(frozen=True)
class SouthernExtractedResult:
    raw_response: bytes
    result: SouthernDailyResult


class SouthernResultExtractor(ResultExtractor):
    def extract(self, selected_date: date) -> SouthernExtractedResult:
        url = self.build_source_url(selected_date)
        response = self._get_with_retry(url)
        raw_response = bytes(response.content)
        if not raw_response:
            raise SourcePageError(f'source returned an empty response for {url}')
        result = parse_southern_result_page(raw_response, selected_date=selected_date, source_url=url)
        return SouthernExtractedResult(raw_response=raw_response, result=result)

    def build_source_url(self, selected_date: date) -> str:
        base_url = str(self.settings.source_base_url).rstrip('/')
        return f'{base_url}/xsmn-{selected_date:%d-%m-%Y}.html'


def parse_southern_result_page(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> SouthernDailyResult:
    soup = BeautifulSoup(raw_response, 'lxml')
    represented_date, result_container = _represented_date_and_container(soup)
    if represented_date is None:
        raise SourcePageError('could not determine the XSMN draw date represented by the source page')
    if represented_date != selected_date:
        raise RequestedDateMismatchError(
            f'requested {selected_date.isoformat()} but page represents {represented_date.isoformat()}'
        )

    no_draw_notice = explicit_no_draw_notice(soup)
    if no_draw_notice is not None:
        raise NoDrawSourcePageError(selected_date, source_url, no_draw_notice)

    container = result_container or soup
    table = container.select_one('table.table-result.table-xsmn')
    if not isinstance(table, Tag):
        raise SourcePageError('could not find the XSMN result table')

    station_links = table.select('thead h3 a[href]')
    if not station_links:
        raise SourcePageError('XSMN result table does not contain station headers')

    stations = []
    for station_index, station_link in enumerate(station_links):
        station_name = station_link.get_text(' ', strip=True)
        href = station_link.get('href')
        if not isinstance(href, str):
            raise SourcePageError(f'station {station_name or station_index + 1} has no source URL')
        code_match = STATION_CODE_PATTERN.search(href)
        if code_match is None:
            raise SourcePageError(f'could not determine station code from {href}')
        station_code = code_match.group('code').upper()
        groups: dict[str, list[str]] = {}

        for row in table.select('tbody tr'):
            header = row.find('th', recursive=False)
            if not isinstance(header, Tag):
                continue
            label = header.get_text(' ', strip=True).upper()
            group = ROW_GROUPS.get(label)
            if group is None:
                continue
            cells = row.find_all('td', recursive=False)
            if len(cells) != len(station_links):
                raise SourcePageError(f'{group.value} expected {len(station_links)} station columns, got {len(cells)}')
            values = [element.get_text(strip=True) for element in cells[station_index].select('span.xs_prize1')]
            groups[group.value] = values

        try:
            stations.append(
                SouthernStationResult.from_prize_groups(
                    draw_date=selected_date,
                    station_code=station_code,
                    station_name=station_name,
                    station_url=urljoin(source_url, href),
                    source_url=source_url,
                    groups=groups,
                )
            )
        except ValueError as exc:
            raise PrizeCountError(f'invalid {station_name} result: {exc}') from exc

    try:
        return SouthernDailyResult(draw_date=selected_date, source_url=source_url, stations=tuple(stations))
    except ValueError as exc:
        raise SourcePageError(f'invalid XSMN daily result: {exc}') from exc


def _represented_date_and_container(soup: BeautifulSoup) -> tuple[date | None, Tag | None]:
    section = soup.find('section', id=XSMN_SECTION_ID_PATTERN)
    if isinstance(section, Tag):
        section_id = section.get('id')
        if isinstance(section_id, str):
            match = XSMN_SECTION_ID_PATTERN.fullmatch(section_id)
            if match:
                return datetime.strptime(match.group(1), '%d%m%Y').date(), section

    canonical = soup.select_one('link[rel~="canonical"]')
    if isinstance(canonical, Tag):
        href = canonical.get('href')
        if isinstance(href, str):
            match = XSMN_CANONICAL_DATE_PATTERN.search(href)
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
