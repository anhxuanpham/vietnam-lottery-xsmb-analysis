from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo


VIETNAM_TIMEZONE = ZoneInfo('Asia/Ho_Chi_Minh')
DRAW_CUTOFF = time(18, 35)
MAX_SAME_DAY_WAIT = timedelta(minutes=20)


@dataclass(frozen=True)
class DailyDrawWindow:
    target_date: date
    wait_seconds: int


def resolve_daily_draw_window(now: datetime, *, wait_now: datetime | None = None) -> DailyDrawWindow:
    if now.tzinfo is None:
        raise ValueError('now must include a timezone')
    if wait_now is not None and wait_now.tzinfo is None:
        raise ValueError('wait_now must include a timezone')
    if wait_now is not None and wait_now < now:
        raise ValueError('wait_now must not precede now')

    vietnam_now = now.astimezone(VIETNAM_TIMEZONE)
    cutoff = datetime.combine(vietnam_now.date(), DRAW_CUTOFF, tzinfo=VIETNAM_TIMEZONE)
    remaining = cutoff - vietnam_now
    if remaining <= timedelta(0):
        target_date = vietnam_now.date()
    elif remaining <= MAX_SAME_DAY_WAIT:
        target_date = vietnam_now.date()
    else:
        target_date = vietnam_now.date() - timedelta(days=1)

    wait_reference = (wait_now or now).astimezone(VIETNAM_TIMEZONE)
    target_cutoff = datetime.combine(target_date, DRAW_CUTOFF, tzinfo=VIETNAM_TIMEZONE)
    wait_seconds = max(0, math.ceil((target_cutoff - wait_reference).total_seconds()))
    return DailyDrawWindow(target_date=target_date, wait_seconds=wait_seconds)


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('datetime must be ISO-8601') from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError('datetime must include a timezone')
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Resolve the completed draw date for the scheduled daily ETL')
    parser.add_argument('--github-output', type=Path, required=True)
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--now', type=_datetime, help=argparse.SUPPRESS)
    parser.add_argument('--wait-now', type=_datetime, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    now = args.now or datetime.now(UTC)
    wait_now = args.wait_now
    if args.wait and args.now is not None and wait_now is None:
        wait_now = datetime.now(UTC)
    window = resolve_daily_draw_window(now, wait_now=wait_now)
    with args.github_output.open('a', encoding='utf-8') as output:
        output.write(f'target_date={window.target_date.isoformat()}\n')
        output.write(f'wait_seconds={window.wait_seconds}\n')

    vietnam_now = now.astimezone(VIETNAM_TIMEZONE)
    if window.wait_seconds:
        print(f'Scheduled early; waiting {window.wait_seconds} seconds for the {window.target_date} draw cutoff.')
        if args.wait:
            sleep(window.wait_seconds)
    elif window.target_date == vietnam_now.date():
        print(f'Draw cutoff has passed; processing {window.target_date} immediately.')
    else:
        print(
            'Runner is outside the bounded same-day wait window; '
            f'processing the completed {window.target_date} draw immediately.'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
