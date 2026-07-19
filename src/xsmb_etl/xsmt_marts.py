"""BI-friendly Gold facts and dimensions for XSMT."""

from xsmb_etl.xsmn_marts import (
    build_southern_gold_tables,
    dim_station_frame,
    southern_special_prize_frame,
)


build_central_gold_tables = build_southern_gold_tables
central_special_prize_frame = southern_special_prize_frame


__all__ = [
    'build_central_gold_tables',
    'central_special_prize_frame',
    'dim_station_frame',
]
