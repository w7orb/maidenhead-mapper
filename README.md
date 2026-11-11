# Maidenhead Mapper (QGIS plugin)

QGIS plugin for amateur-radio Maidenhead maps.

- **Create Maidenhead grid**: field (2-char, 20°×10°) and square (4-char, 2°×1°) polygon layers with a `locator` attribute. They come pre-styled and labelled; squares appear when zoomed in past 1:20,000,000.
- **Fetch from PSK Reporter**: downloads the last 1–24 hours of reports for a callsign into a folder, then maps the whole folder. It keeps to PSK Reporter's limit of one query per 5 minutes.
- **Import PSK Reporter reports**: a folder of `psk_*.json` pulls (from *Fetch*, or from [pskreporter-analytics](https://github.com/cwtews/pskreporter-analytics)) becomes one marker per remote station and direction: *heard* (I decoded them) or *heard_me* (they decoded me). Each marker has report count, best/median/last SNR, bands, modes, first/last time (UTC), and distance/bearing from my grid. You can filter by direction, band and time. It can be styled by SNR, band or direction, and callsign labels appear when zoomed in.
- **Import WSJT-X ALL.TXT**: replays WSJT-X logs through the live map's logic, giving station markers and exchange lines (including exchanges between other stations) for past sessions.
- **Import ADIF log**: maps your logged QSOs from `wsjtx_log.adi`, JTDX, N1MM, LoTW or any logger's ADIF export (.adi or .adx), merging the same QSO across files.
- **Ionosphere (KC2G)**: fetch current ionosonde data and MUF/foF2/hmF2 maps, and check any station or exchange layer against them. Each path gets its MUF, layer height, hop count and whether F2 can explain it, plus a layer of reflection points.
- **Sporadic-E, skip distance, station balance**: find paths only sporadic-E explains (with the minimum foEs), follow the skip zone hour by hour, and compare stations' signal vs receive from two-way reports.
- **Activity timeline**: a chart of reports per hour for any of the above (PSK Reporter, ALL.TXT, live or saved sessions). Drag across it to map only those hours.
- **WSJT-X live map**: listens to WSJT-X's UDP feed and plots every station it decodes. Markers update every 2 s and fade over a set time since each station was last heard. Stations you log a QSO with become red stars. Works alongside JTAlert/GridTracker via multicast.

The grid always spans exactly -180..180 / -90..90. The earlier hand-built grid took its extent from the OSM basemap (Web Mercator stops at ±85.05°), which shifted every field about 5° south.

Requires QGIS 3.34 or later (tested on 3.44). No extra Python packages. Licensed GPLv3 or later (see `LICENSE`).

## Install for development (Windows)

Link the plugin folder into your QGIS profile (run in `cmd`):

```
mklink /J "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\maidenhead_mapper" "d:\projects\gis\maidenhead-mapper\maidenhead_mapper"
```

Restart QGIS, then enable **Maidenhead Mapper** under *Plugins → Manage and Install Plugins → Installed*. Install the **Plugin Reloader** plugin to pick up code changes without restarting.

## Use

Everything is under *Plugins → Maidenhead Mapper* (and the toolbar button's drop-down), grouped by purpose:

| Section | Tools |
|---|---|
| Grid | Create Maidenhead grid |
| PSK Reporter | Fetch from PSK Reporter, Import PSK Reporter reports |
| WSJT-X | WSJT-X live map (panel), Import WSJT-X ALL.TXT, Import ADIF log, Station balance from two-way reports, Antenna A/B comparison |
| Ionosphere | Fetch ionosphere (KC2G), Check paths against the ionosphere (MUF), Find sporadic-E paths, Skip distance by hour |
| (top level) | Activity timeline (panel) |

The ionosphere tools take any station or exchange layer; they aren't tied to the live map. All tools are also in the *Processing Toolbox → Maidenhead Mapper*.

### Create Maidenhead grid

- **Grid level**: fields, squares, or both.
- **Extent**: leave empty for the whole world (324 fields / 32,400 squares), or pick the map canvas or a layer. Any CRS works; the grid itself is always aligned to whole degrees.
- **Outputs**: temporary layers or files (e.g. GeoPackage). Uncheck an output to skip it.

### Fetch from PSK Reporter

*Plugins → Maidenhead Mapper → Fetch from PSK Reporter…*

- **Callsign**, **Hours back** (1–24; PSK Reporter keeps about a day), optional **Mode** (e.g. `FT8`).
- **Save into folder**: each fetch adds `psk_YYYY_MM_DD-HH_MM_SS.json` (UTC), the same format `pskreporter-pull.py` writes. Fetch every so often into the same folder to build up a history; overlaps are counted once.
- The output layer maps everything in the folder with default settings. For filters, run *Import* on the folder.
- Asking again within 5 minutes is refused with the time left, as PSK Reporter requests.

### Import PSK Reporter reports

*Plugins → Maidenhead Mapper → Import PSK Reporter reports…*

- **Folder**: a folder of `psk_*.json` files. Repeats across overlapping pulls are counted once; empty or corrupt files are listed in the log and skipped.
- **My callsign**: remembered between runs. Portable forms (`W7ORB/P`) count as you.
- **My grid**: for distance/bearing. Leave empty to use the locator most often reported for you.
- **Locator precision**: positions use the station's most recent locator, capped here (default 6 characters).
- **Style**: SNR (best report), band (lowest band heard), direction, or a single symbol. Restyle any time in *Layer Properties*.

### Import WSJT-X ALL.TXT

*Plugins → Maidenhead Mapper → Import WSJT-X ALL.TXT…*

- **ALL.TXT file(s)**: one or more logs (WSJT-X 2.1+ format). Don't mix two receivers' logs from the same time; that counts everything twice.
- **My callsign / grid**: your callsign marks your own QSOs, and ALL.TXT `Tx` lines fill in your side of them. Your grid is used for distance, bearing and hops.
- Outputs two layers: **WSJT-X stations (ALL.TXT)** and **WSJT-X exchanges (ALL.TXT)**, with the same fields and colours as the live map (no fading).

### Import ADIF log

*Plugins → Maidenhead Mapper → WSJT-X → Import ADIF log (.adi / .adx)…*

- **ADIF file(s)**: WSJT-X writes `wsjtx_log.adi` next to `ALL.TXT` (*File → Open log directory*). Any logger's ADIF export or a LoTW download works too. The same QSO in several files (same call, band and mode within 5 minutes) is merged, keeping every confirmation.
- **Only QSOs made as this callsign**: for logs holding several of your calls; empty keeps all.
- **My grid**: used when a QSO has no `MY_GRIDSQUARE`.
- Their location is `GRIDSQUARE`, else the first `VUCC_GRIDS`, else `LAT`/`LON`.
- Outputs **Worked stations (ADIF)**: one marker per callsign with QSO count, bands, modes, best reports both ways and confirmations; and **QSOs (ADIF)**: one line per QSO, coloured by band. The QSO lines have the same fields as the exchanges layer (you are `call_a`; `a_hears_b` is the report you sent, `b_hears_a` the one you received), so *Check paths*, *Station balance*, *Find sporadic-E paths* and *Skip distance* work on them. Reports are numbers for digital modes only; phone/CW RST is kept as text in `rst_sent`/`rst_rcvd`.

### Ionosphere: fetch and check paths

Two sources, chosen per path (*Ionosphere source*; `iono_source` records which was used):

- **KC2G** ([prop.kc2g.com](https://prop.kc2g.com)): current GIRO/INGV ionosonde measurements and worldwide contour maps of MUF(3000), foF2 and hmF2 built from them over the IRI-2020 model. Cached, re-downloaded at most every 15 minutes. Used for paths within 3 hours (adjustable) of its data.
- **CCIR model** (ITU-R P.1239, the F2 maps inside IRI): the *monthly median* foF2 and M(3000) for any date, time and sunspot number, so past logs (your 2022 ALL.TXT, old PSK pulls) can be checked too. It shows typical conditions for that month, not that day's: expect foF2 within ~20%. The coefficients (~0.5 MB) are downloaded once from the MIT-licensed [iri2016](https://github.com/space-physics/iri2016) repository. The modified dip latitude comes from an IGRF grid shipped with the plugin. The sunspot number (R12) is SILSO's monthly smoothed value (WDC-SILSO, Royal Observatory of Belgium, CC BY-NC 4.0), or KC2G's effective sunspot number for recent days, or one you enter. Checked against PyIRI: foF2 within 0.8%.

- **Fetch ionosphere (KC2G)**: ionosondes that reported recently (foF2, hmF2, MUF(3000), confidence) and one contour map (MUF(3000), foF2 or hmF2).
- **Check paths against the ionosphere**: input any station layer (paths from *My grid*) or exchange layer (grid_a to grid_b). For each path it works out:
  - the likely hop count using the layer height at the reflection points (`hops_iono`, `takeoff_iono`);
  - the path's F2 **basic MUF** by ITU-R P.533-14 (`muf_mhz`, with `fof2_mhz`, `hmf2_km`):
    - up to one hop's maximum length: the path midpoint (Table 1a, eq. 3);
    - longer: the lower of the values at control points half a hop from each end (§3.5.1.2);
    - over 9,000 km: the long-path method (§5.3.1, eqs. 29–30).

    This is the *basic* MUF. P.533's operational MUF is higher, by 10–20% or more, so contacts slightly above it are common, especially on long paths;
  - `f2_status`: **F2** (the band is at or below the MUF), **above F2 MUF** (not explained by simple F2 hops: sporadic-E, scatter, long path, or the map is off), or **no data**;
  - `bands_f2`: which of the path's bands F2 supports.
  With *Auto*, recent paths use KC2G and older ones the CCIR model. A band stands for its FT8 frequency; a station on several bands is judged on its highest. Optional output: **reflection points**, with `kind` *hop* (where each hop meets the layer; grey dots) and *control point* (where P.533 judges the MUF, coloured by whether the band fits).
- Near a current, trusted ionosonde (confidence 50 or higher), the maps are corrected towards its measurements; the correction fades out over about 1,000 km. Contour maps can't show peaks above their top level (MUF(3000) stops at 28 MHz), and this fills that in where there's an ionosonde.

### Find sporadic-E paths

Paths of 500–2,300 km (one hop off a ~105 km sporadic-E layer) on 12m, 10m, 6m and up that F2 can't explain:
- **high** confidence: 6m and up, or the band is above the F2 MUF (from *Check paths*, KC2G for recent paths, or the CCIR model for older ones);
- **possible**: 12m/10m without ionosphere data.

Each candidate is a point at the path midpoint with `foes_min_mhz`, the lowest Es critical frequency that returns that band at that range. Candidates within 500 km and 60 minutes of each other form **Es clouds** (count, bands, highest foEs).

### Skip distance by hour

For a PSK Reporter layer, an ALL.TXT import or a saved session: a table per band and UTC hour with the skip edge (`skip_km`, the 10th percentile of sky-wave distances, from 20+ contacts), median and 90th-percentile distance, local contacts, and the foF2 the skip edge implies. For comparison it adds the CCIR monthly-median foF2 at your location (`ccir_fof2_mhz`), and KC2G's within 3 hours of its data. The Activity timeline also shows the skip edge per band for the chosen hours.

### Station balance from two-way reports (WSJT-X)

In an exchange each side reports how it hears the other. Path loss and antennas are the same both ways, so the difference between the two reports is one station's *power + receiver noise* minus the other's. A least-squares fit over all two-way exchanges (one or more exchange layers, per band) gives each station a **balance**: *loud but deaf* (> +3 dB), *good ears* (< −3 dB), *balanced*, or *uncertain* (too few exchanges, or a standard error over 3 dB).

The fit allows for FT8's limits (DK3BA 2024). Reports at or below about −20 dB sit at the decode floor, and reports at or above about +15 dB are compressed, so both are treated as bounds, not exact values (a Tobit-style step). Fading outliers are down-weighted with a Huber loss. `n_censored` and `n_dropped` say how many pairs that affected.

It also compares **what you see with what they see**: `hears_vs_me_db` is how a station heard a signal minus how you heard the same signal (median over exchanges). The optional exchanges output has the per-exchange numbers (`report_diff_db`, `b_vs_me_db`, `a_vs_me_db`). Paths differ, so trust stations with many exchanges in different directions; this needs hours to days of decoding.

### Antenna A/B comparison (WSJT-X)

Two antennas (or receivers) decoding the same band at the same time: the same message in the same slot means the same transmitter and path, so SNR(A) − SNR(B) is the difference between the antennas towards that station (plus a fixed receiver offset). Inputs are two sets of ALL.TXT files, or a live/saved session with two WSJT-X instances (picked by id, or the two busiest).

- **By direction**: 16 wedges around your grid, coloured by the median A − B (green = A stronger, purple = B), labelled with the number of pairs and the decodes *only A* / *only B* got.
- **By station**: one point per station heard.
- **By distance and takeoff**: a table by distance and by estimated takeoff angle.

Takeoff angle is estimated, never measured. By default it uses the F2 layer height at the reflection points *at the time of each decode* (KC2G when the decodes are recent, otherwise the CCIR monthly-median model), the fewest hops off that height, and the ITU-R P.533 basic MUF. Paths of 500–2,300 km on 12m and up that F2 can't support count as sporadic-E (one hop off ~105 km) and get their own *Es* bin. Angles are worked out per station and 15 minutes, so a station's angle follows the layer through the day (`takeoff_min_deg`/`takeoff_max_deg`). The old fixed-300 km estimate stays available as an option and as `takeoff_300_deg`. Expect ±1–2° from layer-height error, and a path can use more hops than the fewest.

Below the decode floor the weaker antenna reports nothing rather than a low SNR, so read `decode_adv` (only A − only B, over all decodes) next to the median. In the 2022 DXCommander/loop recordings the median at 10–15° takeoff is about even, but the loop got 222 decodes the vertical missed against 81 the other way (152 vs 42 with the fixed 300 km layer). The result is a relative pattern; trust bins with ~100 pairs or more.

### Activity timeline

*Plugins → Maidenhead Mapper → Activity timeline* opens a panel under the map. Choose a dataset: a PSK Reporter station layer, an ALL.TXT import, a saved live session, or **WSJT-X live map (running)**.

- Bars show reports per UTC hour. For PSK Reporter: blue = stations I heard, orange = stations that heard me. For WSJT-X data: blue = decodes, orange = decodes addressed to me (they heard me). Hover over a bar for its counts.
- **Drag** across the bars to pick hours. The map updates as you drag: markers are rebuilt from only those reports, so counts, SNR, bands and first/last times describe that window. Attribute tables and labels update too, and the layer's name shows the window (e.g. *PSK Reporter stations · 11-24 08:00–20:00 UTC*).
- Below the chart: stations heard / heard me, stations per band, and the farthest and strongest station in the window. **Double-click** or **All hours** picks everything; **◀ ▶** step the window by its own length.
- For WSJT-X data, both the stations and exchanges layers are rebuilt by replaying the log for the chosen hours.
- With the **running live map**, choosing hours holds live updates (the listener keeps recording). **All hours** returns to live.
- Layers remember their source (folder, files or session file) and the chosen hours, so this works again after reopening the project. New pulls or log entries are read in.
- The timeline replaces the layer's features. It refuses while the layer is being edited. Choosing a layer turns it on; other station layers above it in the list can still cover its markers.

### WSJT-X live map

*Plugins → Maidenhead Mapper → WSJT-X live map* (or the toolbar toggle) opens a panel on the right. Press **Start**.

**One-time setup: multicast (recommended, and required if you also run JTAlert, GridTracker or a logger)**

1. WSJT-X: *File → Settings → Reporting → UDP Server*. Set **UDP Server** to `239.255.0.1`, port `2237`, and tick the network interface(s) under *Outgoing interfaces* (include loopback).
2. JTAlert / GridTracker / your logger: change their WSJT-X UDP setting to the same multicast address and port.
3. In the QGIS panel, keep *Multicast*, `239.255.0.1`, `2237`, and press Start.

With only WSJT-X and QGIS, *Unicast* on `127.0.0.1:2237` (the WSJT-X default) also works. But then no other program can listen.

**What you see**

- One marker per station heard, at the centre of its grid. FT8/FT4 carry 4-character grids, taken from CQs and grid exchanges. Stations seen only in messages without a grid are remembered and placed once a grid turns up; the panel counts them as *waiting*.
- The band comes from WSJT-X's dial frequency. Colour shows best SNR. Opacity fades from 100% to 15% over the *Fade over* time.
- Logging a QSO in WSJT-X adds a red star for that station, which never fades.
- `called_me` counts messages from that station addressed to your call.
- **Exchanges between any two stations** go on a second layer, *WSJT-X exchanges*: one great-circle line per exchange between the two grids. FT8 reports say how the *sender* hears the *addressee*, so a full exchange gives the SNR both ways, and your decodes add how you hear each side:

  ```
  AH6U N5ER EM34     N5ER answers AH6U                 (you hear N5ER -12)
  N5ER AH6U +07      AH6U hears N5ER at +07            (you hear AH6U -13)
  AH6U N5ER R-04     N5ER hears AH6U at -04
  N5ER AH6U RR73     signed off: confirmed
  ```

  Fields: `a_hears_b`, `b_hears_a` (reports exchanged), `me_hears_a`, `me_hears_b` (your decodes), `stage` (started / one report / both reports / confirmed), `paths` (label, e.g. `N5ER→AH6U +07  AH6U→N5ER -04`, read as "signal from → to, arrived at"), `transcript`, `band`, `distance_km`, and what you see compared with what they see:
  - `b_vs_me_db`: how B heard A minus how you heard A (positive = B heard that signal better than you);
  - `a_vs_me_db`: how A heard B minus how you heard B;
  - `report_diff_db`: B's report of A minus A's report of B. Path and antennas cancel, leaving A's (power + receiver noise) minus B's.

  Single exchanges mix receivers with path differences. *Station balance from two-way reports* takes medians per station over many exchanges. Messages between the same pair and band within 3 minutes belong to one exchange.
- Your own QSOs are included (your side comes from WSJT-X's Status and QSO Logged, since your own transmissions aren't decoded); filter on `involves_me = 1` to see only those. Colours: green = confirmed, blue = both reports, dashed purple = one report, dotted grey = started. Lines fade like markers; report labels appear when zoomed in past 1:10M.
- Every station and exchange has `hops` and `takeoff_deg`: the fewest F2 hops (layer at 300 km, takeoff of at least 2°) that can cover the distance, and the takeoff angle that needs. This is geometry only: real paths may use more hops, a different layer height, or sporadic-E.
- **Save session to a GeoPackage**: tick it and choose a folder before Start. Each new session file is `wsjtx_session_YYYYMMDD_HHMMSS.gpkg` with `stations`, `exchanges` and a `log` of every WSJT-X message, written every 2 s. The layers on the map *are* the file's tables, so nothing is lost if QGIS closes, and the timeline can replay any hours from the log later.
- A line is drawn once both stations' grids are known. FT8 only sends grids when calling or answering, so some exchanges wait; the panel shows how many are on the map.
- The layer *WSJT-X live* is temporary. Use right-click → *Make Permanent* to save a session. If you delete it, it is rebuilt on the next update.

**Testing without a radio**: replay a recorded `ALL.TXT` as live packets:

```
python tools/replay_alltxt.py path\to\ALL.TXT --speed 30 --de-call MYCALL --de-grid CN85
```

`--speed 0` sends as fast as possible. `--qso K7ABC:DN17` logs a QSO at the end. `--address 127.0.0.1` sends unicast.

## Layout

```
maidenhead_mapper/
  core/          pure Python, no QGIS imports: locator math, bands, PSK Reporter
                 loading/summaries/queries, hourly counts, WSJT-X UDP protocol,
                 FT8 message parsing, exchanges, live model + replay, ALL.TXT,
                 hop geometry, ionosphere (KC2G maps, MUF per path)
  algorithms/    Processing algorithms and shared styling
  timeline/      activity timeline panel, bar chart, and data sources
  live/          UDP listener, live station/exchange layers, session saving, dock panel
  provider.py    Processing provider (id "maidenhead")
  plugin.py      menu/toolbar wiring
tools/           replay_alltxt.py (fake WSJT-X from a recorded log), package.py (release zip)
tests/           pytest for core/
```

## Tests

```
python -m pytest
```

Runs with any Python 3.8+ that has pytest installed; QGIS is not needed. The plugin itself uses only the Python standard library and QGIS.

## Release

```
python -m pytest
python tools/package.py      # -> dist/maidenhead_mapper-<version>.zip
```

Bump `version` and `changelog` in `maidenhead_mapper/metadata.txt` first. Test the zip with *Plugins → Manage and Install Plugins → Install from ZIP*, then upload it at plugins.qgis.org.
