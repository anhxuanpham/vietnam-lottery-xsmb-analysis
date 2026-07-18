from __future__ import annotations

from datetime import date

from xsmb_etl.control import DrawStatus
from xsmb_etl.marts import build_gold_tables, dim_date_frame, dim_number_frame
from xsmb_etl.models import LotteryResult
from xsmb_etl.transform import draw_results_frame


def test_dimensions_are_deterministic() -> None:
    numbers = dim_number_frame()
    dates = dim_date_frame(
        date(2026, 7, 15),
        date(2026, 7, 16),
        {date(2026, 7, 15): DrawStatus.FAILED, date(2026, 7, 16): DrawStatus.SUCCESS},
    )

    assert numbers.shape == (100, 7)
    assert numbers.loc[numbers['number_2d'].eq('00'), 'is_double'].item()
    assert numbers.loc[numbers['number_2d'].eq('42'), 'digit_sum'].item() == 6
    assert dates['draw_status'].tolist() == ['failed', 'success']
    assert dates['day_of_week'].tolist() == [3, 4]


def test_gold_tables_share_run_id(grouped_prize_values: dict[str, list[str]]) -> None:
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    tables = build_gold_tables(draw_results_frame([result], 'source-run'), run_id='gold-run')

    assert set(tables) == {
        'fact-draw-result',
        'fact-loto-daily',
        'fact-special-prize',
        'dim-date',
        'dim-number',
    }
    assert tables['fact-special-prize'].iloc[0]['formatted_number'] == '96763'
    for dataframe in tables.values():
        if 'run_id' in dataframe:
            assert dataframe['run_id'].unique().tolist() == ['gold-run']
