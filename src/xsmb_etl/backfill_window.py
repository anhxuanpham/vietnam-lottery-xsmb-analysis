"""Resolve the completed-date boundary for historical backfill jobs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def completed_backfill_end_date(batch_year: int, *, today: date) -> date | None:
    """Return the last completed date that a historical yearly batch may process."""

    if batch_year > today.year:
        raise ValueError('batch year must not be in the future')
    if batch_year < today.year:
        return date(batch_year, 12, 31)

    yesterday = today - timedelta(days=1)
    return yesterday if yesterday.year == batch_year else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Print the safe XSMN historical backfill end date')
    parser.add_argument('--year', type=int, required=True, help='batch year')
    parser.add_argument('--today', type=date.fromisoformat, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    today = args.today or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    end_date = completed_backfill_end_date(args.year, today=today)
    if end_date is not None:
        print(end_date.isoformat())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
