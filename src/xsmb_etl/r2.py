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
    ObjectPreconditionFailedError,
    StoredObject,
    content_type_for_key,
)


class R2ConfigurationError(RuntimeError):
    pass


def create_r2_client(settings: Settings, region: LotteryRegion = LotteryRegion.XSMB):
    if region is LotteryRegion.XSMT:
        endpoint_url = settings.resolved_xsmt_r2_endpoint_url
        access_key = settings.r2_xsmt_access_key_id or settings.r2_access_key_id
        secret_key = settings.r2_xsmt_secret_access_key or settings.r2_secret_access_key
    elif region is LotteryRegion.XSMN:
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


def create_backup_r2_client(settings: Settings):
    """Create the backup client, allowing a separate account and credential."""

    override_values = (
        settings.r2_backup_account_id,
        settings.r2_backup_endpoint_url,
        settings.r2_backup_access_key_id,
        settings.r2_backup_secret_access_key,
    )
    uses_dedicated_credentials = any(value is not None for value in override_values)
    if not uses_dedicated_credentials:
        return create_r2_client(settings, LotteryRegion.XSMB)

    endpoint_url = settings.resolved_backup_r2_endpoint_url
    access_key = settings.r2_backup_access_key_id
    secret_key = settings.r2_backup_secret_access_key
    missing = []
    if not endpoint_url:
        missing.append('R2_BACKUP_ACCOUNT_ID or R2_BACKUP_ENDPOINT_URL')
    if access_key is None:
        missing.append('R2_BACKUP_ACCESS_KEY_ID')
    if secret_key is None:
        missing.append('R2_BACKUP_SECRET_ACCESS_KEY')
    if missing:
        raise R2ConfigurationError(f'missing backup R2 configuration: {", ".join(missing)}')

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
        bucket_name: str | None = None,
        use_backup_credentials: bool = False,
    ) -> None:
        self.settings = settings
        self.region = region
        buckets = {
            LotteryRegion.XSMB: settings.r2_bucket_name,
            LotteryRegion.XSMN: settings.r2_xsmn_bucket_name,
            LotteryRegion.XSMT: settings.r2_xsmt_bucket_name,
        }
        self.bucket = bucket_name or buckets[region]
        if not self.bucket:
            raise R2ConfigurationError(f'missing {region.value.upper()} R2 bucket name')
        self.client = client or (
            create_backup_r2_client(settings) if use_backup_credentials else create_r2_client(settings, region)
        )

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
        if if_match is not None and not overwrite:
            raise ValueError('if_match and overwrite=False are mutually exclusive')
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
        if if_match is not None:
            request['IfMatch'] = if_match
        try:
            response = self.client.put_object(**request) or {}
        except ClientError as exc:
            code = str(exc.response.get('Error', {}).get('Code', ''))
            if code in {'PreconditionFailed', '412'}:
                if if_match is not None:
                    raise ObjectPreconditionFailedError(f'object precondition failed: {key}') from exc
                raise ObjectAlreadyExistsError(f'object already exists: {key}') from exc
            raise
        return StoredObject(
            key=key,
            size=len(data),
            sha256=digest,
            content_type=content_type,
            cache_control=cache_control,
            etag=_clean_etag(response.get('ETag')),
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
            etag=_clean_etag(response.get('ETag')),
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


def _clean_etag(value: object) -> str | None:
    if value is None:
        return None
    # S3/R2 conditional requests require the quoted HTTP entity-tag exactly as
    # returned by PutObject/HeadObject (for example, ``"abc123"``).
    return str(value)
