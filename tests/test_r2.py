from __future__ import annotations

import hashlib
from io import BytesIO

from botocore.exceptions import ClientError
import pytest

from xsmb_etl.config import Settings
from xsmb_etl.r2 import R2ObjectStore, create_backup_r2_client
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.storage import ObjectPreconditionFailedError


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.last_put: dict | None = None

    def put_object(self, **kwargs):
        self.last_put = kwargs
        key = kwargs['Key']
        if kwargs.get('IfNoneMatch') == '*' and key in self.objects:
            raise ClientError(
                {'Error': {'Code': 'PreconditionFailed'}, 'ResponseMetadata': {'HTTPStatusCode': 412}},
                'PutObject',
            )
        if_match = kwargs.get('IfMatch')
        if if_match is not None:
            current = self.objects.get(key)
            current_etag = current and current['ETag']
            if current_etag != if_match:
                raise ClientError(
                    {'Error': {'Code': 'PreconditionFailed'}, 'ResponseMetadata': {'HTTPStatusCode': 412}},
                    'PutObject',
                )
        etag = f'"{hashlib.md5(kwargs["Body"], usedforsecurity=False).hexdigest()}"'
        kwargs['ETag'] = etag
        self.objects[key] = kwargs
        return {'ETag': etag}

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise _not_found('GetObject')
        return {'Body': BytesIO(self.objects[Key]['Body'])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise _not_found('HeadObject')
        item = self.objects[Key]
        return {
            'ContentLength': len(item['Body']),
            'ContentType': item['ContentType'],
            'CacheControl': item.get('CacheControl'),
            'Metadata': item['Metadata'],
            'ETag': item['ETag'],
        }

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        return {
            'Contents': [{'Key': key} for key in sorted(self.objects) if key.startswith(Prefix)],
            'IsTruncated': False,
        }


def _not_found(operation: str) -> ClientError:
    return ClientError(
        {'Error': {'Code': 'NoSuchKey'}, 'ResponseMetadata': {'HTTPStatusCode': 404}},
        operation,
    )


def test_r2_store_sets_content_metadata_cache_and_sha256() -> None:
    settings = Settings(
        _env_file=None,
        r2_account_id='account',
        r2_access_key_id='access',
        r2_secret_access_key='secret',
        r2_bucket_name='bucket',
    )
    client = FakeS3Client()
    store = R2ObjectStore(settings, client=client)
    data = b'number_2d,frequency\n00,1\n'

    stored = store.put_bytes(
        'gold/latest/fact.csv',
        data,
        content_type='text/csv; charset=utf-8',
        cache_control='public, max-age=300',
        metadata={'run-id': 'run-1'},
        overwrite=False,
    )

    assert client.last_put is not None
    assert client.last_put['Bucket'] == 'bucket'
    assert client.last_put['IfNoneMatch'] == '*'
    assert client.last_put['Metadata'] == {
        'sha256': hashlib.sha256(data).hexdigest(),
        'run-id': 'run-1',
    }
    assert client.last_put['CacheControl'] == 'public, max-age=300'
    assert store.get_bytes(stored.key) == data
    assert store.exists(stored.key)
    assert store.head(stored.key).sha256 == stored.sha256
    assert store.list_keys('gold/') == [stored.key]


def test_r2_store_preserves_quoted_etag_for_compare_and_swap() -> None:
    settings = Settings(
        _env_file=None,
        r2_account_id='account',
        r2_access_key_id='access',
        r2_secret_access_key='secret',
        r2_bucket_name='bucket',
    )
    client = FakeS3Client()
    store = R2ObjectStore(settings, client=client)
    original = store.put_bytes('control/latest.json', b'one', content_type='application/json')

    assert original.etag is not None and original.etag.startswith('"')
    updated = store.put_bytes(
        'control/latest.json',
        b'two',
        content_type='application/json',
        if_match=original.etag,
    )

    assert client.last_put is not None
    assert client.last_put['IfMatch'] == original.etag
    assert updated.etag != original.etag

    with pytest.raises(ObjectPreconditionFailedError):
        store.put_bytes(
            'control/latest.json',
            b'three',
            content_type='application/json',
            if_match=original.etag,
        )


def test_r2_store_selects_an_independent_xsmn_bucket() -> None:
    settings = Settings(
        _env_file=None,
        r2_account_id='account',
        r2_access_key_id='access',
        r2_secret_access_key='secret',
        r2_bucket_name='north',
        r2_xsmn_bucket_name='south',
    )
    client = FakeS3Client()
    store = R2ObjectStore(settings, client=client, region=LotteryRegion.XSMN)

    store.put_bytes('manifests/latest.json', b'{}', content_type='application/json')

    assert store.bucket == 'south'
    assert client.last_put['Bucket'] == 'south'


def test_r2_store_selects_an_independent_xsmt_bucket() -> None:
    settings = Settings(
        _env_file=None,
        r2_account_id='account',
        r2_access_key_id='access',
        r2_secret_access_key='secret',
        r2_bucket_name='north',
        r2_xsmt_bucket_name='central',
    )
    client = FakeS3Client()
    store = R2ObjectStore(settings, client=client, region=LotteryRegion.XSMT)

    store.put_bytes('manifests/latest.json', b'{}', content_type='application/json')

    assert store.bucket == 'central'
    assert client.last_put['Bucket'] == 'central'


def test_backup_client_uses_dedicated_failure_domain_credentials(monkeypatch) -> None:
    captured = {}

    def fake_client(service, **kwargs):
        captured.update({'service': service, **kwargs})
        return object()

    monkeypatch.setattr('xsmb_etl.r2.boto3.client', fake_client)
    settings = Settings(
        _env_file=None,
        r2_account_id='primary-account',
        r2_access_key_id='primary-access',
        r2_secret_access_key='primary-secret',
        r2_backup_account_id='backup-account',
        r2_backup_access_key_id='backup-access',
        r2_backup_secret_access_key='backup-secret',
    )

    create_backup_r2_client(settings)

    assert captured['service'] == 's3'
    assert captured['endpoint_url'] == 'https://backup-account.r2.cloudflarestorage.com'
    assert captured['aws_access_key_id'] == 'backup-access'
    assert captured['aws_secret_access_key'] == 'backup-secret'
