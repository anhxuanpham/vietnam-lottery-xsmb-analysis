from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas.testing as pdt

from xsmb_etl.marts import build_gold_tables
from xsmb_etl.models import LotteryResult
from xsmb_etl.transform import draw_results_frame, loto_daily_frame
from xsmb_etl.xsmn_extract import parse_southern_result_page
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_transform import southern_draw_results_frame, southern_loto_daily_frame


XSMN_FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


def test_gold_tables_reuse_precomputed_loto_without_recomputing(grouped_prize_values: dict[str, list[str]]) -> None:
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    draw = draw_results_frame([result], 'source-run')
    loto = loto_daily_frame(draw, run_id='gold-run')
    loto_before = loto.copy()

    recomputed = build_gold_tables(draw, run_id='gold-run')
    reused = build_gold_tables(draw, run_id='gold-run', loto_daily=loto)

    assert set(recomputed) == set(reused)
    for name in recomputed:
        pdt.assert_frame_equal(recomputed[name], reused[name])
    pdt.assert_frame_equal(loto, loto_before)


def test_gold_tables_restamp_provided_loto_run_id_without_mutation(
    grouped_prize_values: dict[str, list[str]],
) -> None:
    result = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    draw = draw_results_frame([result], 'source-run')
    loto = loto_daily_frame(draw, run_id='stale-run')

    tables = build_gold_tables(draw, run_id='gold-run', loto_daily=loto)

    assert tables['fact-loto-daily']['run_id'].unique().tolist() == ['gold-run']
    assert loto['run_id'].unique().tolist() == ['stale-run']


def test_southern_gold_tables_reuse_precomputed_loto_without_recomputing() -> None:
    result = parse_southern_result_page(
        XSMN_FIXTURE.read_bytes(),
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )
    draw = southern_draw_results_frame([result], 'source-run')
    loto = southern_loto_daily_frame(draw, run_id='gold-run')
    loto_before = loto.copy()

    recomputed = build_southern_gold_tables(draw, run_id='gold-run')
    reused = build_southern_gold_tables(draw, run_id='gold-run', loto_daily=loto)

    assert set(recomputed) == set(reused)
    for name in recomputed:
        pdt.assert_frame_equal(recomputed[name], reused[name])
    pdt.assert_frame_equal(loto, loto_before)
