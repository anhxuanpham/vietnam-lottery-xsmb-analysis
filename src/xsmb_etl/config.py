"""Typed application settings.

R2 values are represented here so configuration can be validated safely, but
Local mode does not require R2 credentials; production mode does.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EtlEnvironment(StrEnum):
    LOCAL = 'local'
    TEST = 'test'
    PRODUCTION = 'production'


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_ignore_empty=True,
        case_sensitive=False,
        extra='ignore',
    )

    etl_env: EtlEnvironment = EtlEnvironment.LOCAL
    source_base_url: HttpUrl = HttpUrl('https://xoso.com.vn')
    xsmn_fallback_base_url: HttpUrl = HttpUrl('https://xskt.com.vn')
    xsmt_fallback_base_url: HttpUrl = HttpUrl('https://xskt.com.vn')
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    http_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    local_data_dir: Path = Path('data')
    local_output_dir: Path = Path('output')
    local_xsmn_output_dir: Path = Path('output-xsmn')
    local_xsmt_output_dir: Path = Path('output-xsmt')

    r2_account_id: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket_name: str = 'xsmb-data-lake'
    r2_xsmn_bucket_name: str = 'xsmn-data-lake'
    r2_xsmt_bucket_name: str = 'xsmt-data-lake'
    r2_endpoint_url: HttpUrl | None = None
    r2_xsmn_account_id: str | None = None
    r2_xsmn_access_key_id: SecretStr | None = None
    r2_xsmn_secret_access_key: SecretStr | None = None
    r2_xsmn_endpoint_url: HttpUrl | None = None
    r2_xsmt_account_id: str | None = None
    r2_xsmt_access_key_id: SecretStr | None = None
    r2_xsmt_secret_access_key: SecretStr | None = None
    r2_xsmt_endpoint_url: HttpUrl | None = None
    r2_region: str = 'auto'
    r2_public_base_url: HttpUrl | None = None
    r2_xsmn_public_base_url: HttpUrl | None = None
    r2_xsmt_public_base_url: HttpUrl | None = None
    r2_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    r2_read_timeout_seconds: float = Field(default=60.0, gt=0)
    r2_max_retries: int = Field(default=5, ge=0)
    gold_cache_control: str = 'public, max-age=300, stale-while-revalidate=60'

    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'

    @property
    def resolved_r2_endpoint_url(self) -> str | None:
        """Return an explicit endpoint or derive one without exposing secrets."""

        if self.r2_endpoint_url is not None:
            return str(self.r2_endpoint_url).rstrip('/')
        if self.r2_account_id:
            return f'https://{self.r2_account_id}.r2.cloudflarestorage.com'
        return None

    @property
    def resolved_xsmn_r2_endpoint_url(self) -> str | None:
        """Return the optional XSMN endpoint, falling back to the XSMB account."""

        if self.r2_xsmn_endpoint_url is not None:
            return str(self.r2_xsmn_endpoint_url).rstrip('/')
        if self.r2_xsmn_account_id:
            return f'https://{self.r2_xsmn_account_id}.r2.cloudflarestorage.com'
        return self.resolved_r2_endpoint_url

    @property
    def resolved_xsmt_r2_endpoint_url(self) -> str | None:
        """Return the optional XSMT endpoint, falling back to the XSMB account."""

        if self.r2_xsmt_endpoint_url is not None:
            return str(self.r2_xsmt_endpoint_url).rstrip('/')
        if self.r2_xsmt_account_id:
            return f'https://{self.r2_xsmt_account_id}.r2.cloudflarestorage.com'
        return self.resolved_r2_endpoint_url

    @model_validator(mode='after')
    def validate_production_r2_credentials(self) -> Settings:
        if self.etl_env is not EtlEnvironment.PRODUCTION:
            return self

        missing = []
        if not self.r2_account_id:
            missing.append('R2_ACCOUNT_ID')
        if self.r2_access_key_id is None or not self.r2_access_key_id.get_secret_value():
            missing.append('R2_ACCESS_KEY_ID')
        if self.r2_secret_access_key is None or not self.r2_secret_access_key.get_secret_value():
            missing.append('R2_SECRET_ACCESS_KEY')
        if not self.r2_bucket_name:
            missing.append('R2_BUCKET_NAME')

        if missing:
            names = ', '.join(missing)
            raise ValueError(f'R2 credentials are required in production; missing: {names}')
        return self
