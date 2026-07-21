"""Read-only historical integrity audit for published lottery Gold tables."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from xsmb_etl.control import DrawStatus
from xsmb_etl.lake_status import Repository, inspect_lake
from xsmb_etl.models import EXPECTED_RESULT_COUNT, PRIZE_SPECS
from xsmb_etl.run_models import DataObjectReference, LatestManifest, LotteryRegion
from xsmb_etl.station_calendar import expected_station_codes
from xsmb_etl.storage import ObjectNotFoundError
from xsmb_etl.xsmn_models import SOUTHERN_EXPECTED_RESULT_COUNT, SOUTHERN_PRIZE_SPECS


VIETNAM_TIME_ZONE = ZoneInfo('Asia/Ho_Chi_Minh')

DEFAULT_START_DATES: dict[LotteryRegion, date] = {
    LotteryRegion.XSMB: date(2005, 10, 1),
    LotteryRegion.XSMN: date(2010, 1, 1),
    LotteryRegion.XSMT: date(2018, 1, 1),
}

DRAW_CUTOFFS: dict[LotteryRegion, time] = {
    LotteryRegion.XSMN: time(16, 35),
    LotteryRegion.XSMT: time(17, 35),
    LotteryRegion.XSMB: time(18, 35),
}

DIM_DATE_KEY = 'gold/latest/dim-date.parquet'
FACT_DRAW_RESULT_KEY = 'gold/latest/fact-draw-result.parquet'
FACT_LOTO_DAILY_KEY = 'gold/latest/fact-loto-daily.parquet'
DIM_STATION_KEY = 'gold/latest/dim-station.parquet'

_STATION_REGIONS = frozenset({LotteryRegion.XSMN, LotteryRegion.XSMT})
_STATION_CODE_PATTERN = re.compile(r'^[A-Z0-9]{2,8}$')
_SAMPLE_LIMIT = 25


class HistoryAuditSeverity(StrEnum):
    """Impact of a historical audit finding."""

    CRITICAL = 'critical'
    WARNING = 'warning'


class HistoryAuditIssue(BaseModel):
    """One JSON-serializable historical data finding."""

    model_config = ConfigDict(frozen=True)

    severity: HistoryAuditSeverity = HistoryAuditSeverity.CRITICAL
    code: str
    message: str
    count: int = Field(default=1, ge=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class HistoryAuditReport(BaseModel):
    """Deterministic report for one published regional lake and date range."""

    model_config = ConfigDict(frozen=True)

    region: LotteryRegion
    run_id: str | None = None
    dataset_version: str | None = None
    manifest_target_date: date | None = None
    from_date: date
    to_date: date
    latest_completed_date: date
    fact_row_count: int = Field(default=0, ge=0)
    loto_row_count: int = Field(default=0, ge=0)
    station_count: int = Field(default=0, ge=0)
    status_counts: dict[str, int] = Field(default_factory=dict)
    issues: tuple[HistoryAuditIssue, ...] = ()

    @computed_field
    @property
    def healthy(self) -> bool:
        """A report is healthy only when the audit produced no findings."""

        return not self.issues


def audit_history(
    repository: Repository,
    from_date: date | None = None,
    to_date: date | None = None,
    now: datetime | None = None,
) -> HistoryAuditReport:
    """Audit only manifest-published Gold tables; never list or mutate the lake."""

    latest_completed_date = _latest_completed_date(repository.region, now=now)
    effective_from = from_date or DEFAULT_START_DATES[repository.region]
    effective_to = to_date or latest_completed_date
    if effective_to < effective_from:
        raise ValueError('to_date must be on or after from_date')

    issues: list[HistoryAuditIssue] = []

    # Publication metadata is deliberately the first lake operation.  It catches
    # a broken consumer boundary before any potentially large Gold download.
    lake_status = inspect_lake(repository)
    for message in lake_status.issues:
        _add_issue(
            issues,
            'lake-publication-invalid',
            message,
        )

    latest = _load_latest_manifest(repository, issues)
    if latest is None:
        return _report(
            repository.region,
            effective_from,
            effective_to,
            latest_completed_date,
            issues=issues,
        )

    references = _references_by_key(latest, issues)
    required_keys = [DIM_DATE_KEY, FACT_DRAW_RESULT_KEY, FACT_LOTO_DAILY_KEY]
    if repository.region in _STATION_REGIONS:
        required_keys.append(DIM_STATION_KEY)

    tables = {key: _read_verified_parquet(repository, references, key, issues) for key in required_keys}

    dim_date = tables[DIM_DATE_KEY]
    fact = tables[FACT_DRAW_RESULT_KEY]
    loto = tables[FACT_LOTO_DAILY_KEY]
    dim_station = tables.get(DIM_STATION_KEY)

    status_by_date, status_counts = _audit_status_dimension(
        dim_date,
        effective_from,
        effective_to,
        issues,
    )
    fact_full, fact_in_range = _prepare_fact(
        fact,
        repository.region,
        latest.run_id,
        effective_from,
        effective_to,
        issues,
    )
    _, loto_in_range = _prepare_loto(
        loto,
        repository.region,
        latest.run_id,
        effective_from,
        effective_to,
        issues,
    )

    _audit_status_fact_alignment(status_by_date, fact_in_range, loto_in_range, issues)
    _audit_fact_rows(fact_in_range, repository.region, issues)
    _audit_loto_rows(loto_in_range, fact_in_range, repository.region, issues)

    if repository.region in _STATION_REGIONS:
        _audit_station_sets(status_by_date, fact_in_range, loto_in_range, repository.region, issues)
        station_count = _audit_station_dimension(dim_station, fact_full, issues)
    else:
        station_count = 0

    return _report(
        repository.region,
        effective_from,
        effective_to,
        latest_completed_date,
        latest=latest,
        fact_row_count=len(fact_in_range),
        loto_row_count=len(loto_in_range),
        station_count=station_count,
        status_counts=status_counts,
        issues=issues,
    )


def _latest_completed_date(region: LotteryRegion, *, now: datetime | None) -> date:
    current = now or datetime.now(VIETNAM_TIME_ZONE)
    local = (
        current.replace(tzinfo=VIETNAM_TIME_ZONE) if current.tzinfo is None else current.astimezone(VIETNAM_TIME_ZONE)
    )
    cutoff = datetime.combine(local.date(), DRAW_CUTOFFS[region], tzinfo=VIETNAM_TIME_ZONE)
    return local.date() if local >= cutoff else local.date() - timedelta(days=1)


def _load_latest_manifest(repository: Repository, issues: list[HistoryAuditIssue]) -> LatestManifest | None:
    key = 'manifests/latest.json'
    try:
        payload = repository.store.get_bytes(key)
    except ObjectNotFoundError:
        _add_issue(issues, 'latest-manifest-missing', f'{key} is missing')
        return None

    try:
        return LatestManifest.model_validate_json(payload)
    except (ValueError, TypeError) as exc:
        _add_issue(
            issues,
            'latest-manifest-invalid',
            f'{key} is invalid',
            details={'error': _brief_error(exc)},
        )
        return None


def _references_by_key(
    latest: LatestManifest,
    issues: list[HistoryAuditIssue],
) -> dict[str, DataObjectReference]:
    references: dict[str, DataObjectReference] = {}
    duplicate_keys: list[str] = []
    for reference in latest.objects:
        if reference.key in references:
            duplicate_keys.append(reference.key)
            continue
        references[reference.key] = reference
    if duplicate_keys:
        unique_duplicates = sorted(set(duplicate_keys))
        _add_issue(
            issues,
            'gold-reference-duplicate',
            'latest manifest contains duplicate Gold references',
            count=len(unique_duplicates),
            details={'keys': unique_duplicates},
        )
    return references


def _read_verified_parquet(
    repository: Repository,
    references: dict[str, DataObjectReference],
    key: str,
    issues: list[HistoryAuditIssue],
) -> pd.DataFrame | None:
    reference = references.get(key)
    if reference is None:
        _add_issue(
            issues,
            'gold-reference-missing',
            f'latest manifest does not reference required object {key}',
            details={'key': key},
        )
        return None

    try:
        payload = repository.store.get_bytes(key)
    except ObjectNotFoundError:
        _add_issue(
            issues,
            'gold-object-missing',
            f'manifest-referenced object {key} is missing',
            details={'key': key},
        )
        return None

    if len(payload) != reference.size:
        _add_issue(
            issues,
            'gold-object-size-mismatch',
            f'{key} byte size does not match its manifest reference',
            details={'key': key, 'expected': reference.size, 'actual': len(payload)},
        )
        return None

    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != reference.sha256:
        _add_issue(
            issues,
            'gold-object-checksum-mismatch',
            f'{key} SHA-256 does not match its manifest reference',
            details={'key': key, 'expected': reference.sha256, 'actual': checksum},
        )
        return None

    try:
        return pd.read_parquet(BytesIO(payload))
    except Exception as exc:  # Parquet engines expose several unrelated parse exceptions.
        _add_issue(
            issues,
            'gold-parquet-invalid',
            f'{key} cannot be decoded as Parquet',
            details={'key': key, 'error': _brief_error(exc)},
        )
        return None


def _audit_status_dimension(
    dim_date: pd.DataFrame | None,
    from_date: date,
    to_date: date,
    issues: list[HistoryAuditIssue],
) -> tuple[dict[date, DrawStatus], dict[str, int]]:
    expected_dates = _date_range(from_date, to_date)
    status_counts = {status.value: 0 for status in DrawStatus}
    if dim_date is None:
        status_counts[DrawStatus.MISSING.value] = len(expected_dates)
        return (
            {draw_date: DrawStatus.MISSING for draw_date in expected_dates},
            {DrawStatus.MISSING.value: len(expected_dates)},
        )

    required = {'date', 'draw_status'}
    missing_columns = sorted(required.difference(dim_date.columns))
    if missing_columns:
        _add_issue(
            issues,
            'dim-date-columns-missing',
            f'{DIM_DATE_KEY} is missing required columns',
            count=len(missing_columns),
            details={'columns': missing_columns},
        )
        status_counts[DrawStatus.MISSING.value] = len(expected_dates)
        return (
            {draw_date: DrawStatus.MISSING for draw_date in expected_dates},
            {DrawStatus.MISSING.value: len(expected_dates)},
        )

    working = dim_date[['date', 'draw_status']].copy()
    working['_audit_date'] = _coerce_timestamps(working['date'])
    invalid_dates = int(working['_audit_date'].isna().sum())
    if invalid_dates:
        _add_issue(
            issues,
            'dim-date-values-invalid',
            f'{DIM_DATE_KEY} contains invalid date values',
            count=invalid_dates,
        )

    start_timestamp = pd.Timestamp(from_date)
    end_timestamp = pd.Timestamp(to_date)
    selected = working.loc[working['_audit_date'].between(start_timestamp, end_timestamp)].copy()
    duplicate_mask = selected.duplicated('_audit_date', keep=False)
    duplicate_dates = sorted(
        {pd.Timestamp(value).date() for value in selected.loc[duplicate_mask, '_audit_date'].dropna()}
    )
    if duplicate_dates:
        _add_issue(
            issues,
            'dim-date-business-key-duplicate',
            f'{DIM_DATE_KEY} contains duplicate dates',
            count=len(duplicate_dates),
            details=_date_details(duplicate_dates),
        )

    selected = selected.drop_duplicates('_audit_date', keep='last')
    rows_by_date = {
        pd.Timestamp(draw_timestamp).date(): raw_status
        for draw_timestamp, raw_status in zip(selected['_audit_date'], selected['draw_status'])
        if not pd.isna(draw_timestamp)
    }
    absent_dates = [draw_date for draw_date in expected_dates if draw_date not in rows_by_date]
    if absent_dates:
        _add_issue(
            issues,
            'dim-date-calendar-gap',
            f'{DIM_DATE_KEY} is not contiguous across the requested range',
            count=len(absent_dates),
            details=_date_details(absent_dates),
        )

    status_by_date: dict[date, DrawStatus] = {}
    invalid_status_dates: list[date] = []
    for draw_date in expected_dates:
        raw_status = rows_by_date.get(draw_date)
        try:
            status = (
                DrawStatus(str(raw_status))
                if raw_status is not None and not pd.isna(raw_status)
                else DrawStatus.MISSING
            )
        except ValueError:
            status = DrawStatus.MISSING
            invalid_status_dates.append(draw_date)
        status_by_date[draw_date] = status
        status_counts[status.value] += 1

    if invalid_status_dates:
        _add_issue(
            issues,
            'draw-status-invalid',
            f'{DIM_DATE_KEY} contains unsupported draw_status values',
            count=len(invalid_status_dates),
            details=_date_details(invalid_status_dates),
        )

    missing_dates = [draw_date for draw_date, status in status_by_date.items() if status is DrawStatus.MISSING]
    failed_dates = [draw_date for draw_date, status in status_by_date.items() if status is DrawStatus.FAILED]
    if missing_dates:
        _add_issue(
            issues,
            'draw-status-missing',
            'requested range contains dates without a completed classification',
            count=len(missing_dates),
            details=_date_details(missing_dates),
        )
    if failed_dates:
        _add_issue(
            issues,
            'draw-status-failed',
            'requested range contains failed draw dates',
            count=len(failed_dates),
            details=_date_details(failed_dates),
        )
    return status_by_date, {status: count for status, count in status_counts.items() if count}


def _prepare_fact(
    dataframe: pd.DataFrame | None,
    region: LotteryRegion,
    run_id: str,
    from_date: date,
    to_date: date,
    issues: list[HistoryAuditIssue],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {
        'draw_date',
        'prize_group',
        'prize_order',
        'prize_width',
        'full_number',
        'formatted_number',
        'loto_2d',
        'tens_digit',
        'ones_digit',
        'source_url',
        'run_id',
    }
    if region in _STATION_REGIONS:
        required.update({'station_code', 'station_order', 'station_name', 'station_url'})
    if dataframe is None:
        return pd.DataFrame(), pd.DataFrame()
    if not _require_columns(dataframe, required, FACT_DRAW_RESULT_KEY, 'fact-columns-missing', issues):
        return pd.DataFrame(), pd.DataFrame()

    working = dataframe.copy()
    working['_audit_date'] = _coerce_timestamps(working['draw_date'])
    working['_audit_prize_group'] = working['prize_group'].astype('string')
    if region in _STATION_REGIONS:
        working['_audit_station_code'] = working['station_code'].astype('string')
    invalid_dates = int(working['_audit_date'].isna().sum())
    if invalid_dates:
        _add_issue(
            issues,
            'fact-draw-date-invalid',
            f'{FACT_DRAW_RESULT_KEY} contains invalid draw_date values',
            count=invalid_dates,
        )
    _audit_run_ids(working, run_id, FACT_DRAW_RESULT_KEY, 'fact-run-id-mismatch', issues)
    selected = working.loc[working['_audit_date'].between(pd.Timestamp(from_date), pd.Timestamp(to_date))].copy()
    return working, selected


def _prepare_loto(
    dataframe: pd.DataFrame | None,
    region: LotteryRegion,
    run_id: str,
    from_date: date,
    to_date: date,
    issues: list[HistoryAuditIssue],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {
        'draw_date',
        'number_2d',
        'frequency',
        'appeared',
        'draws_since_previous',
        'calendar_days_since_previous',
        'previous_appearance_status',
        'rolling_7_frequency',
        'rolling_30_frequency',
        'rolling_90_frequency',
        'run_id',
    }
    if region in _STATION_REGIONS:
        required.update({'station_code', 'station_name'})
    if dataframe is None:
        return pd.DataFrame(), pd.DataFrame()
    if not _require_columns(dataframe, required, FACT_LOTO_DAILY_KEY, 'loto-columns-missing', issues):
        return pd.DataFrame(), pd.DataFrame()

    working = dataframe.copy()
    working['_audit_date'] = _coerce_timestamps(working['draw_date'])
    working['_audit_number'] = working['number_2d'].astype('string')
    if region in _STATION_REGIONS:
        working['_audit_station_code'] = working['station_code'].astype('string')
    invalid_dates = int(working['_audit_date'].isna().sum())
    if invalid_dates:
        _add_issue(
            issues,
            'loto-draw-date-invalid',
            f'{FACT_LOTO_DAILY_KEY} contains invalid draw_date values',
            count=invalid_dates,
        )
    _audit_run_ids(working, run_id, FACT_LOTO_DAILY_KEY, 'loto-run-id-mismatch', issues)
    selected = working.loc[working['_audit_date'].between(pd.Timestamp(from_date), pd.Timestamp(to_date))].copy()
    return working, selected


def _audit_run_ids(
    dataframe: pd.DataFrame,
    expected_run_id: str,
    key: str,
    code: str,
    issues: list[HistoryAuditIssue],
) -> None:
    normalized = dataframe['run_id'].astype('string')
    invalid = normalized.isna() | ~normalized.eq(expected_run_id).fillna(False)
    if invalid.any():
        run_ids = sorted(set(normalized.dropna().astype(str)))
        _add_issue(
            issues,
            code,
            f'{key} contains run_id rows that do not match the latest manifest',
            count=int(invalid.sum()),
            details={
                'expected': expected_run_id,
                'actual': run_ids,
                'null_count': int(normalized.isna().sum()),
            },
        )


def _audit_status_fact_alignment(
    status_by_date: dict[date, DrawStatus],
    fact: pd.DataFrame,
    loto: pd.DataFrame,
    issues: list[HistoryAuditIssue],
) -> None:
    fact_dates = _frame_dates(fact)
    loto_dates = _frame_dates(loto)
    success_without_fact = [
        draw_date
        for draw_date, status in status_by_date.items()
        if status is DrawStatus.SUCCESS and draw_date not in fact_dates
    ]
    success_without_loto = [
        draw_date
        for draw_date, status in status_by_date.items()
        if status is DrawStatus.SUCCESS and draw_date not in loto_dates
    ]
    if success_without_fact:
        _add_issue(
            issues,
            'success-date-missing-draw-facts',
            'every success date must have fact-draw-result rows',
            count=len(success_without_fact),
            details=_date_details(success_without_fact),
        )
    if success_without_loto:
        _add_issue(
            issues,
            'success-date-missing-loto-facts',
            'every success date must have fact-loto-daily rows',
            count=len(success_without_loto),
            details=_date_details(success_without_loto),
        )

    invalid_no_draw = [
        draw_date
        for draw_date, status in status_by_date.items()
        if status is DrawStatus.NO_DRAW and (draw_date in fact_dates or draw_date in loto_dates)
    ]
    if invalid_no_draw:
        _add_issue(
            issues,
            'no-draw-has-facts',
            'no_draw is valid only when both Gold fact tables contain no rows for that date',
            count=len(invalid_no_draw),
            details=_date_details(invalid_no_draw),
        )

    invalid_incomplete = [
        draw_date
        for draw_date, status in status_by_date.items()
        if status in {DrawStatus.MISSING, DrawStatus.FAILED} and (draw_date in fact_dates or draw_date in loto_dates)
    ]
    if invalid_incomplete:
        _add_issue(
            issues,
            'incomplete-date-has-facts',
            'missing and failed dates must not have rows in either Gold fact table',
            count=len(invalid_incomplete),
            details=_date_details(invalid_incomplete),
        )


def _audit_fact_rows(
    fact: pd.DataFrame,
    region: LotteryRegion,
    issues: list[HistoryAuditIssue],
) -> None:
    if fact.empty:
        return

    station_grain = region in _STATION_REGIONS
    group_columns = ['_audit_date', '_audit_station_code'] if station_grain else ['_audit_date']
    fact = fact.copy()
    fact['_audit_prize_order_key'] = fact['prize_order'].astype('string')
    business_key = [*group_columns, '_audit_prize_group', '_audit_prize_order_key']
    duplicate_mask = fact.duplicated(business_key, keep=False)
    if duplicate_mask.any():
        _add_issue(
            issues,
            'fact-business-key-duplicate',
            f'{FACT_DRAW_RESULT_KEY} contains duplicate business keys',
            count=int(duplicate_mask.sum()),
            details={'samples': _row_samples(fact.loc[duplicate_mask], business_key)},
        )

    expected_count = SOUTHERN_EXPECTED_RESULT_COUNT if station_grain else EXPECTED_RESULT_COUNT
    group_sizes = fact.groupby(group_columns, dropna=False).size()
    bad_sizes = group_sizes.loc[~group_sizes.eq(expected_count)]
    if not bad_sizes.empty:
        _add_issue(
            issues,
            'fact-row-count-invalid',
            f'each draw grain must contain exactly {expected_count} prize rows',
            count=len(bad_sizes),
            details={'samples': _series_group_samples(bad_sizes)},
        )

    specs = SOUTHERN_PRIZE_SPECS if station_grain else PRIZE_SPECS
    expected_layout = {(group.value, order) for group, spec in specs.items() for order in range(1, spec.count + 1)}
    bad_layouts: list[str] = []
    for group_key, rows in fact.groupby(group_columns, dropna=False, sort=False):
        normalized_groups = rows['_audit_prize_group']
        normalized_orders = pd.to_numeric(rows['prize_order'], errors='coerce')
        actual_layout = {
            (str(group), int(order))
            for group, order in zip(normalized_groups, normalized_orders)
            if not pd.isna(group) and not pd.isna(order) and float(order).is_integer()
        }
        if actual_layout != expected_layout:
            bad_layouts.append(_group_key_text(group_key))
    if bad_layouts:
        _add_issue(
            issues,
            'fact-prize-layout-invalid',
            'draw grains do not contain the exact expected prize-group and order layout',
            count=len(bad_layouts),
            details={'samples': bad_layouts[:_SAMPLE_LIMIT]},
        )

    expected_widths = {group.value: spec.width for group, spec in specs.items()}
    group_values = fact['_audit_prize_group']
    expected_width = pd.to_numeric(group_values.map(expected_widths), errors='coerce')
    order_values = pd.to_numeric(fact['prize_order'], errors='coerce')
    width_values = pd.to_numeric(fact['prize_width'], errors='coerce')
    full_values = pd.to_numeric(fact['full_number'], errors='coerce')
    formatted_values = fact['formatted_number'].astype('string')
    loto_values = fact['loto_2d'].astype('string')

    numeric_shape_valid = (
        expected_width.notna()
        & order_values.notna()
        & order_values.mod(1).eq(0)
        & width_values.notna()
        & width_values.mod(1).eq(0)
        & width_values.eq(expected_width)
        & full_values.notna()
        & np.isfinite(full_values)
        & full_values.mod(1).eq(0)
        & full_values.ge(0)
    )
    range_valid = full_values.lt(10 ** expected_width.fillna(0))
    expected_formatted = pd.Series(
        [_format_prize_value(full_number, width) for full_number, width in zip(full_values, expected_width)],
        index=fact.index,
        dtype='string',
    )
    formatted_valid = formatted_values.map(_ascii_digits).fillna(False) & formatted_values.eq(expected_formatted)
    loto_valid = loto_values.map(_two_ascii_digits).fillna(False) & loto_values.eq(expected_formatted.str[-2:])
    tens_values = pd.to_numeric(fact['tens_digit'], errors='coerce')
    ones_values = pd.to_numeric(fact['ones_digit'], errors='coerce')
    digit_valid = tens_values.eq(pd.to_numeric(loto_values.str[0], errors='coerce')) & ones_values.eq(
        pd.to_numeric(loto_values.str[1], errors='coerce')
    )
    valid = numeric_shape_valid & range_valid & formatted_valid & loto_valid & digit_valid
    invalid_mask = ~valid.fillna(False)
    if invalid_mask.any():
        _add_issue(
            issues,
            'fact-prize-integrity-invalid',
            'prize width, numeric value, formatted value, or loto digits are inconsistent',
            count=int(invalid_mask.sum()),
            details={'samples': _row_samples(fact.loc[invalid_mask], business_key)},
        )


def _audit_loto_rows(
    loto: pd.DataFrame,
    fact: pd.DataFrame,
    region: LotteryRegion,
    issues: list[HistoryAuditIssue],
) -> None:
    if loto.empty:
        return

    station_grain = region in _STATION_REGIONS
    group_columns = ['_audit_date', '_audit_station_code'] if station_grain else ['_audit_date']
    business_key = [*group_columns, '_audit_number']
    duplicate_mask = loto.duplicated(business_key, keep=False)
    if duplicate_mask.any():
        _add_issue(
            issues,
            'loto-business-key-duplicate',
            f'{FACT_LOTO_DAILY_KEY} contains duplicate business keys',
            count=int(duplicate_mask.sum()),
            details={'samples': _row_samples(loto.loc[duplicate_mask], business_key)},
        )

    group_sizes = loto.groupby(group_columns, dropna=False).size()
    bad_sizes = group_sizes.loc[~group_sizes.eq(100)]
    if not bad_sizes.empty:
        _add_issue(
            issues,
            'loto-row-count-invalid',
            'each draw grain must contain exactly 100 loto rows',
            count=len(bad_sizes),
            details={'samples': _series_group_samples(bad_sizes)},
        )

    number_values = loto['_audit_number']
    invalid_number_mask = ~number_values.map(_two_ascii_digits).fillna(False)
    if invalid_number_mask.any():
        _add_issue(
            issues,
            'loto-number-invalid',
            'loto number_2d values must be the ASCII strings 00 through 99',
            count=int(invalid_number_mask.sum()),
            details={'samples': _row_samples(loto.loc[invalid_number_mask], business_key)},
        )

    expected_numbers = {f'{number:02d}' for number in range(100)}
    bad_coverage: list[str] = []
    for group_key, rows in loto.groupby(group_columns, dropna=False, sort=False):
        if set(rows['_audit_number'].dropna().astype(str)) != expected_numbers:
            bad_coverage.append(_group_key_text(group_key))
    if bad_coverage:
        _add_issue(
            issues,
            'loto-number-coverage-invalid',
            'draw grains do not cover 00 through 99 exactly once',
            count=len(bad_coverage),
            details={'samples': bad_coverage[:_SAMPLE_LIMIT]},
        )

    frequency_values = pd.to_numeric(loto['frequency'], errors='coerce')
    invalid_frequency = frequency_values.isna() | ~frequency_values.mod(1).eq(0) | frequency_values.lt(0)
    if invalid_frequency.any():
        _add_issue(
            issues,
            'loto-frequency-invalid',
            'loto frequency values must be non-negative integers',
            count=int(invalid_frequency.sum()),
            details={'samples': _row_samples(loto.loc[invalid_frequency], business_key)},
        )

    expected_sum = SOUTHERN_EXPECTED_RESULT_COUNT if station_grain else EXPECTED_RESULT_COUNT
    sums = frequency_values.groupby([loto[column] for column in group_columns], dropna=False).sum(min_count=1)
    bad_sums = sums.loc[~sums.eq(expected_sum)]
    if not bad_sums.empty:
        _add_issue(
            issues,
            'loto-frequency-sum-invalid',
            f'each draw grain loto frequency must sum to {expected_sum}',
            count=len(bad_sums),
            details={'samples': _series_group_samples(bad_sums)},
        )

    appeared_values = loto['appeared'].map(_strict_bool)
    invalid_appeared = appeared_values.isna() | ~appeared_values.eq(frequency_values.gt(0))
    if invalid_appeared.any():
        _add_issue(
            issues,
            'loto-appeared-invalid',
            'appeared must equal frequency > 0',
            count=int(invalid_appeared.sum()),
            details={'samples': _row_samples(loto.loc[invalid_appeared], business_key)},
        )

    if fact.empty:
        return
    fact_frequency = (
        fact.assign(_audit_number=fact['loto_2d'].astype('string'))
        .groupby([*group_columns, '_audit_number'], dropna=False)
        .size()
        .rename('_expected_frequency')
        .reset_index()
    )
    comparison = loto.assign(_audit_number=number_values, _actual_frequency=frequency_values).merge(
        fact_frequency,
        on=[*group_columns, '_audit_number'],
        how='left',
    )
    mismatch = ~comparison['_actual_frequency'].eq(comparison['_expected_frequency'].fillna(0))
    if mismatch.any():
        _add_issue(
            issues,
            'loto-frequency-fact-mismatch',
            'loto frequencies do not equal frequencies derived from fact-draw-result',
            count=int(mismatch.sum()),
            details={
                'samples': _row_samples(
                    comparison.loc[mismatch],
                    [*group_columns, '_audit_number'],
                )
            },
        )


def _audit_station_sets(
    status_by_date: dict[date, DrawStatus],
    fact: pd.DataFrame,
    loto: pd.DataFrame,
    region: LotteryRegion,
    issues: list[HistoryAuditIssue],
) -> None:
    fact_sets = _station_sets_by_date(fact)
    loto_sets = _station_sets_by_date(loto)
    mismatches: list[dict[str, JsonValue]] = []
    for draw_date, status in status_by_date.items():
        if status is not DrawStatus.SUCCESS:
            continue
        expected = expected_station_codes(region, draw_date)
        actual_fact = fact_sets.get(draw_date, frozenset())
        actual_loto = loto_sets.get(draw_date, frozenset())
        if actual_fact != expected or actual_loto != expected:
            mismatches.append(
                {
                    'date': draw_date.isoformat(),
                    'expected': sorted(expected),
                    'fact': sorted(actual_fact),
                    'loto': sorted(actual_loto),
                }
            )
    if mismatches:
        _add_issue(
            issues,
            'station-set-mismatch',
            'successful dates must contain the exact scheduled station set in both fact tables',
            count=len(mismatches),
            details={'samples': mismatches[:_SAMPLE_LIMIT]},
        )


def _audit_station_dimension(
    dim_station: pd.DataFrame | None,
    fact_full: pd.DataFrame,
    issues: list[HistoryAuditIssue],
) -> int:
    if dim_station is None:
        return 0
    required = {'station_code', 'station_name', 'station_url', 'first_draw_date', 'latest_draw_date'}
    if not _require_columns(
        dim_station,
        required,
        DIM_STATION_KEY,
        'dim-station-columns-missing',
        issues,
    ):
        return 0

    working = dim_station.copy()
    code_values = working['station_code'].astype('string')
    invalid_codes = ~code_values.map(
        lambda value: isinstance(value, str) and bool(_STATION_CODE_PATTERN.fullmatch(value))
    ).fillna(False)
    if invalid_codes.any():
        _add_issue(
            issues,
            'dim-station-code-invalid',
            f'{DIM_STATION_KEY} contains invalid station codes',
            count=int(invalid_codes.sum()),
            details={'codes': sorted(set(code_values.loc[invalid_codes].dropna().astype(str)))[:_SAMPLE_LIMIT]},
        )

    duplicate_codes = code_values.duplicated(keep=False)
    if duplicate_codes.any():
        codes = sorted(set(code_values.loc[duplicate_codes].dropna().astype(str)))
        _add_issue(
            issues,
            'dim-station-business-key-duplicate',
            f'{DIM_STATION_KEY} contains duplicate station codes',
            count=len(codes),
            details={'codes': codes[:_SAMPLE_LIMIT]},
        )

    working['_audit_first_date'] = _coerce_timestamps(working['first_draw_date'])
    working['_audit_latest_date'] = _coerce_timestamps(working['latest_draw_date'])
    invalid_ranges = (
        working['_audit_first_date'].isna()
        | working['_audit_latest_date'].isna()
        | working['_audit_first_date'].gt(working['_audit_latest_date'])
    )
    if invalid_ranges.any():
        _add_issue(
            issues,
            'dim-station-range-invalid',
            f'{DIM_STATION_KEY} contains invalid station date ranges',
            count=int(invalid_ranges.sum()),
            details={'codes': sorted(set(code_values.loc[invalid_ranges].dropna().astype(str)))[:_SAMPLE_LIMIT]},
        )

    dimension_codes = set(code_values.dropna().astype(str))
    if fact_full.empty or not {'station_code', '_audit_date'}.issubset(fact_full.columns):
        return len(dimension_codes)

    fact_codes = set(fact_full['station_code'].dropna().astype(str))
    if fact_codes != dimension_codes:
        _add_issue(
            issues,
            'dim-station-code-set-mismatch',
            'dim-station codes must exactly match station codes in fact-draw-result',
            details={
                'missing': sorted(fact_codes - dimension_codes),
                'extra': sorted(dimension_codes - fact_codes),
            },
        )

    valid_fact = fact_full.loc[fact_full['_audit_date'].notna()].copy()
    fact_ranges = valid_fact.groupby(valid_fact['station_code'].astype(str))['_audit_date'].agg(['min', 'max'])
    mismatches: list[dict[str, JsonValue]] = []
    for code_value, first_date, latest_date in zip(
        working['station_code'],
        working['_audit_first_date'],
        working['_audit_latest_date'],
    ):
        code = str(code_value)
        if code not in fact_ranges.index or pd.isna(first_date) or pd.isna(latest_date):
            continue
        actual_first = pd.Timestamp(fact_ranges.loc[code, 'min'])
        actual_latest = pd.Timestamp(fact_ranges.loc[code, 'max'])
        if first_date != actual_first or latest_date != actual_latest:
            mismatches.append(
                {
                    'station_code': code,
                    'expected_first': actual_first.date().isoformat(),
                    'actual_first': pd.Timestamp(first_date).date().isoformat(),
                    'expected_latest': actual_latest.date().isoformat(),
                    'actual_latest': pd.Timestamp(latest_date).date().isoformat(),
                }
            )
    if mismatches:
        _add_issue(
            issues,
            'dim-station-range-mismatch',
            'dim-station first/latest dates must match fact-draw-result',
            count=len(mismatches),
            details={'samples': mismatches[:_SAMPLE_LIMIT]},
        )
    return len(dimension_codes)


def _require_columns(
    dataframe: pd.DataFrame,
    required: set[str],
    key: str,
    code: str,
    issues: list[HistoryAuditIssue],
) -> bool:
    missing = sorted(required.difference(dataframe.columns))
    if not missing:
        return True
    _add_issue(
        issues,
        code,
        f'{key} is missing required columns',
        count=len(missing),
        details={'columns': missing},
    )
    return False


def _report(
    region: LotteryRegion,
    from_date: date,
    to_date: date,
    latest_completed_date: date,
    *,
    latest: LatestManifest | None = None,
    fact_row_count: int = 0,
    loto_row_count: int = 0,
    station_count: int = 0,
    status_counts: dict[str, int] | None = None,
    issues: list[HistoryAuditIssue],
) -> HistoryAuditReport:
    return HistoryAuditReport(
        region=region,
        run_id=latest.run_id if latest else None,
        dataset_version=latest.dataset_version if latest else None,
        manifest_target_date=latest.target_date if latest else None,
        from_date=from_date,
        to_date=to_date,
        latest_completed_date=latest_completed_date,
        fact_row_count=fact_row_count,
        loto_row_count=loto_row_count,
        station_count=station_count,
        status_counts=status_counts or {},
        issues=tuple(issues),
    )


def _date_range(from_date: date, to_date: date) -> list[date]:
    return [from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)]


def _coerce_timestamps(values: pd.Series) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, errors='coerce')
        if parsed.dt.tz is not None:
            parsed = parsed.dt.tz_localize(None)
        return parsed.dt.normalize()
    except (AttributeError, ValueError, TypeError):
        return values.map(_coerce_calendar_timestamp)


def _frame_dates(dataframe: pd.DataFrame) -> set[date]:
    if dataframe.empty or '_audit_date' not in dataframe.columns:
        return set()
    return {pd.Timestamp(value).date() for value in dataframe['_audit_date'].dropna().unique()}


def _station_sets_by_date(dataframe: pd.DataFrame) -> dict[date, frozenset[str]]:
    if dataframe.empty or not {'_audit_date', 'station_code'}.issubset(dataframe.columns):
        return {}
    station_column = '_audit_station_code' if '_audit_station_code' in dataframe.columns else 'station_code'
    return {
        pd.Timestamp(draw_timestamp).date(): frozenset(rows[station_column].dropna().astype(str))
        for draw_timestamp, rows in dataframe.groupby('_audit_date', sort=False)
    }


def _coerce_calendar_timestamp(value: Any) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return pd.NaT
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _ascii_digits(value: Any) -> bool:
    return isinstance(value, str) and value.isascii() and value.isdigit()


def _two_ascii_digits(value: Any) -> bool:
    return _ascii_digits(value) and len(value) == 2


def _strict_bool(value: Any) -> bool | None:
    return bool(value) if isinstance(value, (bool, np.bool_)) else None


def _format_prize_value(full_number: Any, width: Any) -> str | None:
    if pd.isna(full_number) or pd.isna(width):
        return None
    if not np.isfinite(full_number) or not np.isfinite(width):
        return None
    return f'{int(full_number):0{int(width)}d}'


def _date_details(dates: list[date]) -> dict[str, JsonValue]:
    return {
        'dates': [draw_date.isoformat() for draw_date in dates[:_SAMPLE_LIMIT]],
        'truncated': len(dates) > _SAMPLE_LIMIT,
    }


def _row_samples(dataframe: pd.DataFrame, columns: list[str]) -> list[str]:
    samples = []
    for row in (
        dataframe.loc[:, [column for column in columns if column in dataframe.columns]]
        .head(_SAMPLE_LIMIT)
        .itertuples(index=False, name=None)
    ):
        samples.append('/'.join(_sample_value(value) for value in row))
    return samples


def _series_group_samples(values: pd.Series) -> list[str]:
    samples = []
    for key, value in values.head(_SAMPLE_LIMIT).items():
        samples.append(f'{_group_key_text(key)}={_sample_value(value)}')
    return samples


def _group_key_text(value: Any) -> str:
    values = value if isinstance(value, tuple) else (value,)
    return '/'.join(_sample_value(item) for item in values)


def _sample_value(value: Any) -> str:
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and missing:
        return '<null>'
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).date().isoformat()
    return str(value)


def _add_issue(
    issues: list[HistoryAuditIssue],
    code: str,
    message: str,
    *,
    count: int = 1,
    details: dict[str, JsonValue] | None = None,
    severity: HistoryAuditSeverity = HistoryAuditSeverity.CRITICAL,
) -> None:
    issues.append(
        HistoryAuditIssue(
            severity=severity,
            code=code,
            message=message,
            count=max(1, count),
            details=details or {},
        )
    )


def _brief_error(error: Exception) -> str:
    return str(error).replace('\n', ' ')[:240]


__all__ = [
    'DEFAULT_START_DATES',
    'DRAW_CUTOFFS',
    'HistoryAuditIssue',
    'HistoryAuditReport',
    'HistoryAuditSeverity',
    'audit_history',
]
