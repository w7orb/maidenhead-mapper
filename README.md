# Maidenhead Mapper (QGIS plugin)

QGIS plugin for amateur-radio Maidenhead maps.

- **Create Maidenhead grid**: field (2-char, 20°×10°) and square (4-char, 2°×1°) polygon layers with a `locator` attribute. They come pre-styled and labelled; squares appear when zoomed in past 1:20,000,000.
- **Import PSK Reporter reports**: a folder of `psk_*.json` pulls (from [pskreporter-analytics](https://github.com/cwtews/pskreporter-analytics)) becomes one marker per remote station and direction: *heard* (I decoded them) or *heard_me* (they decoded me). Each marker has report count, best/median/last SNR, bands, modes, first/last time (UTC), and distance/bearing from my grid. You can filter by direction, band and time. It can be styled by SNR, band or direction, and callsign labels appear when zoomed in. Needs pandas, which QGIS bundles.
- *Planned:* live map from WSJT-X's UDP feed (multicast, so it can share with JTAlert/GridTracker), with markers that fade with age.

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

## Layout

```
maidenhead_mapper/
  core/          pure Python, no QGIS imports (locator math, ADIF/summary stubs)
  algorithms/    Processing algorithms
  provider.py    Processing provider (id "maidenhead")
  plugin.py      menu/toolbar wiring
tests/           pytest for core/
```

## Tests

```
python -m pytest
```

Runs with any Python 3.8+ that has pytest installed; QGIS is not needed.
