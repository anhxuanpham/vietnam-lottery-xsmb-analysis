# Data dictionary

XSMB and XSMN use the same Gold filenames in different data lakes. All dates use the official Vietnam draw date. Facts carry the publication `run_id`; dimensions are deterministic and versioned by the manifest that lists them.

## Grain and cardinality

| Table | XSMB | XSMN |
|---|---|---|
| `fact-draw-result` | 27 rows/date; key `draw_date, prize_group, prize_order` | 18 rows/station/date; key also includes `station_code` |
| `fact-loto-daily` | 100 rows/date; frequency sum 27 | 100 rows/station/date; frequency sum 18 |
| `fact-special-prize` | 1 row/date | 1 row/station/date |
| `dim-station` | not present | 1 row per observed station |

An XSMN date contains the three or four stations represented by the source page. Station-level facts are never aggregated into the XSMB grain.

## `fact-draw-result`

| Column | Type | Meaning |
|---|---|---|
| `draw_date` | date | Official draw date |
| `station_code` | string | XSMN only: stable code derived from the station link |
| `station_order` | integer | XSMN only: one-based source-page column order |
| `station_name` | string | XSMN only: displayed station/province name |
| `station_url` | string | XSMN only: absolute station source URL |
| `prize_group` | string | XSMB: `special`, `prize1`…`prize7`; XSMN also has `prize8` |
| `prize_order` | integer | One-based order inside the prize group |
| `prize_width` | integer | Official width: XSMB 2–5; XSMN 2–6 |
| `full_number` | integer | Numeric value; may omit leading zeros |
| `formatted_number` | string | Zero-padded official representation |
| `loto_2d` | string | Final two digits, always `00`–`99` |
| `tens_digit` | integer | First digit of `loto_2d` |
| `ones_digit` | integer | Second digit of `loto_2d` |
| `source_url` | string | Daily source page or explicit legacy lineage marker |
| `run_id` | string | Gold dataset version that produced the row |

## `fact-loto-daily`

| Column | Type | Meaning |
|---|---|---|
| `draw_date` | date | Official draw date |
| `station_code` | string | XSMN only: station grain |
| `station_name` | string | XSMN only: display name |
| `number_2d` | string | Every value from `00` through `99` |
| `frequency` | integer | Occurrences in that XSMB draw or XSMN station draw |
| `appeared` | boolean | `frequency > 0` |
| `draws_since_previous` | nullable integer | Draw-position distance to the most recent prior appearance |
| `calendar_days_since_previous` | nullable integer | Calendar-day distance to the most recent prior appearance |
| `previous_appearance_status` | string | `never_seen` or `seen_before` |
| `rolling_7_frequency` | integer | Current draw plus preceding six available draws at the same grain |
| `rolling_30_frequency` | integer | Current draw plus preceding 29 available draws at the same grain |
| `rolling_90_frequency` | integer | Current draw plus preceding 89 available draws at the same grain |
| `run_id` | string | Gold dataset version |

XSMN waiting-time and rolling fields are partitioned by `station_code`; they compare a station only with its own previous scheduled draws. Null is retained until a number has appeared earlier. Zero never means “never appeared.”

## `fact-special-prize`

Business key is `draw_date` for XSMB and `draw_date, station_code` for XSMN.

| Column | Type | Meaning |
|---|---|---|
| `draw_date` | date | Official draw date |
| `station_code` | string | XSMN only |
| `station_name` | string | XSMN only |
| `full_number` | integer | Numeric special-prize value |
| `formatted_number` | string | XSMB five digits; XSMN six digits |
| `tail_2d` | string | Final two digits |
| `first_digit` | integer | First digit of the formatted value |
| `last_digit` | integer | Final digit |
| `digit_sum` | integer | Sum of all formatted digits |
| `is_even_tail` | boolean | Whether the final digit is even |
| `run_id` | string | Gold dataset version |

## `dim-station` (XSMN only)

| Column | Type | Meaning |
|---|---|---|
| `station_code` | string | Station business key |
| `station_name` | string | Latest observed display name |
| `station_url` | string | Latest observed station URL |
| `first_draw_date` | date | Earliest observed station draw in Silver |
| `latest_draw_date` | date | Latest observed station draw in Silver |

## `dim-number`

Exactly 100 deterministic rows: `number_2d`, `numeric_value`, `tens_digit`, `ones_digit`, `digit_sum`, `is_even`, and `is_double`.

## `dim-date`

One row per calendar date between the lake's minimum and maximum dates. Columns are `date`, ISO `day_of_week`, `day_name`, `iso_week`, `month`, `quarter`, `year`, `is_weekend`, and `draw_status`. Status is one of `success`, `no_draw`, `missing`, or `failed`.

## Publication metadata

Each bucket's `manifests/latest.json` contains `region`, the complete current Gold object list, SHA-256 checksums, content types, sizes, `run_id`, and dataset version. Consumers should treat that manifest—not an individual object upload—as the signal that one regional dataset is complete.
