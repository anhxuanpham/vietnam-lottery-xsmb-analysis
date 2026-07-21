"""Canonical object keys for immutable Gold dataset releases."""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath


LEGACY_GOLD_PREFIX = 'gold/latest/'
GOLD_RELEASES_PREFIX = 'gold/releases/'


def gold_release_prefix(run_id: str) -> str:
    """Return the immutable release prefix for one successful run."""

    _require_path_segment(run_id, field='run_id')
    return f'{GOLD_RELEASES_PREFIX}run-id={run_id}/'


def gold_object_key(run_id: str, table_name: str, extension: str = 'parquet') -> str:
    """Build an immutable Gold table key."""

    _require_path_segment(table_name, field='table_name')
    normalized_extension = extension.removeprefix('.')
    _require_path_segment(normalized_extension, field='extension')
    return f'{gold_release_prefix(run_id)}{table_name}.{normalized_extension}'


def snapshot_manifest_key(target_date: date, run_id: str) -> str:
    """Build a collision-free immutable publication snapshot key."""

    _require_path_segment(run_id, field='run_id')
    return f'gold/snapshots/as-of={target_date.isoformat()}/run-id={run_id}/manifest.json'


def legacy_snapshot_manifest_key(target_date: date) -> str:
    return f'gold/snapshots/as-of={target_date.isoformat()}/manifest.json'


def is_gold_table_key(key: str) -> bool:
    """Return whether a key can be a manifest-published Gold table."""

    return key.startswith(LEGACY_GOLD_PREFIX) or key.startswith(GOLD_RELEASES_PREFIX)


def gold_filename(key: str) -> str | None:
    """Return a flat Gold table filename for legacy or release object keys."""

    if not is_gold_table_key(key):
        return None
    filename = PurePosixPath(key).name
    return filename or None


def _require_path_segment(value: str, *, field: str) -> None:
    if not value or value in {'.', '..'} or '/' in value or '\\' in value:
        raise ValueError(f'{field} must be one non-empty object-key segment')
