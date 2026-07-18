"""Canonical models for one Southern Vietnam lottery result page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SouthernPrizeGroup(StrEnum):
    PRIZE8 = 'prize8'
    PRIZE7 = 'prize7'
    PRIZE6 = 'prize6'
    PRIZE5 = 'prize5'
    PRIZE4 = 'prize4'
    PRIZE3 = 'prize3'
    PRIZE2 = 'prize2'
    PRIZE1 = 'prize1'
    SPECIAL = 'special'


@dataclass(frozen=True)
class SouthernPrizeSpec:
    count: int
    width: int


SOUTHERN_PRIZE_SPECS: dict[SouthernPrizeGroup, SouthernPrizeSpec] = {
    SouthernPrizeGroup.PRIZE8: SouthernPrizeSpec(count=1, width=2),
    SouthernPrizeGroup.PRIZE7: SouthernPrizeSpec(count=1, width=3),
    SouthernPrizeGroup.PRIZE6: SouthernPrizeSpec(count=3, width=4),
    SouthernPrizeGroup.PRIZE5: SouthernPrizeSpec(count=1, width=4),
    SouthernPrizeGroup.PRIZE4: SouthernPrizeSpec(count=7, width=5),
    SouthernPrizeGroup.PRIZE3: SouthernPrizeSpec(count=2, width=5),
    SouthernPrizeGroup.PRIZE2: SouthernPrizeSpec(count=1, width=5),
    SouthernPrizeGroup.PRIZE1: SouthernPrizeSpec(count=1, width=5),
    SouthernPrizeGroup.SPECIAL: SouthernPrizeSpec(count=1, width=6),
}

SOUTHERN_EXPECTED_RESULT_COUNT = sum(spec.count for spec in SOUTHERN_PRIZE_SPECS.values())


class SouthernPrize(BaseModel):
    """One XSMN prize value, preserving its official leading-zero width."""

    model_config = ConfigDict(frozen=True)

    prize_group: SouthernPrizeGroup
    prize_order: int = Field(ge=1)
    prize_width: int = Field(ge=2, le=6)
    full_number: int = Field(ge=0)

    @property
    def formatted_number(self) -> str:
        return f'{self.full_number:0{self.prize_width}d}'

    @property
    def loto_2d(self) -> str:
        return self.formatted_number[-2:]

    @model_validator(mode='after')
    def validate_against_group_spec(self) -> SouthernPrize:
        spec = SOUTHERN_PRIZE_SPECS[self.prize_group]
        if self.prize_width != spec.width:
            raise ValueError(f'{self.prize_group.value} must use width {spec.width}')
        if self.prize_order > spec.count:
            raise ValueError(f'{self.prize_group.value} order must be at most {spec.count}')
        if self.full_number >= 10**self.prize_width:
            raise ValueError(f'{self.prize_group.value} value is outside its numeric range')
        return self


class SouthernStationResult(BaseModel):
    """A complete 18-prize result for one XSMN station."""

    model_config = ConfigDict(frozen=True)

    draw_date: date
    station_code: str = Field(pattern=r'^[A-Z0-9]{2,8}$')
    station_name: str = Field(min_length=1)
    station_url: str
    source_url: str
    prizes: tuple[SouthernPrize, ...]

    @model_validator(mode='after')
    def validate_complete_station_draw(self) -> SouthernStationResult:
        if len(self.prizes) != SOUTHERN_EXPECTED_RESULT_COUNT:
            raise ValueError(f'a station draw must contain exactly {SOUTHERN_EXPECTED_RESULT_COUNT} prizes')
        for group, spec in SOUTHERN_PRIZE_SPECS.items():
            group_prizes = self.prizes_for(group)
            if len(group_prizes) != spec.count:
                raise ValueError(f'{group.value} expected {spec.count} values, got {len(group_prizes)}')
            if [prize.prize_order for prize in group_prizes] != list(range(1, spec.count + 1)):
                raise ValueError(f'{group.value} prize order must be unique and consecutive')
        return self

    @classmethod
    def from_prize_groups(
        cls,
        *,
        draw_date: date,
        station_code: str,
        station_name: str,
        station_url: str,
        source_url: str,
        groups: Mapping[str | SouthernPrizeGroup, Sequence[str | int]],
    ) -> SouthernStationResult:
        normalized: dict[SouthernPrizeGroup, Sequence[str | int]] = {}
        for raw_group, values in groups.items():
            group = raw_group if isinstance(raw_group, SouthernPrizeGroup) else SouthernPrizeGroup(raw_group)
            if group in normalized:
                raise ValueError(f'duplicate group: {group.value}')
            normalized[group] = values

        prizes: list[SouthernPrize] = []
        for group, spec in SOUTHERN_PRIZE_SPECS.items():
            values = normalized.get(group, ())
            if len(values) != spec.count:
                raise ValueError(f'{group.value} expected {spec.count} values, got {len(values)}')
            for order, raw_value in enumerate(values, start=1):
                text = str(raw_value).strip()
                if isinstance(raw_value, bool) or not text.isascii() or not text.isdigit():
                    raise ValueError(f'{group.value} value must contain only ASCII digits')
                if isinstance(raw_value, str) and len(text) != spec.width:
                    raise ValueError(f'{group.value} value must contain exactly {spec.width} digits')
                if len(text) > spec.width:
                    raise ValueError(f'{group.value} value must contain exactly {spec.width} digits')
                prizes.append(
                    SouthernPrize(
                        prize_group=group,
                        prize_order=order,
                        prize_width=spec.width,
                        full_number=int(text),
                    )
                )

        return cls(
            draw_date=draw_date,
            station_code=station_code.upper(),
            station_name=station_name.strip(),
            station_url=station_url,
            source_url=source_url,
            prizes=tuple(prizes),
        )

    def prizes_for(self, group: SouthernPrizeGroup) -> tuple[SouthernPrize, ...]:
        return tuple(prize for prize in self.prizes if prize.prize_group is group)


class SouthernDailyResult(BaseModel):
    """All station results represented by one XSMN daily source page."""

    model_config = ConfigDict(frozen=True)

    draw_date: date
    source_url: str
    stations: tuple[SouthernStationResult, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_stations(self) -> SouthernDailyResult:
        codes = [station.station_code for station in self.stations]
        if len(codes) != len(set(codes)):
            raise ValueError('station codes must be unique within one draw date')
        if any(station.draw_date != self.draw_date for station in self.stations):
            raise ValueError('every station must use the daily result draw date')
        if any(station.source_url != self.source_url for station in self.stations):
            raise ValueError('every station must use the daily result source URL')
        return self
