"""Skip distance and hop structure by band and hour. Pure Python, no QGIS.

Above the critical frequency foF2, signals sent up too steeply pass through
the F2 layer, so nothing comes back between ground-wave range and the skip
distance. The skip edge from many contacts is estimated as the 10th
percentile of sky-wave distances (over LOCAL_KM), robust to the odd
scatter or Es contact. From the secant law at that edge,

    foF2 ~= f / sec(i(skip, h))

which is only meaningful when there *is* a skip zone (f well above foF2)
and the edge is within one hop; otherwise no estimate is given.
Treat it as an indicator for comparing hours, not a measurement.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from . import ft8msg
from . import maidenhead as mh
from . import wsjtx_udp as w
from .bands import band_for_hz, band_mhz
from .hops import MIN_TAKEOFF_DEG, max_hop_km
from .iono import sec_incidence
from .timeline import hour_floor

LOCAL_KM = 300.0         # closer than this: ground wave / local, not counted as sky wave
MIN_SAMPLES = 20         # sky-wave contacts needed for a skip estimate (one odd contact sets a small sample's edge)
SKIP_PCT = 0.10
F2_HEIGHT_KM = 300.0


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass
class SkipStats:
    n: int
    local_n: int
    skip_km: Optional[float]
    median_km: Optional[float]
    p90_km: Optional[float]
    fof2_est_mhz: Optional[float]


def stats(distances, band=None, height_km=F2_HEIGHT_KM):
    sky = sorted(d for d in distances if d is not None and d >= LOCAL_KM)
    local = sum(1 for d in distances if d is not None and d < LOCAL_KM)
    if len(sky) < MIN_SAMPLES:
        return SkipStats(len(sky), local, None, _pct(sky, 0.5), _pct(sky, 0.9), None)
    skip = _pct(sky, SKIP_PCT)
    f = band_mhz(band) if band else None
    # Past one hop the edge is the band closing, not a skip zone: no foF2 from it
    single = skip <= max_hop_km(height_km, MIN_TAKEOFF_DEG)
    fof2 = f / sec_incidence(skip, height_km) if f and single else None
    return SkipStats(len(sky), local, round(skip), round(_pct(sky, 0.5)), round(_pct(sky, 0.9)),
                     None if fof2 is None else round(fof2, 2))


def by_hour(samples):
    """samples: (time, band, distance_km) -> {(band, hour): SkipStats}."""
    groups = defaultdict(list)
    for t, band, d in samples:
        if t is None or not band:
            continue
        groups[(band, hour_floor(t))].append(d)
    return {k: stats(v, k[0]) for k, v in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]))}


def psk_samples(reports, my_grid, directions=("heard", "heard_me")):
    """(time, band, km) for labelled PSK Reporter reports (psk.add_remote_station)."""
    from .stations import band_of
    if not mh.is_valid(my_grid or ""):
        return []
    out = []
    for r in reports:
        loc = r.get("remoteLocator")
        if r.get("direction") not in directions or not (isinstance(loc, str) and mh.is_valid(loc)):
            continue
        out.append((r.get("time"), band_of(r), mh.distance_bearing(my_grid, loc)[0]))
    return out


def log_samples(log, my_grid=""):
    """(time, band, km) for each decode in a live/ALL.TXT log whose sender's
    grid is known anywhere in the log (grids are sent only now and then)."""
    grids = {}
    for _, m in log:
        if isinstance(m, w.Decode):
            p = ft8msg.parse_message(m.message)
            if p and p.grid:
                grids[p.sender] = p.grid
        elif isinstance(m, w.Status) and not my_grid and mh.is_valid(m.de_grid or ""):
            my_grid = m.de_grid
    if not mh.is_valid(my_grid or ""):
        return []
    dial, out = {}, []
    for t, m in log:
        if isinstance(m, w.Status):
            dial[m.id] = m.dial_hz
        elif isinstance(m, w.Decode):
            p = ft8msg.parse_message(m.message)
            g = p and grids.get(p.sender)
            band = band_for_hz(dial.get(m.id)) if dial.get(m.id) else None
            if g and band:
                out.append((t, band, mh.distance_bearing(my_grid, g)[0]))
    return out


def summary_line(pairs):
    """'10m ~1,950 km, 20m ~620 km' from (band, km) pairs, bands with enough data."""
    from .bands import BAND_NAMES
    by = defaultdict(list)
    for band, d in pairs:
        if band:
            by[band].append(d)
    parts = []
    for band in sorted(by, key=lambda b: BAND_NAMES.index(b) if b in BAND_NAMES else 99):
        s = stats(by[band], band)
        if s.skip_km is not None:
            parts.append(f"{band} ~{s.skip_km:,} km")
    return ", ".join(parts)
