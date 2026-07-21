from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from xsmb_etl.control import DrawStatus
from xsmb_etl.history_audit import (
    DEFAULT_START_DATES,
    HistoryAuditIssue,
    HistoryAuditSeverity,
    audit_history,
)
from xsmb_etl.marts import build_gold_tables, dim_date_frame
from xsmb_etl.models import LotteryResult
from xsmb_etl.repository import DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import DataObjectReference, LotteryRegion, RunManifest, RunStatus, SourceLineage
from xsmb_etl.storage import LocalObjectStore, ObjectStoreError
from xsmb_etl.transform import draw_results_frame
from xsmb_etl.xsmn_extract import parse_southern_result_page
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_transform import southern_draw_results_frame


XSMN_FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


def _publish(repository, tables, *, run_id: str, target_date: date) -> None:
    objects = repository.write_gold_tables(tables, run_id=run_id)
    completed_at = datetime(2026, 7, 20, tzinfo=UTC)
    repository.write_run_manifest(
        RunManifest(
            run_id=run_id,
            region=repository.region,
            target_date=target_date,
            status=RunStatus.SUCCESS,
            source_lineage=SourceLineage.DERIVED_REBUILD,
            started_at=completed_at,
            completed_at=completed_at,
            quality_passed=True,
            covered_dates=tuple(
                pd_timestamp.date() for pd_timestamp in sorted(tables['dim-date']['date'].drop_duplicates())
            ),
            objects=tuple(DataObjectReference.from_stored(item) for item in objects),
        )
    )
    repository.publish_snapshot_and_latest(
        run_id=run_id,
        target_date=target_date,
        gold_objects=objects,
    )


def _xsmb_repository(tmp_path, grouped_prize_values, *, status: DrawStatus = DrawStatus.SUCCESS):
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'audit-xsmb')
    tables = build_gold_tables(fact, run_id='audit-xsmb')
    tables['dim-date']['draw_status'] = status.value
    _publish(repository, tables, run_id='audit-xsmb', target_date=draw_date)
    return repository, draw_date


def test_audit_history_accepts_a_complete_xsmb_gold_snapshot(tmp_path, grouped_prize_values) -> None:
    repository, draw_date = _xsmb_repository(tmp_path, grouped_prize_values)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    assert report.healthy is True
    assert report.fact_row_count == 27
    assert report.loto_row_count == 100
    assert report.station_count == 0
    assert report.status_counts == {'success': 1}
    assert report.issues == ()
    assert report.model_dump(mode='json')['healthy'] is True
    with pytest.raises(ValidationError, match='frozen'):
        report.fact_row_count = 0

    warning_report = report.model_copy(
        update={
            'issues': (
                HistoryAuditIssue(
                    severity=HistoryAuditSeverity.WARNING,
                    code='example-warning',
                    message='warnings still make an audit unhealthy',
                ),
            )
        }
    )
    assert warning_report.healthy is False


def test_audit_history_uses_region_cutoff_for_default_end_date(tmp_path) -> None:
    cases = (
        (LotteryRegion.XSMN, datetime(2026, 7, 21, 16, 34), date(2026, 7, 20)),
        (LotteryRegion.XSMT, datetime(2026, 7, 21, 17, 35), date(2026, 7, 21)),
        (LotteryRegion.XSMB, datetime(2026, 7, 21, 18, 34), date(2026, 7, 20)),
    )
    for region, now, expected_end in cases:
        repository = DataLakeRepository(
            LocalObjectStore(tmp_path / region.value),
            gold_cache_control='no-cache',
            region=region,
        )

        report = audit_history(repository, now=now)

        assert report.from_date == DEFAULT_START_DATES[region]
        assert report.to_date == expected_end
        assert report.latest_completed_date == expected_end
        assert report.healthy is False


def test_audit_history_rejects_reversed_range_before_touching_storage() -> None:
    class UnexpectedStore:
        def exists(self, _key):
            raise AssertionError('storage must not be read for an invalid range')

    repository = DataLakeRepository(UnexpectedStore(), gold_cache_control='no-cache')

    with pytest.raises(ValueError, match='to_date must be on or after from_date'):
        audit_history(
            repository,
            from_date=date(2026, 7, 17),
            to_date=date(2026, 7, 16),
        )


def test_audit_history_requires_exact_status_to_fact_alignment(tmp_path, grouped_prize_values) -> None:
    no_draw_repository, draw_date = _xsmb_repository(
        tmp_path / 'no-draw',
        grouped_prize_values,
        status=DrawStatus.NO_DRAW,
    )
    no_draw_report = audit_history(no_draw_repository, from_date=draw_date, to_date=draw_date)

    assert no_draw_report.healthy is False
    assert 'no-draw-has-facts' in {issue.code for issue in no_draw_report.issues}

    success_repository = DataLakeRepository(
        LocalObjectStore(tmp_path / 'missing-success'),
        gold_cache_control='no-cache',
    )
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'missing-success')
    tables = build_gold_tables(fact, run_id='missing-success')
    next_date = date(2026, 7, 17)
    tables['dim-date'] = dim_date_frame(
        draw_date,
        next_date,
        {draw_date: DrawStatus.SUCCESS, next_date: DrawStatus.SUCCESS},
    )
    _publish(success_repository, tables, run_id='missing-success', target_date=next_date)

    success_report = audit_history(success_repository, from_date=draw_date, to_date=next_date)
    issue_codes = {issue.code for issue in success_report.issues}
    assert success_report.healthy is False
    assert 'success-date-missing-draw-facts' in issue_codes
    assert 'success-date-missing-loto-facts' in issue_codes


def test_audit_history_validates_exact_xsmn_station_calendar_and_dimension(tmp_path) -> None:
    repository = SouthernDataLakeRepository(
        LocalObjectStore(tmp_path),
        gold_cache_control='no-cache',
    )
    draw_date = date(2026, 7, 16)
    result = parse_southern_result_page(
        XSMN_FIXTURE.read_bytes(),
        selected_date=draw_date,
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )
    fact = southern_draw_results_frame([result], 'audit-xsmn')
    tables = build_southern_gold_tables(fact, run_id='audit-xsmn')
    _publish(repository, tables, run_id='audit-xsmn', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    assert report.healthy is True
    assert report.fact_row_count == 54
    assert report.loto_row_count == 300
    assert report.station_count == 3
    assert report.issues == ()


def test_audit_history_reports_corrupt_manifest_referenced_gold(tmp_path, grouped_prize_values) -> None:
    repository, draw_date = _xsmb_repository(tmp_path, grouped_prize_values)
    repository.store.put_bytes(
        'gold/latest/fact-draw-result.parquet',
        b'changed after publication',
        content_type='application/vnd.apache.parquet',
    )

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    issue_codes = {issue.code for issue in report.issues}
    assert report.healthy is False
    assert 'lake-publication-invalid' in issue_codes
    assert 'gold-object-size-mismatch' in issue_codes


def test_audit_history_returns_data_findings_for_malformed_prize_values(tmp_path, grouped_prize_values) -> None:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'malformed-prize')
    tables = build_gold_tables(fact, run_id='malformed-prize')
    tables['fact-draw-result']['full_number'] = tables['fact-draw-result']['full_number'].astype('float64')
    tables['fact-draw-result'].loc[0, 'full_number'] = float('inf')
    tables['fact-draw-result'].loc[0, 'formatted_number'] = 'not-digits'
    _publish(repository, tables, run_id='malformed-prize', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    assert report.healthy is False
    assert 'fact-prize-integrity-invalid' in {issue.code for issue in report.issues}


def test_audit_history_normalizes_timezone_aware_gold_dates(tmp_path, grouped_prize_values) -> None:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'timezone-aware')
    tables = build_gold_tables(fact, run_id='timezone-aware')
    for table_name, date_column in (
        ('dim-date', 'date'),
        ('fact-draw-result', 'draw_date'),
        ('fact-loto-daily', 'draw_date'),
    ):
        tables[table_name][date_column] = pd.to_datetime(tables[table_name][date_column]).dt.tz_localize(
            'Asia/Ho_Chi_Minh'
        )
    _publish(repository, tables, run_id='timezone-aware', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    assert report.healthy is True
    assert report.status_counts == {'success': 1}


def test_audit_history_reports_unhashable_scalar_columns_instead_of_crashing(
    tmp_path,
    grouped_prize_values,
) -> None:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'list-scalar')
    tables = build_gold_tables(fact, run_id='list-scalar')
    tables['fact-draw-result']['prize_group'] = tables['fact-draw-result']['prize_group'].map(
        lambda value: [str(value)]
    )
    _publish(repository, tables, run_id='list-scalar', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    issue_codes = {issue.code for issue in report.issues}
    assert report.healthy is False
    assert 'fact-prize-layout-invalid' in issue_codes
    assert 'fact-prize-integrity-invalid' in issue_codes


def test_audit_history_rejects_null_run_id_rows(tmp_path, grouped_prize_values) -> None:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'null-run-id')
    tables = build_gold_tables(fact, run_id='null-run-id')
    tables['fact-draw-result'].loc[0, 'run_id'] = None
    _publish(repository, tables, run_id='null-run-id', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    issue = next(issue for issue in report.issues if issue.code == 'fact-run-id-mismatch')
    assert report.healthy is False
    assert issue.count == 1
    assert issue.details['null_count'] == 1


def test_audit_history_requires_the_full_published_gold_schema(tmp_path, grouped_prize_values) -> None:
    repository = DataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    draw_date = date(2026, 7, 16)
    result = LotteryResult.from_prize_groups(
        draw_date,
        'https://example.test/xsmb',
        grouped_prize_values,
    )
    fact = draw_results_frame([result], 'missing-columns')
    tables = build_gold_tables(fact, run_id='missing-columns')
    tables['fact-draw-result'] = tables['fact-draw-result'].drop(columns='source_url')
    tables['fact-loto-daily'] = tables['fact-loto-daily'].drop(columns='rolling_90_frequency')
    _publish(repository, tables, run_id='missing-columns', target_date=draw_date)

    report = audit_history(repository, from_date=draw_date, to_date=draw_date)

    issue_codes = {issue.code for issue in report.issues}
    assert report.healthy is False
    assert 'fact-columns-missing' in issue_codes
    assert 'loto-columns-missing' in issue_codes


def test_audit_history_does_not_swallow_storage_failures() -> None:
    class FailingStore:
        def exists(self, _key):
            raise ObjectStoreError('network unavailable')

    repository = DataLakeRepository(FailingStore(), gold_cache_control='no-cache')

    with pytest.raises(ObjectStoreError, match='network unavailable'):
        audit_history(
            repository,
            from_date=date(2026, 7, 16),
            to_date=date(2026, 7, 16),
        )
