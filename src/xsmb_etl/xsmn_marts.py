"""BI-friendly Gold facts and dimensions for XSMN."""

from __future__ import annotations

from datetime import date

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.marts import dim_date_frame, dim_number_frame
from xsmb_etl.xsmn_transform import southern_loto_daily_frame


def southern_special_prize_frame(draw_results: pd.DataFrame) -> pd.DataFrame:
    special = draw_results.loc[draw_results['prize_group'].eq('special')].copy()
    formatted = special['formatted_number'].astype('string')
    output = pd.DataFrame(
        {
            'draw_date': pd.to_datetime(special['draw_date']),
            'station_code': special['station_code'].astype('string'),
            'station_name': special['station_name'].astype('string'),
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
    return output.sort_values(['draw_date', 'station_code'], kind='stable').reset_index(drop=True)


def dim_station_frame(draw_results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for station_code, station_rows in draw_results.groupby('station_code', sort=True):
        rows.append(
            {
                'station_code': station_code,
                'station_name': station_rows['station_name'].iloc[-1],
                'station_url': station_rows['station_url'].iloc[-1],
                'first_draw_date': pd.to_datetime(station_rows['draw_date']).min(),
                'latest_draw_date': pd.to_datetime(station_rows['draw_date']).max(),
            }
        )
    dataframe = pd.DataFrame(rows)
    for column in ('station_code', 'station_name', 'station_url'):
        dataframe[column] = dataframe[column].astype('string')
    return dataframe


def build_southern_gold_tables(
    draw_results: pd.DataFrame,
    *,
    run_id: str,
    statuses: dict[date, DrawStatus] | None = None,
) -> dict[str, pd.DataFrame]:
    if draw_results.empty:
        raise ValueError('cannot build XSMN Gold tables from an empty draw result dataset')

    fact_draw_result = draw_results.copy()
    fact_draw_result['run_id'] = run_id
    fact_draw_result['run_id'] = fact_draw_result['run_id'].astype('string')
    fact_loto_daily = southern_loto_daily_frame(fact_draw_result, run_id=run_id)
    fact_special_prize = southern_special_prize_frame(fact_draw_result)
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
        'dim-station': dim_station_frame(fact_draw_result),
    }
