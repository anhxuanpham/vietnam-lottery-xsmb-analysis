from __future__ import annotations

from datetime import date

import pandas as pd

from dtos import Result as CompatibilityResult
from lottery import Lottery as CompatibilityLottery
from xsmb_etl.config import Settings
from xsmb_etl.legacy import Lottery
from xsmb_etl.models import LotteryResult, Result


def test_original_flat_modules_remain_compatible() -> None:
    assert CompatibilityLottery is Lottery
    assert CompatibilityResult is Result


def test_legacy_dataframes_and_dump_keep_existing_local_shapes(
    tmp_path,
    grouped_prize_values: dict[str, list[str]],
) -> None:
    canonical = LotteryResult.from_prize_groups(date(2026, 7, 16), '', grouped_prize_values)
    legacy = Result.model_validate(canonical.to_legacy_dict())
    (tmp_path / 'xsmb.json').write_text(
        f'[{legacy.model_dump_json()}]',
        encoding='utf-8',
    )
    lottery = Lottery(settings=Settings(_env_file=None, local_data_dir=tmp_path))

    lottery.load()
    lottery.dump()

    raw = lottery.get_raw_data()
    two_digits = lottery.get_2_digits_data()
    sparse = lottery.get_sparse_data()
    assert raw.shape == (1, 28)
    assert list(raw.columns[:4]) == ['date', 'special', 'prize1', 'prize2_1']
    assert raw.loc[0, 'prize3_6'] == 916
    assert two_digits.loc[0, 'special'] == 63
    assert sparse.shape == (1, 101)
    assert sparse.iloc[0, 64] == 3

    for stem in ('xsmb', 'xsmb-2-digits', 'xsmb-sparse'):
        assert (tmp_path / f'{stem}.csv').is_file()
        assert (tmp_path / f'{stem}.json').is_file()
        assert (tmp_path / f'{stem}.parquet').is_file()

    dumped_raw = pd.read_csv(tmp_path / 'xsmb.csv')
    assert dumped_raw.loc[0, 'special'] == 96763
