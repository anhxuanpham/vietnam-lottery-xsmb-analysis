from __future__ import annotations

import hashlib
from io import BytesIO

from botocore.exceptions import ClientError

from xsmb_etl.config import Settings
from xsmb_etl.r2 import R2ObjectStore
from xsmb_etl.run_models import LotteryRegion


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
        self.objects[key] = kwargs

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
