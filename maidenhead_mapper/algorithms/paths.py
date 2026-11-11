"""Reading station and exchange layers as paths (shared by the analysis tools).

Exchange layers (grid_a/grid_b) are paths between two stations; station
layers (grid) are paths from My grid to the station.
"""

import datetime as _dt
import json

from qgis.core import QgsSettings

from ..core import maidenhead as mh
from ..core.bands import band_mhz
from .import_psk import LAYER_PROPERTY

SETTINGS_MYGRID = "maidenhead_mapper/mygrid"


class PathError(Exception):
    pass


def my_grid_for(layer, explicit=""):
    """My grid: the explicit value, else the layer's import settings, else the last used."""
    g = (explicit or "").strip()
    if not g and layer is not None:
        for key, name in ((LAYER_PROPERTY, "my_grid"), ("maidenhead_mapper/log", "mygrid")):
            try:
                g = json.loads(layer.customProperty(key) or "{}").get(name) or ""
            except ValueError:
                g = ""
            if g:
                break
    return g or QgsSettings().value(SETTINGS_MYGRID, "", type=str)


def when_of(f, names):
    """A feature's last_utc as an aware datetime, or None."""
    if "last_utc" not in names:
        return None
    q = f["last_utc"]
    try:
        if q is None or not q.isValid():
            return None
        return _dt.datetime.fromtimestamp(q.toSecsSinceEpoch(), _dt.timezone.utc)
    except AttributeError:
        return None


class PathReader:
    def __init__(self, fields, my_grid=None):
        self.names = fields.names()
        self.exchanges = "grid_a" in self.names and "grid_b" in self.names
        if not self.exchanges and "grid" not in self.names:
            raise PathError("The layer needs grid_a/grid_b (exchanges) or grid (stations).")
        if not self.exchanges and not mh.is_valid(my_grid or ""):
            raise PathError("Enter My grid: station layers are paths from you.")
        self.my_grid = my_grid

    def read(self, f):
        """dict: ga, gb (grids or None), calls, bands [(name, MHz)], when, ok."""
        n = self.names
        if self.exchanges:
            ga, gb = f["grid_a"], f["grid_b"]
            calls = f"{f['call_a']}–{f['call_b']}" if "call_a" in n else ""
            bands = [f["band"]] if "band" in n and f["band"] else []
        else:
            ga, gb = self.my_grid, f["grid"]
            calls = f["call"] if "call" in n else ""
            bands = [b for b in str(f["bands"] or "").split(",") if b] if "bands" in n else []
        ok = isinstance(ga, str) and isinstance(gb, str) and mh.is_valid(ga) and mh.is_valid(gb)
        return {"ga": ga if ok else None, "gb": gb if ok else None, "calls": calls,
                "bands": [(b, band_mhz(b)) for b in bands if band_mhz(b)], "when": when_of(f, n), "ok": ok}
