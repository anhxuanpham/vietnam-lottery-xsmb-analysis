"""Compatibility layer for the original local JSON/CSV/Parquet workflow."""

from __future__ import annotations

from copy import copy
from datetime import date

import numpy as np
import pandas as pd

from xsmb_etl.config import Settings
from xsmb_etl.extract import ExtractionError, ResultExtractor
from xsmb_etl.models import Result, ResultList


class Lottery:
    def __init__(self, settings: Settings | None = None, extractor: ResultExtractor | None = None) -> None:
        self._settings = settings or Settings()
        self._extractor = extractor or ResultExtractor(settings=self._settings)
        self._data: dict[date, Result] = {}
        self._raw_data = pd.DataFrame()
        self._2_digits_data = pd.DataFrame()
        self._sparse_data = pd.DataFrame()
        self._begin_date = date.today()
        self._last_date = date.today()

    def load(self) -> None:
        source_path = self._settings.local_data_dir / 'xsmb.json'
        data = ResultList.model_validate_json(source_path.read_text(encoding='utf-8'))
        for result in data.root:
            self._data[result.date] = result
        self.generate_dataframes()

    def dump(self) -> None:
        self._settings.local_data_dir.mkdir(parents=True, exist_ok=True)

        def _dump(dataframe: pd.DataFrame, file_name: str) -> None:
            output_path = self._settings.local_data_dir / file_name
            dataframe.to_csv(output_path.with_suffix('.csv'), index=False)
            dataframe.to_json(
                output_path.with_suffix('.json'),
                orient='records',
                date_format='iso',
                indent=2,
                index=False,
            )
            dataframe.to_parquet(output_path.with_suffix('.parquet'), index=False)

        _dump(self._raw_data, 'xsmb')
        _dump(self._2_digits_data, 'xsmb-2-digits')
        _dump(self._sparse_data, 'xsmb-sparse')

    def fetch(self, selected_date: date) -> None:
        try:
            extracted = self._extractor.extract(selected_date)
        except ExtractionError:
            return
        result = Result.from_canonical(extracted.result)
        self._data[result.date] = result

    def generate_dataframes(self) -> None:
        self._raw_data = pd.DataFrame([result.model_dump() for result in self._data.values()])
        self._raw_data['date'] = pd.to_datetime(self._raw_data['date'])
        self._raw_data.iloc[:, 1:] = self._raw_data.iloc[:, 1:].astype('int64')

        self._2_digits_data = copy(self._raw_data)
        self._2_digits_data.iloc[:, 1:] = self._2_digits_data.iloc[:, 1:].apply(lambda column: column % 100)

        self._sparse_data = pd.concat(
            [
                self._2_digits_data.iloc[:, 0:1],
                pd.DataFrame(np.zeros((self._2_digits_data.shape[0], 100), dtype=int)),
            ],
            axis=1,
        )
        self._sparse_data.iloc[:, 1:] = self._sparse_data.iloc[:, 1:].astype('int64')
        for row_index in range(self._2_digits_data.shape[0]):
            counts = self._2_digits_data.iloc[row_index, 1:].value_counts()
            for number, frequency in counts.items():
                self._sparse_data.iloc[row_index, number + 1] = int(frequency)

        begin_date = self._raw_data['date'].min()
        self._begin_date = begin_date.to_pydatetime().date()
        last_date = self._raw_data['date'].max()
        self._last_date = last_date.to_pydatetime().date()

    def get_raw_data(self) -> pd.DataFrame:
        return self._raw_data

    def get_2_digits_data(self) -> pd.DataFrame:
        return self._2_digits_data

    def get_sparse_data(self) -> pd.DataFrame:
        return self._sparse_data

    def get_last_date(self) -> date:
        return self._last_date
