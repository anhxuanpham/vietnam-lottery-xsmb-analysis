from __future__ import annotations

from datetime import date

import pytest

from xsmb_etl.models import LotteryResult, PrizeGroup


def test_canonical_result_preserves_width_and_leading_zero_semantics(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    result = LotteryResult.from_prize_groups(
        draw_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmb-16-07-2026.html',
        groups=grouped_prize_values,
    )

    assert len(result.prizes) == 27
    leading_zero_prize = result.prizes_for(PrizeGroup.PRIZE3)[5]
    assert leading_zero_prize.full_number == 916
    assert leading_zero_prize.prize_width == 5
    assert leading_zero_prize.formatted_number == '00916'
    assert leading_zero_prize.loto_2d == '16'


def test_canonical_result_rejects_wrong_prize_count(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    grouped_prize_values['prize2'] = ['56517']

    with pytest.raises(ValueError, match='prize2 expected 2 values, got 1'):
        LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)


def test_canonical_result_rejects_value_outside_prize_width(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    grouped_prize_values['prize7'][0] = '100'

    with pytest.raises(ValueError, match='prize7 value must contain exactly 2 digits'):
        LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)


def test_legacy_mapping_keeps_original_27_column_shape(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    legacy = result.to_legacy_dict()

    assert len(legacy) == 28
    assert legacy['date'] == date(2026, 7, 16)
    assert legacy['special'] == 96763
    assert legacy['prize3_6'] == 916
    assert legacy['prize7_4'] == 61
