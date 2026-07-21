"""Object-store protocol and a filesystem implementation for local runs."""

from __future__ import annotations

import hashlib
import json
import os
import fcntl
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Protocol


class ObjectStoreError(RuntimeError):
    pass


class ObjectAlreadyExistsError(ObjectStoreError):
    pass


class ObjectPreconditionFailedError(ObjectStoreError):
    pass


class ObjectNotFoundError(ObjectStoreError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str
    content_type: str
    cache_control: str | None = None
    etag: str | None = None


class ObjectStore(Protocol):
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
        overwrite: bool = True,
        if_match: str | None = None,
    ) -> StoredObject: ...

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def head(self, key: str) -> StoredObject: ...

    def list_keys(self, prefix: str = '') -> list[str]: ...


class LocalObjectStore:
    """Filesystem-backed object store with atomic writes and metadata sidecars."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.metadata_root = self.root / '.metadata'

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
        overwrite: bool = True,
        if_match: str | None = None,
    ) -> StoredObject:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if if_match is not None and not overwrite:
            raise ValueError('if_match and overwrite=False are mutually exclusive')

        lock_path = self.metadata_root / '.locks' / f'{hashlib.sha256(key.encode()).hexdigest()}.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.exists() and not overwrite:
                raise ObjectAlreadyExistsError(f'object already exists: {key}')
            if if_match is not None:
                if not path.exists() or _etag(path.read_bytes()) != if_match:
                    raise ObjectPreconditionFailedError(f'object precondition failed: {key}')

            digest = hashlib.sha256(data).hexdigest()
            stored = StoredObject(
                key=key,
                size=len(data),
                sha256=digest,
                content_type=content_type,
                cache_control=cache_control,
                etag=_etag(data),
            )
            self._atomic_write(path, data)
            sidecar = {
                **asdict(stored),
                'metadata': {'sha256': digest, **(metadata or {})},
            }
            metadata_path = self._metadata_path_for(key)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(metadata_path, json.dumps(sidecar, indent=2, sort_keys=True).encode('utf-8'))
            return stored

    def get_bytes(self, key: str) -> bytes:
        path = self._path_for(key)
        if not path.is_file():
            raise ObjectNotFoundError(f'object does not exist: {key}')
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def head(self, key: str) -> StoredObject:
        path = self._path_for(key)
        if not path.is_file():
            raise ObjectNotFoundError(f'object does not exist: {key}')
        metadata_path = self._metadata_path_for(key)
        if metadata_path.is_file():
            try:
                values = json.loads(metadata_path.read_text(encoding='utf-8'))
                sha256 = str(values['sha256'])
                content_type = str(values['content_type'])
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ObjectStoreError(f'invalid object metadata: {key}') from exc
            return StoredObject(
                key=key,
                size=path.stat().st_size,
                sha256=sha256,
                content_type=content_type,
                cache_control=values.get('cache_control'),
                etag=str(values.get('etag') or _etag(path.read_bytes())),
            )
        data = self.get_bytes(key)
        return StoredObject(
            key=key,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type_for_key(key),
            etag=_etag(data),
        )

    def list_keys(self, prefix: str = '') -> list[str]:
        if not self.root.exists():
            return []
        keys = []
        for path in self.root.rglob('*'):
            if not path.is_file() or self.metadata_root in path.parents:
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                keys.append(key)
        return sorted(keys)

    def _path_for(self, key: str) -> Path:
        normalized = _normalize_key(key)
        path = (self.root / normalized).resolve()
        if not path.is_relative_to(self.root):
            raise ObjectStoreError(f'object key escapes storage root: {key}')
        return path

    def _metadata_path_for(self, key: str) -> Path:
        normalized = _normalize_key(key)
        return self.metadata_root / f'{normalized}.json'

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)


def content_type_for_key(key: str) -> str:
    suffix = PurePosixPath(key).suffix.lower()
    return {
        '.json': 'application/json; charset=utf-8',
        '.csv': 'text/csv; charset=utf-8',
        '.parquet': 'application/vnd.apache.parquet',
        '.html': 'text/html; charset=utf-8',
    }.get(suffix, 'application/octet-stream')


def _normalize_key(key: str) -> str:
    path = PurePosixPath(key)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ObjectStoreError(f'invalid object key: {key}')
    return path.as_posix()


def _etag(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()
