"""XSMT transformation API backed by the shared regional station grain."""

from xsmb_etl.xsmn_transform import (
    SOUTHERN_DRAW_RESULT_COLUMNS,
    SOUTHERN_LOTO_DAILY_COLUMNS,
    canonical_southern_results_from_frame,
    southern_draw_results_frame,
    southern_loto_daily_frame,
)


CENTRAL_DRAW_RESULT_COLUMNS = SOUTHERN_DRAW_RESULT_COLUMNS
CENTRAL_LOTO_DAILY_COLUMNS = SOUTHERN_LOTO_DAILY_COLUMNS
canonical_central_results_from_frame = canonical_southern_results_from_frame
central_draw_results_frame = southern_draw_results_frame
central_loto_daily_frame = southern_loto_daily_frame


__all__ = [
    'CENTRAL_DRAW_RESULT_COLUMNS',
    'CENTRAL_LOTO_DAILY_COLUMNS',
    'canonical_central_results_from_frame',
    'central_draw_results_frame',
    'central_loto_daily_frame',
]
