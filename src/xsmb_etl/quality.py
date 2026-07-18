"""Structured data-quality checks for transformed and Gold datasets."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, computed_field

from xsmb_etl.control import DrawStatus
from xsmb_etl.models import EXPECTED_RESULT_COUNT, LotteryResult, PRIZE_SPECS


class QualitySeverity(StrEnum):
    CRITICAL = 'critical'
    WARNING = 'warning'


class QualityCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    severity: QualitySeverity
    passed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    target_dates: tuple[date, ...]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checks: tuple[QualityCheck, ...]

    @computed_field
    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks if check.severity is QualitySeverity.CRITICAL)

    @property
    def critical_failures(self) -> tuple[QualityCheck, ...]:
        return tuple(check for check in self.checks if check.severity is QualitySeverity.CRITICAL and not check.passed)


class CriticalQualityError(RuntimeError):
    def __init__(self, report: QualityReport) -> None:
        self.report = report
        failed_names = ', '.join(check.name for check in report.critical_failures)
        super().__init__(f'critical data-quality checks failed: {failed_names}')


def build_quality_report(
    results: list[LotteryResult],
    draw_results: pd.DataFrame,
    loto_daily: pd.DataFrame,
    *,
    run_id: str,
    gold_tables: dict[str, pd.DataFrame] | None = None,
    statuses: dict[date, DrawStatus] | None = None,
    today: date | None = None,
) -> QualityReport:
    today = today or datetime.now(UTC).date()
    target_dates = tuple(sorted(result.draw_date for result in results))
    checks: list[QualityCheck] = []

    checks.append(_check('requested-date-validated', bool(results), 'source pages passed selected-date validation'))
    for group, spec in PRIZE_SPECS.items():
        valid = bool(results) and all(len(result.prizes_for(group)) == spec.count for result in results)
        checks.append(_check(f'{group.value}-count', valid, f'each draw has {spec.count} {group.value} value(s)'))
    checks.append(
        _check(
            'total-result-count',
            bool(results) and all(len(result.prizes) == EXPECTED_RESULT_COUNT for result in results),
            f'each draw has exactly {EXPECTED_RESULT_COUNT} results',
        )
    )
    checks.append(
        _check(
            'prize-value-range',
            bool(results)
            and all(prize.full_number < 10**prize.prize_width for result in results for prize in result.prizes),
            'all prize values fit their official width',
        )
    )
    checks.append(
        _check(
            'draw-date-not-future',
            bool(results) and all(result.draw_date <= today for result in results),
            'draw dates are not in the future',
        )
    )

    draw_counts = draw_results.groupby('draw_date').size() if not draw_results.empty else pd.Series(dtype='int64')
    loto_counts = loto_daily.groupby('draw_date').size() if not loto_daily.empty else pd.Series(dtype='int64')
    loto_sums = loto_daily.groupby('draw_date')['frequency'].sum() if not loto_daily.empty else pd.Series(dtype='int64')
    expected_numbers = {f'{number:02d}' for number in range(100)}
    coverage_valid = not loto_daily.empty and all(
        len(group) == 100 and set(group['number_2d'].astype(str)) == expected_numbers
        for _, group in loto_daily.groupby('draw_date')
    )
    checks.extend(
        [
            _check(
                'fact-draw-result-row-count', not draw_counts.empty and draw_counts.eq(27).all(), '27 rows per draw'
            ),
            _check(
                'fact-loto-daily-row-count', not loto_counts.empty and loto_counts.eq(100).all(), '100 rows per draw'
            ),
            _check('loto-frequency-sum', not loto_sums.empty and loto_sums.eq(27).all(), 'daily frequency sums to 27'),
            _check(
                'loto-number-coverage',
                coverage_valid,
                'each draw covers 00 through 99 exactly once',
            ),
            _check(
                'draw-business-key-unique',
                not draw_results.duplicated(['draw_date', 'prize_group', 'prize_order']).any(),
                'draw business keys are unique',
            ),
            _check(
                'loto-business-key-unique',
                not loto_daily.duplicated(['draw_date', 'number_2d']).any(),
                'loto business keys are unique',
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


def require_quality(report: QualityReport) -> None:
    if not report.passed:
        raise CriticalQualityError(report)


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
