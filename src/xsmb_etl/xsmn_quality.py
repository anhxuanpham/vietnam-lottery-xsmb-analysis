"""Structured data-quality checks for the XSMN lake."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.quality import QualityCheck, QualityReport, QualitySeverity
from xsmb_etl.xsmn_models import SOUTHERN_EXPECTED_RESULT_COUNT, SOUTHERN_PRIZE_SPECS, SouthernDailyResult


def build_southern_quality_report(
    results: list[SouthernDailyResult],
    draw_results: pd.DataFrame,
    loto_daily: pd.DataFrame,
    *,
    run_id: str,
    gold_tables: dict[str, pd.DataFrame] | None = None,
    statuses: dict[date, DrawStatus] | None = None,
    today: date | None = None,
) -> QualityReport:
    today = today or datetime.now(UTC).date()
    target_dates = tuple(sorted({result.draw_date for result in results}))
    station_results = [station for result in results for station in result.stations]
    checks: list[QualityCheck] = [
        _check('requested-date-validated', bool(results), 'source pages passed selected-date validation'),
        _check(
            'station-count-per-day',
            bool(results) and all(len(result.stations) in {3, 4} for result in results),
            'each XSMN date contains three or four stations',
        ),
        _check(
            'station-code-unique-per-day',
            bool(results)
            and all(
                len({station.station_code for station in result.stations}) == len(result.stations) for result in results
            ),
            'station codes are unique within each date',
        ),
    ]
    for group, spec in SOUTHERN_PRIZE_SPECS.items():
        valid = bool(station_results) and all(len(result.prizes_for(group)) == spec.count for result in station_results)
        checks.append(_check(f'{group.value}-count', valid, f'each station has {spec.count} {group.value} value(s)'))
    checks.extend(
        [
            _check(
                'total-result-count',
                bool(station_results)
                and all(len(result.prizes) == SOUTHERN_EXPECTED_RESULT_COUNT for result in station_results),
                f'each station draw has exactly {SOUTHERN_EXPECTED_RESULT_COUNT} results',
            ),
            _check(
                'prize-value-range',
                bool(station_results)
                and all(
                    prize.full_number < 10**prize.prize_width for result in station_results for prize in result.prizes
                ),
                'all prize values fit their official width',
            ),
            _check(
                'draw-date-not-future',
                bool(results) and all(result.draw_date <= today for result in results),
                'draw dates are not in the future',
            ),
        ]
    )

    station_key = ['draw_date', 'station_code']
    draw_counts = draw_results.groupby(station_key).size() if not draw_results.empty else pd.Series(dtype='int64')
    loto_counts = loto_daily.groupby(station_key).size() if not loto_daily.empty else pd.Series(dtype='int64')
    loto_sums = loto_daily.groupby(station_key)['frequency'].sum() if not loto_daily.empty else pd.Series(dtype='int64')
    expected_numbers = {f'{number:02d}' for number in range(100)}
    coverage_valid = not loto_daily.empty and all(
        len(group) == 100 and set(group['number_2d'].astype(str)) == expected_numbers
        for _, group in loto_daily.groupby(station_key)
    )
    checks.extend(
        [
            _check(
                'fact-draw-result-row-count',
                not draw_counts.empty and draw_counts.eq(SOUTHERN_EXPECTED_RESULT_COUNT).all(),
                f'{SOUTHERN_EXPECTED_RESULT_COUNT} rows per station draw',
            ),
            _check(
                'fact-loto-daily-row-count',
                not loto_counts.empty and loto_counts.eq(100).all(),
                '100 rows per station draw',
            ),
            _check(
                'loto-frequency-sum',
                not loto_sums.empty and loto_sums.eq(SOUTHERN_EXPECTED_RESULT_COUNT).all(),
                f'station frequency sums to {SOUTHERN_EXPECTED_RESULT_COUNT}',
            ),
            _check('loto-number-coverage', coverage_valid, 'each station draw covers 00 through 99 exactly once'),
            _check(
                'draw-business-key-unique',
                not draw_results.duplicated(['draw_date', 'station_code', 'prize_group', 'prize_order']).any(),
                'station draw business keys are unique',
            ),
            _check(
                'loto-business-key-unique',
                not loto_daily.duplicated(['draw_date', 'station_code', 'number_2d']).any(),
                'station loto business keys are unique',
            ),
        ]
    )

    if gold_tables is not None:
        run_ids = {
            str(value)
            for dataframe in gold_tables.values()
            if 'run_id' in dataframe.columns
            for value in dataframe['run_id'].dropna().unique()
        }
        checks.append(_check('gold-run-id-consistent', run_ids == {run_id}, 'all versioned Gold facts use one run_id'))

    if statuses is not None:
        unresolved = sorted(
            draw_date.isoformat() for draw_date, status in statuses.items() if status is DrawStatus.MISSING
        )
        checks.append(
            _check(
                'unclassified-calendar-gaps',
                not unresolved,
                'calendar gaps are classified',
                severity=QualitySeverity.WARNING,
                details={'dates': unresolved},
            )
        )

    return QualityReport(run_id=run_id, target_dates=target_dates, checks=tuple(checks))


def _check(
    name: str,
    passed: bool,
    message: str,
    *,
    severity: QualitySeverity = QualitySeverity.CRITICAL,
    details: dict[str, Any] | None = None,
) -> QualityCheck:
    return QualityCheck(
        name=name,
        severity=severity,
        passed=bool(passed),
        message=message,
        details=details or {},
    )
