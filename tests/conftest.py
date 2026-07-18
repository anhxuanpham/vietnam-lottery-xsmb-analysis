from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def valid_result_page() -> bytes:
    return (PROJECT_ROOT / 'tests' / 'fixtures' / 'valid-result-page.html').read_bytes()


@pytest.fixture
def grouped_prize_values() -> dict[str, list[str]]:
    return {
        'special': ['96763'],
        'prize1': ['16246'],
        'prize2': ['56517', '64137'],
        'prize3': ['43177', '31665', '74360', '98165', '59063', '00916'],
        'prize4': ['2733', '2653', '2083', '1856'],
        'prize5': ['0452', '6287', '6628', '4037', '3904', '7946'],
        'prize6': ['329', '663', '879'],
        'prize7': ['66', '80', '49', '61'],
    }
