"""One summary row per remote station and direction. Pure Python.

Input is a list of report dicts that already have `direction`,
`remoteCallsign`, `remoteLocator` and `time` (see psk.load_reports and
psk.add_remote_station). Output is a list of StationSummary, whose fields
become the marker layer attributes.
"""

import datetime as _dt
import statistics
from collections import Counter
from dataclasses import dataclass, fields
from datetime import datetime
from typing import List, Optional, Tuple

from . import maidenhead as mh
from .bands import BAND_NAMES, band_for_hz

DIRECTIONS = ("heard", "heard_me")
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


@dataclass
class StationSummary:
    call: str
    grid: str                       # display case, capped at the requested precision
    direction: str                  # heard | heard_me (live adds: worked)
    reports: int
    snr_best: Optional[int]
    snr_median: Optional[float]
    snr_last: Optional[int]
    bands: str                      # comma-separated, low to high frequency
    modes: str                      # comma-separated
    first_utc: datetime
    last_utc: datetime
    distance_km: Optional[float]
    bearing_deg: Optional[float]
    source: str
    lon: float
    lat: float


FIELD_NAMES = [f.name for f in fields(StationSummary) if f.name not in ("lon", "lat")]


@dataclass
class SummaryStats:
    reports_in: int = 0
    no_direction: int = 0           # mycall on neither side
    self_spots: int = 0
    no_locator: int = 0             # remote locator missing or invalid
    filtered_out: int = 0
    stations: int = 0


def guess_my_grid(reports) -> Optional[str]:
    """Most common valid locator reported on my side, or None."""
    locs = [mh.normalize(r["myLocator"]) for r in reports
            if isinstance(r.get("myLocator"), str) and mh.is_valid(r["myLocator"])]
    if not locs:
        return None
    return mh.display(Counter(locs).most_common(1)[0][0])


def _band_order(name):
    return BAND_NAMES.index(name) if name in BAND_NAMES else len(BAND_NAMES)


def _number(v):
    """SNR as a number, or None (PSK Reporter sends ints; be tolerant)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def band_of(report):
    """Band name for a report (cached on the dict), or None."""
    if "band" not in report:
        report["band"] = band_for_hz(report.get("frequency"))
    return report["band"]


def select(reports, directions=DIRECTIONS, bands=None, start=None, end=None):
    """Reports usable for markers that pass the filters, plus counts of the rest.

    Usable = heard/heard_me with a valid remote locator.
    bands      iterable of band names to keep (None = all)
    start/end  tz-aware datetimes bounding `time`, inclusive (None = open)
    """
    stats = SummaryStats(reports_in=len(reports))
    directions, bands = set(directions), set(bands) if bands else None
    kept = []
    for r in reports:
        d = r.get("direction")
        if d is None:
            stats.no_direction += 1
            continue
        if d == "self":
            stats.self_spots += 1
            continue
        if d not in DIRECTIONS:
            continue
        loc = r.get("remoteLocator")
        if not (isinstance(loc, str) and mh.is_valid(loc)):
            stats.no_locator += 1
            continue
        t = r.get("time")
        if (d not in directions
                or (bands is not None and band_of(r) not in bands)
                or (start is not None and (t is None or t < start))
                or (end is not None and (t is None or t > end))):
            stats.filtered_out += 1
            continue
        kept.append(r)
    return kept, stats


def summarize(reports, precision: int = mh.SUBSQUARE, my_grid: Optional[str] = None,
              directions=DIRECTIONS, bands=None, start=None, end=None,
              source: str = "psk") -> Tuple[List[StationSummary], SummaryStats]:
    """Group reports into one StationSummary per (remote callsign, direction).

    precision  cap on the locator used for position (2-10)
    my_grid    for distance/bearing; defaults to guess_my_grid(reports)
    Filters are as in `select`. The grid is the station's most recent valid
    locator.
    """
    kept, stats = select(reports, directions, bands, start, end)
    if not kept:
        return [], stats

    my_grid = my_grid or guess_my_grid(reports)
    if my_grid and not mh.is_valid(my_grid):
        my_grid = None

    groups = {}
    for r in sorted(kept, key=lambda r: r.get("time") or _EPOCH):  # stable: ties keep file order
        groups.setdefault((r["remoteCallsign"].upper(), r["direction"]), []).append(r)

    out = []
    for (call, direction), g in sorted(groups.items()):
        grid = mh.display(mh.truncate(g[-1]["remoteLocator"], precision))
        lon, lat = mh.center(grid)
        dist = bear = None
        if my_grid:
            dist, bear = mh.distance_bearing(my_grid, grid)
            dist, bear = round(dist, 1), round(bear, 1)
        snrs = [s for s in (_number(r.get("sNR")) for r in g) if s is not None]
        bands_seen = sorted({b for b in map(band_of, g) if b}, key=_band_order)
        modes = sorted({r["mode"] for r in g if isinstance(r.get("mode"), str)})
        out.append(StationSummary(
            call=call, grid=grid, direction=direction, reports=len(g),
            snr_best=int(max(snrs)) if snrs else None,
            snr_median=float(statistics.median(snrs)) if snrs else None,
            snr_last=int(snrs[-1]) if snrs else None,
            bands=",".join(bands_seen), modes=",".join(modes),
            first_utc=g[0]["time"], last_utc=g[-1]["time"],
            distance_km=dist, bearing_deg=bear, source=source, lon=lon, lat=lat,
        ))
    stats.stations = len(out)
    return out, stats
