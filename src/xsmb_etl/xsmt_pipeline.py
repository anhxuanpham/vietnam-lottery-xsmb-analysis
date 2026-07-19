"""End-to-end orchestration for the independent XSMT data lake."""

from xsmb_etl.xsmn_pipeline import SouthernPipeline
from xsmb_etl.xsmt_quality import XSMT_DOCUMENTED_PARTIAL_DRAWS


class CentralPipeline(SouthernPipeline):
    """XSMT specialization of the shared station-grain regional pipeline."""

    documented_partial_draws = XSMT_DOCUMENTED_PARTIAL_DRAWS


__all__ = ['CentralPipeline']
