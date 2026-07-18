"""Idempotent migration of the repository's historical wide JSON dataset."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from xsmb_etl.control import DrawStatus
from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult, ResultList, legacy_result_to_groups
from xsmb_etl.quality import build_quality_report, require_quality
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.run_models import (
    DataObjectReference,
    MigrationIssue,
    MigrationReport,
    PipelineRunResult,
    RunManifest,
    RunStatus,
    SourceLineage,
)
from xsmb_etl.storage import StoredObject
from xsmb_etl.transform import draw_results_frame, loto_daily_frame


class MigrationValidationError(RuntimeError):
    def __init__(self, report: MigrationReport) -> None:
        self.report = report
        super().__init__('legacy dataset failed migration validation; see the migration report')


class HistoricalMigrator:
    def __init__(self, repository: DataLakeRepository) -> None:
        self.repository = repository

    def migrate(self, source_path: Path, *, force: bool = False) -> PipelineRunResult:
        previous = [
            manifest
            for manifest in self.repository.run_manifests()
            if manifest.source_lineage is SourceLineage.LEGACY_REPOSITORY_DATASET
            and manifest.status is RunStatus.SUCCESS
        ]
        if previous and not force:
            latest = previous[-1]
            return PipelineRunResult(
                run_id=latest.run_id,
                target_date=latest.target_date,
                status=latest.status.value,
                skipped=True,
                message='legacy migration already completed; use --force to rebuild it',
            )

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        objects: list[StoredObject] = []
        target_date = date.min
        quality_passed = False
        try:
            legacy = ResultList.model_validate_json(source_path.read_text(encoding='utf-8'))
            canonical, report = self._validate_rows(legacy, run_id)
            objects.append(self.repository.write_migration_report(report))
            if not report.passed:
                raise MigrationValidationError(report)
            if not canonical:
                raise MigrationValidationError(report)

            target_date = canonical[-1].draw_date
            draw = draw_results_frame(canonical, run_id)
            loto = loto_daily_frame(draw, run_id=run_id)
            statuses = self._migration_statuses(report)
            gold_tables = build_gold_tables(draw, run_id=run_id, statuses=statuses)
            quality = build_quality_report(
                canonical,
                draw,
                loto,
                run_id=run_id,
                gold_tables=gold_tables,
                statuses=statuses,
                today=datetime.now(UTC).date(),
            )
            require_quality(quality)
            quality_passed = True

            objects.extend(self.repository.replace_silver_draw_results(draw))
            objects.extend(self.repository.replace_silver_loto_daily(loto))
            objects.append(self.repository.write_quality_report(quality, target_date))
            gold_objects = self.repository.write_gold_tables(gold_tables, run_id=run_id)
            objects.extend(gold_objects)
            manifest = RunManifest(
                run_id=run_id,
                target_date=target_date,
                status=RunStatus.SUCCESS,
                source_lineage=SourceLineage.LEGACY_REPOSITORY_DATASET,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
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
                message=f'migrated {len(canonical)} historical draw dates',
            )
        except Exception as exc:
            failure_manifest = RunManifest(
                run_id=run_id,
                target_date=target_date,
                status=RunStatus.FAILED,
                source_lineage=SourceLineage.LEGACY_REPOSITORY_DATASET,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                forced=force,
                quality_passed=quality_passed,
                objects=tuple(DataObjectReference.from_stored(item) for item in objects),
                error_type=type(exc).__name__,
                error_message=str(exc).replace('\n', ' ')[:500],
            )
            try:
                self.repository.write_run_manifest(failure_manifest)
            except Exception:
                pass
            raise

    @staticmethod
    def _validate_rows(legacy: ResultList, run_id: str) -> tuple[list[LotteryResult], MigrationReport]:
        canonical: list[LotteryResult] = []
        issues: list[MigrationIssue] = []
        for row_number, result in enumerate(legacy.root, start=1):
            try:
                canonical.append(
                    LotteryResult.from_prize_groups(
                        result.date,
                        SourceLineage.LEGACY_REPOSITORY_DATASET.value,
                        legacy_result_to_groups(result),
                    )
                )
            except ValueError as exc:
                issues.append(MigrationIssue(row_number=row_number, draw_date=result.date, message=str(exc)[:500]))

        canonical.sort(key=lambda result: result.draw_date)
        counts = Counter(result.draw_date for result in canonical)
        duplicate_dates = tuple(sorted(draw_date for draw_date, count in counts.items() if count > 1))
        minimum_date = canonical[0].draw_date if canonical else None
        maximum_date = canonical[-1].draw_date if canonical else None
        missing_dates: tuple[date, ...] = ()
        if minimum_date and maximum_date:
            expected = {
                minimum_date + timedelta(days=offset) for offset in range((maximum_date - minimum_date).days + 1)
            }
            missing_dates = tuple(sorted(expected.difference(counts)))
        report = MigrationReport(
            run_id=run_id,
            source_rows=len(legacy.root),
            valid_rows=len(canonical),
            minimum_date=minimum_date,
            maximum_date=maximum_date,
            duplicate_dates=duplicate_dates,
            missing_calendar_dates=missing_dates,
            invalid_rows=tuple(issues),
        )
        return canonical, report

    @staticmethod
    def _migration_statuses(report: MigrationReport) -> dict[date, DrawStatus]:
        if report.minimum_date is None or report.maximum_date is None:
            return {}
        missing = set(report.missing_calendar_dates)
        return {
            report.minimum_date + timedelta(days=offset): (
                DrawStatus.MISSING if report.minimum_date + timedelta(days=offset) in missing else DrawStatus.SUCCESS
            )
            for offset in range((report.maximum_date - report.minimum_date).days + 1)
        }
