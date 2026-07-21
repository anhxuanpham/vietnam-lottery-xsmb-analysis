"""Run, object, and publication manifest models."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from xsmb_etl.storage import StoredObject


class RunStatus(StrEnum):
    SUCCESS = 'success'
    FAILED = 'failed'
    NO_DRAW = 'no_draw'


class LotteryRegion(StrEnum):
    XSMB = 'xsmb'
    XSMN = 'xsmn'
    XSMT = 'xsmt'


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

    schema_version: int = Field(default=1, ge=1)
    run_id: str
    region: LotteryRegion = LotteryRegion.XSMB
    dataset_version: str
    target_date: date
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    release_prefix: str | None = None
    objects: tuple[DataObjectReference, ...]

    @model_validator(mode='after')
    def validate_publication_boundary(self) -> LatestManifest:
        if self.dataset_version != self.run_id:
            raise ValueError('dataset_version must equal run_id')
        if not self.objects:
            raise ValueError('latest manifest must reference at least one Gold object')
        keys = [reference.key for reference in self.objects]
        if len(keys) != len(set(keys)):
            raise ValueError('latest manifest must not contain duplicate object keys')
        if self.schema_version == 1:
            if self.release_prefix is not None or any(not key.startswith('gold/latest/') for key in keys):
                raise ValueError('schema v1 latest manifest must reference only gold/latest objects')
            return self
        if self.schema_version == 2:
            expected_prefix = f'gold/releases/run-id={self.run_id}/'
            if self.release_prefix != expected_prefix or any(not key.startswith(expected_prefix) for key in keys):
                raise ValueError('schema v2 latest manifest must reference its exact immutable release prefix')
            return self
        raise ValueError(f'unsupported latest manifest schema_version: {self.schema_version}')


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
