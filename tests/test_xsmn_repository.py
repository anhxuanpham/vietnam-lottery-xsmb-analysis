from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from xsmb_etl.repository import BronzeConflictError, SouthernDataLakeRepository
from xsmb_etl.storage import LocalObjectStore, ObjectAlreadyExistsError
from xsmb_etl.xsmn_extract import SouthernExtractedResult, parse_southern_result_page
from xsmb_etl.xsmn_transform import southern_draw_results_frame, southern_loto_daily_frame


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


class ConcurrentMetadataStore(LocalObjectStore):
    """Simulate another writer winning the conditional PUT race."""

    def __init__(self, root) -> None:
        super().__init__(root)
        self.injected = False

    def put_bytes(self, key, data, **kwargs):
        if key.endswith('/metadata.json') and not kwargs.get('overwrite', True) and not self.injected:
            self.injected = True
            super().put_bytes(key, data, **{**kwargs, 'overwrite': True})
            raise ObjectAlreadyExistsError(f'object already exists: {key}')
        return super().put_bytes(key, data, **kwargs)


def _extracted() -> SouthernExtractedResult:
    raw = FIXTURE.read_bytes()
    result = parse_southern_result_page(
        raw,
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )
    return SouthernExtractedResult(raw_response=raw, result=result)


def test_southern_repository_writes_station_grain_bronze_and_silver(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='public, max-age=300')
    extracted = _extracted()

    bronze = repository.write_bronze(extracted, run_id='run-1')
    draw = southern_draw_results_frame([extracted.result], 'run-1')
    loto = southern_loto_daily_frame(draw)
    draw_objects = repository.upsert_silver_draw_results(draw)
    loto_objects = repository.replace_silver_loto_daily(loto)

    assert len(bronze) == 3
    assert bronze[1].key.endswith('/parsed-results.json')
    assert repository.load_bronze(extracted.result.draw_date) == extracted
    assert draw_objects[0].key == 'silver/draw-results/year=2026/month=07/draw-results.parquet'
    assert loto_objects[0].key == 'silver/loto-daily/year=2026/month=07/loto-daily.parquet'
    assert repository.read_all_silver_draw_results().shape == (54, 15)


def test_southern_repository_preserves_reconciliation_evidence(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    base = _extracted()
    extracted = SouthernExtractedResult(
        raw_response=b'primary historical response',
        result=base.result,
        fallback_response=b'independent fallback response',
        fallback_url='https://fallback.test/ngay/16-07-2026',
    )

    objects = repository.write_bronze(extracted, run_id='run-fallback')
    metadata = json.loads(
        repository.store.get_bytes('bronze/source=xoso/year=2026/month=07/date=2026-07-16/metadata.json')
    )

    assert len(objects) == 4
    assert objects[2].key.endswith('/fallback-response.html')
    assert metadata['fallback_source_url'] == extracted.fallback_url
    assert metadata['fallback_raw_sha256']
    assert metadata['reconciliation'] == 'full_station_prize_comparison'
    assert repository.load_bronze(date(2026, 7, 16)) == extracted
    assert repository.bronze_objects(date(2026, 7, 16)) == objects


def test_southern_repository_rejects_changed_immutable_bronze(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    extracted = _extracted()
    repository.write_bronze(extracted, run_id='run-1')

    with pytest.raises(BronzeConflictError):
        repository.write_bronze(
            SouthernExtractedResult(raw_response=b'changed', result=extracted.result),
            run_id='run-2',
        )


def test_southern_repository_reuses_matching_bronze_after_conditional_put_race(tmp_path) -> None:
    store = ConcurrentMetadataStore(tmp_path)
    repository = SouthernDataLakeRepository(store, gold_cache_control='no-cache')

    objects = repository.write_bronze(_extracted(), run_id='run-1')

    assert len(objects) == 3
    assert store.injected
    assert repository.bronze_complete(date(2026, 7, 16))


def test_southern_repository_reuses_matching_bronze_from_an_interrupted_run(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    extracted = _extracted()
    first = repository.write_bronze(extracted, run_id='run-1')

    resumed = repository.write_bronze(extracted, run_id='run-2')

    assert [item.key for item in resumed] == [item.key for item in first]
    assert repository.load_bronze(extracted.result.draw_date) == extracted


def test_southern_repository_recovers_changed_html_for_equivalent_partial_bronze(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    original = _extracted()
    current_raw = original.raw_response.replace(b'</body>', b'<div>volatile markup</div></body>')
    current_result = parse_southern_result_page(
        current_raw,
        selected_date=original.result.draw_date,
        source_url=original.result.source_url,
    )
    current = SouthernExtractedResult(raw_response=current_raw, result=current_result)
    prefix = repository._bronze_prefix(original.result.draw_date)
    repository.store.put_bytes(
        f'{prefix}/response.html',
        original.raw_response,
        content_type='text/html; charset=utf-8',
        overwrite=False,
    )
    repository.store.put_bytes(
        f'{prefix}/parsed-results.json',
        f'{original.result.model_dump_json(indent=2)}\n'.encode(),
        content_type='application/json; charset=utf-8',
        overwrite=False,
    )

    objects = repository.write_bronze(current, run_id='recovery-run')
    metadata = json.loads(repository.store.get_bytes(f'{prefix}/metadata.json'))

    assert len(objects) == 3
    assert repository.store.get_bytes(f'{prefix}/response.html') == original.raw_response
    assert metadata['raw_sha256'] == hashlib.sha256(original.raw_response).hexdigest()
    assert metadata['recovery_candidate_raw_sha256'] == hashlib.sha256(current_raw).hexdigest()
    assert metadata['partial_recovery'] == 'canonical_result_match'
    assert metadata['fetched_at'] is None
    assert metadata['recovered_at']
    assert repository.bronze_complete(original.result.draw_date)


def test_southern_repository_rejects_partial_bronze_with_different_canonical_result(tmp_path) -> None:
    repository = SouthernDataLakeRepository(LocalObjectStore(tmp_path), gold_cache_control='no-cache')
    extracted = _extracted()
    prefix = repository._bronze_prefix(extracted.result.draw_date)
    changed_raw = extracted.raw_response.replace(b'>07<', b'>08<', 1)
    changed_result = parse_southern_result_page(
        changed_raw,
        selected_date=extracted.result.draw_date,
        source_url=extracted.result.source_url,
    )
    repository.store.put_bytes(
        f'{prefix}/response.html',
        changed_raw,
        content_type='text/html; charset=utf-8',
        overwrite=False,
    )
    repository.store.put_bytes(
        f'{prefix}/parsed-results.json',
        f'{changed_result.model_dump_json(indent=2)}\n'.encode(),
        content_type='application/json; charset=utf-8',
        overwrite=False,
    )

    with pytest.raises(BronzeConflictError):
        repository.write_bronze(extracted, run_id='recovery-run')
