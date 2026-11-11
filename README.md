# Maidenhead Mapper (QGIS plugin)

QGIS plugin for amateur-radio Maidenhead maps.

- **Create Maidenhead grid**: field (2-char, 20°×10°) and square (4-char, 2°×1°) polygon layers with a `locator` attribute. They come pre-styled and labelled; square labels appear when zoomed in past 1:15,000,000.
- *Planned:* import a WSJT-X ADIF log (`wsjtx_log.adi`) and place one marker per station worked, styled by band, mode, SNR or QSO count.

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
