from datetime import date

import pytest

from xsmb_etl.backfill_window import completed_backfill_end_date, main


def test_completed_backfill_end_date_uses_year_end_for_historical_year() -> None:
    assert completed_backfill_end_date(2025, today=date(2026, 7, 19)) == date(2025, 12, 31)


def test_completed_backfill_end_date_excludes_current_day() -> None:
    assert completed_backfill_end_date(2026, today=date(2026, 7, 19)) == date(2026, 7, 18)


def test_completed_backfill_end_date_skips_current_year_on_january_first() -> None:
    assert completed_backfill_end_date(2026, today=date(2026, 1, 1)) is None


def test_completed_backfill_end_date_rejects_future_year() -> None:
    with pytest.raises(ValueError, match='must not be in the future'):
        completed_backfill_end_date(2027, today=date(2026, 7, 19))


def test_backfill_window_cli_prints_deterministic_end_date(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['--year', '2026', '--today', '2026-07-19']) == 0
    assert capsys.readouterr().out == '2026-07-18\n'
