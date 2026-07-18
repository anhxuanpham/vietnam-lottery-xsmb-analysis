"""Deterministic transformations from canonical draws to analytical facts."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from xsmb_etl.models import LotteryResult, PRIZE_SPECS


DRAW_RESULT_COLUMNS = [
    'draw_date',
    'prize_group',
    'prize_order',
    'prize_width',
    'full_number',
    'formatted_number',
    'loto_2d',
    'tens_digit',
    'ones_digit',
    'source_url',
    'run_id',
]

LOTO_DAILY_COLUMNS = [
    'draw_date',
    'number_2d',
    'frequency',
    'appeared',
    'draws_since_previous',
    'calendar_days_since_previous',
    'previous_appearance_status',
    'rolling_7_frequency',
    'rolling_30_frequency',
    'rolling_90_frequency',
    'run_id',
]


def draw_results_frame(results: Sequence[LotteryResult], run_id: str) -> pd.DataFrame:
    rows = []
    for result in results:
        for prize in result.prizes:
            rows.append(
                {
                    'draw_date': result.draw_date,
                    'prize_group': prize.prize_group.value,
                    'prize_order': prize.prize_order,
                    'prize_width': prize.prize_width,
                    'full_number': prize.full_number,
                    'formatted_number': prize.formatted_number,
                    'loto_2d': prize.loto_2d,
                    'tens_digit': int(prize.loto_2d[0]),
                    'ones_digit': int(prize.loto_2d[1]),
                    'source_url': result.source_url,
                    'run_id': run_id,
                }
            )

    dataframe = pd.DataFrame(rows, columns=DRAW_RESULT_COLUMNS)
    if dataframe.empty:
        return _empty_draw_results_frame()

    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    for column in ('prize_group', 'formatted_number', 'loto_2d', 'source_url', 'run_id'):
        dataframe[column] = dataframe[column].astype('string')
    for column in ('prize_order', 'prize_width', 'full_number', 'tens_digit', 'ones_digit'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe.sort_values(['draw_date', 'prize_group', 'prize_order'], kind='stable').reset_index(drop=True)


def loto_daily_frame(draw_results: pd.DataFrame, run_id: str | None = None) -> pd.DataFrame:
    if draw_results.empty:
        return _empty_loto_daily_frame()

    required = {'draw_date', 'loto_2d', 'run_id'}
    missing = required.difference(draw_results.columns)
    if missing:
        raise ValueError(f'draw results missing required columns: {sorted(missing)}')

    dates = sorted(pd.to_datetime(draw_results['draw_date']).dt.normalize().unique())
    numbers = [f'{number:02d}' for number in range(100)]
    frequencies = (
        draw_results.assign(draw_date=pd.to_datetime(draw_results['draw_date']).dt.normalize())
        .groupby(['draw_date', 'loto_2d'], observed=True)
        .size()
        .to_dict()
    )
    output_run_id = run_id or str(draw_results['run_id'].iloc[-1])
    rows = [
        {
            'draw_date': draw_date,
            'number_2d': number,
            'frequency': int(frequencies.get((draw_date, number), 0)),
            'run_id': output_run_id,
        }
        for draw_date in dates
        for number in numbers
    ]
    dataframe = pd.DataFrame(rows)
    dataframe['number_2d'] = dataframe['number_2d'].astype('string')
    dataframe['frequency'] = dataframe['frequency'].astype('int64')
    dataframe['appeared'] = dataframe['frequency'].gt(0)

    draws_since: list[int | None] = [None] * len(dataframe)
    calendar_days_since: list[int | None] = [None] * len(dataframe)
    appearance_status = ['never_seen'] * len(dataframe)
    for _, indices in dataframe.groupby('number_2d', sort=False).groups.items():
        previous_draw_position: int | None = None
        previous_date: pd.Timestamp | None = None
        for draw_position, row_index in enumerate(indices):
            current_date = pd.Timestamp(dataframe.at[row_index, 'draw_date'])
            if previous_draw_position is not None and previous_date is not None:
                draws_since[row_index] = draw_position - previous_draw_position
                calendar_days_since[row_index] = (current_date - previous_date).days
                appearance_status[row_index] = 'seen_before'
            if bool(dataframe.at[row_index, 'appeared']):
                previous_draw_position = draw_position
                previous_date = current_date

    dataframe['draws_since_previous'] = pd.array(draws_since, dtype='Int64')
    dataframe['calendar_days_since_previous'] = pd.array(calendar_days_since, dtype='Int64')
    dataframe['previous_appearance_status'] = pd.array(appearance_status, dtype='string')
    for window in (7, 30, 90):
        dataframe[f'rolling_{window}_frequency'] = (
            dataframe.groupby('number_2d', sort=False)['frequency']
            .transform(lambda values: values.rolling(window=window, min_periods=1).sum())
            .astype('int64')
        )
    dataframe['run_id'] = dataframe['run_id'].astype('string')
    return dataframe[LOTO_DAILY_COLUMNS].sort_values(['draw_date', 'number_2d'], kind='stable').reset_index(drop=True)


def canonical_results_from_frame(draw_results: pd.DataFrame) -> list[LotteryResult]:
    """Reconstruct canonical results from a validated long-form Silver dataset."""

    required = {
        'draw_date',
        'prize_group',
        'prize_order',
        'formatted_number',
        'source_url',
    }
    missing = required.difference(draw_results.columns)
    if missing:
        raise ValueError(f'draw results missing required columns: {sorted(missing)}')

    results = []
    working = draw_results.sort_values(['draw_date', 'prize_group', 'prize_order'], kind='stable')
    for draw_timestamp, rows in working.groupby('draw_date', sort=True):
        groups = {
            group.value: rows.loc[rows['prize_group'].eq(group.value)]
            .sort_values('prize_order', kind='stable')['formatted_number']
            .astype(str)
            .tolist()
            for group in PRIZE_SPECS
        }
        source_urls = rows['source_url'].dropna().astype(str)
        source_url = source_urls.iloc[0] if not source_urls.empty else ''
        results.append(
            LotteryResult.from_prize_groups(
                pd.Timestamp(draw_timestamp).date(),
                source_url,
                groups,
            )
        )
    return results


def _empty_draw_results_frame() -> pd.DataFrame:
    dataframe = pd.DataFrame(columns=DRAW_RESULT_COLUMNS)
    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    for column in ('prize_group', 'formatted_number', 'loto_2d', 'source_url', 'run_id'):
        dataframe[column] = dataframe[column].astype('string')
    for column in ('prize_order', 'prize_width', 'full_number', 'tens_digit', 'ones_digit'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe


def _empty_loto_daily_frame() -> pd.DataFrame:
    dataframe = pd.DataFrame(columns=LOTO_DAILY_COLUMNS)
    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    for column in ('number_2d', 'previous_appearance_status', 'run_id'):
        dataframe[column] = dataframe[column].astype('string')
    dataframe['appeared'] = dataframe['appeared'].astype('bool')
    for column in ('draws_since_previous', 'calendar_days_since_previous'):
        dataframe[column] = dataframe[column].astype('Int64')
    for column in ('frequency', 'rolling_7_frequency', 'rolling_30_frequency', 'rolling_90_frequency'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe
