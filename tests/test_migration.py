from __future__ import annotations

from datetime import date

from xsmb_etl.migration import HistoricalMigrator
from xsmb_etl.models import LotteryResult, Result
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.run_models import MigrationReport
from xsmb_etl.storage import LocalObjectStore


def test_historical_migration_is_reconciled_and_idempotent(
    tmp_path,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    first = LotteryResult.from_prize_groups(date(2026, 7, 14), '', grouped_prize_values)
    second = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    source_path = tmp_path / 'legacy.json'
    source_path.write_text(
        f'[{Result.from_canonical(first).model_dump_json()},{Result.from_canonical(second).model_dump_json()}]',
        encoding='utf-8',
    )
    lake_root = tmp_path / 'lake'
    repository = DataLakeRepository(LocalObjectStore(lake_root), gold_cache_control='public, max-age=300')
    migrator = HistoricalMigrator(repository)

    migrated = migrator.migrate(source_path)
    repeated = migrator.migrate(source_path)

    assert migrated.status == 'success'
    assert repeated.skipped
    report_key = f'quality/migrations/run-id={migrated.run_id}/report.json'
    report = MigrationReport.model_validate_json(repository.store.get_bytes(report_key))
    assert report.source_rows == 2
    assert report.valid_rows == 2
    assert report.missing_calendar_dates == (date(2026, 7, 15),)
    assert repository.latest_manifest().run_id == migrated.run_id
    assert not repository.store.list_keys('bronze/')
    assert repository.control_state().status_for(date(2026, 7, 14)).value == 'success'
