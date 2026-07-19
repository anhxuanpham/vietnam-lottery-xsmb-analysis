"""End-to-end orchestration for station-based XSMN and XSMT data lakes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from xsmb_etl.control import DrawStatus
from xsmb_etl.extract import NoDrawSourcePageError
from xsmb_etl.pipeline import backfill_failure_result
from xsmb_etl.quality import require_quality
from xsmb_etl.repository import SouthernDataLakeRepository
from xsmb_etl.run_models import (
    DataObjectReference,
    LotteryRegion,
    PipelineRunResult,
    RunManifest,
    RunStatus,
    SourceLineage,
)
from xsmb_etl.storage import StoredObject
from xsmb_etl.xsmn_extract import SouthernResultExtractor
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_quality import build_southern_quality_report
from xsmb_etl.xsmn_transform import (
    canonical_southern_results_from_frame,
    southern_draw_results_frame,
    southern_loto_daily_frame,
)


class SouthernPipeline:
    def __init__(
        self,
        repository: SouthernDataLakeRepository,
        extractor: SouthernResultExtractor,
        *,
        source_lineage: SourceLineage = SourceLineage.LIVE_SOURCE,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.source_lineage = source_lineage
        self.region = repository.region
        if self.region not in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
            raise ValueError('SouthernPipeline requires an XSMN or XSMT repository')
        self.region_label = self.region.value.upper()

    def run(self, target_date: date, *, force: bool = False) -> PipelineRunResult:
        control_state = self.repository.control_state()
        if not control_state.should_process(target_date, force=force):
            status = control_state.status_for(target_date)
            return PipelineRunResult(
                run_id=None,
                region=self.region,
                target_date=target_date,
                status=status.value,
                skipped=True,
                message=(
                    f'{target_date} is already classified as {status.value} in {self.region_label}; '
                    'use --force to replace it'
                ),
            )

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        objects: list[StoredObject] = []
        quality_passed = False
        try:
            if self.repository.bronze_complete(target_date) and not force:
                extracted = self.repository.load_bronze(target_date)
                objects.extend(self.repository.bronze_objects(target_date))
            else:
                extracted = self.extractor.extract(target_date)
                objects.extend(
                    self.repository.write_bronze(
                        extracted,
                        run_id=run_id,
                        force=force,
                        fetched_at=datetime.now(UTC),
                        source_lineage=self.source_lineage.value,
                    )
                )

            current_draw = southern_draw_results_frame([extracted.result], run_id)
            current_loto = southern_loto_daily_frame(current_draw, run_id=run_id)
            current_gold = build_southern_gold_tables(current_draw, run_id=run_id)
            pre_write_report = build_southern_quality_report(
                [extracted.result],
                current_draw,
                current_loto,
                run_id=run_id,
                gold_tables=current_gold,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
                region=self.region,
            )
            require_quality(pre_write_report)

            objects.extend(self.repository.upsert_silver_draw_results(current_draw))
            all_draw = self.repository.read_all_silver_draw_results()
            all_loto = southern_loto_daily_frame(all_draw, run_id=run_id)
            objects.extend(self.repository.replace_silver_loto_daily(all_loto))

            minimum_date = pd_timestamp_date(all_draw['draw_date'].min())
            maximum_date = pd_timestamp_date(all_draw['draw_date'].max())
            statuses = control_state.status_map(minimum_date, maximum_date)
            statuses[target_date] = DrawStatus.SUCCESS
            gold_tables = build_southern_gold_tables(all_draw, run_id=run_id, statuses=statuses)
            report = build_southern_quality_report(
                [extracted.result],
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
                region=self.region,
            )
            require_quality(report)
            quality_passed = True
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id)
            objects.extend(gold_objects)

            success_manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.SUCCESS,
                source_lineage=self.source_lineage,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                quality_passed=True,
                covered_dates=(target_date,),
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
            )
            objects.append(self.repository.write_run_manifest(success_manifest))
            snapshot, latest = self.repository.publish_snapshot_and_latest(
                run_id=run_id,
                target_date=target_date,
                gold_objects=gold_objects,
            )
            objects.extend([snapshot, latest])
            return PipelineRunResult(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=f'published {self.region_label} dataset version {run_id}',
            )
        except NoDrawSourcePageError as exc:
            no_draw_manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.NO_DRAW,
                source_lineage=self.source_lineage,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_message=exc.notice,
            )
            objects.append(self.repository.write_run_manifest(no_draw_manifest))
            return PipelineRunResult(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.NO_DRAW.value,
                object_count=len(objects),
                message=exc.notice,
            )
        except Exception as exc:
            failure_manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.FAILED,
                source_lineage=self.source_lineage,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                quality_passed=quality_passed,
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
            try:
                self.repository.write_run_manifest(failure_manifest)
            except Exception:
                pass
            raise

    def backfill(self, start_date: date, end_date: date, *, force: bool = False) -> list[PipelineRunResult]:
        pending = self.repository.control_state().pending_dates(start_date, end_date, force=force)
        results: list[PipelineRunResult] = []
        successful_result_indexes: list[int] = []
        for target_date in pending:
            try:
                result = self._ingest_backfill_date(target_date, force=force)
                results.append(result)
                if result.status == RunStatus.SUCCESS.value:
                    successful_result_indexes.append(len(results) - 1)
            except Exception as exc:
                results.append(backfill_failure_result(self.repository, target_date, exc))

        if successful_result_indexes:
            publication = self.build_gold()
            for index in successful_result_indexes:
                result = results[index]
                results[index] = result.model_copy(
                    update={
                        'message': (
                            f'ingested {self.region_label} Silver for {result.target_date}; '
                            f'published batch dataset version {publication.run_id}'
                        )
                    }
                )
        return results

    def _ingest_backfill_date(self, target_date: date, *, force: bool) -> PipelineRunResult:
        """Validate and persist one regional date without rebuilding derived datasets."""

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        objects: list[StoredObject] = []
        quality_passed = False
        try:
            if self.repository.bronze_complete(target_date) and not force:
                extracted = self.repository.load_bronze(target_date)
                objects.extend(self.repository.bronze_objects(target_date))
            else:
                extracted = self.extractor.extract(target_date)
                objects.extend(
                    self.repository.write_bronze(
                        extracted,
                        run_id=run_id,
                        force=force,
                        fetched_at=datetime.now(UTC),
                        source_lineage=self.source_lineage.value,
                    )
                )

            current_draw = southern_draw_results_frame([extracted.result], run_id)
            current_loto = southern_loto_daily_frame(current_draw, run_id=run_id)
            current_gold = build_southern_gold_tables(current_draw, run_id=run_id)
            report = build_southern_quality_report(
                [extracted.result],
                current_draw,
                current_loto,
                run_id=run_id,
                gold_tables=current_gold,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
                region=self.region,
            )
            require_quality(report)
            quality_passed = True
            objects.extend(self.repository.upsert_silver_draw_results(current_draw))
            return PipelineRunResult(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=(
                    f'ingested validated {self.region_label} Silver for {target_date}; awaiting batch publication'
                ),
            )
        except NoDrawSourcePageError as exc:
            manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.NO_DRAW,
                source_lineage=self.source_lineage,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_message=exc.notice,
            )
            objects.append(self.repository.write_run_manifest(manifest))
            return PipelineRunResult(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.NO_DRAW.value,
                object_count=len(objects),
                message=exc.notice,
            )
        except Exception as exc:
            manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.FAILED,
                source_lineage=self.source_lineage,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                quality_passed=quality_passed,
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
            try:
                self.repository.write_run_manifest(manifest)
            except Exception:
                pass
            raise

    def record_no_draw(self, target_date: date, *, detail: str) -> PipelineRunResult:
        run_id = str(uuid4())
        now = datetime.now(UTC)
        manifest = RunManifest(
            run_id=run_id,
            region=self.region,
            target_date=target_date,
            status=RunStatus.NO_DRAW,
            source_lineage=SourceLineage.LIVE_SOURCE,
            started_at=now,
            completed_at=now,
            error_message=detail,
        )
        self.repository.write_run_manifest(manifest)
        return PipelineRunResult(
            run_id=run_id,
            region=self.region,
            target_date=target_date,
            status=RunStatus.NO_DRAW.value,
            message=detail,
        )

    def build_gold(self) -> PipelineRunResult:
        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        objects: list[StoredObject] = []
        all_draw = self.repository.read_all_silver_draw_results()
        if all_draw.empty:
            raise ValueError(f'no {self.region_label} Silver draw results are available')
        canonical = canonical_southern_results_from_frame(all_draw)
        target_date = canonical[-1].draw_date
        try:
            all_loto = southern_loto_daily_frame(all_draw, run_id=run_id)
            minimum_date = canonical[0].draw_date
            statuses = self.repository.control_state().status_map(minimum_date, target_date)
            for result in canonical:
                statuses[result.draw_date] = DrawStatus.SUCCESS
            gold_tables = build_southern_gold_tables(all_draw, run_id=run_id, statuses=statuses)
            report = build_southern_quality_report(
                canonical,
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
                region=self.region,
            )
            require_quality(report)
            objects.extend(self.repository.replace_silver_loto_daily(all_loto))
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id)
            objects.extend(gold_objects)
            manifest = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.SUCCESS,
                source_lineage=SourceLineage.DERIVED_REBUILD,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                quality_passed=True,
                covered_dates=tuple(result.draw_date for result in canonical),
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
            )
            objects.append(self.repository.write_run_manifest(manifest))
            snapshot, latest = self.repository.publish_snapshot_and_latest(
                run_id=run_id,
                target_date=target_date,
                gold_objects=gold_objects,
            )
            objects.extend([snapshot, latest])
            return PipelineRunResult(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=f'rebuilt {self.region_label} Gold dataset version {run_id}',
            )
        except Exception as exc:
            failure = RunManifest(
                run_id=run_id,
                region=self.region,
                target_date=target_date,
                status=RunStatus.FAILED,
                source_lineage=SourceLineage.DERIVED_REBUILD,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
            try:
                self.repository.write_run_manifest(failure)
            except Exception:
                pass
            raise


def pd_timestamp_date(value) -> date:
    import pandas as pd

    return pd.Timestamp(value).date()


def _safe_error_message(error: Exception) -> str:
    return str(error).replace('\n', ' ')[:500]
