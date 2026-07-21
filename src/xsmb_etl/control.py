"""Draw-date control state and deterministic gap detection."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xsmb_etl.run_models import LotteryRegion


class DrawStatus(StrEnum):
    SUCCESS = 'success'
    NO_DRAW = 'no_draw'
    MISSING = 'missing'
    FAILED = 'failed'


class DrawStateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    draw_date: date
    status: DrawStatus
    run_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = None


class ControlStateSnapshot(BaseModel):
    """Immutable, complete materialization of the latest state per draw date."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    region: LotteryRegion
    revision: str = Field(min_length=1)
    parent_revision: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    records: tuple[DrawStateRecord, ...] = ()

    @model_validator(mode='after')
    def validate_unique_sorted_records(self) -> ControlStateSnapshot:
        dates = [record.draw_date for record in self.records]
        if dates != sorted(dates):
            raise ValueError('control-state records must be sorted by draw_date')
        if len(dates) != len(set(dates)):
            raise ValueError('control-state records must contain unique draw dates')
        return self

    def to_state(self) -> ControlState:
        return ControlState(self.records)


class ControlStatePointer(BaseModel):
    """Small mutable pointer to one immutable control-state version."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    region: LotteryRegion
    revision: str = Field(min_length=1)
    version_key: str = Field(pattern=r'^control/versions/[^/]+\.json$')
    size: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    record_count: int = Field(ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ControlState:
    """Latest explicit state for each draw date."""

    def __init__(self, records: Iterable[DrawStateRecord] = ()) -> None:
        self._records: dict[date, DrawStateRecord] = {}
        for record in records:
            existing = self._records.get(record.draw_date)
            if existing is None or record.updated_at >= existing.updated_at:
                self._records[record.draw_date] = record

    def status_for(self, draw_date: date) -> DrawStatus:
        record = self._records.get(draw_date)
        return record.status if record else DrawStatus.MISSING

    def record_for(self, draw_date: date) -> DrawStateRecord | None:
        return self._records.get(draw_date)

    def should_process(self, draw_date: date, *, force: bool = False) -> bool:
        if force:
            return True
        return self.status_for(draw_date) not in {DrawStatus.SUCCESS, DrawStatus.NO_DRAW}

    def pending_dates(self, start_date: date, end_date: date, *, force: bool = False) -> list[date]:
        if end_date < start_date:
            raise ValueError('end_date must not be before start_date')
        days = (end_date - start_date).days
        expected_dates = (start_date + timedelta(days=offset) for offset in range(days + 1))
        return [draw_date for draw_date in expected_dates if self.should_process(draw_date, force=force)]

    def status_map(self, start_date: date, end_date: date) -> dict[date, DrawStatus]:
        if end_date < start_date:
            raise ValueError('end_date must not be before start_date')
        days = (end_date - start_date).days
        return {
            draw_date: self.status_for(draw_date)
            for draw_date in (start_date + timedelta(days=offset) for offset in range(days + 1))
        }

    def with_record(self, record: DrawStateRecord) -> ControlState:
        return self.with_records((record,))

    def with_records(self, records: Iterable[DrawStateRecord]) -> ControlState:
        return ControlState([*self._records.values(), *records])

    @property
    def records(self) -> tuple[DrawStateRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda record: record.draw_date))
