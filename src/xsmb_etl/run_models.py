"""Run, object, and publication manifest models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from xsmb_etl.storage import StoredObject


class RunStatus(StrEnum):
    SUCCESS = 'success'
    FAILED = 'failed'
    NO_DRAW = 'no_draw'


class LotteryRegion(StrEnum):
    XSMB = 'xsmb'
    XSMN = 'xsmn'


class SourceLineage(StrEnum):
    LIVE_SOURCE = 'live_source'
    TEST_FIXTURE = 'test_fixture'
    LEGACY_REPOSITORY_DATASET = 'legacy_repository_dataset'
    DERIVED_REBUILD = 'derived_rebuild'


class DataObjectReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    size: int
    sha256: str
    content_type: str
    cache_control: str | None = None

    @classmethod
    def from_stored(cls, stored: StoredObject) -> DataObjectReference:
        return cls(**stored.__dict__)


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    region: LotteryRegion = LotteryRegion.XSMB
    target_date: date
    status: RunStatus
    source_lineage: SourceLineage
    started_at: datetime
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    forced: bool = False
    quality_passed: bool = False
    covered_dates: tuple[date, ...] = ()
    objects: tuple[DataObjectReference, ...] = ()
    error_type: str | None = None
    error_message: str | None = None


class LatestManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    region: LotteryRegion = LotteryRegion.XSMB
    dataset_version: str
    target_date: date
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    objects: tuple[DataObjectReference, ...]


class PipelineRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str | None
    region: LotteryRegion = LotteryRegion.XSMB
    target_date: date
    status: str
    skipped: bool = False
    object_count: int = 0
    message: str


class MigrationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_number: int
    draw_date: date | None = None
    message: str


class MigrationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    source_lineage: SourceLineage = SourceLineage.LEGACY_REPOSITORY_DATASET
    source_rows: int
    valid_rows: int
    minimum_date: date | None = None
    maximum_date: date | None = None
    duplicate_dates: tuple[date, ...] = ()
    missing_calendar_dates: tuple[date, ...] = ()
    invalid_rows: tuple[MigrationIssue, ...] = ()

    @computed_field
    @property
    def passed(self) -> bool:
        return self.source_rows == self.valid_rows and not self.duplicate_dates and not self.invalid_rows
