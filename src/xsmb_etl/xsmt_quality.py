"""Structured data-quality checks for the XSMT lake."""

from __future__ import annotations

from datetime import date

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.quality import QualityReport
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.xsmn_quality import build_southern_quality_report
from xsmb_etl.xsmt_models import CentralDailyResult


XSMT_DOCUMENTED_PARTIAL_DRAWS: dict[date, frozenset[str]] = {
    date(2021, 7, 27): frozenset({'QNA'}),
    date(2021, 8, 3): frozenset({'QNA'}),
    date(2021, 8, 6): frozenset({'GL'}),
    date(2021, 8, 18): frozenset({'KH'}),
}


def build_central_quality_report(
    results: list[CentralDailyResult],
    draw_results: pd.DataFrame,
    loto_daily: pd.DataFrame,
    *,
    run_id: str,
    gold_tables: dict[str, pd.DataFrame] | None = None,
    statuses: dict[date, DrawStatus] | None = None,
    today: date | None = None,
) -> QualityReport:
    return build_southern_quality_report(
        results,
        draw_results,
        loto_daily,
        run_id=run_id,
        gold_tables=gold_tables,
        statuses=statuses,
        today=today,
        region=LotteryRegion.XSMT,
        documented_partial_draws=XSMT_DOCUMENTED_PARTIAL_DRAWS,
    )


__all__ = ['build_central_quality_report']
