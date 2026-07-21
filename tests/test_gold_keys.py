from __future__ import annotations

from datetime import date

import pytest

from xsmb_etl.gold_keys import (
    gold_filename,
    gold_object_key,
    gold_release_prefix,
    is_gold_table_key,
    snapshot_manifest_key,
)


def test_builds_versioned_gold_and_snapshot_keys() -> None:
    assert gold_release_prefix('run-1') == 'gold/releases/run-id=run-1/'
    assert gold_object_key('run-1', 'fact-draw-result') == ('gold/releases/run-id=run-1/fact-draw-result.parquet')
    assert snapshot_manifest_key(date(2026, 7, 21), 'run-1') == (
        'gold/snapshots/as-of=2026-07-21/run-id=run-1/manifest.json'
    )


@pytest.mark.parametrize(
    ('key', 'expected'),
    [
        ('gold/latest/fact-draw-result.parquet', 'fact-draw-result.parquet'),
        ('gold/releases/run-id=run-1/fact-draw-result.parquet', 'fact-draw-result.parquet'),
        ('silver/draw-results.parquet', None),
    ],
)
def test_recognizes_legacy_and_versioned_gold_keys(key: str, expected: str | None) -> None:
    assert is_gold_table_key(key) is (expected is not None)
    assert gold_filename(key) == expected


@pytest.mark.parametrize('value', ['', '..', 'nested/value', r'nested\\value'])
def test_rejects_unsafe_key_segments(value: str) -> None:
    with pytest.raises(ValueError):
        gold_release_prefix(value)
