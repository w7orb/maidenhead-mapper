"""One summary row per remote station and direction. Pure Python + pandas.

Input is a DataFrame of reports that already has `direction`,
`remoteCallsign` and `remoteLocator` (see psk.add_remote_station). Output is
a list of StationSummary, whose fields become the marker layer attributes.
"""

from collections import Counter
from dataclasses import dataclass, fields
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from . import maidenhead as mh
from .bands import band_for_hz

DIRECTIONS = ("heard", "heard_me")


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


def guess_my_grid(df: pd.DataFrame) -> Optional[str]:
    """Most common valid locator reported on my side, or None."""
    if "myLocator" not in df.columns:
        return None
    locs = [mh.normalize(x) for x in df["myLocator"].dropna() if isinstance(x, str) and mh.is_valid(x)]
    if not locs:
        return None
    return mh.display(Counter(locs).most_common(1)[0][0])


def _band_order(name):
    from .bands import BAND_NAMES
    return BAND_NAMES.index(name) if name in BAND_NAMES else len(BAND_NAMES)


def _opt_int(v):
    return None if v is None or pd.isna(v) else int(v)


def summarize(df: pd.DataFrame, precision: int = mh.SUBSQUARE, my_grid: Optional[str] = None,
              directions=DIRECTIONS, bands=None, start=None, end=None,
              source: str = "psk") -> Tuple[List[StationSummary], SummaryStats]:
    """Group reports into one StationSummary per (remote callsign, direction).

    precision  cap on the locator used for position (2-10)
    my_grid    for distance/bearing; defaults to guess_my_grid(df)
    bands      iterable of band names to keep (None = all)
    start/end  tz-aware timestamps bounding `time` (None = open)

    The grid is the most recent valid locator reported for that station.
    """
    stats = SummaryStats(reports_in=len(df))
    if df.empty:
        return [], stats

    d = df.copy()
    stats.no_direction = int(d["direction"].isna().sum())
    stats.self_spots = int((d["direction"] == "self").sum())
    d = d[d["direction"].isin(DIRECTIONS)]

    valid = d["remoteLocator"].map(lambda x: isinstance(x, str) and mh.is_valid(x))
    stats.no_locator = int((~valid).sum())
    d = d[valid]

    d["band"] = d["frequency"].map(band_for_hz) if "frequency" in d.columns else None
    keep = d["direction"].isin(list(directions))
    if bands:
        keep &= d["band"].isin(list(bands))
    if start is not None:
        keep &= d["time"] >= start
    if end is not None:
        keep &= d["time"] <= end
    stats.filtered_out = int((~keep).sum())
    d = d[keep]
    if d.empty:
        return [], stats

    my_grid = my_grid or guess_my_grid(df)
    snr = pd.to_numeric(d["sNR"], errors="coerce") if "sNR" in d.columns else pd.Series(index=d.index, dtype=float)
    d = d.assign(_snr=snr, _call=d["remoteCallsign"].str.upper()).sort_values("time")

    out = []
    for (call, direction), g in d.groupby(["_call", "direction"], sort=True):
        grid = mh.display(mh.truncate(g["remoteLocator"].iloc[-1], precision))
        lon, lat = mh.center(grid)
        dist = bear = None
        if my_grid and mh.is_valid(my_grid):
            dist, bear = mh.distance_bearing(my_grid, grid)
            dist, bear = round(dist, 1), round(bear, 1)
        s = g["_snr"].dropna()
        band_list = sorted({b for b in g["band"] if isinstance(b, str)}, key=_band_order)
        modes = sorted({m for m in g.get("mode", pd.Series(dtype=object)) if isinstance(m, str)})
        out.append(StationSummary(
            call=call, grid=grid, direction=direction, reports=len(g),
            snr_best=_opt_int(s.max()) if len(s) else None,
            snr_median=float(s.median()) if len(s) else None,
            snr_last=_opt_int(s.iloc[-1]) if len(s) else None,
            bands=",".join(band_list), modes=",".join(modes),
            first_utc=g["time"].iloc[0].to_pydatetime(), last_utc=g["time"].iloc[-1].to_pydatetime(),
            distance_km=dist, bearing_deg=bear, source=source, lon=lon, lat=lat,
        ))
    stats.stations = len(out)
    return out, stats
