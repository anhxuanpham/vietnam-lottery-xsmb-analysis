"""Shared run, backfill, and gold-rebuild orchestration for every regional lake."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from xsmb_etl.control import DrawStatus
from xsmb_etl.extract import NoDrawSourcePageError
from xsmb_etl.quality import QualityReport, require_quality
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.run_models import (
    DataObjectReference,
    LotteryRegion,
    PipelineRunResult,
    RunManifest,
    RunStatus,
    SourceLineage,
)
from xsmb_etl.storage import StoredObject


class DrawExtractor(Protocol):
    def extract(self, selected_date: date) -> Any: ...


class BasePipeline:
    """Template-method skeleton; subclasses supply region frames, quality, and wording."""

    # Subclasses assign their module logger so log records keep per-lake logger names.
    _logger: logging.Logger

    def __init__(
        self,
        repository: DataLakeRepository,
        extractor: DrawExtractor,
        *,
        source_lineage: SourceLineage = SourceLineage.LIVE_SOURCE,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self.source_lineage = source_lineage
        self.region = repository.region
        self.region_label = self.region.value.upper()

    def run(self, target_date: date, *, force: bool = False) -> PipelineRunResult:
        self.repository.require_etl_writable()
        control_state = self.repository.control_state()
        if not control_state.should_process(target_date, force=force):
            status = control_state.status_for(target_date)
            return PipelineRunResult(
                run_id=None,
                region=self.repository.region,
                target_date=target_date,
                status=status.value,
                skipped=True,
                message=self._skipped_message(target_date, status),
            )

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        self._logger.info('%s run started for %s (run_id=%s)', self.region_label, target_date, run_id)
        objects: list[StoredObject] = []
        quality_passed = False
        publication_committed = False
        try:
            extracted = self._acquire_bronze(target_date, run_id=run_id, force=force, objects=objects)
            current_draw = self._validate_current_draw(extracted, run_id=run_id)

            objects.extend(self.repository.upsert_silver_draw_results(current_draw))
            all_draw = self.repository.read_all_silver_draw_results()
            all_loto = self._loto_daily_frame(all_draw, run_id=run_id)
            current_loto_with_history = all_loto.loc[all_loto['draw_date'].dt.date == target_date].reset_index(
                drop=True
            )
            objects.extend(self.repository.upsert_silver_loto_daily(current_loto_with_history))

            minimum_date = self._boundary_date(all_draw['draw_date'].min())
            maximum_date = self._boundary_date(all_draw['draw_date'].max())
            statuses = control_state.status_map(minimum_date, maximum_date)
            statuses[target_date] = DrawStatus.SUCCESS
            gold_tables = self._gold_tables(all_draw, run_id=run_id, statuses=statuses, loto_daily=all_loto)
            report = self._quality_report(
                [extracted.result],
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=_vietnam_today(),
            )
            require_quality(report)
            quality_passed = True
            self._logger.info('quality report passed for %s (run_id=%s)', target_date, run_id)
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id, formats=('parquet',))
            objects.extend(gold_objects)
            self._logger.info('wrote %d gold objects (run_id=%s)', len(gold_objects), run_id)

            success_manifest = RunManifest(
                run_id=run_id,
                **self._publication_region_kwargs(),
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
            objects.append(self.repository.write_run_manifest(success_manifest, update_control=False))
            snapshot, latest = self.repository.publish_snapshot_and_latest(
                run_id=run_id,
                target_date=target_date,
                gold_objects=gold_objects,
                published_at=success_manifest.completed_at,
            )
            objects.extend([snapshot, latest])
            publication_committed = True
            self.repository.publish_manifest_control_state(success_manifest)
            self._logger.info('published snapshot and latest for %s (run_id=%s)', target_date, run_id)
            return PipelineRunResult(
                run_id=run_id,
                region=self.repository.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=self._published_message(run_id),
            )
        except NoDrawSourcePageError as exc:
            return self._no_draw_outcome(
                exc, run_id=run_id, target_date=target_date, started_at=started_at, force=force, objects=objects
            )
        except Exception as exc:
            if publication_committed:
                raise
            self._record_failure(
                exc,
                run_id=run_id,
                target_date=target_date,
                started_at=started_at,
                force=force,
                quality_passed=quality_passed,
                objects=objects,
            )
            raise

    def backfill(self, start_date: date, end_date: date, *, force: bool = False) -> list[PipelineRunResult]:
        self.repository.require_etl_writable()
        control_state = self.repository.control_state()
        pending = control_state.pending_dates(start_date, end_date, force=force)
        results: list[PipelineRunResult] = []
        successful_result_indexes: list[int] = []
        latest = self.repository.latest_manifest()
        publication_needed = latest is None or any(
            record.updated_at > latest.published_at for record in control_state.records
        )
        for target_date in pending:
            try:
                result = self._ingest_backfill_date(target_date, force=force)
                results.append(result)
                if result.status in {RunStatus.SUCCESS.value, RunStatus.NO_DRAW.value}:
                    publication_needed = True
                if result.status == RunStatus.SUCCESS.value:
                    successful_result_indexes.append(len(results) - 1)
            except Exception as exc:
                results.append(backfill_failure_result(self.repository, target_date, exc))

        if publication_needed:
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
        """Validate and persist one date without rebuilding derived datasets."""

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        self._logger.info('%s backfill ingest started for %s (run_id=%s)', self.region_label, target_date, run_id)
        objects: list[StoredObject] = []
        quality_passed = False
        try:
            extracted = self._acquire_bronze(target_date, run_id=run_id, force=force, objects=objects)
            current_draw = self._validate_current_draw(extracted, run_id=run_id)
            quality_passed = True
            self._logger.info('quality report passed for %s (run_id=%s)', target_date, run_id)
            objects.extend(self.repository.upsert_silver_draw_results(current_draw))
            return PipelineRunResult(
                run_id=run_id,
                region=self.repository.region,
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=(
                    f'ingested validated {self.region_label} Silver for {target_date}; awaiting batch publication'
                ),
            )
        except NoDrawSourcePageError as exc:
            return self._no_draw_outcome(
                exc, run_id=run_id, target_date=target_date, started_at=started_at, force=force, objects=objects
            )
        except Exception as exc:
            self._record_failure(
                exc,
                run_id=run_id,
                target_date=target_date,
                started_at=started_at,
                force=force,
                quality_passed=quality_passed,
                objects=objects,
            )
            raise

    def record_no_draw(self, target_date: date, *, detail: str) -> PipelineRunResult:
        self.repository.require_etl_writable()
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
        self._logger.info('recorded no-draw for %s: %s', target_date, detail)
        return PipelineRunResult(
            run_id=run_id,
            region=self.repository.region,
            target_date=target_date,
            status=RunStatus.NO_DRAW.value,
            message=detail,
        )

    def build_gold(self) -> PipelineRunResult:
        self.repository.require_etl_writable()
        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        objects: list[StoredObject] = []
        publication_committed = False
        all_draw = self.repository.read_all_silver_draw_results()
        if all_draw.empty:
            raise ValueError(self._empty_silver_message())
        canonical = self._canonical_results(all_draw)
        target_date = canonical[-1].draw_date
        self._logger.info('%s gold rebuild started for %s (run_id=%s)', self.region_label, target_date, run_id)
        try:
            all_loto = self._loto_daily_frame(all_draw, run_id=run_id)
            minimum_date = canonical[0].draw_date
            statuses = self.repository.control_state().status_map(minimum_date, target_date)
            for result in canonical:
                statuses[result.draw_date] = DrawStatus.SUCCESS
            gold_tables = self._gold_tables(all_draw, run_id=run_id, statuses=statuses, loto_daily=all_loto)
            report = self._quality_report(
                canonical,
                all_draw,
                all_loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=_vietnam_today(),
            )
            require_quality(report)
            self._logger.info('quality report passed for %s (run_id=%s)', target_date, run_id)
            objects.extend(self.repository.replace_silver_loto_daily(all_loto))
            objects.append(self.repository.write_quality_report(report, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id, formats=('parquet',))
            objects.extend(gold_objects)
            self._logger.info('wrote %d gold objects (run_id=%s)', len(gold_objects), run_id)
            manifest = RunManifest(
                run_id=run_id,
                **self._publication_region_kwargs(),
                target_date=target_date,
                status=RunStatus.SUCCESS,
                source_lineage=SourceLineage.DERIVED_REBUILD,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                quality_passed=True,
                covered_dates=tuple(result.draw_date for result in canonical),
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
            )
            objects.append(self.repository.write_run_manifest(manifest, update_control=False))
            snapshot, latest = self.repository.publish_snapshot_and_latest(
                run_id=run_id,
                target_date=target_date,
                gold_objects=gold_objects,
                published_at=manifest.completed_at,
            )
            objects.extend([snapshot, latest])
            publication_committed = True
            self.repository.publish_manifest_control_state(manifest)
            self._logger.info('published snapshot and latest for %s (run_id=%s)', target_date, run_id)
            return PipelineRunResult(
                run_id=run_id,
                **self._publication_region_kwargs(),
                target_date=target_date,
                status=RunStatus.SUCCESS.value,
                object_count=len(objects),
                message=self._rebuilt_message(run_id),
            )
        except Exception as exc:
            if publication_committed:
                raise
            failure = RunManifest(
                run_id=run_id,
                **self._publication_region_kwargs(),
                target_date=target_date,
                status=RunStatus.FAILED,
                source_lineage=SourceLineage.DERIVED_REBUILD,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
            self._write_failure_manifest(failure, target_date)
            raise

    def _acquire_bronze(self, target_date: date, *, run_id: str, force: bool, objects: list[StoredObject]) -> Any:
        if self.repository.bronze_complete(target_date) and not force:
            self._logger.info('reusing complete bronze data for %s', target_date)
            extracted = self.repository.load_bronze(target_date)
            objects.extend(self.repository.bronze_objects(target_date))
        else:
            self._logger.info('extracting fresh source data for %s', target_date)
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
        return extracted

    def _validate_current_draw(self, extracted: Any, *, run_id: str) -> pd.DataFrame:
        """Quality-gate one draw's frames before any Silver write; returns the draw frame."""

        results = [extracted.result]
        current_draw = self._draw_results_frame(results, run_id)
        current_loto = self._loto_daily_frame(current_draw, run_id=run_id)
        current_gold = self._gold_tables(current_draw, run_id=run_id, loto_daily=current_loto)
        report = self._quality_report(
            results,
            current_draw,
            current_loto,
            run_id=run_id,
            gold_tables=current_gold,
            today=_vietnam_today(),
        )
        require_quality(report)
        return current_draw

    def _no_draw_outcome(
        self,
        exc: NoDrawSourcePageError,
        *,
        run_id: str,
        target_date: date,
        started_at: datetime,
        force: bool,
        objects: list[StoredObject],
    ) -> PipelineRunResult:
        manifest = RunManifest(
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
        objects.append(self.repository.write_run_manifest(manifest))
        self._logger.info('recorded no-draw for %s: %s', target_date, exc.notice)
        return PipelineRunResult(
            run_id=run_id,
            region=self.repository.region,
            target_date=target_date,
            status=RunStatus.NO_DRAW.value,
            object_count=len(objects),
            message=exc.notice,
        )

    def _record_failure(
        self,
        exc: Exception,
        *,
        run_id: str,
        target_date: date,
        started_at: datetime,
        force: bool,
        quality_passed: bool,
        objects: list[StoredObject],
    ) -> None:
        manifest = RunManifest(
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
        self._write_failure_manifest(manifest, target_date)

    def _write_failure_manifest(self, manifest: RunManifest, target_date: date) -> None:
        try:
            self.repository.write_run_manifest(manifest)
        except Exception:
            self._logger.warning('failed to write failure manifest for %s', target_date, exc_info=True)

    def _draw_results_frame(self, results: list, run_id: str) -> pd.DataFrame:
        raise NotImplementedError

    def _loto_daily_frame(self, draw_results: pd.DataFrame, *, run_id: str) -> pd.DataFrame:
        raise NotImplementedError

    def _gold_tables(
        self,
        draw_results: pd.DataFrame,
        *,
        run_id: str,
        statuses: dict[date, DrawStatus] | None = None,
        loto_daily: pd.DataFrame | None = None,
    ) -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    def _quality_report(
        self,
        results: list,
        draw_results: pd.DataFrame,
        loto_daily: pd.DataFrame,
        *,
        run_id: str,
        gold_tables: dict[str, pd.DataFrame] | None = None,
        statuses: dict[date, DrawStatus] | None = None,
        today: date | None = None,
    ) -> QualityReport:
        raise NotImplementedError

    def _canonical_results(self, draw_results: pd.DataFrame) -> list:
        raise NotImplementedError

    def _boundary_date(self, value: Any) -> date:
        """Convert a draw_date column min/max scalar to a plain date."""

        raise NotImplementedError

    def _skipped_message(self, target_date: date, status: DrawStatus) -> str:
        raise NotImplementedError

    def _published_message(self, run_id: str) -> str:
        raise NotImplementedError

    def _rebuilt_message(self, run_id: str) -> str:
        raise NotImplementedError

    def _empty_silver_message(self) -> str:
        raise NotImplementedError

    def _publication_region_kwargs(self) -> dict[str, LotteryRegion]:
        """Region kwargs for publication manifests and rebuild results.

        XSMB publication records predate multi-region support and rely on the
        models' XSMB default instead of passing region explicitly.
        """

        raise NotImplementedError


def _vietnam_today() -> date:
    return datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()


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
