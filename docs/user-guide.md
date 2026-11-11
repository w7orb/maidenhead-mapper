# Maidenhead Mapper user guide

Maidenhead Mapper is a QGIS plugin for amateur radio. It draws the Maidenhead locator grid and maps where your signals go and come from, using PSK Reporter, WSJT-X (live or from logs) and ADIF logs. It also checks paths against the ionosphere.

This guide is the reference: what every tool does, its settings, and what its output fields mean. For a guided first hour, see the [tutorial](tutorial.md).

**Contents**

1. [Before you start](#1-before-you-start)
2. [Install](#2-install)
3. [Where everything is](#3-where-everything-is)
4. [Words used in this guide](#4-words-used-in-this-guide)
5. [Grid: Create Maidenhead grid](#5-grid-create-maidenhead-grid)
6. [PSK Reporter](#6-psk-reporter)
7. [WSJT-X](#7-wsjt-x)
8. [Ionosphere](#8-ionosphere)
9. [Activity timeline](#9-activity-timeline)
10. [Field reference](#10-field-reference)
11. [Data files, caches and settings](#11-data-files-caches-and-settings)
12. [Troubleshooting](#12-troubleshooting)
13. [Credits and licences](#13-credits-and-licences)

---

## 1. Before you start

- **QGIS 3.34 or later.** Tested on QGIS 3.44 on Windows.
- **No extra Python packages.** The plugin uses only QGIS and the Python standard library.
- **Internet**, only for PSK Reporter, KC2G and the one-time downloads of the ionosphere model data. Everything else works offline.
- All times in the plugin are **UTC**.

A few QGIS basics help:

- The **Layers panel** lists your layers. Untick a layer to hide it. Right-click → *Open Attribute Table* shows its data.
- **Processing tools** open a dialog with settings, then a log as they run. The log tells you what was read, skipped and found. Read it when a result looks wrong.
- Most outputs are **temporary layers** (a small icon next to the name). They disappear when you close QGIS unless you right-click → *Make Permanent* or choose a file in the tool's output box.

## 2. Install

### From a zip (most users)

1. Get `maidenhead_mapper-<version>.zip`. To build it from the source code, run `python tools/package.py`; the zip appears in `dist/`.
2. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP*, pick the zip, and click *Install Plugin*.
3. Check that **Maidenhead Mapper** is ticked under *Installed*.

### For development (Windows)

Link the source folder into your QGIS profile so QGIS runs the code you edit. In `cmd`:

```
mklink /J "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\maidenhead_mapper" "<path to the repository>\maidenhead_mapper"
```

Restart QGIS and enable the plugin. The **Plugin Reloader** plugin picks up code changes without a restart.

### Your callsign and grid

The plugin remembers your callsign and grid between tools. The first tool you run that asks for them stores them; later tools fill them in for you. You can always type a different value for one run.

## 3. Where everything is

Everything is under *Plugins → Maidenhead Mapper*, and under the Maidenhead Mapper toolbar button's drop-down. It is grouped by purpose:

| Menu section | Entries |
|---|---|
| Grid | Create Maidenhead grid… |
| PSK Reporter | Fetch from PSK Reporter…, Import PSK Reporter reports… |
| WSJT-X | WSJT-X live map (panel), Import WSJT-X ALL.TXT…, Import ADIF log (.adi / .adx)…, Station balance from two-way reports…, Antenna A/B comparison… |
| Ionosphere | Fetch ionosphere (KC2G)…, Check paths against the ionosphere (MUF)…, Find sporadic-E paths…, Skip distance by hour… |
| (top level) | Activity timeline (panel) |

The ionosphere tools work on any station or exchange layer, from any source. They are not part of the live map.

Every tool is also in the **Processing Toolbox** (*Processing → Toolbox*) under *Maidenhead Mapper*. From there you can run them in batch mode or use them in models.

## 4. Words used in this guide

| Term | Meaning |
|---|---|
| **Field / square / subsquare** | Maidenhead locator levels: 2 characters (20° × 10°, e.g. `CN`), 4 characters (2° × 1°, e.g. `CN85`), 6 characters (e.g. `CN85pm`). FT8 sends 4-character squares. |
| **Station layer** | Points, one per station (and direction), at the centre of its grid. Has a `grid` field. A path runs from *your* grid to the station. |
| **Exchange layer** | Lines, one per exchange or QSO between two stations. Has `grid_a` and `grid_b`. A path runs from A to B. |
| **SNR / report** | Signal-to-noise ratio in dB, as WSJT-X reports it. In FT8 a report says how the **sender** hears the **addressee**. |
| **F2 layer** | The highest, densest layer of the ionosphere, roughly 200–400 km up. It carries most long-distance HF contacts. |
| **foF2** | The F2 layer's critical frequency: the highest frequency it reflects straight back down. Higher foF2 means more bands open. |
| **hmF2** | The height of the F2 layer's peak, in km. It sets how far one hop reaches and at what angle. |
| **MUF** | Maximum usable frequency for a path. Signals above it pass through the layer and are lost. A band works by F2 only if its frequency is at or below the path's MUF. |
| **MUF(3000)** | The MUF for a 3,000 km hop at one place; what ionosonde maps show. |
| **Hop** | One trip up to the layer and back down. Long paths use several hops. |
| **Takeoff angle** | How far above the horizon the signal leaves (and arrives at) the antenna. Long single hops need low angles (a few degrees); more hops or short paths need higher angles. |
| **Sporadic-E (Es)** | Patches of dense ionisation at about 105 km that come and go, mostly in summer. They open 10m and 6m over 500–2,300 km when F2 can't. |
| **Skip zone** | The ring around you too far for ground wave but too close for sky wave. Its outer edge is the *skip distance*. |
| **Ionosonde** | A radar that measures foF2, hmF2 and MUF directly above it. |
| **Sunspot number (R12)** | The 12-month smoothed sunspot number. The CCIR model uses it to scale the ionosphere to the solar cycle. |

## 5. Grid: Create Maidenhead grid

Draws the locator grid as polygon layers with a `locator` label.

**Settings**

| Setting | Default | Notes |
|---|---|---|
| Grid level | Fields and squares | *Fields (2-char)*, *Squares (4-char)* or both. |
| Extent (empty = whole world) | empty | Limit to the map view or a layer's extent. Any CRS works; cells still line up with whole degrees. |
| Maidenhead fields / Maidenhead squares | temporary layers | Untick one to skip it, or save to a file. |

**Output**: 324 fields and 32,400 squares for the whole world. Fields: `locator`, `field`, `level` (2 or 4). The layers come styled and labelled. Squares appear only when zoomed in past 1:20,000,000, and their labels past 1:15,000,000, so the world view stays readable.

The grid always spans exactly −180 to 180 and −90 to 90, whatever basemap or projection you use.

## 6. PSK Reporter

[PSK Reporter](https://pskreporter.info) collects reception reports from receivers worldwide. For your callsign it has two kinds:

- **heard**: stations *your* receiver decoded and reported;
- **heard_me**: stations that decoded *you*.

### 6.1 Fetch from PSK Reporter

Downloads the reports PSK Reporter has for a callsign and saves them to a folder, then maps the whole folder.

| Setting | Default | Notes |
|---|---|---|
| Callsign | your stored callsign | |
| Hours back (max 24) | 24 | PSK Reporter keeps about a day. |
| Mode, e.g. FT8 | empty (all modes) | |
| Save into folder | last folder used | Each fetch adds a file `psk_YYYY_MM_DD-HH_MM_SS.json` (UTC). |
| Style | SNR (best report) | See [6.3](#63-station-styles). |
| PSK Reporter stations (whole folder) | temporary layer | Untick to only download. |

**Limits**

- PSK Reporter asks clients to query **no more than once every 5 minutes**. The tool refuses earlier attempts and tells you how long to wait. A failed attempt counts too.
- There is no history beyond about 24 hours. To build one, fetch into the **same folder** every few hours. Reports repeated across overlapping fetches are counted once.

### 6.2 Import PSK Reporter reports

Maps a folder of `psk_*.json` files with filters. The output is a station layer: one point per remote station **and direction**, at the centre of its most recent locator.

| Setting | Default | Notes |
|---|---|---|
| Folder of psk_*.json files | | Empty or corrupt files are listed in the log and skipped. |
| My callsign | stored callsign | Portable forms (`N0CALL/P`) count as you. |
| My grid (empty = from the reports) | empty | Used for distance and bearing. Empty uses the locator most often reported for you. |
| Direction | Both | *Both*, *Stations I heard*, *Stations that heard me*. |
| Bands (none selected = all) | all | |
| From / To | empty | Optional UTC time limits. |
| Locator precision for positions | 6 (subsquare) | Caps how precisely markers are placed: 2, 4, 6, 8 or 10 characters. |
| Style | SNR (best report) | |

Fields: see [station layers](#101-station-layers). Reports without a usable locator are counted in the log, not silently dropped.

### 6.3 Station styles

Station layers from the PSK Reporter tools can be styled four ways. Restyle any time in *Layer Properties → Symbology*.

- **SNR (best report)**: colour by `snr_best`, from dark purple (≤ −20 dB) to yellow (> 0 dB).
- **Band**: colour by the lowest band heard, using colours close to PSK Reporter's and GridTracker's.
- **Direction**: blue circles = I heard them; orange triangles = they heard me.
- **None**: a single symbol.

Callsign labels appear when zoomed in past 1:3,000,000.

## 7. WSJT-X

### 7.1 WSJT-X live map

*WSJT-X → WSJT-X live map* opens a panel on the right. It listens to the UDP messages WSJT-X sends and plots every station WSJT-X decodes, as it happens.

#### One-time setup

WSJT-X sends UDP messages to one address. **Multicast** lets several programs (QGIS, JTAlert, GridTracker, your logger) all receive them. Use it if you run anything else that listens to WSJT-X.

1. **WSJT-X**: *File → Settings → Reporting*. Set **UDP Server** to `239.255.0.1` and **UDP Server port number** to `2237`. Under *Outgoing interfaces*, tick your network interface(s), including loopback.
2. **JTAlert, GridTracker or your logger**: change their WSJT-X UDP setting to the same address and port.
3. **QGIS panel**: keep *Multicast*, `239.255.0.1`, port `2237`.

With only WSJT-X and QGIS, *Unicast* on `127.0.0.1:2237` (the WSJT-X default) also works, but then no other program can receive WSJT-X's messages.

#### Panel settings

| Setting | Default | Notes |
|---|---|---|
| Mode | Multicast | Switching mode swaps the standard address (`239.255.0.1` ↔ `127.0.0.1`). |
| Address, Port | `239.255.0.1`, `2237` | |
| Fade over | 30 min | Markers fade from full colour to 15% over this time since a station was last heard. Changes apply at once. |
| Save session to a GeoPackage in | off | Tick it and choose a folder **before** pressing Start. See below. |

Press **Start**. The status area shows:

- each WSJT-X instance heard, with its dial frequency, mode, band, callsign and grid;
- decodes in total and per minute;
- stations on the map, and stations *waiting for a grid*;
- exchanges by stage;
- the session file, if saving.

#### What you see

**Stations** (layer *WSJT-X live*):

- One marker per station heard, at the centre of its grid. FT8 and FT4 send 4-character grids, in CQs and when answering. A station seen only in messages without a grid is remembered, and placed once a grid turns up.
- Colour shows the best SNR. Markers fade with time since last heard.
- **Logging a QSO** in WSJT-X adds a red star for that station. Stars never fade.
- `called_me` counts messages from that station addressed to your call.

**Exchanges** (layer *WSJT-X exchanges*): one great-circle line per exchange between **any** two stations you can decode, not just your own QSOs. FT8 reports say how the sender hears the addressee, so a complete exchange gives the SNR both ways. Your own decodes add how *you* hear each side:

```
AH6U N5ER EM34     N5ER answers AH6U                 (you hear N5ER -12)
N5ER AH6U +07      AH6U hears N5ER at +07            (you hear AH6U -13)
AH6U N5ER R-04     N5ER hears AH6U at -04
N5ER AH6U RR73     signed off: confirmed
```

Each exchange has a **stage**, shown by line style:

| Stage | Meaning | Line |
|---|---|---|
| confirmed | both reports and a sign-off (RRR/RR73/73) | solid green |
| both reports | each side has reported the other | solid blue |
| one report | one side has reported | dashed purple |
| started | a call, no report yet | dotted grey |

- Messages between the same pair on the same band within 3 minutes belong to one exchange.
- A line is drawn once both grids are known. Some exchanges wait for a grid.
- The label (shown when zoomed in past 1:10,000,000) reads like `N5ER→AH6U +07  AH6U→N5ER -04`: signal from → to, arrived at that SNR.
- Your own QSOs are included. Your side comes from WSJT-X's status and *QSO Logged* messages, because your own transmissions aren't decoded. Filter on `involves_me = 1` to see only yours.
- Lines fade like markers; your logged QSOs don't.

Every station and exchange also gets `hops` and `takeoff_deg`: the fewest F2 hops (layer at 300 km, takeoff angle at least 2°) that cover the distance, and the angle that needs. This is geometry only. *Check paths* (section [8.2](#82-check-paths-against-the-ionosphere)) uses the real layer height.

#### Saving sessions

With *Save session to a GeoPackage* ticked, Start creates `wsjtx_session_YYYYMMDD_HHMMSS.gpkg` (UTC) in the chosen folder. It holds:

- `stations`: the station markers;
- `exchanges`: the exchange lines;
- `log`: every WSJT-X message the map used.

It is written every 2 seconds. The layers on the map **are** the file's tables, so nothing is lost if QGIS closes. Stopping and starting again with the same folder keeps writing the same file; choosing a different folder starts a new one.

- **Choose a permanent folder**, such as `Documents\WSJT-X sessions`. Don't use a temporary folder: Windows or a cleaner may empty it.
- The `log` table is what lets the [Activity timeline](#9-activity-timeline), *Skip distance* and *Antenna A/B comparison* replay any hours later.
- To reopen a session later, drag its `stations` and `exchanges` tables from the `.gpkg` into any project (or use *Layer → Add Layer → Add Vector Layer*). The plugin recognises a session file from its tables, so it doesn't matter which project it was recorded in.

Without saving, the layers are temporary. Right-click → *Make Permanent* keeps the current state but not the log. If you delete a live layer while it runs, it is rebuilt on the next update.

#### Testing without a radio

`tools/replay_alltxt.py` (in the source code) plays a recorded `ALL.TXT` as if WSJT-X were running:

```
python tools/replay_alltxt.py path\to\ALL.TXT --speed 30 --de-call N0CALL --de-grid FN31
```

- `--speed 0` sends as fast as possible.
- `--address 127.0.0.1` sends unicast.
- `--qso K1ABC:FN42` logs a QSO at the end.
- `--limit N` stops after N decodes.

### 7.2 Import WSJT-X ALL.TXT

Maps past sessions from WSJT-X's `ALL.TXT` (WSJT-X 2.1 or later; find it with *File → Open log directory* in WSJT-X). It runs the log through the same logic as the live map, so you get the same two layers.

| Setting | Default | Notes |
|---|---|---|
| ALL.TXT file(s) | | One or more files. Don't mix logs from two receivers covering the same time: everything would count twice. |
| My callsign (for my own QSOs) | stored callsign | Marks your QSOs. `Tx` lines in the log fill in your side. |
| My grid (for distance, bearing and hops) | stored grid | |
| From / To | empty | Optional UTC limits. |

**Outputs**: *WSJT-X stations (ALL.TXT)* and *WSJT-X exchanges (ALL.TXT)*, with the same fields and colours as the live map, without fading. The Activity timeline can rebuild both for any hours.

ALL.TXT has no record of which QSOs you actually logged. Use the ADIF import for that.

### 7.3 Import ADIF log (.adi / .adx)

Maps your logged QSOs from ADIF files: WSJT-X's `wsjtx_log.adi` (next to `ALL.TXT`), JTDX, MSHV, N1MM, Log4OM, QRZ, or a LoTW download. Both `.adi` and the XML `.adx` format work.

| Setting | Default | Notes |
|---|---|---|
| ADIF file(s) | | Several files are combined. |
| Only QSOs made as this callsign (optional) | stored callsign | For logs holding several of your calls; `/P` etc. count as the same call. Empty keeps everything. |
| My grid, when the log has no MY_GRIDSQUARE | stored grid | |
| From / To | empty | Optional UTC limits on the QSO start time. |

**How it reads the log**

- **Their location**: `GRIDSQUARE` (with `GRIDSQUARE_EXT`), else the first of `VUCC_GRIDS`, else `LAT`/`LON`. QSOs without any are counted in the log.
- **Your location**: each QSO's `MY_GRIDSQUARE`, else *My grid*. If you operated from several grids, lines start at each QSO's own grid; station distances use your most common grid.
- **Mode**: `SUBMODE` if present (FT4 is logged as `MODE=MFSK`, `SUBMODE=FT4`), else `MODE`.
- **Reports**: `RST_SENT` is how you heard them; `RST_RCVD` is how they heard you. For digital modes (FT8, FT4, JT65, JT9, Q65, MSK144, FST4, JS8, WSPR…) these are SNR in dB and become numbers. Phone and CW RST like `59` stay as text only.
- **Duplicates**: the same QSO in several files (same call and band, same mode when both have one, start times within 5 minutes) is merged. Blank fields are filled from the other copy, a longer grid wins, and confirmations from both are kept. Importing your log together with a LoTW download therefore marks confirmed QSOs.
- **Confirmed** means `QSL_RCVD`, `LOTW_QSL_RCVD` or `EQSL_QSL_RCVD` is `Y` or `V`.

**Outputs**

- **Worked stations (ADIF)**: one point per callsign, coloured by band. See [fields](#103-adif-layers).
- **QSOs (ADIF)**: one great-circle line per QSO, coloured by band. These lines have the **same fields as an exchange layer**, plus a few ADIF ones. You are `call_a`, so `a_hears_b` is the report you sent and `b_hears_a` the one you received. That makes the layer usable by *Check paths*, *Station balance*, *Find sporadic-E paths* and *Skip distance by hour*.

ADIF layers don't appear in the Activity timeline: a log spanning years doesn't suit an hourly chart. Use the layer's filter (right-click → *Filter…*) on `first_utc` to look at a period.

### 7.4 Station balance from two-way reports

In an exchange, each side reports how it hears the other. The path and both antennas are the same in both directions, so the **difference between the two reports** is one station's (transmit power + receiver noise) minus the other's.

A fit over many exchanges gives each station a **balance**:

| Verdict | Balance | Meaning |
|---|---|---|
| loud but deaf | above +3 dB | Strong signal and/or a noisy receiver: others hear it better than it hears them. |
| balanced | −3 to +3 dB | |
| good ears | below −3 dB | Weak signal and/or a quiet receiver. |
| uncertain | | Too few usable exchanges, or a standard error over 3 dB. |

Balances are relative: each connected group of stations is centred on 0.

It also compares **what you hear with what they hear**. When you decode a signal and the station it was sent to reports it, `hears_vs_me_db` is how that station heard it minus how you did (median). Positive means they hear those signals better than you.

| Setting | Default | Notes |
|---|---|---|
| Exchange layer(s) | | Live, saved-session, ALL.TXT or ADIF QSO layers. Several can be combined. |
| My callsign (marks my own row) | stored callsign | |
| Separately per band | on | Noise floors differ by band. |
| Exchanges needed for a verdict | 3 | |
| Station balance | temporary layer | Points coloured by verdict. |
| Exchanges with contrasts | off | Optional copy of the exchanges with `report_diff_db`, `b_vs_me_db`, `a_vs_me_db`. |

**Limits**

- FT8 reports are noisy. Single reports fade by up to 30 dB and the two reports come from different 15-second slots. The fit is robust (one wild exchange barely moves it), but it needs **lots of data**: averaging about 100 exchanges per station is recommended (DK3BA 2024). An hour of decoding gives a few per station.
- Reports at or below −20 dB are at the decode floor, and at or above +15 dB WSJT-X compresses them. Such reports are used only as limits, not exact values; `n_censored` and `n_dropped` count them.
- For `hears_vs_me_db`, paths differ between you and the other station. Trust stations with many exchanges in different directions.

### 7.5 Antenna A/B comparison

Compares two antennas (or receivers) that decode the same band at the same time. When both decode the same message in the same 15-second slot, the transmitter, its antenna and the path are identical, so:

> SNR(A) − SNR(B) = difference between the antennas towards that station + a fixed receiver offset

Binned by direction, distance and takeoff angle, this shows the **relative** pattern of the two antennas.

#### Setting up two receivers

Either:

- **Two WSJT-X instances** on the same band at the same time, each on its own antenna and radio (or receiver). Start the second with `wsjtx --rig-name=B` so each keeps its own settings and its own `ALL.TXT`. Or:
- Point both at the live map (both on the same multicast address) with session saving on. The session then holds both instances' decodes, told apart by their WSJT-X id (for example `WSJT-X` and `WSJT-X - B`). The tool's log lists the ids it found.

Keep both running for as long as you can. A few hours gives a direction pattern; elevation needs days covering day and night.

#### Settings

| Setting | Default | Notes |
|---|---|---|
| Receiver A: ALL.TXT file(s) | | Same period as B. |
| Receiver B: ALL.TXT file(s) | | |
| …or a live / saved session layer | | Instead of files: a session layer with two WSJT-X instances. |
| Session: WSJT-X id for A / B | busiest / second busiest | The log lists the instances found. |
| My grid (where both antennas are) | stored grid | |
| Takeoff angle from | Ionosphere: KC2G when recent, else the CCIR model | Or *CCIR model only*, or *Fixed 300 km layer (geometric)*. |
| Sunspot number R12 for the model | 0 (look it up) | |
| Wedge radius on the map (km) | 2500 | Only changes the drawing. |

#### Outputs

- **Antenna A vs B by direction**: 16 wedges around your grid. Green means A is stronger, purple means B is. Labels show the median, the number of pairs, and `+only A/only B`.
- **Antenna A vs B by station**: one point per station heard.
- **Antenna A/B by distance and takeoff**: a table with one row for everything, then rows by distance band and by takeoff-angle band (0–3°, 3–6°, 6–10°, 10–15°, 15–25°, 25–90°, and *Es*).

**Read `decode_adv` next to the median.** Below the decode floor (about −20 dB) the weaker antenna doesn't report a low SNR; it reports nothing. A median over matched pairs alone hides that. `only_a`, `only_b` and `decode_adv` show it.

#### How the takeoff angle is estimated

The takeoff angle is **estimated, never measured**. FT8 carries no arrival-angle information. With the default setting, for each station and each 15 minutes:

1. Take the F2 layer height (hmF2) at the reflection points at that time: KC2G if the decodes are recent (within 3 hours), else the CCIR monthly-median model.
2. Use the fewest hops off that height that cover the distance.
3. Check the ITU-R P.533 basic MUF. Paths of 500–2,300 km on 12m and up that F2 can't support are taken as sporadic-E (one hop off ~105 km) and go in the *Es* bin instead.

Because the layer rises and falls through the day, the same station is heard at different angles at different times. `takeoff_min_deg` and `takeoff_max_deg` show the range. `takeoff_300_deg` keeps the simple fixed-300 km estimate for comparison.

**Limits**

- Expect about ±1–2° from layer-height error.
- A path may use more hops than the fewest, at a much higher angle. Around 3,300 km the estimate can jump between one hop (~2°) and two hops (~14°).
- The result is a **relative** pattern (A versus B), not absolute gain.
- Fading makes single pairs noisy (about 5 dB scatter). Trust bins with about 100 pairs or more.

## 8. Ionosphere

Two sources of ionosphere data are available:

- **KC2G** ([prop.kc2g.com](https://prop.kc2g.com)): current ionosonde measurements (from GIRO and INGV) and worldwide maps of MUF(3000), foF2 and hmF2 built from them over the IRI-2020 model. Updated about every 15 minutes. **"Now" only**: there is no history.
- **CCIR model** (ITU-R P.1239, the F2 maps inside the IRI model): the **monthly median** ionosphere for any date, time and sunspot number. It lets you check old logs. It describes typical conditions for that month, not what happened that day: expect foF2 within about 20%.

### 8.1 Fetch ionosphere (KC2G)

Downloads KC2G's current data and shows it.

| Setting | Default | Notes |
|---|---|---|
| Ionosondes reported within (hours) | 3 | Older stations are left out. |
| Contour map | MUF(3000)F2, MHz | Or *foF2, MHz* or *hmF2, km*. |
| Ionosondes, Ionosphere contours | temporary layers | |

**Outputs**: *Ionosondes HH:MM UTC* (points labelled with the station code) and a contour layer named after the map and time. See [fields](#104-ionosphere-layers).

The data is cached and downloaded again at most every 15 minutes. KC2G is a volunteer service: please don't automate frequent requests.

### 8.2 Check paths against the ionosphere

For each path in a station or exchange layer, works out whether the F2 layer could have carried it.

| Setting | Default | Notes |
|---|---|---|
| Station or exchange layer | | Any layer with `grid` (station) or `grid_a`/`grid_b` (exchange). |
| My grid (station layers) | from the layer, else stored | Station-layer paths start here. |
| Only paths within this many hours of the ionosphere data | 3 | For KC2G. |
| Download fresh data if the cache is over 15 minutes old | on | |
| Ionosphere source | Auto | *Auto: KC2G when the path is recent, else the CCIR model*; *KC2G only*; *CCIR model only*. |
| Sunspot number R12 for the model (0 = look it up) | 0 | |
| Paths with ionosphere | temporary layer | A copy of the input with new fields. |
| Reflection points | temporary layer | Optional. |

**New fields** on each path:

| Field | Meaning |
|---|---|
| `f2_status` | **F2**: the band is at or below the path's MUF. **above F2 MUF**: simple F2 hops don't explain it (sporadic-E, scatter, an unusual path, or the map is off). **no data**: no ionosphere for that time, or no grid or band. |
| `muf_mhz` | The path's F2 **basic MUF** by ITU-R P.533-14. |
| `fof2_mhz`, `hmf2_km` | foF2 and layer height where the MUF was judged. |
| `hops_iono`, `takeoff_iono` | Likely hop count and takeoff angle at the measured layer height. |
| `bands_f2` | Which of the path's bands F2 supports. |
| `freq_mhz` | The frequency judged: the band's FT8 frequency; a station on several bands is judged on its highest. |
| `iono_source`, `iono_utc`, `iono_note` | Which data was used, its time, and why there was none. |

**How the MUF is worked out** (ITU-R P.533-14):

- Paths up to one hop's maximum length: at the path midpoint.
- Longer paths: the lower of the values at two *control points*, half a hop in from each end.
- Paths over 9,000 km: P.533's long-path method.

This is the **basic** MUF. P.533's *operational* MUF is typically 10–20% higher, so contacts slightly "above F2 MUF" are common, especially on long paths. Treat "above" as "worth a look", not proof of sporadic-E.

Near a current, trusted ionosonde (confidence 50 or more), the KC2G maps are nudged towards its measurements; the correction fades out over about 1,000 km. This also fills in peaks above the top contour (the MUF(3000) map stops at 28 MHz).

**Reflection points layer**: `kind` = *hop* (grey dots, where each hop meets the layer) or *control point* (diamonds where P.533 judges the MUF, green if the band fits, red if not). `path_fid` links each point to its path.

The output paths are coloured by `f2_status`: green F2, red above, grey no data.

### 8.3 Find sporadic-E paths

Finds paths sporadic-E most likely carried: one hop of 500–2,300 km on 12m, 10m, 6m or higher that the F2 layer can't explain.

| Setting | Default | Notes |
|---|---|---|
| Station or exchange layer | | |
| My grid (station layers) | from the layer, else stored | |
| Bands | 12m, 10m, 6m, 4m, 2m | |
| Check 12m/10m paths against the ionosphere | on | KC2G if recent, else the CCIR model. |
| Use KC2G for paths within this many hours of its data | 3 | |
| Cloud: midpoints within (km) | 500 | |
| Cloud: and within (minutes) | 60 | |

**Confidence**

- **high**: 6m and up (F2 rarely supports such short paths there), or the band is above the F2 MUF.
- **possible**: 12m or 10m without ionosphere data.

If the input has been through *Check paths*, its `f2_status` is used. With the CCIR model, "above F2 MUF" means above the typical MUF for that month, so treat 10m results as likely rather than certain.

**Outputs**

- **Sporadic-E candidates**: a point at each path's midpoint (where it met the Es layer). `foes_min_mhz` is the lowest Es critical frequency that returns that band at that distance; `takeoff_es` is the takeoff angle off a 105 km layer.
- **Sporadic-E clouds**: candidates within the cloud distance and time of each other, grouped. Each has its count, bands and highest `foes_max_mhz`.

### 8.4 Skip distance by hour

A table, per band and UTC hour, of how far contacts reached. It shows the skip zone growing and shrinking through the day.

**Input**: any of these:

- a layer from *Fetch/Import PSK Reporter*, *Import ALL.TXT*, a saved live session (also one loaded straight from its `.gpkg`) or the running live map. The tool reads the raw data behind the layer: every report or decode counts.
- any other layer with grids: ADIF QSO lines or worked stations, or an exchange layer. Each feature counts as one contact per band, at its `last_utc` (or `first_utc`) time. Line layers use the distance between their two grids. A station layer has one row per station, so its hours count stations, not every contact.

Otherwise distances are from your grid.

| Field | Meaning |
|---|---|
| `band`, `hour_utc` | |
| `n_sky`, `n_local` | Contacts over 300 km (sky wave) and under 300 km (ground wave or local). |
| `skip_km` | The skip edge: the 10th percentile of sky-wave distances. Needs 20 or more contacts in the hour. |
| `median_km`, `p90_km` | Median and 90th-percentile distance. |
| `fof2_est_mhz` | The foF2 the skip edge implies (secant law, F2 at 300 km), only when the edge is within one hop. An indicator for comparing hours, not a measurement. |
| `kc2g_fof2_mhz` | KC2G's foF2 at your location, when its cached data is within 3 hours. |
| `ccir_fof2_mhz` | The CCIR monthly-median foF2 at your location for that hour. |

Plot the table with the DataPlotly plugin, or look at it in the Activity timeline, which shows the skip edge per band for the chosen hours.

## 9. Activity timeline

The top-level **Activity timeline** entry opens a panel under the map: a bar chart of activity per UTC hour that also filters the map.

1. Choose a dataset at the top. The list holds:
   - PSK Reporter station layers;
   - ALL.TXT imports;
   - saved live sessions, including a session `.gpkg` loaded into a new project;
   - **WSJT-X live map (running)**.
2. Bars show reports per hour:
   - PSK Reporter: blue = stations I heard, orange = stations that heard me.
   - WSJT-X: blue = decodes, orange = decodes addressed to me.

   Hover for counts.
3. **Drag** across the bars to choose hours. The map updates as you drag. The layers are rebuilt from only those reports, so counts, SNR, bands and first/last times all describe that window. The layer names show the window, e.g. *PSK Reporter stations · 11-24 08:00–20:00 UTC*.
4. Below the chart:
   - stations heard and heard-me, stations per band, and the farthest and strongest station in the window;
   - for logs, the skip edge per band.
5. **Double-click** or **All hours** chooses everything. **◀ ▶** move the window by its own length.

Notes:

- With the **running live map**, choosing hours holds live updates on the map; the listener keeps recording. *All hours* returns to live.
- Layers remember their source (folder, files or session file) and the chosen hours, so this works again after you reopen the project. New fetches or log entries are read in.
- The timeline replaces the layer's features. It refuses while a layer is being edited.
- ADIF layers are not listed.

## 10. Field reference

Times are UTC. Distances are great-circle distances between grid centres.

### 10.1 Station layers

From *Fetch/Import PSK Reporter*, the live map (*WSJT-X live*) and *Import ALL.TXT*.

| Field | Meaning |
|---|---|
| `call` | Callsign. |
| `grid` | Locator the marker is placed at (the most recent one heard). |
| `direction` | PSK Reporter: `heard` (I decoded them) or `heard_me` (they decoded me). WSJT-X: `heard`, or `worked` for logged QSOs. |
| `reports` | Number of reports or decodes. |
| `snr_best`, `snr_median`, `snr_last` | SNR in dB. On WSJT-X layers the median covers every decode of the station (in the chosen hours, for the timeline). |
| `bands`, `modes` | Comma-separated, low to high frequency. |
| `first_utc`, `last_utc` | First and last time heard. |
| `distance_km`, `bearing_deg` | From your grid. |
| `source` | `psk` or `live`. |
| `hops`, `takeoff_deg` | Fewest F2 hops (layer at 300 km) and the takeoff angle needed. Geometry only. |
| `called_me` | WSJT-X layers only: messages from this station addressed to you. |

### 10.2 Exchange layers

From the live map (*WSJT-X exchanges*) and *Import ALL.TXT*. A and B are the two stations.

| Field | Meaning |
|---|---|
| `id` | Exchange number. |
| `call_a`, `grid_a`, `call_b`, `grid_b` | The two stations. A sent the first message decoded in the exchange (often the one answering a CQ). |
| `band`, `mode` | |
| `stage` | `started`, `one report`, `both reports` or `confirmed`. |
| `a_hears_b` | The report A sent: how A hears B (dB). |
| `b_hears_a` | The report B sent: how B hears A (dB). |
| `me_hears_a`, `me_hears_b` | How *you* decoded A and B (dB). |
| `paths` | Label text, e.g. `N5ER→AH6U +07  AH6U→N5ER -04`. |
| `involves_me` | 1 if one side is you. |
| `logged` | 1 if you logged it in WSJT-X. |
| `messages`, `transcript` | Number of messages, and the messages themselves (repeats shown as ×n). |
| `first_utc`, `last_utc` | |
| `distance_km`, `hops`, `takeoff_deg` | As for stations. |
| `report_diff_db` | `b_hears_a − a_hears_b`: A's (power + receiver noise) minus B's. |
| `b_vs_me_db` | `b_hears_a − me_hears_a`: positive = B heard A better than you did. |
| `a_vs_me_db` | `a_hears_b − me_hears_b`: positive = A heard B better than you did. |

### 10.3 ADIF layers

**Worked stations (ADIF)**

| Field | Meaning |
|---|---|
| `call`, `grid` | The most recent (most precise) grid logged. |
| `direction` | Always `worked`. |
| `qsos` | Number of QSOs. |
| `bands`, `modes` | |
| `first_utc`, `last_utc` | First and last QSO. |
| `sent_best_db`, `rcvd_best_db` | Best report you sent and received (digital modes only). |
| `confirmed` | Number of confirmed QSOs. |
| `country` | As logged. |
| `distance_km`, `bearing_deg`, `hops`, `takeoff_deg` | From your grid. |
| `source` | `adif`. |

**QSOs (ADIF)**: all the [exchange fields](#102-exchange-layers), filled as follows:

- you are A: `a_hears_b` = report sent, `b_hears_a` = report received;
- `stage` is `confirmed` and `logged` is 1;
- `me_hears_a` and `me_hears_b` are empty;
- `transcript` holds the log comment.

Plus:

| Field | Meaning |
|---|---|
| `freq_mhz` | Logged frequency. |
| `rst_sent`, `rst_rcvd` | Reports as logged (text, any mode). |
| `confirmed` | `lotw`, `qsl`, `eqsl`, comma-separated. |
| `prop_mode` | ADIF propagation mode (e.g. `ES`, `SAT`, `EME`), if logged. |
| `country`, `comment` | |
| `source` | File(s) the QSO came from. |

### 10.4 Ionosphere layers

**Ionosondes**: `code`, `name`, `utc`, `age_min`, `fof2` (MHz), `hmf2` (km), `mufd` (MUF(3000), MHz), `md` (M(3000) factor), `foes` (sporadic-E critical frequency, MHz), `confidence` (KC2G's 0–100 rating), `source`.

**Ionosphere contours**: `level` (MHz or km).

**Reflection points** (from *Check paths*): `path_fid`, `calls`, `kind` (*hop* or *control point*), `n` (number along the path), `hops`, `fof2_mhz`, `hmf2_km`, `mufd_mhz`, `point_muf_mhz` (control points: the MUF judged there), `freq_mhz`, `f2_status`.

**Sporadic-E candidates**: `path_fid`, `calls`, `grid_a`, `grid_b`, `band`, `freq_mhz`, `distance_km`, `takeoff_es`, `foes_min_mhz`, `f2_status`, `confidence` (*high*/*possible*), `cloud`, `last_utc`.

**Sporadic-E clouds**: `cloud`, `n_paths`, `bands`, `foes_max_mhz`, `high` (high-confidence candidates), `first_utc`, `last_utc`.

### 10.5 Analysis layers

**Station balance**

| Field | Meaning |
|---|---|
| `call`, `band`, `grid` | |
| `balance_db`, `std_err_db` | Balance and its standard error. |
| `n_two_way` | Two-way exchanges used. |
| `group_size` | Stations in its connected group. |
| `verdict` | `loud but deaf`, `balanced`, `good ears` or `uncertain`. |
| `n_censored`, `n_dropped` | Exchanges with a report at the floor or compressed; pairs left out. |
| `hears_vs_me_db`, `n_vs_me`, `n_vs_me_skipped` | Median "how they heard it minus how I did", its count, and skipped comparisons. |
| `is_me` | 1 for your own row. |

**Antenna A/B** (all three outputs share these counts):

| Field | Meaning |
|---|---|
| `n_both` | Messages both receivers decoded (pairs). |
| `only_a`, `only_b` | Messages only A or only B decoded. |
| `median_db` | Median SNR(A) − SNR(B) over pairs. |
| `iqr_lo_db`, `iqr_hi_db` | Interquartile range (needs 4+ pairs). |
| `decode_adv` | `(only_a − only_b) / all decodes`. Positive favours A. |

- By direction adds `sector` and `bearing_deg`.
- By station adds:
  - `call`, `grid`, `distance_km`, `bearing_deg`;
  - `takeoff_deg`, `takeoff_min_deg`, `takeoff_max_deg`, `takeoff_300_deg`;
  - `hops`, `hmf2_km`;
  - `angle_method` (`KC2G`, `model` or `geometric`);
  - `prop_mode` (`F2`, `above MUF` or `Es`).
- The table adds `by` (`all`, `distance`, `takeoff`) and `bin`.

**Skip distance by hour**: see [8.4](#84-skip-distance-by-hour).

## 11. Data files, caches and settings

The plugin keeps downloads in your QGIS profile folder. On Windows this is `%APPDATA%\QGIS\QGIS3\profiles\default\`. In QGIS, *Settings → User Profiles → Open Active Profile Folder* opens it.

| Folder | Contents |
|---|---|
| `maidenhead_mapper\kc2g\` | KC2G cache: `stations.json`, `mufd.geojson`, `fof2.geojson`, `hmf2.geojson`, `essn.json`, `meta.json`. Refreshed at most every 15 minutes. |
| `maidenhead_mapper\ccir\` | CCIR model coefficients `ccir11.asc`–`ccir22.asc` (~0.5 MB, downloaded once), and SILSO sunspot numbers `smoothed.csv` and `monthly.csv` (refreshed weekly). |

Your own data stays where you put it:

- PSK Reporter fetches in the folder you chose;
- live sessions in the session folder;
- outputs wherever you saved them.

**Settings** are stored in QGIS's settings under `maidenhead_mapper/`:

- `mycall`, `mygrid`: your callsign and grid;
- `psk_folder`: the last PSK Reporter folder;
- `live/…`: the live panel's mode, address, port, fade, save and folder.

Deleting the cache folders is safe; the data is downloaded again when needed.

## 12. Troubleshooting

**Live map: "Could not listen on 127.0.0.1:2237"**
Another program (JTAlert, GridTracker, a logger) owns the port in unicast mode. Switch everything to multicast `239.255.0.1:2237` (see [7.1](#71-wsjt-x-live-map)).

**Live map is listening but shows "No WSJT-X heard yet"**
- Check WSJT-X's *UDP Server* address and port match the panel.
- Check the right *Outgoing interfaces* are ticked in WSJT-X (include loopback).
- A firewall may block UDP for QGIS.

**A station never appears on the map**
It hasn't sent a grid yet. FT8 sends grids only in CQs and when answering a call. The panel counts these as *waiting for a grid*; they appear once a grid is decoded. PSK Reporter reports without a locator are counted in the import log.

**"Feature (…) has invalid geometry" when running Check paths, Find sporadic-E paths or Skip distance**
On versions before 0.8.2, exchanges between two stations in the same grid square could be drawn as zero-length lines, which QGIS rejects. Either:
- update the plugin and restart QGIS (0.8.2 and later ignore geometry when reading paths); or
- click the wrench icon next to the input layer and set **Invalid features filtering** to **Do not filter**.

**"The layer needs grid_a/grid_b (exchanges) or grid (stations)"**
The input isn't a station or exchange layer: for example *Ionosondes* or a contour layer. Choose a layer from the PSK Reporter, WSJT-X or ADIF tools.

**"Enter My grid: station layers are paths from you"**
Station-layer paths start at your grid. Enter it in *My grid*.

**Skip distance: "This layer has no raw data to read and no usable paths"**
The layer has no grid fields (for example the Maidenhead grid or the ionosonde layer). Use a PSK Reporter, ALL.TXT, session, live or ADIF layer, or any layer with `grid_a`/`grid_b` or `grid` fields plus a band and a time.

**Skip distance: "This live map is no longer running and wasn't saved"**
An unsaved live map's decodes are kept only while it runs. Tick *Save session to a GeoPackage* before pressing Start next time.

**The Activity timeline doesn't list my layer**
It lists layers made by the PSK Reporter tools, *Import ALL.TXT* and the live map, and session GeoPackages loaded from the `.gpkg` (recognised by their `stations`, `exchanges` and `log` tables). Load the `stations` or `exchanges` table, not `log`. ADIF layers are not listed.

**PSK Reporter: "wait N min"**
PSK Reporter allows one query per 5 minutes. Wait, or map what you already have with *Import PSK Reporter reports*.

**Check paths says "no data" for everything**
For KC2G, the paths must be within the time limit (3 hours by default) of KC2G's data. Choose *Auto* or *CCIR model only* for older paths. The first CCIR run needs internet to download the coefficients.

## 13. Credits and licences

- **Plugin**: GNU General Public License v3 or later (see `LICENSE`).
- **KC2G** ([prop.kc2g.com](https://prop.kc2g.com)): ionosonde data and MUF/foF2/hmF2 maps, built from GIRO and INGV ionosondes over IRI-2020. A volunteer service; please use it gently.
- **CCIR coefficients**: the ITU-R P.1239 (CCIR) foF2 and M(3000) coefficient files as distributed with IRI, downloaded from the MIT-licensed [space-physics/iri2016](https://github.com/space-physics/iri2016) repository (pinned to one commit).
- **Sunspot numbers**: WDC-SILSO, Royal Observatory of Belgium, Brussels. Licensed CC BY-NC 4.0 (non-commercial use).
- **Methods**: ITU-R P.533-14 (HF path MUF), ITU-R P.1239 (ionospheric reference characteristics). The station-balance method follows DK3BA's (2024) analysis of FT8 report statistics.
- **Modified dip latitude**: a grid derived from the IGRF magnetic field model, shipped with the plugin.
- **Band colours**: close to the schemes used by PSK Reporter and GridTracker.
