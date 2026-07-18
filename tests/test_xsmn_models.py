from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from xsmb_etl.xsmn_models import (
    SOUTHERN_EXPECTED_RESULT_COUNT,
    SOUTHERN_PRIZE_SPECS,
    SouthernDailyResult,
    SouthernPrizeGroup,
    SouthernStationResult,
)


def _groups() -> dict[str, list[str]]:
    return {
        'prize8': ['07'],
        'prize7': ['067'],
        'prize6': ['0219', '7281', '9418'],
        'prize5': ['2023'],
        'prize4': ['00022', '64615', '28858', '13087', '08404', '45891', '42815'],
        'prize3': ['17532', '66620'],
        'prize2': ['09628'],
        'prize1': ['01378'],
        'special': ['005113'],
    }


def _station(code: str = 'TN') -> SouthernStationResult:
    return SouthernStationResult.from_prize_groups(
        draw_date=date(2026, 7, 16),
        station_code=code,
        station_name='Tây Ninh',
        station_url='https://example.test/xstn',
        source_url='https://example.test/xsmn',
        groups=_groups(),
    )


def test_southern_station_model_preserves_leading_zero_widths() -> None:
    result = _station()

    assert len(result.prizes) == SOUTHERN_EXPECTED_RESULT_COUNT == 18
    assert result.prizes_for(SouthernPrizeGroup.PRIZE8)[0].formatted_number == '07'
    assert result.prizes_for(SouthernPrizeGroup.PRIZE6)[0].formatted_number == '0219'
    assert result.prizes_for(SouthernPrizeGroup.SPECIAL)[0].formatted_number == '005113'
    assert SOUTHERN_PRIZE_SPECS[SouthernPrizeGroup.PRIZE4].count == 7


def test_southern_station_model_rejects_incomplete_groups() -> None:
    groups = _groups()
    groups['prize4'].pop()

    with pytest.raises(ValueError, match='prize4 expected 7'):
        SouthernStationResult.from_prize_groups(
            draw_date=date(2026, 7, 16),
            station_code='TN',
            station_name='Tây Ninh',
            station_url='',
            source_url='',
            groups=groups,
        )


def test_southern_daily_result_rejects_duplicate_station_codes() -> None:
    with pytest.raises(ValidationError, match='station codes must be unique'):
        SouthernDailyResult(
            draw_date=date(2026, 7, 16),
            source_url='https://example.test/xsmn',
            stations=(_station(), _station()),
        )
