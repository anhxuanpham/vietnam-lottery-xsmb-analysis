from __future__ import annotations

from datetime import date

import pytest

from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult
from xsmb_etl.quality import CriticalQualityError, build_quality_report, require_quality
from xsmb_etl.transform import draw_results_frame, loto_daily_frame


def _dataset(grouped_prize_values: dict[str, list[str]]):
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    draw = draw_results_frame([result], 'run-1')
    loto = loto_daily_frame(draw)
    gold = build_gold_tables(draw, run_id='run-1')
    return result, draw, loto, gold


def test_complete_dataset_passes_critical_quality_checks(grouped_prize_values: dict[str, list[str]]) -> None:
    result, draw, loto, gold = _dataset(grouped_prize_values)
    report = build_quality_report(
        [result],
        draw,
        loto,
        run_id='run-1',
        gold_tables=gold,
        today=date(2026, 7, 16),
    )

    assert report.passed
    assert not report.critical_failures
    require_quality(report)


def test_corrupt_frequency_fails_quality_and_pipeline_gate(grouped_prize_values: dict[str, list[str]]) -> None:
    result, draw, loto, gold = _dataset(grouped_prize_values)
    loto.loc[loto['number_2d'].eq('63'), 'frequency'] = 99
    report = build_quality_report(
        [result],
        draw,
        loto,
        run_id='run-1',
        gold_tables=gold,
        today=date(2026, 7, 16),
    )

    assert not report.passed
    assert 'loto-frequency-sum' in {check.name for check in report.critical_failures}
    with pytest.raises(CriticalQualityError):
        require_quality(report)
