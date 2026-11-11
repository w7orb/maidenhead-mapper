"""Hop geometry for HF sky-wave paths. Pure Python, no QGIS.

For a path of great-circle length D made of n equal hops off a layer at
virtual height h (spherical Earth, mirror reflection), each hop spans a
central half-angle phi = D / (2 n R) and the takeoff (elevation) angle is

    beta = atan( (cos(phi) - R / (R + h)) / sin(phi) )

A path is possible in n hops if beta is at least the lowest practical
takeoff angle (terrain and antennas rarely radiate usefully below ~2-3
degrees). The *likely* hop count is the smallest possible one: each extra
hop adds ground-reflection and absorption loss, so the fewest hops usually
dominate. This is a geometric estimate. The layer height is assumed (or
measured; see TODO item 5), and the MUF is not checked here.

Default height: 300 km, a typical F2 virtual height. Use ~110 km for E or
sporadic-E.
"""

import math
from typing import List, Optional, Tuple

R_EARTH = 6371.0
F2_HEIGHT_KM = 300.0
E_HEIGHT_KM = 110.0
MIN_TAKEOFF_DEG = 2.0
MAX_HOPS = 20


def max_hop_km(height_km: float = F2_HEIGHT_KM, min_takeoff_deg: float = MIN_TAKEOFF_DEG) -> float:
    """Longest single hop off a layer at height_km for a takeoff angle >= min_takeoff_deg."""
    b = math.radians(min_takeoff_deg)
    return 2 * R_EARTH * (math.acos(R_EARTH * math.cos(b) / (R_EARTH + height_km)) - b)


def takeoff_deg(distance_km: float, hops: int, height_km: float = F2_HEIGHT_KM) -> float:
    """Takeoff angle (degrees) for distance_km in `hops` equal hops. Negative =
    impossible (the hop is longer than the horizon allows)."""
    phi = distance_km / (2 * hops * R_EARTH)
    if phi <= 0:
        return 90.0
    return math.degrees(math.atan((math.cos(phi) - R_EARTH / (R_EARTH + height_km)) / math.sin(phi)))


def hop_options(distance_km: float, height_km: float = F2_HEIGHT_KM, min_takeoff_deg: float = MIN_TAKEOFF_DEG,
                max_takeoff_deg: float = 90.0) -> List[Tuple[int, float]]:
    """Every (hops, takeoff_deg) with the takeoff angle in range, fewest hops first."""
    out = []
    n_min = max(1, math.ceil(distance_km / max_hop_km(height_km, min_takeoff_deg) - 1e-9))
    for n in range(n_min, MAX_HOPS + 1):
        b = takeoff_deg(distance_km, n, height_km)
        if b > max_takeoff_deg:
            break
        if b >= min_takeoff_deg:
            out.append((n, b))
    return out


def likely_hops(distance_km: Optional[float], height_km: float = F2_HEIGHT_KM,
                min_takeoff_deg: float = MIN_TAKEOFF_DEG) -> Tuple[Optional[int], Optional[float]]:
    """(fewest possible hops, its takeoff angle) or (None, None) if unknown.

    Short paths give one hop at a steep angle (near-vertical incidence)."""
    if distance_km is None or distance_km <= 0:
        return None, None
    opts = hop_options(distance_km, height_km, min_takeoff_deg)
    if not opts:
        return None, None
    n, b = opts[0]
    return n, round(b, 1)


def reflection_points(lon1: float, lat1: float, lon2: float, lat2: float, hops: int):
    """(lon, lat) of each hop's midpoint along the great circle: where the
    signal meets the ionosphere."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2
                                + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    out = []
    for k in range(hops):
        f = (2 * k + 1) / (2 * hops)
        if d < 1e-12:
            out.append((lon1, lat1))
            continue
        a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        out.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    return out
