"""End-to-end extract, validate, transform, load, and publish orchestration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from xsmb_etl.control import DrawStatus
from xsmb_etl.extract import NoDrawSourcePageError, ResultExtractor
from xsmb_etl.marts import build_gold_tables
from xsmb_etl.quality import build_quality_report, require_quality
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.run_models import (
    DataObjectReference,
    PipelineRunResult,
    RunManifest,
    RunStatus,
    SourceLineage,
)
from xsmb_etl.storage import StoredObject
from xsmb_etl.transform import canonical_results_from_frame, draw_results_frame, loto_daily_frame


class Pipeline:
    def __init__(
        self,
        repository: DataLakeRepository,
        extractor: ResultExtractor,
        *,
        source_lineage: SourceLineage = SourceLineage.LIVE_SOURCE,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.source_lineage = source_lineage

    def run(self, target_date: date, *, force: bool = False) -> PipelineRunResult:
        control_state = self.repository.control_state()
        if not control_state.should_process(target_date, force=force):
            status = control_state.status_for(target_date)
            return PipelineRunResult(
                run_id=None,
                region=self.repository.region,
                target_date=target_date,
                status=status.value,
                skipped=True,
                message=f'{target_date} is already classified as {status.value}; use --force to replace it',
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

            current_draw = draw_results_frame([extracted.result], run_id)
            current_loto = loto_daily_frame(current_draw, run_id=run_id)
            current_gold = build_gold_tables(current_draw, run_id=run_id)
            pre_write_report = build_quality_report(
                [extracted.result],
                current_draw,
                current_loto,
                run_id=run_id,
                gold_tables=current_gold,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
            )
            require_quality(pre_write_report)

            objects.extend(self.repository.upsert_silver_draw_results(current_draw))
            all_draw = self.repository.read_all_silver_draw_results()
            all_loto = loto_daily_frame(all_draw, run_id=run_id)
            objects.extend(self.repository.replace_silver_loto_daily(all_loto))

            minimum_date = all_draw['draw_date'].min().date()
            maximum_date = all_draw['draw_date'].max().date()
            statuses = control_state.status_map(minimum_date, maximum_date)
            statuses[target_date] = DrawStatus.SUCCESS
            gold_tables = build_gold_tables(all_draw, run_id=run_id, statuses=statuses)
            report = build_quality_report(
                [extracted.result],
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
            )
            require_quality(report)
            quality_passed = True
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id)
            objects.extend(gold_objects)

            success_manifest = RunManifest(
                run_id=run_id,
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
                region=self.repository.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=f'published dataset version {run_id}',
            )
        except NoDrawSourcePageError as exc:
            no_draw_manifest = RunManifest(
                run_id=run_id,
                region=self.repository.region,
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
                region=self.repository.region,
                target_date=target_date,
                status=RunStatus.NO_DRAW.value,
                object_count=len(objects),
                message=exc.notice,
            )
        except Exception as exc:
            failure_manifest = RunManifest(
                run_id=run_id,
                region=self.repository.region,
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
        results = []
        for target_date in pending:
            try:
                results.append(self.run(target_date, force=force))
            except Exception as exc:
                results.append(backfill_failure_result(self.repository, target_date, exc))
        return results

    def record_no_draw(self, target_date: date, *, detail: str) -> PipelineRunResult:
        run_id = str(uuid4())
        now = datetime.now(UTC)
        manifest = RunManifest(
            run_id=run_id,
            region=self.repository.region,
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
            region=self.repository.region,
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
            raise ValueError('no Silver draw results are available')
        canonical = canonical_results_from_frame(all_draw)
        target_date = canonical[-1].draw_date
        try:
            all_loto = loto_daily_frame(all_draw, run_id=run_id)
            minimum_date = canonical[0].draw_date
            statuses = self.repository.control_state().status_map(minimum_date, target_date)
            for result in canonical:
                statuses[result.draw_date] = DrawStatus.SUCCESS
            gold_tables = build_gold_tables(all_draw, run_id=run_id, statuses=statuses)
            report = build_quality_report(
                canonical,
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
            )
            require_quality(report)
            objects.extend(self.repository.replace_silver_loto_daily(all_loto))
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id)
            objects.extend(gold_objects)
            manifest = RunManifest(
                run_id=run_id,
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
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=f'rebuilt Gold dataset version {run_id}',
            )
        except Exception as exc:
            failure = RunManifest(
                run_id=run_id,
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


def _safe_error_message(error: Exception) -> str:
    return str(error).replace('\n', ' ')[:500]


def backfill_failure_result(
    repository: DataLakeRepository,
    target_date: date,
    error: Exception,
) -> PipelineRunResult:
    message = _safe_error_message(error)
    return PipelineRunResult(
        run_id=None,
        region=repository.region,
        target_date=target_date,
        status=RunStatus.FAILED.value,
        message=f'{message}; backfill continued with the next date',
    )
