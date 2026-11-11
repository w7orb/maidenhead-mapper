"""Ionosphere conditions along HF paths, from KC2G's maps. Pure Python.

KC2G (https://prop.kc2g.com) publishes the current state of the F2 layer as
contour maps (GeoJSON LineStrings with a `level-value`):
    mufd   MUF(3000)F2, MHz: highest frequency F2 supports on a 3,000 km hop
    fof2   foF2, MHz: critical (vertical-incidence) frequency
    hmf2   hmF2, km: height of the F2 peak
built from GIRO/INGV ionosondes (weighted by confidence) over the IRI-2020
model, plus the ionosondes themselves (stations.json). Updated about every
15 minutes; there is no history.

For a path we take the likely hop count, the reflection point of each hop
(its midpoint), the layer there, and the MUF for that hop's length from the
secant law, scaled so a 3,000 km hop gives KC2G's MUF(3000):

    MUF(d) = MUF(3000) * sec(i(d, h)) / sec(i(3000, h))
    sin(i) = R cos(beta) / (R + h)     (i: incidence angle at the layer)

The path MUF is the lowest of its hops. A frequency above it isn't
supported by F2: the path used sporadic-E, scatter, or the map is wrong.
"""

import datetime as _dt
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import hops as _hops

R = _hops.R_EARTH
CELL_DEG = 5.0
STATUS_F2, STATUS_ABOVE, STATUS_NO_DATA = "F2", "above F2 MUF", "no data"


# ------------------------------------------------------------------ ionosondes
@dataclass
class Ionosonde:
    code: str
    name: str
    lon: float
    lat: float
    time: _dt.datetime
    fof2: Optional[float]
    hmf2: Optional[float]
    mufd: Optional[float]
    md: Optional[float]
    foes: Optional[float]
    confidence: Optional[float]
    source: str = ""


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def parse_stations(data, now=None, max_age_h=3.0) -> Tuple[List[Ionosonde], int]:
    """KC2G stations.json -> (ionosondes reported within max_age_h, number stale)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    fresh, stale = [], 0
    for x in data or []:
        st = x.get("station") or {}
        try:
            t = _dt.datetime.fromisoformat(x["time"]).replace(tzinfo=_dt.timezone.utc)
            lon, lat = float(st["longitude"]), float(st["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs((now - t).total_seconds()) > max_age_h * 3600:
            stale += 1
            continue
        fresh.append(Ionosonde(st.get("code", ""), st.get("name", ""), lon - 360 if lon > 180 else lon, lat, t,
                               _f(x.get("fof2")), _f(x.get("hmf2")), _f(x.get("mufd")), _f(x.get("md")),
                               _f(x.get("foes")), _f(x.get("cs")), x.get("source", "")))
    return fresh, stale


# ------------------------------------------------------------- contour maps
class ContourField:
    """Interpolates a value from contour lines.

    At a point, find the nearest contour level and the nearer of its two
    neighbouring levels, then interpolate linearly by distance to each.
    Beyond the outermost contour there is nothing to extrapolate from, so
    the value stays between the two outermost levels: peaks (and troughs)
    are underestimated.
    """

    def __init__(self, geojson):
        self.levels: List[float] = []
        self._cells: Dict[Tuple[int, int], List[tuple]] = defaultdict(list)
        levels = set()
        for feat in (geojson or {}).get("features", []):
            try:
                level = float(feat["properties"]["level-value"])
                geom = feat["geometry"]
            except (KeyError, TypeError, ValueError):
                continue
            lines = [geom["coordinates"]] if geom["type"] == "LineString" else (
                geom["coordinates"] if geom["type"] == "MultiLineString" else [])
            for line in lines:
                for (x1, y1, *_), (x2, y2, *_) in zip(line, line[1:]):
                    if abs(x2 - x1) > 180:   # seam at the antimeridian
                        continue
                    seg = (level, x1, y1, x2, y2)
                    for cell in self._cells_for(x1, y1, x2, y2):
                        self._cells[cell].append(seg)
                    levels.add(level)
        self.levels = sorted(levels)
        self._cache: Dict[Tuple[int, int], Optional[float]] = {}

    def __bool__(self):
        return bool(self.levels)

    @staticmethod
    def _cell(x, y):
        return int(math.floor((x + 180) / CELL_DEG)) % int(360 / CELL_DEG), int(math.floor((y + 90) / CELL_DEG))

    def _cells_for(self, x1, y1, x2, y2):
        a, b = self._cell(min(x1, x2), min(y1, y2)), self._cell(max(x1, x2), max(y1, y2))
        return {(i, j) for i in range(a[0], b[0] + 1) for j in range(a[1], b[1] + 1)}

    def value_at(self, lon, lat):
        key = (round(lon * 4), round(lat * 4))           # 0.25 deg cache
        if key not in self._cache:
            self._cache[key] = self._value(lon, lat)
        return self._cache[key]

    def _value(self, lon, lat):
        if not self.levels:
            return None
        if len(self.levels) == 1:
            return self.levels[0]
        ncols, nrows = int(360 / CELL_DEG), int(180 / CELL_DEG)
        ci, cj = self._cell(lon, lat)
        coslat = max(math.cos(math.radians(lat)), 0.05)
        best: Dict[float, float] = {}
        ring = 0
        while ring <= max(ncols, nrows):
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if max(abs(i - ci), abs(j - cj)) != ring or not 0 <= j < nrows:
                        continue
                    for level, x1, y1, x2, y2 in self._cells.get((i % ncols, j), ()):
                        d = _seg_dist(lon, lat, x1, y1, x2, y2, coslat)
                        if d < best.get(level, 1e9):
                            best[level] = d
            # Stop once the nearest level and both of its neighbours are settled
            # (everything closer than the searched radius has been seen).
            searched = ring * CELL_DEG * coslat
            if len(best) >= 2 and sorted(best.values())[1] <= searched:
                break
            ring += 1
        if not best:
            return None
        l1 = min(best, key=best.get)
        k = self.levels.index(l1)
        neighbours = [self.levels[n] for n in (k - 1, k + 1) if 0 <= n < len(self.levels) and self.levels[n] in best]
        if not neighbours:
            return l1
        l2 = min(neighbours, key=lambda lv: best[lv])
        d1, d2 = best[l1], best[l2]
        if d1 + d2 == 0:
            return l1
        return (l1 * d2 + l2 * d1) / (d1 + d2)


def _seg_dist(px, py, x1, y1, x2, y2, coslat):
    """Distance (degrees of latitude) from P to a segment, locally flattened."""
    def dx(a):
        d = (a - px + 180) % 360 - 180
        return d * coslat
    ax, ay, bx, by = dx(x1), y1 - py, dx(x2), y2 - py
    vx, vy = bx - ax, by - ay
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / L))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(cx, cy)


# -------------------------------------------------------------- the physics
def sec_incidence(hop_km, height_km):
    """sec(i) at the layer for a single hop of hop_km (takeoff clamped >= 0)."""
    beta = max(0.0, _hops.takeoff_deg(hop_km, 1, height_km))
    s = R * math.cos(math.radians(beta)) / (R + height_km)
    return 1.0 / math.sqrt(max(1e-9, 1 - s * s))


def hop_muf(hop_km, mufd, hmf2):
    """MUF (MHz) for one hop of hop_km, scaled from MUF(3000)."""
    return mufd * sec_incidence(hop_km, hmf2) / sec_incidence(3000.0, hmf2)


NUDGE_KM = 600.0          # e-folding distance of an ionosonde's correction
MIN_CONFIDENCE = 50.0     # KC2G confidence score (0-100) to trust an ionosonde


def _gc_km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, a)))


@dataclass
class Conditions:
    """KC2G maps at one time, nudged towards nearby current ionosondes.

    Contours are coarse (MUF(3000) levels stop at 28 MHz), so near a trusted
    ionosonde the map is corrected by (measured - map) at the ionosonde,
    weighted by exp(-(distance / NUDGE_KM)^2).
    """
    time: Optional[_dt.datetime]
    mufd: ContourField
    fof2: ContourField
    hmf2: ContourField
    ionosondes: List[Ionosonde] = field(default_factory=list)

    def __post_init__(self):
        self._residuals = []   # (lon, lat, dfof2, dhmf2, dmufd)
        for s in self.ionosondes:
            if s.confidence is None or s.confidence < MIN_CONFIDENCE:
                continue
            f2, h, m = self._map(s.lon, s.lat)
            res = tuple(None if (meas is None or mp is None) else meas - mp
                        for meas, mp in ((s.fof2, f2), (s.hmf2, h), (s.mufd, m)))
            if any(r is not None for r in res):
                self._residuals.append((s.lon, s.lat) + res)

    def _map(self, lon, lat):
        return self.fof2.value_at(lon, lat), self.hmf2.value_at(lon, lat), self.mufd.value_at(lon, lat)

    def at(self, lon, lat):
        """(foF2, hmF2, MUF(3000)) at a point; None where a map has no value."""
        vals = list(self._map(lon, lat))
        if self._residuals and all(v is not None for v in vals):
            for k in range(3):
                num = den = 0.0
                for rlon, rlat, *res in self._residuals:
                    if res[k] is None:
                        continue
                    w = math.exp(-(_gc_km(lon, lat, rlon, rlat) / NUDGE_KM) ** 2)
                    num, den = num + w * res[k], den + w
                if den > 1e-6:
                    # weighted mean residual, faded by the strongest weight
                    vals[k] += (num / den) * min(1.0, den)
        return tuple(vals)


@dataclass
class PathIono:
    status: str
    hops: Optional[int] = None
    takeoff_deg: Optional[float] = None
    hmf2_km: Optional[float] = None          # mean over reflection points
    fof2_mhz: Optional[float] = None         # lowest over reflection points
    muf_mhz: Optional[float] = None          # path MUF (lowest hop MUF)
    points: List[tuple] = field(default_factory=list)  # (lon, lat, fof2, hmf2, mufd, hop_muf)


def analyse_path(lon1, lat1, lon2, lat2, freq_mhz, cond: Conditions,
                 min_takeoff_deg=_hops.MIN_TAKEOFF_DEG) -> PathIono:
    """Hops, takeoff and MUF for a path using the layer at its reflection points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d = 2 * R * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2)
                                    * math.sin(math.radians(lon2 - lon1) / 2) ** 2))
    if d < 1:
        return PathIono(STATUS_NO_DATA)
    height = _hops.F2_HEIGHT_KM
    n = None
    for _ in range(3):       # hop count depends on height, height on where the hops land
        n_new, _ = _hops.likely_hops(d, height, min_takeoff_deg)
        if n_new is None:
            return PathIono(STATUS_NO_DATA)
        pts = _hops.reflection_points(lon1, lat1, lon2, lat2, n_new)
        samples = [cond.at(x, y) for x, y in pts]
        if any(v is None for s in samples for v in s):
            return PathIono(STATUS_NO_DATA, n_new, round(_hops.takeoff_deg(d, n_new, height), 1))
        new_height = sum(s[1] for s in samples) / len(samples)
        if n_new == n and abs(new_height - height) < 5:
            break
        n, height = n_new, new_height
    n, takeoff = _hops.likely_hops(d, height, min_takeoff_deg)
    pts = _hops.reflection_points(lon1, lat1, lon2, lat2, n)
    rows = []
    for x, y in pts:
        f2, h, mufd = cond.at(x, y)
        rows.append((x, y, f2, h, mufd, hop_muf(d / n, mufd, h)))
    muf = min(r[5] for r in rows)
    return PathIono(STATUS_F2 if freq_mhz <= muf else STATUS_ABOVE, n, takeoff,
                    round(sum(r[3] for r in rows) / n, 1), round(min(r[2] for r in rows), 2),
                    round(muf, 2), rows)
