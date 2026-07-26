"""End-to-end extract, validate, transform, load, and publish orchestration."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult
from xsmb_etl.pipeline_core import BasePipeline, backfill_failure_result
from xsmb_etl.quality import QualityReport, build_quality_report
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.transform import canonical_results_from_frame, draw_results_frame, loto_daily_frame


__all__ = ['Pipeline', 'backfill_failure_result']

logger = logging.getLogger(__name__)


class Pipeline(BasePipeline):
    """XSMB draw-grain specialization of the shared pipeline skeleton."""

    _logger = logger

    def _draw_results_frame(self, results: list[LotteryResult], run_id: str) -> pd.DataFrame:
        return draw_results_frame(results, run_id)

    def _loto_daily_frame(self, draw_results: pd.DataFrame, *, run_id: str) -> pd.DataFrame:
        return loto_daily_frame(draw_results, run_id=run_id)

    def _gold_tables(
        self,
        draw_results: pd.DataFrame,
        *,
        run_id: str,
        statuses: dict[date, DrawStatus] | None = None,
        loto_daily: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        return build_gold_tables(draw_results, run_id=run_id, statuses=statuses, loto_daily=loto_daily)

    def _quality_report(
        self,
        results: list[LotteryResult],
        draw_results: pd.DataFrame,
        loto_daily: pd.DataFrame,
        *,
        run_id: str,
        gold_tables: dict[str, pd.DataFrame] | None = None,
        statuses: dict[date, DrawStatus] | None = None,
        today: date | None = None,
    ) -> QualityReport:
        return build_quality_report(
            results,
            draw_results,
            loto_daily,
            run_id=run_id,
            gold_tables=gold_tables,
            statuses=statuses,
            today=today,
        )

    def _canonical_results(self, draw_results: pd.DataFrame) -> list[LotteryResult]:
        return canonical_results_from_frame(draw_results)

    def _boundary_date(self, value: Any) -> date:
        return value.date()

    def _skipped_message(self, target_date: date, status: DrawStatus) -> str:
        return f'{target_date} is already classified as {status.value}; use --force to replace it'

    def _published_message(self, run_id: str) -> str:
        return f'published dataset version {run_id}'

    def _rebuilt_message(self, run_id: str) -> str:
        return f'rebuilt Gold dataset version {run_id}'

    def _empty_silver_message(self) -> str:
        return 'no Silver draw results are available'

    def _publication_region_kwargs(self) -> dict[str, LotteryRegion]:
        return {}
