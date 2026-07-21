from __future__ import annotations

from datetime import date

import pytest

from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.station_calendar import XSMT_DOCUMENTED_PARTIAL_DRAWS, expected_station_codes


@pytest.mark.parametrize(
    ('draw_date', 'expected'),
    [
        (date(2010, 1, 4), {'HCM', 'DT', 'CM'}),
        (date(2010, 1, 5), {'BTR', 'VT', 'BL'}),
        (date(2010, 1, 6), {'DN', 'CT', 'ST'}),
        (date(2010, 1, 7), {'TN', 'AG', 'BTH'}),
        (date(2010, 1, 8), {'VL', 'BD', 'TV'}),
        (date(2010, 1, 9), {'HCM', 'LA', 'BP', 'HG'}),
        (date(2010, 1, 10), {'TG', 'KG', 'DL'}),
    ],
)
def test_xsmn_calendar_has_the_exact_weekday_station_sets(
    draw_date: date,
    expected: set[str],
) -> None:
    assert expected_station_codes(LotteryRegion.XSMN, draw_date) == expected


@pytest.mark.parametrize(
    ('draw_date', 'expected'),
    [
        (date(2026, 7, 13), {'TTH', 'PY'}),
        (date(2026, 7, 14), {'DLK', 'QNA'}),
        (date(2026, 7, 15), {'DNA', 'KH'}),
        (date(2026, 7, 16), {'BDI', 'QB', 'QT'}),
        (date(2026, 7, 17), {'GL', 'NT'}),
        (date(2026, 7, 18), {'DNA', 'QNG', 'DNO'}),
        (date(2026, 7, 19), {'KH', 'KT', 'TTH'}),
    ],
)
def test_xsmt_calendar_has_the_exact_weekday_station_sets(
    draw_date: date,
    expected: set[str],
) -> None:
    assert expected_station_codes(LotteryRegion.XSMT, draw_date) == expected


def test_xsmt_sunday_added_tth_on_2022_01_02() -> None:
    assert expected_station_codes(LotteryRegion.XSMT, date(2021, 12, 26)) == {'KH', 'KT'}
    assert expected_station_codes(LotteryRegion.XSMT, date(2022, 1, 2)) == {'KH', 'KT', 'TTH'}


@pytest.mark.parametrize(('draw_date', 'expected'), XSMT_DOCUMENTED_PARTIAL_DRAWS.items())
def test_xsmt_calendar_applies_documented_2021_partial_draws(
    draw_date: date,
    expected: frozenset[str],
) -> None:
    assert expected_station_codes(LotteryRegion.XSMT, draw_date) == expected


def test_station_calendar_rejects_non_station_grain_region() -> None:
    with pytest.raises(ValueError, match='not available'):
        expected_station_codes(LotteryRegion.XSMB, date(2026, 7, 20))
