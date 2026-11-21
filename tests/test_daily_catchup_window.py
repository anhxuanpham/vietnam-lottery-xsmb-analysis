from datetime import date, timedelta
from pathlib import Path

import pytest

from xsmb_etl.control import ControlState, DrawStateRecord, DrawStatus
from xsmb_etl.daily_catchup_window import catchup_start_date, main


def test_default_window_repairs_interior_gap_and_five_week_outage() -> None:
    target = date(2026, 11, 19)
    start = catchup_start_date(target)
    outage_start = date(2026, 10, 15)
    gap = date(2026, 10, 5)
    state = ControlState(
        DrawStateRecord(draw_date=day, status=DrawStatus.SUCCESS)
        for offset in range((outage_start - start).days)
        if (day := start + timedelta(days=offset)) != gap
    )

    pending = state.pending_dates(start, target)

    assert len(pending) == 37
    assert pending == [gap] + [outage_start + timedelta(days=offset) for offset in range(36)]
    assert start == date(2026, 9, 21)


def test_completed_window_has_no_pending_dates() -> None:
    target = date(2026, 10, 6)
    start = catchup_start_date(target, 2)
    state = ControlState(
        [
            DrawStateRecord(draw_date=start, status=DrawStatus.NO_DRAW),
            DrawStateRecord(draw_date=target, status=DrawStatus.SUCCESS),
        ]
    )
    assert state.pending_dates(start, target) == []


def test_empty_lake_is_bounded_and_failed_dates_are_retried() -> None:
    target = date(2026, 10, 6)
    start = catchup_start_date(target, 2)
    assert ControlState().pending_dates(start, target) == [start, target]
    state = ControlState([DrawStateRecord(draw_date=start, status=DrawStatus.FAILED)])
    assert state.pending_dates(start, target) == [start, target]


@pytest.mark.parametrize('days', [0, -1])
def test_invalid_window_fails_before_processing(days: int, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(['--target-date', '2026-10-06', '--days', str(days)])
    assert exc.value.code == 2
    assert 'catch-up days must be positive' in capsys.readouterr().err


def test_cli_prints_inclusive_start(capsys) -> None:
    assert main(['--target-date', '2026-10-06', '--days', '1']) == 0
    assert capsys.readouterr().out == '2026-10-06\n'


def test_workflow_catchup_is_scheduled_only_and_checks_failed_results() -> None:
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/daily-etl.yml').read_text()
    scheduled = workflow.split('target_date="$SCHEDULED_TARGET_DATE"', 1)[1].split('          fi', 1)[0]
    assert '--target-date "$target_date" --days "$CATCHUP_DAYS"' in scheduled
    assert '--from "$from_date" --to "$target_date"' in scheduled
    assert 'any(.[]; .status == "failed")' in scheduled
    assert 'exit 1' in scheduled
    assert "vars.DAILY_ETL_CATCHUP_DAYS || '60'" in workflow
    manual = workflow.split('            exit 0\n          fi\n', 1)[1]
    assert 'command+=(--target-date "$target_date")' in manual
    assert 'command+=(--force)' in manual
