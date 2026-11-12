"""Sporadic-E (Es) candidates among HF/VHF paths. Pure Python, no QGIS.

Sporadic-E is a thin, patchy layer of ionisation at about 100-110 km. It
supports single hops of roughly 500-2,300 km (the horizon limits one hop off
105 km) on frequencies F2 can't reach, mostly 10m and 6m, mostly in local
summer. Rules used here:

  candidate    band 12m or higher, single-Es-hop distance (MIN_KM .. max hop)
  confidence   'high'      on 6m and up (F2 rarely supports these short paths
                           there), or the ionosphere check says the band is
                           above the F2 MUF
               'possible'  12m/10m with no ionosphere data
               (not a candidate when F2 can explain the path)

The reflection point is the path midpoint. foes_min is the critical
frequency the Es patch must at least have had for the observed frequency
to come back at that range (secant law): freq / sec(i).
"""

import datetime as _dt
import math

from . import hops as _hops
from .iono import STATUS_ABOVE, STATUS_F2, sec_incidence

ES_HEIGHT_KM = 105.0
MIN_KM = 500.0
ES_BANDS = ("12m", "10m", "6m", "4m", "2m")
VHF_BANDS = ("6m", "4m", "2m", "1.25m", "70cm")
HIGH, POSSIBLE = "high", "possible"
# mode_guess values
MODE_F2, MODE_ES, MODE_ABOVE, MODE_NO_DATA = "F2", "Es", "above MUF", "no data"


def max_single_hop_km(height_km=ES_HEIGHT_KM):
    return _hops.max_hop_km(height_km, 0.0)


def classify(distance_km, band, f2_status=None, bands=ES_BANDS):
    """'high', 'possible' or None for a path."""
    if band not in bands or distance_km is None:
        return None
    if not MIN_KM <= distance_km <= max_single_hop_km():
        return None
    if band in VHF_BANDS or f2_status == STATUS_ABOVE:
        return HIGH
    if f2_status == STATUS_F2:
        return None
    return POSSIBLE


def mode_guess(distance_km, band, f2_status):
    """Most likely propagation mode for a path, from the F2 check and the Es rules:
    'Es' where classify() is 'high' (6m and up at single-Es-hop range, or 12m/10m
    there when F2 can't support it), else 'F2' / 'above MUF' / 'no data' from
    the F2 check. Double-hop Es is not considered."""
    if classify(distance_km, band, f2_status) == HIGH:
        return MODE_ES
    if f2_status == STATUS_F2:
        return MODE_F2
    if f2_status == STATUS_ABOVE:
        return MODE_ABOVE
    return MODE_NO_DATA


def foes_min_mhz(freq_mhz, distance_km, height_km=ES_HEIGHT_KM):
    """Lowest Es critical frequency that returns freq_mhz at this range."""
    return freq_mhz / sec_incidence(distance_km, height_km)


def takeoff_deg(distance_km, height_km=ES_HEIGHT_KM):
    return _hops.takeoff_deg(distance_km, 1, height_km)


def _km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * _hops.R_EARTH * math.asin(math.sqrt(min(1.0, a)))


def cluster(points, radius_km=500.0, window=_dt.timedelta(minutes=60)):
    """Group (lon, lat, time-or-None) points: two points are linked when within
    radius_km and (if both have times) within `window`. Returns one cluster
    number per point, numbered from 1 by size (largest first)."""
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(n), key=lambda i: points[i][1])   # by latitude: prune by lat difference
    lat_deg = radius_km / 111.2
    for a_pos, i in enumerate(order):
        xi, yi, ti = points[i]
        for j in order[a_pos + 1:]:
            xj, yj, tj = points[j]
            if yj - yi > lat_deg:
                break
            if ti is not None and tj is not None and abs(ti - tj) > window:
                continue
            if _km(xi, yi, xj, yj) <= radius_km:
                parent[find(i)] = find(j)
    roots = [find(i) for i in range(n)]
    sizes = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    rank = {r: k for k, r in enumerate(sorted(sizes, key=lambda r: (-sizes[r], r)), 1)}
    return [rank[r] for r in roots]
