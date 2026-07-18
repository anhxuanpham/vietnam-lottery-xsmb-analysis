"""Export a compact, credential-free dataset for the local analytics dashboard."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'xsmb-2-digits.csv'
OUTPUT = ROOT / 'frontend' / 'public' / 'data' / 'xsmb-demo.json'
RECENT_DRAW_LIMIT = 730


def main() -> None:
    draws: list[dict[str, object]] = []
    full_frequency: Counter[str] = Counter()

    with SOURCE.open(encoding='utf-8', newline='') as source:
        reader = csv.DictReader(source)
        prize_columns = [column for column in reader.fieldnames or [] if column != 'date']
        for row in reader:
            numbers = [f'{int(row[column]):02d}' for column in prize_columns]
            full_frequency.update(numbers)
            draws.append(
                {
                    'date': row['date'],
                    'specialTail': numbers[0],
                    'numbers': numbers,
                }
            )

    if not draws:
        raise ValueError(f'no draws found in {SOURCE}')

    latest = draws[-1]
    output = {
        'region': 'xsmb',
        'source': 'data/xsmb-2-digits.csv',
        'range': {'from': draws[0]['date'], 'to': latest['date']},
        'drawCount': len(draws),
        'resultCount': len(draws) * 27,
        'latest': latest,
        'fullFrequency': {f'{number:02d}': full_frequency[f'{number:02d}'] for number in range(100)},
        'draws': draws[-RECENT_DRAW_LIMIT:],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'exported {len(output["draws"])} recent draws to {OUTPUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
