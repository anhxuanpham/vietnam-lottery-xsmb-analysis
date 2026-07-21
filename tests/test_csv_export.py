from __future__ import annotations

from datetime import date

import pandas as pd

from xsmb_etl.csv_export import export_latest_gold_csv
from xsmb_etl.repository import DataLakeRepository
from xsmb_etl.storage import LocalObjectStore


def test_exports_csv_from_manifest_verified_parquet_and_publishes_pointer_last(tmp_path) -> None:
    class RecordingStore(LocalObjectStore):
        def __init__(self, root) -> None:
            super().__init__(root)
            self.writes: list[str] = []

        def put_bytes(self, key, data, **kwargs):
            result = super().put_bytes(key, data, **kwargs)
            self.writes.append(key)
            return result

    store = RecordingStore(tmp_path)
    repository = DataLakeRepository(store, gold_cache_control='public, max-age=300')
    tables = {'fact-example': pd.DataFrame([{'draw_date': date(2026, 7, 21), 'value': 7}])}
    objects = repository.write_gold_tables(tables, run_id='run-1', formats=('parquet',))
    repository.publish_snapshot_and_latest(
        run_id='run-1',
        target_date=date(2026, 7, 21),
        gold_objects=objects,
    )

    manifest = export_latest_gold_csv(repository)
    repeated = export_latest_gold_csv(repository)

    assert [reference.key for reference in manifest.objects] == ['exports/csv/run-id=run-1/fact-example.csv']
    assert repeated == manifest
    assert store.get_bytes(manifest.objects[0].key).startswith(b'draw_date,value\n2026-07-21,7')
    assert store.writes[-1] == 'exports/csv/latest.json'
