"""Authoritative station calendars for the station-grain lottery regions."""

from __future__ import annotations

from datetime import date
from types import MappingProxyType
from typing import Final

from xsmb_etl.run_models import LotteryRegion


# ``date.weekday()`` uses Monday=0 through Sunday=6.
XSMN_STATIONS_BY_WEEKDAY: Final = MappingProxyType(
    {
        0: frozenset({'HCM', 'DT', 'CM'}),
        1: frozenset({'BTR', 'VT', 'BL'}),
        2: frozenset({'DN', 'CT', 'ST'}),
        3: frozenset({'TN', 'AG', 'BTH'}),
        4: frozenset({'VL', 'BD', 'TV'}),
        5: frozenset({'HCM', 'LA', 'BP', 'HG'}),
        6: frozenset({'TG', 'KG', 'DL'}),
    }
)

XSMT_STATIONS_BY_WEEKDAY: Final = MappingProxyType(
    {
        0: frozenset({'TTH', 'PY'}),
        1: frozenset({'DLK', 'QNA'}),
        2: frozenset({'DNA', 'KH'}),
        3: frozenset({'BDI', 'QB', 'QT'}),
        4: frozenset({'GL', 'NT'}),
        5: frozenset({'DNA', 'QNG', 'DNO'}),
        6: frozenset({'KH', 'KT', 'TTH'}),
    }
)

XSMT_SUNDAY_STATIONS_THROUGH_2021: Final = frozenset({'KH', 'KT'})
XSMT_SUNDAY_TTH_START_DATE: Final = date(2022, 1, 2)

XSMT_DOCUMENTED_PARTIAL_DRAWS: Final = MappingProxyType(
    {
        date(2021, 7, 27): frozenset({'QNA'}),
        date(2021, 8, 3): frozenset({'QNA'}),
        date(2021, 8, 6): frozenset({'GL'}),
        date(2021, 8, 18): frozenset({'KH'}),
        date(2021, 8, 21): frozenset({'DNO', 'QNG'}),
        date(2021, 9, 4): frozenset({'DNA', 'DNO'}),
    }
)


def expected_station_codes(region: LotteryRegion, draw_date: date) -> frozenset[str]:
    """Return the exact station set that is expected to draw on ``draw_date``."""

    if region is LotteryRegion.XSMN:
        return XSMN_STATIONS_BY_WEEKDAY[draw_date.weekday()]

    if region is LotteryRegion.XSMT:
        if partial := XSMT_DOCUMENTED_PARTIAL_DRAWS.get(draw_date):
            return partial
        if draw_date.weekday() == 6 and draw_date < XSMT_SUNDAY_TTH_START_DATE:
            return XSMT_SUNDAY_STATIONS_THROUGH_2021
        return XSMT_STATIONS_BY_WEEKDAY[draw_date.weekday()]

    raise ValueError(f'station calendar is not available for region {region.value}')


__all__ = [
    'XSMN_STATIONS_BY_WEEKDAY',
    'XSMT_DOCUMENTED_PARTIAL_DRAWS',
    'XSMT_STATIONS_BY_WEEKDAY',
    'XSMT_SUNDAY_STATIONS_THROUGH_2021',
    'XSMT_SUNDAY_TTH_START_DATE',
    'expected_station_codes',
]
