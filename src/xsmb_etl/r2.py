"""Cloudflare R2 object storage through its S3-compatible API."""

from __future__ import annotations

import hashlib
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from xsmb_etl.config import Settings
from xsmb_etl.run_models import LotteryRegion
from xsmb_etl.storage import (
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    StoredObject,
    content_type_for_key,
)


class R2ConfigurationError(RuntimeError):
    pass


def create_r2_client(settings: Settings, region: LotteryRegion = LotteryRegion.XSMB):
    if region is LotteryRegion.XSMN:
        endpoint_url = settings.resolved_xsmn_r2_endpoint_url
        access_key = settings.r2_xsmn_access_key_id or settings.r2_access_key_id
        secret_key = settings.r2_xsmn_secret_access_key or settings.r2_secret_access_key
    else:
        endpoint_url = settings.resolved_r2_endpoint_url
        access_key = settings.r2_access_key_id
        secret_key = settings.r2_secret_access_key

    missing = []
    if not endpoint_url:
        missing.append('R2_ACCOUNT_ID or R2_ENDPOINT_URL')
    if access_key is None:
        missing.append('R2_ACCESS_KEY_ID')
    if secret_key is None:
        missing.append('R2_SECRET_ACCESS_KEY')
    if missing:
        raise R2ConfigurationError(f'missing {region.value.upper()} R2 configuration: {", ".join(missing)}')

    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key.get_secret_value(),
        aws_secret_access_key=secret_key.get_secret_value(),
        region_name=settings.r2_region,
        config=Config(
            signature_version='s3v4',
            connect_timeout=settings.r2_connect_timeout_seconds,
            read_timeout=settings.r2_read_timeout_seconds,
            retries={'max_attempts': settings.r2_max_retries, 'mode': 'standard'},
        ),
    )


class R2ObjectStore:
    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        *,
        region: LotteryRegion = LotteryRegion.XSMB,
    ) -> None:
        self.settings = settings
        self.region = region
        self.bucket = settings.r2_xsmn_bucket_name if region is LotteryRegion.XSMN else settings.r2_bucket_name
        if not self.bucket:
            raise R2ConfigurationError(f'missing {region.value.upper()} R2 bucket name')
        self.client = client or create_r2_client(settings, region)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        cache_control: str | None = None,
        metadata: dict[str, str] | None = None,
        overwrite: bool = True,
    ) -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        request: dict[str, Any] = {
            'Bucket': self.bucket,
            'Key': key,
            'Body': data,
            'ContentType': content_type,
            'Metadata': {'sha256': digest, **(metadata or {})},
        }
        if cache_control:
            request['CacheControl'] = cache_control
        if not overwrite:
            request['IfNoneMatch'] = '*'
        try:
            self.client.put_object(**request)
        except ClientError as exc:
            code = str(exc.response.get('Error', {}).get('Code', ''))
            if code in {'PreconditionFailed', '412'}:
                raise ObjectAlreadyExistsError(f'object already exists: {key}') from exc
            raise
        return StoredObject(
            key=key,
            size=len(data),
            sha256=digest,
            content_type=content_type,
            cache_control=cache_control,
        )

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(f'object does not exist: {key}') from exc
            raise
        return bytes(response['Body'].read())

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def head(self, key: str) -> StoredObject:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(f'object does not exist: {key}') from exc
            raise
        metadata = response.get('Metadata', {})
        return StoredObject(
            key=key,
            size=int(response['ContentLength']),
            sha256=str(metadata.get('sha256', '')),
            content_type=str(response.get('ContentType') or content_type_for_key(key)),
            cache_control=response.get('CacheControl'),
        )

    def list_keys(self, prefix: str = '') -> list[str]:
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {'Bucket': self.bucket, 'Prefix': prefix}
            if continuation_token:
                request['ContinuationToken'] = continuation_token
            response = self.client.list_objects_v2(**request)
            keys.extend(item['Key'] for item in response.get('Contents', []))
            if not response.get('IsTruncated'):
                break
            continuation_token = response.get('NextContinuationToken')
        return sorted(keys)


def _is_not_found(error: ClientError) -> bool:
    code = str(error.response.get('Error', {}).get('Code', ''))
    status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    return code in {'404', 'NoSuchKey', 'NotFound'} or status == 404
