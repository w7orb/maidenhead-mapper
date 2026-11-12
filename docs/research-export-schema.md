# Research export: data schema

Schema version **1.1**. Written by *Export research dataset* (Processing id `maidenhead:research_export`) in Maidenhead Mapper 0.15.0 and later; 0.12.0–0.14.x wrote 1.0. What changed is in the [changelog](#changelog) at the end. A 1.1 file is a 1.0 file with four more columns at the end and one more `report_kind`.

The export turns WSJT-X `ALL.TXT` files into a table with **one row per observed SNR**: one measurement of one transmitter at one receiver. Rows from many operators can be stacked into one table and shared (for example with HamSCI, or on Zenodo).

## Files

| File | Contents |
|---|---|
| `<name>.csv` | The rows. UTF-8, comma-separated, one header row. An empty cell means unknown or not applicable. |
| `<name>.schema.json` | What the CSV holds: schema version, every field with type, unit and meaning, the program and plugin version, the time span, row counts, the decode floor, the grid precision cap, and whether callsigns are pseudonymised. Since 1.1 also: `muf_model` (the model, and the sunspot numbers used with their row counts; `null` when the MUF was skipped), `es_rule`, `not_decoded_rule` and `changes` (this changelog). The salt is never written. |

There is no Parquet output: QGIS doesn't ship `pyarrow`, and the plugin uses only the Python standard library.

## Kinds of row

| `report_kind` | What was measured | `tx_call` | `rx_call` |
|---|---|---|---|
| `exchange_report` | The report one station sent another in an exchange. `W4XYZ K1ABC -05` means K1ABC hears W4XYZ at −5 dB. | the station heard (W4XYZ) | the station that sent the report (K1ABC) |
| `my_decode` | The observer's own receiver decoded a message. | the message's sender | the observer |
| `not_decoded` (1.1) | A transmission that the receiving station evidently didn't decode (below). No SNR. | the station whose message was missed (T) | the station that missed it (S) |

- A report repeated in the same exchange (an unanswered call sends it again) is **one** row; `n_decoded` counts the repeats. Its time is the first decode.
- Each decode of the observer's is its own row (`n_decoded` = 1), CQs included.
- Reports the observer sent (`Tx` lines in `ALL.TXT`) are `exchange_report` rows with the observer as `rx_call`.
- `exchange_id` links rows of the same exchange: both directions, and the observer's decodes of either side. With it, one exchange gives up to four SNRs: A→B, B→A, A→observer and B→observer.
- `not_decoded` rows have `snr_db` empty, `censored` = 1 and `n_decoded` = 0, and the time, frequency and mode of the missed message.

## Fields

| Field | Type | Unit | Meaning |
|---|---|---|---|
| `schema_version` | string | | `1.1` (`1.0` in files from 0.12.0–0.14.x) |
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
| `muf_basic_mhz` | float | MHz | **1.1.** Basic MUF of the path at `time_utc` from the CCIR (ITU-R P.1239) monthly-median model, by ITU-R P.533: lowest control point, fewest hops. Empty without both grids or a model value (no sunspot number, the option off, same grid). |
| `f_over_muf` | float | | **1.1.** `freq_mhz` / `muf_basic_mhz`, 3 decimals. Over 1: above the basic MUF. |
| `es_candidate` | integer | 0/1 | **1.1.** 1 if the plugin's sporadic-E rule says Es with high confidence, 0 if it says no, empty if it can't tell (below). |
| `foes_min_mhz` | float | MHz | **1.1.** For `es_candidate` = 1: the lowest Es critical frequency that returns `freq_mhz` at `distance_km` (secant law, Es at 105 km). |

Path fields (`grid_precision` to `day_night`, and the 1.1 fields) are empty unless both grids are known.

Changes to `snr_db`, `censored` and `n_decoded` in 1.1: for `not_decoded` rows they are empty, 1 and 0.

## How values are worked out

- **Grids** come from the messages (CQs and grid exchanges, 4 characters) and from *My grid* for the observer. A grid decoded anywhere in the read period applies to all of that station's rows. A finer grid is not replaced by a coarser one. *Grid precision at most* = 4 cuts every grid to 4 characters.
- **Distance, bearing, control points** use the grid centres on a sphere (R = 6371 km). The control-point rule follows ITU-R P.533 (see `hf_ionosphere_research.md`).
- **Solar zenith** uses the same simple solar-position formula as the plugin's CCIR model (declination from the day of year, no equation of time). It is good to a few degrees, which is enough for day, twilight and night.
- **Exchanges** are rebuilt as in the rest of the plugin: directed messages between the same two calls on the same band, until they fall silent for 3 minutes.
- **Time window**: rows from *From* (inclusive) to *To* (exclusive). Reading starts 6 hours before *From*, so exchanges and grids that began earlier are known.

### Basic MUF and f/MUF (1.1)

- The model is the plugin's CCIR (ITU-R P.1239) monthly-median foF2 and M(3000)F2, the same as *Check paths* with *CCIR model only*: the time is rounded down to 15 minutes, and the sunspot number R12 is the one given or looked up (SILSO smoothed, then KC2G's effective sunspot number within a day, then SILSO monthly). The sidecar lists the values used.
- The path MUF is *Check paths*' ITU-R P.533-14 basic MUF: hop count from the layer height at the reflection points, then the lowest MUF over the control points (the midpoint for paths up to one hop of at most 4,000 km, else half a hop in from each end; P.533's long-path method beyond 9,000 km). The paths' control points for this differ from `cp1`/`cp2`, which follow the simpler 2,000 km rule of 1.0.
- **Basic, not operational.** P.533's operational MUF is a bit higher (typically 10–20%), so long paths with `f_over_muf` a little over 1 may still be ordinary F2.
- **Monthly median, not the day.** foF2 on a given day is typically within ~20% of the median. `f_over_muf` is a feature for a model, not a verdict per row.
- **Not KC2G.** KC2G's maps exist only for the present, and exports are historical.
- Values are cached per grid pair and 15-minute model time, and the model per 0.5° cell, so 45,000 rows take about 9 seconds more than without the MUF.

### Sporadic-E flag (1.1)

The same rule as the plugin's *Find sporadic-E paths*, high confidence (and *Check paths*' `mode_guess` = Es):

- bands 12m, 10m, 6m, 4m and 2m;
- `distance_km` from 500 km to one Es hop (about 2,300 km off a 105 km layer at 0° elevation);
- on 6m and up: any such path; on 12m and 10m: only when `f_over_muf` is over 1.

`es_candidate` is 0 outside those bands or distances, or on 12m/10m at or below the MUF; empty without both grids, or on 12m/10m in range without a MUF. 15m and 17m are never candidates under this rule, even above the MUF.

### not_decoded rows (1.1)

WSJT-X sends the same message again until it decodes the answer. A `not_decoded` row is written when, in one exchange:

1. station S sends T a message at one of the first three steps: a call (with or without grid), a report, or a roger report;
2. in the next slot, T sends S the answer to it: the next step (a report, a roger report, or RR73/RRR);
3. in the slot after that, S sends exactly the same message as in step 1.

The row is T's message in step 2: `tx_call` = T, `rx_call` = S, its time, frequency and mode. The observer must have decoded all three messages, or sent T's message itself (then S missed the observer). The slots must be one T/R period apart (FT8 15 s, FT4 7.5 s, ±1.5 s); other modes give no rows. Two decodes of the same sender in one slot count once.

What it shows: T transmitted to S and S did not act on it, almost always because S didn't decode it. What it **doesn't** show:

- that the path was closed or the signal weak: QRM, S's receiver busy or muted, or S's operator holding the message do the same;
- anything when S is the observer: the observer's own repeats never count, since the observer may have decoded the answer and repeated anyway (and the observer's log has no record of what it didn't decode);
- anything about exchanges that simply stop: the other station may have stopped transmitting, gone to another call, or the observer lost it. Those are not rows.

A late decode (finished after S's next transmission started) also looks like a miss; with modern WSJT-X this is rare.

## Pseudonyms

With *Pseudonymise callsigns*, every callsign (`observer`, `tx_call`, `rx_call`) becomes the first 12 hex characters of SHA-256(salt + newline + upper-case callsign). Grids stay. `exchange_id` is then built from the pseudonyms.

- The same salt always gives the same code for a call, so exports can be joined. Use one secret salt for all your exports.
- Without a salt, anyone with a list of callsigns can rebuild the codes. The tool warns.
- Operators who want their files to join each other's must agree on a salt and keep it private.

## Merging exports from many operators

- Stack the CSVs. `observer` says whose log each row came from.
- The same `exchange_report` can appear once per operator who decoded it. To count each report once, keep one row per (`exchange_id`, `tx_call`, `rx_call`, `snr_db`). `exchange_id` is a hash of the two calls, the band and the exchange's first decoded minute, so two operators get the same id only when their first decodes of it agree; matching on calls, band and a few minutes of `time_utc` is safer.
- `my_decode` rows are the observer's own measurements and never duplicate across operators.
- `not_decoded` rows can appear once per operator who decoded the three messages. Keep one per (`tx_call`, `rx_call`, `band`, `time_utc`).
- 1.0 and 1.1 files stack by column name; the 1.1 columns are empty for 1.0 rows.

## Limits

- **Only what was decoded.** Missing decodes are not rows, except the clear case of `not_decoded` (1.1). Other repeats (`n_decoded` over 1) only hint that a reply wasn't heard.
- **Censoring.** FT8 decodes and reports stop near −24 dB; weaker signals are missing, not low. Treat `censored` rows as upper bounds (e.g. Tobit models).
- **Reports are rounded and capped by WSJT-X.** Very strong signals are compressed.
- **4-character grids** put stations up to ~100 km from their true position.
- **Duplicates across files.** Two `ALL.TXT` files from receivers covering the same time count the observer's decodes twice; export them separately, with different observer names if needed.

## Changelog

**1.1** (Maidenhead Mapper 0.15.0)

- New columns, after all 1.0 columns: `muf_basic_mhz`, `f_over_muf`, `es_candidate`, `foes_min_mhz`. Readers that pick 1.0 columns by name or by position keep working.
- New `report_kind` value `not_decoded`, with `snr_db` empty, `censored` = 1 and `n_decoded` = 0.
- Sidecar: `row_counts` adds `not_decoded`, `with_muf` and `es_candidate` (`censored` now includes the `not_decoded` rows); new `muf_model`, `es_rule`, `not_decoded_rule` and `changes`.
- Everything else is unchanged: the 1.0 rows of an export, with every 1.0 column, are the same as 0.14 writes them, apart from `schema_version`.

**1.0** (Maidenhead Mapper 0.12.0): first version.
