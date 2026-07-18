"""Foundation package for independent XSMB and XSMN extraction pipelines."""

from xsmb_etl.config import Settings
from xsmb_etl.extract import ExtractedResult, ResultExtractor
from xsmb_etl.models import LotteryResult, Prize, PrizeGroup
from xsmb_etl.xsmn_extract import SouthernExtractedResult, SouthernResultExtractor
from xsmb_etl.xsmn_models import SouthernDailyResult, SouthernPrize, SouthernPrizeGroup, SouthernStationResult

__all__ = [
    'ExtractedResult',
    'LotteryResult',
    'Prize',
    'PrizeGroup',
    'ResultExtractor',
    'Settings',
    'SouthernDailyResult',
    'SouthernExtractedResult',
    'SouthernPrize',
    'SouthernPrizeGroup',
    'SouthernResultExtractor',
    'SouthernStationResult',
]
