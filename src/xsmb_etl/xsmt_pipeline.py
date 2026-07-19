"""End-to-end orchestration for the independent XSMT data lake."""

from xsmb_etl.xsmn_pipeline import SouthernPipeline


class CentralPipeline(SouthernPipeline):
    """XSMT specialization of the shared station-grain regional pipeline."""


__all__ = ['CentralPipeline']
