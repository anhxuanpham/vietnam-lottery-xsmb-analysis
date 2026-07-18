from __future__ import annotations

from datetime import date

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype

from xsmb_etl.models import LotteryResult
from xsmb_etl.transform import draw_results_frame, loto_daily_frame


def _result(draw_date: date, values: dict[str, list[str]]) -> LotteryResult:
    return LotteryResult.from_prize_groups(draw_date, f'https://example.test/{draw_date.isoformat()}', values)


def test_draw_results_are_long_form_typed_and_deterministic(grouped_prize_values: dict[str, list[str]]) -> None:
    dataframe = draw_results_frame([_result(date(2026, 7, 16), grouped_prize_values)], 'run-1')

    assert dataframe.shape == (27, 11)
    assert is_datetime64_any_dtype(dataframe['draw_date'])
    assert dataframe['full_number'].dtype == 'int64'
    assert dataframe.loc[dataframe['formatted_number'].eq('00916'), 'full_number'].item() == 916
    assert dataframe['run_id'].unique().tolist() == ['run-1']


def test_loto_daily_has_100_rows_and_frequency_sum_27(grouped_prize_values: dict[str, list[str]]) -> None:
    draw = draw_results_frame([_result(date(2026, 7, 16), grouped_prize_values)], 'run-1')
    loto = loto_daily_frame(draw)

    assert loto.shape == (100, 11)
    assert loto['number_2d'].tolist() == [f'{number:02d}' for number in range(100)]
    assert loto['frequency'].sum() == 27
    assert loto.loc[loto['number_2d'].eq('63'), 'frequency'].item() == 3
    assert loto['draws_since_previous'].isna().all()
    assert set(loto['previous_appearance_status']) == {'never_seen'}


def test_waiting_and_rolling_metrics_use_prior_draws_not_calendar_rows(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    first = _result(date(2026, 7, 14), grouped_prize_values)
    second_values = {key: list(values) for key, values in grouped_prize_values.items()}
    second_values['special'] = ['96700']
    second = _result(date(2026, 7, 16), second_values)
    loto = loto_daily_frame(draw_results_frame([second, first], 'run-2'))
    number_63 = loto.loc[loto['number_2d'].eq('63')].reset_index(drop=True)

    assert number_63.loc[1, 'draws_since_previous'] == 1
    assert number_63.loc[1, 'calendar_days_since_previous'] == 2
    assert number_63.loc[1, 'rolling_7_frequency'] == 5
    assert pd.isna(loto.loc[loto['number_2d'].eq('99')].iloc[1]['draws_since_previous'])
