"""Canonical and compatibility models for XSMB draw results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class PrizeGroup(StrEnum):
    SPECIAL = 'special'
    PRIZE1 = 'prize1'
    PRIZE2 = 'prize2'
    PRIZE3 = 'prize3'
    PRIZE4 = 'prize4'
    PRIZE5 = 'prize5'
    PRIZE6 = 'prize6'
    PRIZE7 = 'prize7'


@dataclass(frozen=True)
class PrizeSpec:
    count: int
    width: int


PRIZE_SPECS: dict[PrizeGroup, PrizeSpec] = {
    PrizeGroup.SPECIAL: PrizeSpec(count=1, width=5),
    PrizeGroup.PRIZE1: PrizeSpec(count=1, width=5),
    PrizeGroup.PRIZE2: PrizeSpec(count=2, width=5),
    PrizeGroup.PRIZE3: PrizeSpec(count=6, width=5),
    PrizeGroup.PRIZE4: PrizeSpec(count=4, width=4),
    PrizeGroup.PRIZE5: PrizeSpec(count=6, width=4),
    PrizeGroup.PRIZE6: PrizeSpec(count=3, width=3),
    PrizeGroup.PRIZE7: PrizeSpec(count=4, width=2),
}

EXPECTED_RESULT_COUNT = sum(spec.count for spec in PRIZE_SPECS.values())


class Prize(BaseModel):
    """One prize with explicit width so leading zeros remain meaningful."""

    model_config = ConfigDict(frozen=True)

    prize_group: PrizeGroup
    prize_order: int = Field(ge=1)
    prize_width: int = Field(ge=2, le=5)
    full_number: int = Field(ge=0)

    @property
    def formatted_number(self) -> str:
        return f'{self.full_number:0{self.prize_width}d}'

    @property
    def loto_2d(self) -> str:
        return self.formatted_number[-2:]

    @model_validator(mode='after')
    def validate_against_group_spec(self) -> Prize:
        spec = PRIZE_SPECS[self.prize_group]
        if self.prize_width != spec.width:
            raise ValueError(f'{self.prize_group.value} must use width {spec.width}')
        if self.prize_order > spec.count:
            raise ValueError(f'{self.prize_group.value} order must be at most {spec.count}')
        if self.full_number >= 10**self.prize_width:
            raise ValueError(f'{self.prize_group.value} value is outside its numeric range')
        return self


class LotteryResult(BaseModel):
    """Validated, source-aware representation of one complete XSMB draw."""

    model_config = ConfigDict(frozen=True)

    draw_date: date
    source_url: str
    prizes: tuple[Prize, ...]

    @model_validator(mode='after')
    def validate_complete_draw(self) -> LotteryResult:
        if len(self.prizes) != EXPECTED_RESULT_COUNT:
            raise ValueError(f'a draw must contain exactly {EXPECTED_RESULT_COUNT} prizes')

        for group, spec in PRIZE_SPECS.items():
            group_prizes = self.prizes_for(group)
            if len(group_prizes) != spec.count:
                raise ValueError(f'{group.value} expected {spec.count} values, got {len(group_prizes)}')
            if [prize.prize_order for prize in group_prizes] != list(range(1, spec.count + 1)):
                raise ValueError(f'{group.value} prize order must be unique and consecutive')
        return self

    @classmethod
    def from_prize_groups(
        cls,
        draw_date: date,
        source_url: str,
        groups: Mapping[str | PrizeGroup, Sequence[str | int]],
    ) -> LotteryResult:
        normalized: dict[PrizeGroup, Sequence[str | int]] = {}
        for raw_group, values in groups.items():
            group = raw_group if isinstance(raw_group, PrizeGroup) else PrizeGroup(raw_group)
            if group in normalized:
                raise ValueError(f'duplicate group: {group.value}')
            normalized[group] = values

        prizes: list[Prize] = []
        for group, spec in PRIZE_SPECS.items():
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
                    Prize(
                        prize_group=group,
                        prize_order=order,
                        prize_width=spec.width,
                        full_number=int(text),
                    )
                )

        return cls(draw_date=draw_date, source_url=source_url, prizes=tuple(prizes))

    def prizes_for(self, group: PrizeGroup) -> tuple[Prize, ...]:
        return tuple(prize for prize in self.prizes if prize.prize_group is group)

    def to_legacy_dict(self) -> dict[str, date | int]:
        """Return the original wide 27-prize row in its original column order."""

        values: dict[str, date | int] = {'date': self.draw_date}
        for group, spec in PRIZE_SPECS.items():
            for prize in self.prizes_for(group):
                field_name = group.value if spec.count == 1 else f'{group.value}_{prize.prize_order}'
                values[field_name] = prize.full_number
        return values


class Result(BaseModel):
    """Compatibility model for the existing wide local JSON dataset."""

    date: date
    special: int
    prize1: int
    prize2_1: int
    prize2_2: int
    prize3_1: int
    prize3_2: int
    prize3_3: int
    prize3_4: int
    prize3_5: int
    prize3_6: int
    prize4_1: int
    prize4_2: int
    prize4_3: int
    prize4_4: int
    prize5_1: int
    prize5_2: int
    prize5_3: int
    prize5_4: int
    prize5_5: int
    prize5_6: int
    prize6_1: int
    prize6_2: int
    prize6_3: int
    prize7_1: int
    prize7_2: int
    prize7_3: int
    prize7_4: int

    @classmethod
    def from_canonical(cls, result: LotteryResult) -> Result:
        return cls.model_validate(result.to_legacy_dict())


class ResultList(RootModel[list[Result]]):
    pass


def legacy_result_to_groups(result: Result) -> dict[str, list[int]]:
    """Convert a compatibility row back into grouped canonical inputs."""

    dumped: dict[str, Any] = result.model_dump()
    groups: dict[str, list[int]] = {}
    for group, spec in PRIZE_SPECS.items():
        if spec.count == 1:
            groups[group.value] = [dumped[group.value]]
        else:
            groups[group.value] = [dumped[f'{group.value}_{order}'] for order in range(1, spec.count + 1)]
    return groups
