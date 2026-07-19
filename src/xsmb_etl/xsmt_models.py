"""Canonical aliases for Central Vietnam lottery station results.

XSMT and XSMN use the same 18-prize station schema.  The aliases keep the
public API region-specific while sharing one validated model implementation.
"""

from xsmb_etl.xsmn_models import (
    SOUTHERN_EXPECTED_RESULT_COUNT,
    SOUTHERN_PRIZE_SPECS,
    SouthernDailyResult,
    SouthernPrize,
    SouthernPrizeGroup,
    SouthernPrizeSpec,
    SouthernStationResult,
)


CENTRAL_EXPECTED_RESULT_COUNT = SOUTHERN_EXPECTED_RESULT_COUNT
CENTRAL_PRIZE_SPECS = SOUTHERN_PRIZE_SPECS
CentralDailyResult = SouthernDailyResult
CentralPrize = SouthernPrize
CentralPrizeGroup = SouthernPrizeGroup
CentralPrizeSpec = SouthernPrizeSpec
CentralStationResult = SouthernStationResult


__all__ = [
    'CENTRAL_EXPECTED_RESULT_COUNT',
    'CENTRAL_PRIZE_SPECS',
    'CentralDailyResult',
    'CentralPrize',
    'CentralPrizeGroup',
    'CentralPrizeSpec',
    'CentralStationResult',
]
