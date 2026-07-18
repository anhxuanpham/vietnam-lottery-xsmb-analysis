"""BI-friendly Gold facts and dimensions."""

from __future__ import annotations

from datetime import date

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.transform import loto_daily_frame


def special_prize_frame(draw_results: pd.DataFrame) -> pd.DataFrame:
    special = draw_results.loc[draw_results['prize_group'].eq('special')].copy()
    formatted = special['formatted_number'].astype('string')
    output = pd.DataFrame(
        {
            'draw_date': pd.to_datetime(special['draw_date']),
            'full_number': special['full_number'].astype('int64'),
            'formatted_number': formatted,
            'tail_2d': formatted.str[-2:],
            'first_digit': formatted.str[0].astype('int64'),
            'last_digit': formatted.str[-1].astype('int64'),
            'digit_sum': formatted.map(lambda value: sum(int(digit) for digit in value)).astype('int64'),
            'is_even_tail': formatted.str[-1].astype('int64').mod(2).eq(0),
            'run_id': special['run_id'].astype('string'),
        }
    )
    return output.sort_values('draw_date', kind='stable').reset_index(drop=True)


def dim_number_frame() -> pd.DataFrame:
    rows = []
    for numeric_value in range(100):
        number = f'{numeric_value:02d}'
        tens_digit = int(number[0])
        ones_digit = int(number[1])
        rows.append(
            {
                'number_2d': number,
                'numeric_value': numeric_value,
                'tens_digit': tens_digit,
                'ones_digit': ones_digit,
                'digit_sum': tens_digit + ones_digit,
                'is_even': numeric_value % 2 == 0,
                'is_double': tens_digit == ones_digit,
            }
        )
    dataframe = pd.DataFrame(rows)
    dataframe['number_2d'] = dataframe['number_2d'].astype('string')
    return dataframe


def dim_date_frame(
    start_date: date,
    end_date: date,
    statuses: dict[date, DrawStatus] | None = None,
) -> pd.DataFrame:
    if end_date < start_date:
        raise ValueError('end_date must not be before start_date')
    statuses = statuses or {}
    dates = pd.date_range(start=start_date, end=end_date, freq='D')
    iso_calendar = dates.isocalendar()
    dataframe = pd.DataFrame(
        {
            'date': dates,
            'day_of_week': dates.dayofweek + 1,
            'day_name': dates.day_name(),
            'iso_week': iso_calendar.week.to_numpy(dtype='int64'),
            'month': dates.month,
            'quarter': dates.quarter,
            'year': dates.year,
            'is_weekend': dates.dayofweek >= 5,
            'draw_status': [statuses.get(timestamp.date(), DrawStatus.MISSING).value for timestamp in dates],
        }
    )
    for column in ('day_name', 'draw_status'):
        dataframe[column] = dataframe[column].astype('string')
    return dataframe


def build_gold_tables(
    draw_results: pd.DataFrame,
    *,
    run_id: str,
    statuses: dict[date, DrawStatus] | None = None,
) -> dict[str, pd.DataFrame]:
    if draw_results.empty:
        raise ValueError('cannot build Gold tables from an empty draw result dataset')

    fact_draw_result = draw_results.copy()
    fact_draw_result['run_id'] = run_id
    fact_draw_result['run_id'] = fact_draw_result['run_id'].astype('string')
    fact_loto_daily = loto_daily_frame(fact_draw_result, run_id=run_id)
    fact_special_prize = special_prize_frame(fact_draw_result)
    minimum_date = pd.Timestamp(fact_draw_result['draw_date'].min()).date()
    maximum_date = pd.Timestamp(fact_draw_result['draw_date'].max()).date()
    effective_statuses = dict(statuses or {})
    for timestamp in pd.to_datetime(fact_draw_result['draw_date']).unique():
        effective_statuses[pd.Timestamp(timestamp).date()] = DrawStatus.SUCCESS

    return {
        'fact-draw-result': fact_draw_result,
        'fact-loto-daily': fact_loto_daily,
        'fact-special-prize': fact_special_prize,
        'dim-date': dim_date_frame(minimum_date, maximum_date, effective_statuses),
        'dim-number': dim_number_frame(),
    }
