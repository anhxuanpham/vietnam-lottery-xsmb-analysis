from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from xsmb_etl.config import Settings


def test_local_settings_do_not_require_r2_credentials() -> None:
    settings = Settings(_env_file=None)

    assert settings.etl_env == 'local'
    assert str(settings.source_base_url).rstrip('/') == 'https://xoso.com.vn'
    assert str(settings.xsmn_fallback_base_url).rstrip('/') == 'https://xskt.com.vn'
    assert settings.resolved_r2_endpoint_url is None
    assert str(settings.xsmt_fallback_base_url).rstrip('/') == 'https://xskt.com.vn'


def test_env_example_loads_with_blank_optional_r2_values() -> None:
    settings = Settings(_env_file=Path('.env.example'))

    assert settings.etl_env == 'local'
    assert settings.r2_account_id is None
    assert settings.r2_endpoint_url is None
    assert settings.r2_xsmn_bucket_name == 'xsmn-data-lake'
    assert settings.local_xsmn_output_dir == Path('output-xsmn')
    assert settings.r2_xsmt_bucket_name == 'xsmt-data-lake'
    assert settings.local_xsmt_output_dir == Path('output-xsmt')
    assert str(settings.xsmn_fallback_base_url).rstrip('/') == 'https://xskt.com.vn'


def test_r2_endpoint_is_derived_without_exposing_secret() -> None:
    settings = Settings(
        _env_file=None,
        r2_account_id='account-id',
        r2_access_key_id='access-key',
        r2_secret_access_key='super-secret',
    )

    assert settings.resolved_r2_endpoint_url == 'https://account-id.r2.cloudflarestorage.com'
    assert settings.resolved_xsmn_r2_endpoint_url == settings.resolved_r2_endpoint_url
    assert settings.resolved_xsmt_r2_endpoint_url == settings.resolved_r2_endpoint_url
    assert 'super-secret' not in repr(settings)


def test_production_settings_require_r2_credentials() -> None:
    try:
        Settings(
            _env_file=None,
            etl_env='production',
            r2_account_id=None,
            r2_access_key_id=None,
            r2_secret_access_key=None,
        )
    except ValidationError as exc:
        assert 'R2 credentials are required' in str(exc)
    else:
        raise AssertionError('production settings unexpectedly accepted missing R2 credentials')
