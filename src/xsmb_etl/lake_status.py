"""Lightweight publication health checks for one lottery data lake."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from xsmb_etl.repository import CentralDataLakeRepository, DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import DataObjectReference, LatestManifest, LotteryRegion, RunManifest, RunStatus
from xsmb_etl.storage import ObjectNotFoundError, ObjectStoreError, StoredObject


Repository = DataLakeRepository | SouthernDataLakeRepository | CentralDataLakeRepository


class ObjectMetadataCheck(BaseModel):
    """Comparison between a published object reference and object-store metadata."""

    model_config = ConfigDict(frozen=True)

    key: str
    healthy: bool
    expected_size: int
    actual_size: int | None = None
    expected_sha256: str
    actual_sha256: str | None = None
    issue: str | None = None


class LakeStatus(BaseModel):
    """Health of the current published consumer boundary for a lake."""

    model_config = ConfigDict(frozen=True)

    region: LotteryRegion
    healthy: bool
    run_id: str | None = None
    target_date: str | None = None
    published_at: datetime | None = None
    run_status: RunStatus | None = None
    quality_passed: bool | None = None
    covered_date_count: int = 0
    object_count: int = 0
    verified_object_count: int = 0
    total_size_bytes: int = 0
    snapshot_matches_latest: bool | None = None
    object_checks: tuple[ObjectMetadataCheck, ...] = ()
    issues: tuple[str, ...] = ()


def inspect_lake(repository: Repository) -> LakeStatus:
    """Inspect manifests and HEAD metadata without reading Silver or Gold payloads."""

    issues: list[str] = []
    latest_key = 'manifests/latest.json'
    if not repository.store.exists(latest_key):
        return LakeStatus(
            region=repository.region,
            healthy=False,
            issues=(f'{latest_key} is missing; this lake has no published consumer boundary',),
        )

    try:
        latest = LatestManifest.model_validate_json(repository.store.get_bytes(latest_key))
    except (ValueError, TypeError) as exc:
        return LakeStatus(
            region=repository.region,
            healthy=False,
            issues=(f'{latest_key} is invalid: {_brief_error(exc)}',),
        )

    if latest.region is not repository.region:
        issues.append(f'{latest_key} declares region {latest.region.value}, expected {repository.region.value}')
    if latest.dataset_version != latest.run_id:
        issues.append(f'{latest_key} dataset_version does not match run_id')
    if not latest.objects:
        issues.append(f'{latest_key} does not reference any published Gold objects')
    for reference in latest.objects:
        if not reference.key.startswith('gold/latest/'):
            issues.append(f'{latest_key} references a non-Gold object: {reference.key}')

    run_manifest = _load_run_manifest(repository, latest, issues)
    if run_manifest is not None:
        _check_run_objects(run_manifest, latest, issues)
    snapshot_matches_latest = _check_snapshot(repository, latest, issues)
    object_checks = tuple(_check_object(repository, reference) for reference in latest.objects)
    issues.extend(check.issue for check in object_checks if check.issue)

    duplicate_keys = sorted(
        key
        for key in {reference.key for reference in latest.objects}
        if sum(item.key == key for item in latest.objects) > 1
    )
    if duplicate_keys:
        issues.append(f'latest manifest contains duplicate object keys: {", ".join(duplicate_keys)}')

    return LakeStatus(
        region=repository.region,
        healthy=not issues,
        run_id=latest.run_id,
        target_date=latest.target_date.isoformat(),
        published_at=latest.published_at,
        run_status=run_manifest.status if run_manifest else None,
        quality_passed=run_manifest.quality_passed if run_manifest else None,
        covered_date_count=len(run_manifest.covered_dates) if run_manifest else 0,
        object_count=len(object_checks),
        verified_object_count=sum(check.healthy for check in object_checks),
        total_size_bytes=sum(check.actual_size or 0 for check in object_checks),
        snapshot_matches_latest=snapshot_matches_latest,
        object_checks=object_checks,
        issues=tuple(issues),
    )


def _load_run_manifest(repository: Repository, latest: LatestManifest, issues: list[str]) -> RunManifest | None:
    key = f'manifests/runs/run-id={latest.run_id}.json'
    if not repository.store.exists(key):
        issues.append(f'{key} is missing')
        return None
    try:
        manifest = RunManifest.model_validate_json(repository.store.get_bytes(key))
    except (ValueError, TypeError) as exc:
        issues.append(f'{key} is invalid: {_brief_error(exc)}')
        return None

    if manifest.run_id != latest.run_id:
        issues.append(f'{key} run_id does not match the latest manifest')
    if manifest.region is not repository.region:
        issues.append(f'{key} declares region {manifest.region.value}, expected {repository.region.value}')
    if manifest.target_date != latest.target_date:
        issues.append(f'{key} target_date does not match the latest manifest')
    if manifest.status is not RunStatus.SUCCESS:
        issues.append(f'{key} status is {manifest.status.value}, expected success')
    if not manifest.quality_passed:
        issues.append(f'{key} did not pass quality checks')
    return manifest


def _check_snapshot(repository: Repository, latest: LatestManifest, issues: list[str]) -> bool:
    key = f'gold/snapshots/as-of={latest.target_date.isoformat()}/manifest.json'
    if not repository.store.exists(key):
        issues.append(f'{key} is missing')
        return False
    try:
        snapshot = LatestManifest.model_validate_json(repository.store.get_bytes(key))
    except (ValueError, TypeError) as exc:
        issues.append(f'{key} is invalid: {_brief_error(exc)}')
        return False
    if snapshot != latest:
        issues.append(f'{key} does not match manifests/latest.json')
        return False
    return True


def _check_run_objects(run_manifest: RunManifest, latest: LatestManifest, issues: list[str]) -> None:
    run_objects = tuple(reference for reference in run_manifest.objects if reference.key.startswith('gold/latest/'))
    run_signatures = sorted(_reference_signature(reference) for reference in run_objects)
    latest_signatures = sorted(_reference_signature(reference) for reference in latest.objects)
    if run_signatures == latest_signatures:
        return

    run_keys = {reference.key for reference in run_objects}
    latest_keys = {reference.key for reference in latest.objects}
    missing = sorted(run_keys - latest_keys)
    unexpected = sorted(latest_keys - run_keys)
    changed = sorted(
        key
        for key in run_keys & latest_keys
        if sorted(_reference_signature(item) for item in run_objects if item.key == key)
        != sorted(_reference_signature(item) for item in latest.objects if item.key == key)
    )
    details = []
    if missing:
        details.append(f'missing from latest: {", ".join(missing)}')
    if unexpected:
        details.append(f'unexpected in latest: {", ".join(unexpected)}')
    if changed:
        details.append(f'metadata differs: {", ".join(changed)}')
    if not details:
        details.append('duplicate references differ')
    issues.append(f'latest Gold objects do not match the successful run manifest ({"; ".join(details)})')


def _reference_signature(reference: DataObjectReference) -> tuple[str, int, str, str, str | None]:
    return (
        reference.key,
        reference.size,
        reference.sha256,
        reference.content_type,
        reference.cache_control,
    )


def _check_object(repository: Repository, expected: DataObjectReference) -> ObjectMetadataCheck:
    try:
        actual = repository.store.head(expected.key)
    except ObjectNotFoundError:
        return ObjectMetadataCheck(
            key=expected.key,
            healthy=False,
            expected_size=expected.size,
            expected_sha256=expected.sha256,
            issue=f'{expected.key} is missing',
        )
    except ObjectStoreError as exc:
        return ObjectMetadataCheck(
            key=expected.key,
            healthy=False,
            expected_size=expected.size,
            expected_sha256=expected.sha256,
            issue=f'{expected.key} metadata cannot be read: {_brief_error(exc)}',
        )

    mismatches = _metadata_mismatches(expected, actual)
    return ObjectMetadataCheck(
        key=expected.key,
        healthy=not mismatches,
        expected_size=expected.size,
        actual_size=actual.size,
        expected_sha256=expected.sha256,
        actual_sha256=actual.sha256 or None,
        issue=f'{expected.key}: {", ".join(mismatches)}' if mismatches else None,
    )


def _metadata_mismatches(expected: DataObjectReference, actual: StoredObject) -> list[str]:
    mismatches = []
    if actual.size != expected.size:
        mismatches.append(f'size {actual.size} != {expected.size}')
    if not actual.sha256:
        mismatches.append('SHA-256 metadata is missing')
    elif actual.sha256 != expected.sha256:
        mismatches.append('SHA-256 does not match manifest')
    if actual.content_type != expected.content_type:
        mismatches.append(f'content type {actual.content_type!r} != {expected.content_type!r}')
    if expected.cache_control is not None and actual.cache_control != expected.cache_control:
        mismatches.append(f'cache control {actual.cache_control!r} != {expected.cache_control!r}')
    return mismatches


def _brief_error(error: Exception) -> str:
    return str(error).replace('\n', ' ')[:240]
