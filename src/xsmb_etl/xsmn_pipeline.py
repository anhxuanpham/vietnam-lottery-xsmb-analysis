"""End-to-end orchestration for station-based XSMN and XSMT data lakes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.pipeline_core import BasePipeline
from xsmb_etl.quality import QualityReport
from xsmb_etl.repository import SouthernDataLakeRepository
from xsmb_etl.run_models import LotteryRegion, SourceLineage
from xsmb_etl.xsmn_extract import SouthernResultExtractor
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_models import SouthernDailyResult
from xsmb_etl.xsmn_quality import build_southern_quality_report
from xsmb_etl.xsmn_transform import (
    canonical_southern_results_from_frame,
    southern_draw_results_frame,
    southern_loto_daily_frame,
)


logger = logging.getLogger(__name__)


class SouthernPipeline(BasePipeline):
    """Station-grain specialization of the shared pipeline skeleton for XSMN and XSMT."""

    documented_partial_draws: Mapping[date, frozenset[str]] = {}

    _logger = logger

    def __init__(
        self,
        repository: SouthernDataLakeRepository,
        extractor: SouthernResultExtractor,
        *,
        source_lineage: SourceLineage = SourceLineage.LIVE_SOURCE,
    ) -> None:
        super().__init__(repository, extractor, source_lineage=source_lineage)
        if self.region not in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
            raise ValueError('SouthernPipeline requires an XSMN or XSMT repository')

    def _draw_results_frame(self, results: list[SouthernDailyResult], run_id: str) -> pd.DataFrame:
        return southern_draw_results_frame(results, run_id)

    def _loto_daily_frame(self, draw_results: pd.DataFrame, *, run_id: str) -> pd.DataFrame:
        return southern_loto_daily_frame(draw_results, run_id=run_id)

    def _gold_tables(
        self,
        draw_results: pd.DataFrame,
        *,
        run_id: str,
        statuses: dict[date, DrawStatus] | None = None,
        loto_daily: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        return build_southern_gold_tables(draw_results, run_id=run_id, statuses=statuses, loto_daily=loto_daily)

    def _quality_report(
        self,
        results: list[SouthernDailyResult],
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
            region=self.region,
            documented_partial_draws=self.documented_partial_draws,
        )

    def _canonical_results(self, draw_results: pd.DataFrame) -> list[SouthernDailyResult]:
        return canonical_southern_results_from_frame(draw_results)

    def _boundary_date(self, value: Any) -> date:
        return pd_timestamp_date(value)

    def _skipped_message(self, target_date: date, status: DrawStatus) -> str:
        return (
            f'{target_date} is already classified as {status.value} in {self.region_label}; use --force to replace it'
        )

    def _published_message(self, run_id: str) -> str:
        return f'published {self.region_label} dataset version {run_id}'

    def _rebuilt_message(self, run_id: str) -> str:
        return f'rebuilt {self.region_label} Gold dataset version {run_id}'

    def _empty_silver_message(self) -> str:
        return f'no {self.region_label} Silver draw results are available'

    def _publication_region_kwargs(self) -> dict[str, LotteryRegion]:
        return {'region': self.region}


def pd_timestamp_date(value) -> date:
    return pd.Timestamp(value).date()
