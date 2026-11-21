"""Resolve the bounded lookback for scheduled daily backfills."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, timedelta


def catchup_start_date(target_date: date, days: int = 60) -> date:
    if days < 1:
        raise ValueError('catch-up days must be positive')
    return target_date - timedelta(days=days - 1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-date', type=date.fromisoformat, required=True)
    parser.add_argument('--days', type=int, default=60)
    args = parser.parse_args(argv)
    try:
        start = catchup_start_date(args.target_date, args.days)
    except (ValueError, OverflowError) as exc:
        parser.error(str(exc))
    print(start.isoformat())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
