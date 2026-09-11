from datetime import UTC, date, datetime
import importlib.util
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    'resolve_daily_window', PROJECT_ROOT / 'scripts' / 'resolve_daily_window.py'
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
RESOLVER = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = RESOLVER
MODULE_SPEC.loader.exec_module(RESOLVER)
main = RESOLVER.main
resolve_daily_draw_window = RESOLVER.resolve_daily_draw_window


@pytest.mark.parametrize(
    ('runner_started_at', 'expected_target_date'),
    [
        (datetime(2026, 8, 27, 21, 1, 14, tzinfo=UTC), date(2026, 8, 27)),
        (datetime(2026, 8, 28, 21, 21, 23, tzinfo=UTC), date(2026, 8, 28)),
        (datetime(2026, 8, 31, 17, 59, 47, tzinfo=UTC), date(2026, 8, 31)),
    ],
)
def test_delayed_runner_processes_previous_completed_draw(
    runner_started_at: datetime,
    expected_target_date: date,
) -> None:
    window = resolve_daily_draw_window(runner_started_at)

    assert window.target_date == expected_target_date
    assert window.wait_seconds == 0


def test_on_time_runner_waits_for_same_day_cutoff() -> None:
    window = resolve_daily_draw_window(datetime(2026, 9, 10, 11, 17, tzinfo=UTC))

    assert window.target_date == date(2026, 9, 10)
    assert window.wait_seconds == 18 * 60


def test_runner_waits_at_maximum_same_day_boundary() -> None:
    window = resolve_daily_draw_window(datetime(2026, 9, 10, 11, 15, tzinfo=UTC))

    assert window.target_date == date(2026, 9, 10)
    assert window.wait_seconds == 20 * 60


def test_runner_before_same_day_boundary_uses_previous_completed_draw() -> None:
    window = resolve_daily_draw_window(datetime(2026, 9, 10, 11, 14, 59, tzinfo=UTC))

    assert window.target_date == date(2026, 9, 9)
    assert window.wait_seconds == 0


def test_runner_after_cutoff_processes_same_day_immediately() -> None:
    window = resolve_daily_draw_window(datetime(2026, 9, 10, 11, 40, tzinfo=UTC))

    assert window.target_date == date(2026, 9, 10)
    assert window.wait_seconds == 0


def test_resolver_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match='timezone'):
        resolve_daily_draw_window(datetime(2026, 9, 10, 18, 17))


def test_cli_writes_github_outputs_for_delayed_runner(tmp_path) -> None:
    github_output = tmp_path / 'github-output'

    assert (
        main(
            [
                '--github-output',
                str(github_output),
                '--now',
                '2026-08-27T21:01:14+00:00',
            ]
        )
        == 0
    )
    assert github_output.read_text(encoding='utf-8') == 'target_date=2026-08-27\nwait_seconds=0\n'


def test_daily_workflow_pins_scheduled_target_across_jobs_and_retries() -> None:
    workflow = (PROJECT_ROOT / '.github' / 'workflows' / 'daily-etl.yml').read_text(encoding='utf-8')

    required_fragments = (
        'target_date: ${{ steps.window.outputs.target_date }}',
        'SCHEDULED_TARGET_DATE: ${{ needs.draw-window.outputs.target_date }}',
        'target_date="$SCHEDULED_TARGET_DATE"',
        'if [[ "$GITHUB_RUN_ATTEMPT" == "1" ]]',
        'repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}',
        'command+=(--now "$run_created_at")',
    )
    assert all(fragment in workflow for fragment in required_fragments)
