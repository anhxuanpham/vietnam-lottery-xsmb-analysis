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
FALLBACK_STATION_CODE_PATTERN = re.compile(r'^/xs(?P<code>[a-z0-9]+)(?:-[a-z0-9]+)*$', re.IGNORECASE)
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
FALLBACK_ROW_GROUPS = {
    'G.8': SouthernPrizeGroup.PRIZE8,
    'G.7': SouthernPrizeGroup.PRIZE7,
    'G.6': SouthernPrizeGroup.PRIZE6,
    'G.5': SouthernPrizeGroup.PRIZE5,
    'G.4': SouthernPrizeGroup.PRIZE4,
    'G.3': SouthernPrizeGroup.PRIZE3,
    'G.2': SouthernPrizeGroup.PRIZE2,
    'G.1': SouthernPrizeGroup.PRIZE1,
    'ĐB': SouthernPrizeGroup.SPECIAL,
    'DB': SouthernPrizeGroup.SPECIAL,
}


class RecoverableHistoricalResultError(PrizeCountError):
    """The primary historical page is malformed in a strictly reconcilable way."""


class TruncatedSpecialPrizeError(RecoverableHistoricalResultError):
    """The primary historical page lost the first special-prize digit."""


class SourceReconciliationError(SourcePageError):
    """Primary and fallback sources do not describe the same draw."""


@dataclass(frozen=True)
class _StationPageValues:
    station_code: str
    station_name: str
    station_url: str
    groups: dict[str, list[str]]


@dataclass(frozen=True)
class SouthernExtractedResult:
    raw_response: bytes
    result: SouthernDailyResult
    fallback_response: bytes | None = None
    fallback_url: str | None = None

    def __post_init__(self) -> None:
        if (self.fallback_response is None) != (self.fallback_url is None):
            raise ValueError('fallback response and URL must be provided together')


class SouthernResultExtractor(ResultExtractor):
    def extract(self, selected_date: date) -> SouthernExtractedResult:
        url = self.build_source_url(selected_date)
        response = self._get_with_retry(url)
        raw_response = bytes(response.content)
        if not raw_response:
            raise SourcePageError(f'source returned an empty response for {url}')
        try:
            result = parse_southern_result_page(raw_response, selected_date=selected_date, source_url=url)
            return SouthernExtractedResult(raw_response=raw_response, result=result)
        except RecoverableHistoricalResultError:
            fallback_url = self.build_fallback_source_url(selected_date)
            fallback_http_response = self._get_with_retry(fallback_url)
            fallback_response = bytes(fallback_http_response.content)
            if not fallback_response:
                raise SourcePageError(f'fallback source returned an empty response for {fallback_url}')
            fallback_result = parse_southern_fallback_page(
                fallback_response,
                selected_date=selected_date,
                source_url=fallback_url,
            )
            result = reconcile_historical_southern_page(
                raw_response,
                fallback_result=fallback_result,
                selected_date=selected_date,
                source_url=url,
            )
            return SouthernExtractedResult(
                raw_response=raw_response,
                result=result,
                fallback_response=fallback_response,
                fallback_url=fallback_url,
            )

    def build_source_url(self, selected_date: date) -> str:
        base_url = str(self.settings.source_base_url).rstrip('/')
        return f'{base_url}/xsmn-{selected_date:%d-%m-%Y}.html'

    def build_fallback_source_url(self, selected_date: date) -> str:
        base_url = str(self.settings.xsmn_fallback_base_url).rstrip('/')
        return f'{base_url}/ngay/{selected_date:%d-%m-%Y}'


def parse_southern_result_page(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> SouthernDailyResult:
    stations = _primary_station_values(
        raw_response,
        selected_date=selected_date,
        source_url=source_url,
    )
    return _build_southern_result(stations, selected_date=selected_date, source_url=source_url)


def _primary_station_values(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> tuple[_StationPageValues, ...]:
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

    stations: list[_StationPageValues] = []
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

        stations.append(
            _StationPageValues(
                station_code=station_code,
                station_name=station_name,
                station_url=urljoin(source_url, href),
                groups=groups,
            )
        )

    return tuple(stations)


def _build_southern_result(
    station_values: tuple[_StationPageValues, ...],
    *,
    selected_date: date,
    source_url: str,
) -> SouthernDailyResult:
    stations = []
    for station in station_values:
        try:
            stations.append(
                SouthernStationResult.from_prize_groups(
                    draw_date=selected_date,
                    station_code=station.station_code,
                    station_name=station.station_name,
                    station_url=station.station_url,
                    source_url=source_url,
                    groups=station.groups,
                )
            )
        except ValueError as exc:
            if str(exc) == 'special value must contain exactly 6 digits' and _has_five_digit_special(station.groups):
                error_type = TruncatedSpecialPrizeError
            elif _has_recoverable_historical_values(station.groups):
                error_type = RecoverableHistoricalResultError
            else:
                error_type = PrizeCountError
            raise error_type(f'invalid {station.station_name} result: {exc}') from exc

    try:
        return SouthernDailyResult(draw_date=selected_date, source_url=source_url, stations=tuple(stations))
    except ValueError as exc:
        raise SourcePageError(f'invalid XSMN daily result: {exc}') from exc


def parse_southern_fallback_page(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> SouthernDailyResult:
    """Parse the independent historical source used only for reconciliation."""

    soup = BeautifulSoup(raw_response, 'lxml')
    represented_date = _fallback_represented_date(soup)
    if represented_date is None:
        raise SourcePageError('could not determine the fallback XSMN draw date')
    if represented_date != selected_date:
        raise RequestedDateMismatchError(
            f'requested {selected_date.isoformat()} but fallback page represents {represented_date.isoformat()}'
        )

    table = soup.select_one('table.tbl-xsmn')
    if not isinstance(table, Tag):
        raise SourcePageError('could not find the fallback XSMN result table')
    header_row = table.find('tr')
    if not isinstance(header_row, Tag):
        raise SourcePageError('fallback XSMN result table has no header row')
    header_cells = header_row.find_all('th', recursive=False)
    station_links = [cell.find('a', href=FALLBACK_STATION_CODE_PATTERN) for cell in header_cells[1:]]
    if not station_links or any(not isinstance(link, Tag) for link in station_links):
        raise SourcePageError('fallback XSMN result table does not contain station headers')

    station_groups: list[dict[str, list[str]]] = [{} for _ in station_links]
    for row in table.find_all('tr', recursive=False)[1:]:
        cells = row.find_all(['th', 'td'], recursive=False)
        if not cells:
            continue
        label = cells[0].get_text(' ', strip=True).upper()
        group = FALLBACK_ROW_GROUPS.get(label)
        if group is None:
            continue
        result_cells = cells[1:]
        if len(result_cells) != len(station_links):
            raise SourcePageError(
                f'fallback {group.value} expected {len(station_links)} station columns, got {len(result_cells)}'
            )
        for station_index, cell in enumerate(result_cells):
            station_groups[station_index][group.value] = [
                value for value in cell.stripped_strings if value.isascii() and value.isdigit()
            ]

    stations: list[_StationPageValues] = []
    for station_link, groups in zip(station_links, station_groups, strict=True):
        assert isinstance(station_link, Tag)
        href = station_link.get('href')
        assert isinstance(href, str)
        code_match = FALLBACK_STATION_CODE_PATTERN.fullmatch(href)
        assert code_match is not None
        stations.append(
            _StationPageValues(
                station_code=code_match.group('code').upper(),
                station_name=station_link.get_text(' ', strip=True),
                station_url=urljoin(source_url, href),
                groups=groups,
            )
        )
    return _build_southern_result(tuple(stations), selected_date=selected_date, source_url=source_url)


def reconcile_historical_southern_page(
    raw_response: bytes,
    *,
    fallback_result: SouthernDailyResult,
    selected_date: date,
    source_url: str,
) -> SouthernDailyResult:
    """Repair only proven historical corruption after full cross-source comparison."""

    primary_stations = _primary_station_values(
        raw_response,
        selected_date=selected_date,
        source_url=source_url,
    )
    fallback_by_primary_code = _match_fallback_stations(primary_stations, fallback_result)
    daywide_special_repair = _daywide_special_truncation_is_consistent(
        primary_stations,
        fallback_by_primary_code,
    )

    reconciled: list[_StationPageValues] = []
    for primary in primary_stations:
        fallback = fallback_by_primary_code[primary.station_code]
        fallback_groups = _groups_from_station(fallback)
        repaired_groups: set[SouthernPrizeGroup] = set()
        if _small_prizes_are_transposed(primary.groups, fallback_groups):
            repaired_groups.update((SouthernPrizeGroup.PRIZE8, SouthernPrizeGroup.PRIZE7))

        groups = {group: list(values) for group, values in primary.groups.items()}
        for group in SouthernPrizeGroup:
            primary_values = primary.groups.get(group.value, [])
            fallback_values = fallback_groups[group.value]
            if primary_values == fallback_values:
                continue
            if group in repaired_groups:
                groups[group.value] = fallback_values
                continue
            if group is SouthernPrizeGroup.SPECIAL and (
                _is_truncated_special(primary_values, fallback_values) or daywide_special_repair
            ):
                groups[group.value] = fallback_values
                continue
            if _values_match_except_placeholders(primary_values, fallback_values):
                groups[group.value] = fallback_values
                continue
            raise SourceReconciliationError(
                f'primary and fallback XSMN values differ for {primary.station_code} {group.value}'
            )

        reconciled.append(
            _StationPageValues(
                station_code=primary.station_code,
                station_name=primary.station_name,
                station_url=primary.station_url,
                groups=groups,
            )
        )

    return _build_southern_result(tuple(reconciled), selected_date=selected_date, source_url=source_url)


def _match_fallback_stations(
    primary_stations: tuple[_StationPageValues, ...],
    fallback_result: SouthernDailyResult,
) -> dict[str, SouthernStationResult]:
    remaining = list(fallback_result.stations)
    matches: dict[str, SouthernStationResult] = {}
    for primary in primary_stations:
        candidates = [station for station in remaining if station.station_code == primary.station_code]
        if not candidates:
            primary_name = _normalized_station_name(primary.station_name)
            candidates = [
                station for station in remaining if _normalized_station_name(station.station_name) == primary_name
            ]
        if len(candidates) != 1:
            raise SourceReconciliationError('primary and fallback XSMN station sets differ')
        match = candidates[0]
        remaining.remove(match)
        matches[primary.station_code] = match
    if remaining:
        raise SourceReconciliationError('primary and fallback XSMN station sets differ')
    return matches


def _normalized_station_name(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip().casefold()


def _groups_from_station(station: SouthernStationResult) -> dict[str, list[str]]:
    return {
        group.value: [prize.formatted_number for prize in station.prizes_for(group)] for group in SouthernPrizeGroup
    }


def _has_five_digit_special(groups: dict[str, list[str]]) -> bool:
    values = groups.get(SouthernPrizeGroup.SPECIAL.value, [])
    return len(values) == 1 and len(values[0]) == 5 and values[0].isascii() and values[0].isdigit()


def _has_recoverable_historical_values(groups: dict[str, list[str]]) -> bool:
    return any(_is_placeholder(value) for values in groups.values() for value in values) or (
        len(groups.get(SouthernPrizeGroup.PRIZE8.value, [])) == 1
        and len(groups.get(SouthernPrizeGroup.PRIZE7.value, [])) == 1
        and _has_ascii_digit_width(groups[SouthernPrizeGroup.PRIZE8.value][0], 3)
        and _has_ascii_digit_width(groups[SouthernPrizeGroup.PRIZE7.value][0], 2)
    )


def _small_prizes_are_transposed(
    primary_groups: dict[str, list[str]],
    fallback_groups: dict[str, list[str]],
) -> bool:
    primary_prize8 = primary_groups.get(SouthernPrizeGroup.PRIZE8.value, [])
    primary_prize7 = primary_groups.get(SouthernPrizeGroup.PRIZE7.value, [])
    fallback_prize8 = fallback_groups[SouthernPrizeGroup.PRIZE8.value]
    fallback_prize7 = fallback_groups[SouthernPrizeGroup.PRIZE7.value]
    return (
        len(primary_prize8) == len(primary_prize7) == len(fallback_prize8) == len(fallback_prize7) == 1
        and primary_prize8 == fallback_prize7
        and primary_prize7 == fallback_prize8
    )


def _is_truncated_special(primary_values: list[str], fallback_values: list[str]) -> bool:
    return (
        len(primary_values) == len(fallback_values) == 1
        and _has_ascii_digit_width(primary_values[0], 5)
        and _has_ascii_digit_width(fallback_values[0], 6)
        and fallback_values[0].endswith(primary_values[0])
    )


def _daywide_special_truncation_is_consistent(
    primary_stations: tuple[_StationPageValues, ...],
    fallback_by_primary_code: dict[str, SouthernStationResult],
) -> bool:
    """Allow one damaged suffix when a four-station page is otherwise consistently truncated."""

    suffix_matches = 0
    if len(primary_stations) < 4:
        return False
    for primary in primary_stations:
        fallback_groups = _groups_from_station(fallback_by_primary_code[primary.station_code])
        primary_values = primary.groups.get(SouthernPrizeGroup.SPECIAL.value, [])
        fallback_values = fallback_groups[SouthernPrizeGroup.SPECIAL.value]
        if not (
            len(primary_values) == len(fallback_values) == 1
            and _has_ascii_digit_width(primary_values[0], 5)
            and _has_ascii_digit_width(fallback_values[0], 6)
        ):
            return False
        suffix_matches += fallback_values[0].endswith(primary_values[0])
    return suffix_matches >= len(primary_stations) - 1


def _values_match_except_placeholders(primary_values: list[str], fallback_values: list[str]) -> bool:
    return (
        len(primary_values) == len(fallback_values)
        and any(_is_placeholder(value) for value in primary_values)
        and all(
            primary == fallback or _is_placeholder(primary)
            for primary, fallback in zip(primary_values, fallback_values)
        )
    )


def _is_placeholder(value: str) -> bool:
    return bool(value) and set(value) == {'.'}


def _has_ascii_digit_width(value: str, width: int) -> bool:
    return len(value) == width and value.isascii() and value.isdigit()


def _fallback_represented_date(soup: BeautifulSoup) -> date | None:
    for element_name in ('h1', 'title'):
        element = soup.find(element_name)
        if element is None:
            continue
        match = DISPLAY_DATE_PATTERN.search(element.get_text(' ', strip=True))
        if match:
            day, month, year = (int(value) for value in match.groups())
            return date(year, month, day)
    return None


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
