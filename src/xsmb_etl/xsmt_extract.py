"""Reliable extraction and parsing for one XSMT result date."""

from __future__ import annotations

import re
from datetime import date

from xsmb_etl.xsmn_extract import (
    RegionalSourceProfile,
    SouthernExtractedResult,
    SouthernResultExtractor,
    parse_southern_fallback_page,
    parse_southern_result_page,
    reconcile_historical_southern_page,
)
from xsmb_etl.xsmt_models import CentralDailyResult


XSMT_SOURCE_PROFILE = RegionalSourceProfile(
    region_label='XSMT',
    source_slug='xsmt',
    section_id_pattern=re.compile(r'^mt_kqngay_(\d{8})(?:_kq)?$'),
    canonical_date_pattern=re.compile(r'/xsmt-(\d{2})-(\d{2})-(\d{4})\.html'),
)

CentralExtractedResult = SouthernExtractedResult


class CentralResultExtractor(SouthernResultExtractor):
    source_profile = XSMT_SOURCE_PROFILE

    def build_fallback_source_url(self, selected_date: date) -> str:
        base_url = str(self.settings.xsmt_fallback_base_url).rstrip('/')
        return f'{base_url}/xsmt/ngay-{selected_date.day}-{selected_date.month}-{selected_date.year}'


def parse_central_result_page(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> CentralDailyResult:
    return parse_southern_result_page(
        raw_response,
        selected_date=selected_date,
        source_url=source_url,
        profile=XSMT_SOURCE_PROFILE,
    )


def parse_central_fallback_page(
    raw_response: bytes,
    *,
    selected_date: date,
    source_url: str,
) -> CentralDailyResult:
    return parse_southern_fallback_page(
        raw_response,
        selected_date=selected_date,
        source_url=source_url,
        profile=XSMT_SOURCE_PROFILE,
    )


def reconcile_historical_central_page(
    raw_response: bytes,
    *,
    fallback_result: CentralDailyResult,
    selected_date: date,
    source_url: str,
) -> CentralDailyResult:
    return reconcile_historical_southern_page(
        raw_response,
        fallback_result=fallback_result,
        selected_date=selected_date,
        source_url=source_url,
        profile=XSMT_SOURCE_PROFILE,
    )


__all__ = [
    'CentralExtractedResult',
    'CentralResultExtractor',
    'XSMT_SOURCE_PROFILE',
    'parse_central_fallback_page',
    'parse_central_result_page',
    'reconcile_historical_central_page',
]
