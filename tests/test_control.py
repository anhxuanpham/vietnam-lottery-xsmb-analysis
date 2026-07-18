from __future__ import annotations

from datetime import UTC, date, datetime

from xsmb_etl.control import ControlState, DrawStateRecord, DrawStatus


def _record(draw_date: date, status: DrawStatus, hour: int = 0) -> DrawStateRecord:
    return DrawStateRecord(draw_date=draw_date, status=status, updated_at=datetime(2026, 7, 16, hour, tzinfo=UTC))


def test_gap_detection_retries_failed_middle_date_after_later_success() -> None:
    state = ControlState(
        [
            _record(date(2026, 7, 14), DrawStatus.SUCCESS),
            _record(date(2026, 7, 15), DrawStatus.FAILED),
            _record(date(2026, 7, 16), DrawStatus.SUCCESS),
        ]
    )

    assert state.pending_dates(date(2026, 7, 14), date(2026, 7, 16)) == [date(2026, 7, 15)]


def test_success_and_no_draw_are_skipped_unless_forced() -> None:
    state = ControlState(
        [
            _record(date(2026, 7, 14), DrawStatus.SUCCESS),
            _record(date(2026, 7, 15), DrawStatus.NO_DRAW),
        ]
    )

    assert state.pending_dates(date(2026, 7, 14), date(2026, 7, 16)) == [date(2026, 7, 16)]
    assert state.pending_dates(date(2026, 7, 14), date(2026, 7, 16), force=True) == [
        date(2026, 7, 14),
        date(2026, 7, 15),
        date(2026, 7, 16),
    ]


def test_latest_record_wins_for_a_date() -> None:
    state = ControlState(
        [
            _record(date(2026, 7, 16), DrawStatus.FAILED, hour=1),
            _record(date(2026, 7, 16), DrawStatus.SUCCESS, hour=2),
        ]
    )

    assert state.status_for(date(2026, 7, 16)) is DrawStatus.SUCCESS
