"""Deterministic transformations for Southern lottery station results."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from xsmb_etl.xsmn_models import SOUTHERN_PRIZE_SPECS, SouthernDailyResult, SouthernStationResult


SOUTHERN_DRAW_RESULT_COLUMNS = [
    'draw_date',
    'station_code',
    'station_order',
    'station_name',
    'station_url',
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

SOUTHERN_LOTO_DAILY_COLUMNS = [
    'draw_date',
    'station_code',
    'station_name',
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


def southern_draw_results_frame(results: Sequence[SouthernDailyResult], run_id: str) -> pd.DataFrame:
    rows = []
    for daily_result in results:
        for station_order, station in enumerate(daily_result.stations, start=1):
            for prize in station.prizes:
                rows.append(
                    {
                        'draw_date': station.draw_date,
                        'station_code': station.station_code,
                        'station_order': station_order,
                        'station_name': station.station_name,
                        'station_url': station.station_url,
                        'prize_group': prize.prize_group.value,
                        'prize_order': prize.prize_order,
                        'prize_width': prize.prize_width,
                        'full_number': prize.full_number,
                        'formatted_number': prize.formatted_number,
                        'loto_2d': prize.loto_2d,
                        'tens_digit': int(prize.loto_2d[0]),
                        'ones_digit': int(prize.loto_2d[1]),
                        'source_url': station.source_url,
                        'run_id': run_id,
                    }
                )

    dataframe = pd.DataFrame(rows, columns=SOUTHERN_DRAW_RESULT_COLUMNS)
    if dataframe.empty:
        return _empty_southern_draw_results_frame()

    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    string_columns = (
        'station_code',
        'station_name',
        'station_url',
        'prize_group',
        'formatted_number',
        'loto_2d',
        'source_url',
        'run_id',
    )
    for column in string_columns:
        dataframe[column] = dataframe[column].astype('string')
    for column in ('station_order', 'prize_order', 'prize_width', 'full_number', 'tens_digit', 'ones_digit'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe.sort_values(
        ['draw_date', 'station_code', 'prize_group', 'prize_order'], kind='stable'
    ).reset_index(drop=True)


def southern_loto_daily_frame(draw_results: pd.DataFrame, run_id: str | None = None) -> pd.DataFrame:
    if draw_results.empty:
        return _empty_southern_loto_daily_frame()

    required = {'draw_date', 'station_code', 'station_name', 'loto_2d', 'run_id'}
    missing = required.difference(draw_results.columns)
    if missing:
        raise ValueError(f'Southern draw results missing required columns: {sorted(missing)}')

    working = draw_results.assign(draw_date=pd.to_datetime(draw_results['draw_date']).dt.normalize())
    station_draws = (
        working[['draw_date', 'station_code', 'station_name']]
        .drop_duplicates()
        .sort_values(['draw_date', 'station_code'], kind='stable')
    )
    frequencies = working.groupby(['draw_date', 'station_code', 'loto_2d'], observed=True).size().to_dict()
    output_run_id = run_id or str(working['run_id'].iloc[-1])
    rows = []
    for station_draw in station_draws.itertuples(index=False):
        for number in (f'{value:02d}' for value in range(100)):
            rows.append(
                {
                    'draw_date': station_draw.draw_date,
                    'station_code': station_draw.station_code,
                    'station_name': station_draw.station_name,
                    'number_2d': number,
                    'frequency': int(frequencies.get((station_draw.draw_date, station_draw.station_code, number), 0)),
                    'run_id': output_run_id,
                }
            )

    dataframe = pd.DataFrame(rows)
    for column in ('station_code', 'station_name', 'number_2d'):
        dataframe[column] = dataframe[column].astype('string')
    dataframe['frequency'] = dataframe['frequency'].astype('int64')
    dataframe['appeared'] = dataframe['frequency'].gt(0)
    dataframe = dataframe.sort_values(['station_code', 'number_2d', 'draw_date'], kind='stable').reset_index(drop=True)

    station_number_keys = [dataframe['station_code'], dataframe['number_2d']]
    draw_position = dataframe.groupby(['station_code', 'number_2d'], sort=False).cumcount()
    # Shift then forward-fill within each station/number pair so every row references
    # the latest appearance strictly before it, including rows that appear again.
    appeared_position = draw_position.where(dataframe['appeared'])
    appeared_date = dataframe['draw_date'].where(dataframe['appeared'])
    previous_position = (
        appeared_position.groupby(station_number_keys, sort=False)
        .shift(1)
        .groupby(station_number_keys, sort=False)
        .ffill()
    )
    previous_date = (
        appeared_date.groupby(station_number_keys, sort=False).shift(1).groupby(station_number_keys, sort=False).ffill()
    )
    dataframe['draws_since_previous'] = (draw_position - previous_position).astype('Int64')
    dataframe['calendar_days_since_previous'] = (dataframe['draw_date'] - previous_date).dt.days.astype('Int64')
    dataframe['previous_appearance_status'] = (
        previous_position.notna().map({True: 'seen_before', False: 'never_seen'}).astype('string')
    )
    for window in (7, 30, 90):
        dataframe[f'rolling_{window}_frequency'] = (
            dataframe.groupby(['station_code', 'number_2d'], sort=False)['frequency']
            .transform(lambda values: values.rolling(window=window, min_periods=1).sum())
            .astype('int64')
        )
    dataframe['run_id'] = dataframe['run_id'].astype('string')
    return (
        dataframe[SOUTHERN_LOTO_DAILY_COLUMNS]
        .sort_values(['draw_date', 'station_code', 'number_2d'], kind='stable')
        .reset_index(drop=True)
    )


def canonical_southern_results_from_frame(draw_results: pd.DataFrame) -> list[SouthernDailyResult]:
    required = {
        'draw_date',
        'station_code',
        'station_order',
        'station_name',
        'station_url',
        'prize_group',
        'prize_order',
        'formatted_number',
        'source_url',
    }
    missing = required.difference(draw_results.columns)
    if missing:
        raise ValueError(f'Southern draw results missing required columns: {sorted(missing)}')

    output = []
    working = draw_results.sort_values(['draw_date', 'station_code', 'prize_group', 'prize_order'], kind='stable')
    for draw_timestamp, daily_rows in working.groupby('draw_date', sort=True):
        stations = []
        station_groups = daily_rows.sort_values('station_order', kind='stable').groupby('station_code', sort=False)
        for station_code, rows in station_groups:
            groups = {
                group.value: rows.loc[rows['prize_group'].eq(group.value)]
                .sort_values('prize_order', kind='stable')['formatted_number']
                .astype(str)
                .tolist()
                for group in SOUTHERN_PRIZE_SPECS
            }
            stations.append(
                SouthernStationResult.from_prize_groups(
                    draw_date=pd.Timestamp(draw_timestamp).date(),
                    station_code=str(station_code),
                    station_name=str(rows['station_name'].iloc[0]),
                    station_url=str(rows['station_url'].iloc[0]),
                    source_url=str(rows['source_url'].iloc[0]),
                    groups=groups,
                )
            )
        output.append(
            SouthernDailyResult(
                draw_date=pd.Timestamp(draw_timestamp).date(),
                source_url=stations[0].source_url,
                stations=tuple(stations),
            )
        )
    return output


def _empty_southern_draw_results_frame() -> pd.DataFrame:
    dataframe = pd.DataFrame(columns=SOUTHERN_DRAW_RESULT_COLUMNS)
    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    for column in (
        'station_code',
        'station_name',
        'station_url',
        'prize_group',
        'formatted_number',
        'loto_2d',
        'source_url',
        'run_id',
    ):
        dataframe[column] = dataframe[column].astype('string')
    for column in ('station_order', 'prize_order', 'prize_width', 'full_number', 'tens_digit', 'ones_digit'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe


def _empty_southern_loto_daily_frame() -> pd.DataFrame:
    dataframe = pd.DataFrame(columns=SOUTHERN_LOTO_DAILY_COLUMNS)
    dataframe['draw_date'] = pd.to_datetime(dataframe['draw_date'])
    for column in ('station_code', 'station_name', 'number_2d', 'previous_appearance_status', 'run_id'):
        dataframe[column] = dataframe[column].astype('string')
    dataframe['appeared'] = dataframe['appeared'].astype('bool')
    for column in ('draws_since_previous', 'calendar_days_since_previous'):
        dataframe[column] = dataframe[column].astype('Int64')
    for column in ('frequency', 'rolling_7_frequency', 'rolling_30_frequency', 'rolling_90_frequency'):
        dataframe[column] = dataframe[column].astype('int64')
    return dataframe
