from __future__ import annotations

import hashlib

import pytest

from xsmb_etl.storage import LocalObjectStore, ObjectAlreadyExistsError, ObjectStoreError


def test_local_store_is_atomic_idempotent_and_records_checksum(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)
    data = b'hello data lake'

    stored = store.put_bytes(
        'gold/latest/example.csv',
        data,
        content_type='text/csv; charset=utf-8',
        cache_control='public, max-age=60',
        overwrite=False,
    )

    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert store.get_bytes(stored.key) == data
    assert store.head(stored.key) == stored
    assert store.list_keys('gold/') == ['gold/latest/example.csv']
    with pytest.raises(ObjectAlreadyExistsError):
        store.put_bytes(stored.key, b'changed', content_type=stored.content_type, overwrite=False)


def test_local_store_rejects_path_traversal(tmp_path) -> None:
    store = LocalObjectStore(tmp_path)

    with pytest.raises(ObjectStoreError):
        store.put_bytes('../secret', b'x', content_type='application/octet-stream')
