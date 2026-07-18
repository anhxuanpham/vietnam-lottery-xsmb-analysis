from __future__ import annotations

from datetime import date
from pathlib import Path

from xsmb_etl.xsmn_extract import parse_southern_result_page
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_quality import build_southern_quality_report
from xsmb_etl.xsmn_transform import (
    canonical_southern_results_from_frame,
    southern_draw_results_frame,
    southern_loto_daily_frame,
)


FIXTURE = Path(__file__).parent / 'fixtures' / 'valid-xsmn-result-page.html'


def _result():
    return parse_southern_result_page(
        FIXTURE.read_bytes(),
        selected_date=date(2026, 7, 16),
        source_url='https://xoso.com.vn/xsmn-16-07-2026.html',
    )


def test_southern_transforms_create_station_grain_facts() -> None:
    result = _result()
    draw = southern_draw_results_frame([result], 'run-1')
    loto = southern_loto_daily_frame(draw)

    assert draw.shape == (54, 15)
    assert loto.shape == (300, 13)
    assert draw.groupby(['draw_date', 'station_code']).size().eq(18).all()
    assert loto.groupby(['draw_date', 'station_code']).size().eq(100).all()
    assert loto.groupby(['draw_date', 'station_code'])['frequency'].sum().eq(18).all()
    assert (
        draw.loc[draw['station_code'].eq('TN') & draw['prize_group'].eq('special'), 'formatted_number'].item()
        == '005113'
    )


def test_southern_canonical_round_trip_preserves_all_stations() -> None:
    result = _result()
    draw = southern_draw_results_frame([result], 'run-1')

    reconstructed = canonical_southern_results_from_frame(draw)

    assert reconstructed == [result]


def test_southern_gold_and_quality_include_station_dimension() -> None:
    result = _result()
    draw = southern_draw_results_frame([result], 'run-1')
    loto = southern_loto_daily_frame(draw)
    gold = build_southern_gold_tables(draw, run_id='run-1')
    report = build_southern_quality_report(
        [result],
        draw,
        loto,
        run_id='run-1',
        gold_tables=gold,
        today=date(2026, 7, 16),
    )

    assert set(gold) == {
        'fact-draw-result',
        'fact-loto-daily',
        'fact-special-prize',
        'dim-date',
        'dim-number',
        'dim-station',
    }
    assert set(gold['dim-station']['station_code']) == {'TN', 'AG', 'BTH'}
    assert report.passed
