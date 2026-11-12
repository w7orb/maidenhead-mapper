# Research export: data schema

Schema version **1.0**. Written by *Export research dataset* (Processing id `maidenhead:research_export`) in Maidenhead Mapper 0.12.0 and later.

The export turns WSJT-X `ALL.TXT` files into a table with **one row per observed SNR**: one measurement of one transmitter at one receiver. Rows from many operators can be stacked into one table and shared (for example with HamSCI, or on Zenodo).

## Files

| File | Contents |
|---|---|
| `<name>.csv` | The rows. UTF-8, comma-separated, one header row. An empty cell means unknown or not applicable. |
| `<name>.schema.json` | What the CSV holds: schema version, every field with type, unit and meaning, the program and plugin version, the time span, row counts, the decode floor, the grid precision cap, and whether callsigns are pseudonymised. The salt is never written. |

There is no Parquet output: QGIS doesn't ship `pyarrow`, and the plugin uses only the Python standard library.

## Kinds of row

| `report_kind` | What was measured | `tx_call` | `rx_call` |
|---|---|---|---|
| `exchange_report` | The report one station sent another in an exchange. `W4XYZ K1ABC -05` means K1ABC hears W4XYZ at −5 dB. | the station heard (W4XYZ) | the station that sent the report (K1ABC) |
| `my_decode` | The observer's own receiver decoded a message. | the message's sender | the observer |

- A report repeated in the same exchange (an unanswered call sends it again) is **one** row; `n_decoded` counts the repeats. Its time is the first decode.
- Each decode of the observer's is its own row (`n_decoded` = 1), CQs included.
- Reports the observer sent (`Tx` lines in `ALL.TXT`) are `exchange_report` rows with the observer as `rx_call`.
- `exchange_id` links rows of the same exchange: both directions, and the observer's decodes of either side. With it, one exchange gives up to four SNRs: A→B, B→A, A→observer and B→observer.

## Fields

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `schema_version` | string | | `1.0` |
| `source` | string | | `alltxt`: a WSJT-X ALL.TXT file. |
| `observer` | string | | Callsign of the operator whose log this is (pseudonymised if the export is). |
| `time_utc` | datetime | ISO 8601, UTC | Start of the decoded transmission, e.g. `2026-11-11T21:00:30Z`. |
| `band` | string | | e.g. `20m`. Empty outside the amateur bands. |
| `freq_mhz` | float | MHz | Dial frequency plus the audio offset: the signal's RF frequency. |
| `mode` | string | | `FT8`, `FT4`, … |
| `report_kind` | string | | `exchange_report` or `my_decode` (above). |
| `exchange_id` | string | | 16 hex characters. Empty for decodes outside an exchange (CQs). |
| `tx_call` | string | | Transmitting station. |
| `tx_grid` | string | Maidenhead | Transmitter's grid. Empty if it was never decoded. |
| `rx_call` | string | | Receiving station: the one that measured the SNR. |
| `rx_grid` | string | Maidenhead | Receiver's grid. The observer's grid comes from *My grid*. |
| `snr_db` | integer | dB | SNR in 2500 Hz, as WSJT-X reports it. |
| `censored` | integer | 0/1 | 1 if `snr_db` is at or below the decode floor (default −24 dB). The true SNR may be lower. |
| `n_decoded` | integer | | Times this measurement was decoded. 1 for `my_decode`. |
| `grid_precision` | integer | characters | Characters in the coarser of the two grids (4 or 6). Empty without both grids. |
| `distance_km` | float | km | Great-circle distance between the grid centres. |
| `bearing_deg` | float | degrees | Initial bearing from tx to rx, clockwise from true north. |
| `n_min_hops` | integer | | Fewest F2 hops: layer at 300 km, takeoff angle at least 2°. Empty for distance 0. |
| `cp1_lat`, `cp1_lon` | float | degrees | Control point 1: the path midpoint for paths up to 2000 km, else the point 1000 km from tx. |
| `cp2_lat`, `cp2_lon` | float | degrees | Control point 2, paths over 2000 km only: the point 1000 km from rx. |
| `cp1_sza_deg`, `cp2_sza_deg` | float | degrees | Solar zenith angle at each control point at `time_utc`. |
| `day_night` | string | | At the control points: `day` (zenith below 90°), `twilight` (90–108°), `night` (108° or more: the F2 layer at ~300 km is dark too), or `mixed` (two control points that differ). |

Path fields (`grid_precision` to `day_night`) are empty unless both grids are known.

## How values are worked out

- **Grids** come from the messages (CQs and grid exchanges, 4 characters) and from *My grid* for the observer. A grid decoded anywhere in the read period applies to all of that station's rows. A finer grid is not replaced by a coarser one. *Grid precision at most* = 4 cuts every grid to 4 characters.
- **Distance, bearing, control points** use the grid centres on a sphere (R = 6371 km). The control-point rule follows ITU-R P.533 (see `hf_ionosphere_research.md`).
- **Solar zenith** uses the same simple solar-position formula as the plugin's CCIR model (declination from the day of year, no equation of time). It is good to a few degrees, which is enough for day, twilight and night.
- **Exchanges** are rebuilt as in the rest of the plugin: directed messages between the same two calls on the same band, until they fall silent for 3 minutes.
- **Time window**: rows from *From* (inclusive) to *To* (exclusive). Reading starts 6 hours before *From*, so exchanges and grids that began earlier are known.

## Pseudonyms

With *Pseudonymise callsigns*, every callsign (`observer`, `tx_call`, `rx_call`) becomes the first 12 hex characters of SHA-256(salt + newline + upper-case callsign). Grids stay. `exchange_id` is then built from the pseudonyms.

- The same salt always gives the same code for a call, so exports can be joined. Use one secret salt for all your exports.
- Without a salt, anyone with a list of callsigns can rebuild the codes. The tool warns.
- Operators who want their files to join each other's must agree on a salt and keep it private.

## Merging exports from many operators

- Stack the CSVs. `observer` says whose log each row came from.
- The same `exchange_report` can appear once per operator who decoded it. To count each report once, keep one row per (`exchange_id`, `tx_call`, `rx_call`, `snr_db`). `exchange_id` is a hash of the two calls, the band and the exchange's first decoded minute, so two operators get the same id only when their first decodes of it agree; matching on calls, band and a few minutes of `time_utc` is safer.
- `my_decode` rows are the observer's own measurements and never duplicate across operators.

## Limits

- **Only what was decoded.** Missing decodes are not rows. Repeats (`n_decoded` over 1) hint that a reply wasn't heard.
- **Censoring.** FT8 decodes and reports stop near −24 dB; weaker signals are missing, not low. Treat `censored` rows as upper bounds (e.g. Tobit models).
- **Reports are rounded and capped by WSJT-X.** Very strong signals are compressed.
- **4-character grids** put stations up to ~100 km from their true position.
- **Duplicates across files.** Two `ALL.TXT` files from receivers covering the same time count the observer's decodes twice; export them separately, with different observer names if needed.
