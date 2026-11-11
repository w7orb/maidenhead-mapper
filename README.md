# Maidenhead Mapper (QGIS plugin)

QGIS plugin for amateur-radio Maidenhead maps.

- **Create Maidenhead grid**: field (2-char, 20°×10°) and square (4-char, 2°×1°) polygon layers with a `locator` attribute. They come pre-styled and labelled; squares appear when zoomed in past 1:20,000,000.
- **Import PSK Reporter reports**: a folder of `psk_*.json` pulls (from [pskreporter-analytics](https://github.com/cwtews/pskreporter-analytics)) becomes one marker per remote station and direction: *heard* (I decoded them) or *heard_me* (they decoded me). Each marker has report count, best/median/last SNR, bands, modes, first/last time (UTC), and distance/bearing from my grid. You can filter by direction, band and time. It can be styled by SNR, band or direction, and callsign labels appear when zoomed in.
- **WSJT-X live map**: listens to WSJT-X's UDP feed and plots every station it decodes. Markers update every 2 s and fade over a set time since each station was last heard. Stations you log a QSO with become red stars. Works alongside JTAlert/GridTracker via multicast.

The grid always spans exactly -180..180 / -90..90. The earlier hand-built grid took its extent from the OSM basemap (Web Mercator stops at ±85.05°), which shifted every field about 5° south.

Requires QGIS 3.34 or later (tested on 3.44).

## Install for development (Windows)

Link the plugin folder into your QGIS profile (run in `cmd`):

```
mklink /J "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\maidenhead_mapper" "d:\projects\gis\maidenhead_plugin\maidenhead_mapper"
```

Restart QGIS, then enable **Maidenhead Mapper** under *Plugins → Manage and Install Plugins → Installed*. Install the **Plugin Reloader** plugin to pick up code changes without restarting.

## Use

*Plugins → Maidenhead Mapper → Create Maidenhead grid…*, the toolbar button, or *Processing Toolbox → Maidenhead Mapper → Grid*.

- **Grid level**: fields, squares, or both.
- **Extent**: leave empty for the whole world (324 fields / 32,400 squares), or pick the map canvas or a layer. Any CRS works; the grid itself is always aligned to whole degrees.
- **Outputs**: temporary layers or files (e.g. GeoPackage). Uncheck an output to skip it.

### Import PSK Reporter reports

*Plugins → Maidenhead Mapper → Import PSK Reporter reports…*

- **Folder**: a folder of `psk_*.json` files. Repeats across overlapping pulls are counted once; empty or corrupt files are listed in the log and skipped.
- **My callsign**: remembered between runs. Portable forms (`W7ORB/P`) count as you.
- **My grid**: for distance/bearing. Leave empty to use the locator most often reported for you.
- **Locator precision**: positions use the station's most recent locator, capped here (default 6 characters).
- **Style**: SNR (best report), band (lowest band heard), direction, or a single symbol. Restyle any time in *Layer Properties*.

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
- The layer *WSJT-X live* is temporary. Use right-click → *Make Permanent* to save a session. If you delete it, it is rebuilt on the next update.

**Testing without a radio**: replay a recorded `ALL.TXT` as live packets:

```
python tools/replay_alltxt.py path\to\ALL.TXT --speed 30
```

`--speed 0` sends as fast as possible. `--qso K7ABC:DN17` logs a QSO at the end. `--address 127.0.0.1` sends unicast.

## Layout

```
maidenhead_mapper/
  core/          pure Python, no QGIS imports: locator math, bands, PSK Reporter
                 loading/summaries, WSJT-X UDP protocol, FT8 message parsing, live model
  algorithms/    Processing algorithms and shared styling
  live/          UDP listener, live layer and dock panel
  provider.py    Processing provider (id "maidenhead")
  plugin.py      menu/toolbar wiring
tools/           replay_alltxt.py (fake WSJT-X from a recorded log)
tests/           pytest for core/
```

## Tests

```
python -m pytest
```

Runs with any Python 3.8+ that has pytest installed; QGIS is not needed. The plugin itself uses only the Python standard library and QGIS.
