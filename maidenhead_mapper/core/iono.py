"""Ionosphere conditions along HF paths, from KC2G's maps. Pure Python.

KC2G (https://prop.kc2g.com) publishes the current state of the F2 layer as
contour maps (GeoJSON LineStrings with a `level-value`):
    mufd   MUF(3000)F2, MHz: highest frequency F2 supports on a 3,000 km hop
    fof2   foF2, MHz: critical (vertical-incidence) frequency
    hmf2   hmF2, km: height of the F2 peak
built from GIRO/INGV ionosondes (weighted by confidence) over the IRI-2020
model, plus the ionosondes themselves (stations.json). Updated about every
15 minutes; there is no history.

For a path we report the likely hop count and takeoff angle (fewest hops
with takeoff >= min_takeoff_deg off hmF2 measured at the reflection points;
see hops.py), and the F2 basic MUF following ITU-R P.533-14 (08/2019),
https://www.itu.int/rec/R-REC-P.533/en, Part 1:

  D <= dmax  (§3.5.1.1; Table 1a "0 < D <= 2 000" and "2 000 < D <= dmax":
             one control point, the path mid-point M)
      lowest-order mode n0: fewest equal hops no longer than the hop at 3 deg
      elevation off the mirror height hr = 1490 / M(3000)F2 - 176 km (<= 500,
      eq. 2), and no longer than 4 000 km; path MUF = n0F2(D)MUF at M for
      hop length d = D / n0 (eq. 3):
          MUF = [1 + (Cd / C3000)(B - 1)] foF2 + (fH / 2)(1 - d / dmax)
      Cd: eq. 4 (C3000 = Cd at 3 000 km); dmax: eq. 5 at M, capped at
      4 000 km; B: eq. 6; x = max(foF2 / foE, 2).
  dmax < D <= 9 000 km  (§3.5.1.2; Table 1a "D > dmax")
      the lower of F2(dmax)MUF (eq. 3 with d = dmax, the mid-point dmax) at
      two control points T + d0/2 and R - d0/2, d0 = D / n0 (ITU's reference
      code ITURHFProp evaluates eq. 3 there at min(d0, dmax) instead, which
      is lower when d0 < dmax; we follow the text). §5.4 keeps this
      basic MUF for 7 000-9 000 km too: only field strength is interpolated
      between the two models there, not the MUF.
  D > 9 000 km  (§5.3.1, eqs. 29-30; used alone beyond 9 000 km, §5.3)
      nM equal hops of at most 4 000 km, one more while the elevation off
      300 km is under 3 deg; hop length dM; control points dM/2 from each
      end; at each, fBM = fz + (f4 - fz) fD with f4 = 1.1 foF2 M(3000)F2,
      fz = foF2 + fH/2, fD the eq. 30 polynomial in dM; the lower is the MUF.

Inputs per point are what a conditions object gives: foF2, hmF2 and
MUF(3000), so M(3000)F2 = MUF(3000) / foF2. Simplifications: fH (electron
gyrofrequency, which eq. 3 needs at 300 km) comes from a centred dipole
(IGRF-13 2020 moment); foE (P.533 §3.2 uses P.1239) is ccir.foe() (simple CCIR
formula, R12 from the conditions or 100) at the condition's time, and x = 2
when there is no time. This is the *basic* MUF: P.533's operational MUF
(K factor, P.1240 Rop) and E modes / E-layer screening are not applied, and
higher-order F2 modes (§3.5.2) are not needed for "can F2 carry it at all".
A frequency above the path MUF isn't supported by F2: the path used
sporadic-E, scatter, or the map is wrong.
"""

import datetime as _dt
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ccir as _ccir
from . import hops as _hops

R = _hops.R_EARTH
CELL_DEG = 5.0
P533_MIN_ELEVATION_DEG = 3.0   # ITU-R P.533 minimum elevation for hop geometry
DMAX_CAP_KM = 4000.0           # §3.5.1.1: dmax is no greater than 4 000 km for the basic MUF
SHORT_MODEL_MAX_KM = 9000.0    # §3.5.1 / §5.4: eq. (3) basic MUF up to 9 000 km
LONG_HOP_KM = 4000.0           # §5.3.1: hops of 4 000 km or less ...
LONG_HEIGHT_KM = 300.0         # ... off a fixed 300 km
MUF4000_FACTOR = 1.1           # §5.3.1: F2(4000)MUF = 1.1 foF2 M(3000)F2
FD_COEFFS = (29.1996868566837e-6, 87.4376851991085e-9, 22.0776941764705e-12, 102.342990689362e-15,
             -92.4986988833091e-18, 25.8520201885984e-21, -2.40074637494790e-24)   # eq. (30), C0..C6
DIPOLE_NT = 29806.0            # IGRF-13 (2020) dipole field at the equator, Earth's surface
GYRO_MHZ_PER_NT = 27.992e-6    # electron gyrofrequency e B / (2 pi m)
DEFAULT_R12 = 100.0            # for foE when the conditions don't say
STATUS_F2, STATUS_ABOVE, STATUS_NO_DATA = "F2", "above basic MUF", "no data"
# Older plugin versions (<= 0.14) wrote these for the same status (f2_status / mode_guess).
LEGACY_ABOVE = ("above F2 MUF", "above MUF")


def normalize_status(status):
    """f2_status / mode_guess as read back from a layer: old "above F2 MUF" /
    "above MUF" values become STATUS_ABOVE, anything else is returned unchanged."""
    return STATUS_ABOVE if status in LEGACY_ABOVE else status


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


def gyro_mhz(lon, lat, height_km=300.0):
    """Electron gyrofrequency fH (MHz) at height_km from a centred dipole."""
    p, l = math.radians(lat), math.radians(lon)
    pp, lp = math.radians(_ccir.POLE_LAT), math.radians(_ccir.POLE_LON)
    sin_m = math.sin(p) * math.sin(pp) + math.cos(p) * math.cos(pp) * math.cos(l - lp)
    b = DIPOLE_NT * (R / (R + height_km)) ** 3 * math.sqrt(1 + 3 * max(-1.0, min(1.0, sin_m)) ** 2)
    return GYRO_MHZ_PER_NT * b


def p533_b(m3000, x):
    """P.533 eq. (6)."""
    return m3000 - 0.124 + (m3000 ** 2 - 4) * (0.0215 + 0.005 * math.sin(7.854 / x - 1.9635))


def p533_dmax(m3000, x):
    """P.533 eq. (5): maximum F2 hop length (km), not capped."""
    return 4780 + (12610 + 2140 / x ** 2 - 49720 / x ** 4 + 688900 / x ** 6) * (1 / p533_b(m3000, x) - 0.303)


def p533_cd(d, dmax):
    """P.533 eq. (4)."""
    z = 1 - 2 * d / dmax
    return 0.74 - 0.591 * z - 0.424 * z ** 2 - 0.090 * z ** 3 + 0.088 * z ** 4 + 0.181 * z ** 5 + 0.096 * z ** 6


def f2_muf(hop_km, fof2, m3000, fh, x=2.0, dmax=None):
    """P.533 eq. (3): F2 basic MUF (MHz) for a hop of hop_km (clipped to dmax).

    dmax defaults to eq. (5) capped at 4 000 km."""
    if dmax is None:
        dmax = min(p533_dmax(m3000, x), DMAX_CAP_KM)
    d = min(hop_km, dmax)
    return (1 + p533_cd(d, dmax) / p533_cd(3000.0, dmax) * (p533_b(m3000, x) - 1)) * fof2 + fh / 2 * (1 - d / dmax)


def mirror_height_km(m3000):
    """P.533 eq. (2): hr = 1490 / M(3000)F2 - 176 km, at most 500 km."""
    return min(1490.0 / m3000 - 176.0, 500.0)


def lowest_order_hops(distance_km, hr_km):
    """P.533 §3.5.1.1 n0: fewest equal hops no longer than the 3 deg hop off hr (and 4 000 km)."""
    dh = min(_hops.max_hop_km(hr_km, P533_MIN_ELEVATION_DEG), DMAX_CAP_KM)
    return max(1, math.ceil(distance_km / dh - 1e-9))


def long_path_hops(distance_km):
    """P.533 §5.3.1: (nM, dM): equal hops <= 4 000 km, elevation off 300 km >= 3 deg."""
    n = max(1, math.ceil(distance_km / LONG_HOP_KM - 1e-9))
    while _hops.takeoff_deg(distance_km, n, LONG_HEIGHT_KM) < P533_MIN_ELEVATION_DEG and n < _hops.MAX_HOPS:
        n += 1
    return n, distance_km / n


def distance_factor(hop_km):
    """P.533 eq. (30): fD, 0 at 0 km to 1 at 4 000 km."""
    v = 0.0
    for c in reversed(FD_COEFFS):
        v = v * hop_km + c
    return v * hop_km


def f2_muf_long(hop_km, fof2, m3000, fh):
    """P.533 eq. (29): fBM = fz + (f4 - fz) fD."""
    f4, fz = MUF4000_FACTOR * fof2 * m3000, fof2 + fh / 2
    return fz + (f4 - fz) * distance_factor(hop_km)


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
    hmf2_km: Optional[float] = None          # mean over the P.533 control points
    fof2_mhz: Optional[float] = None         # lowest over the control points
    muf_mhz: Optional[float] = None          # P.533 F2 basic MUF of the path (lowest control point)
    points: List[tuple] = field(default_factory=list)  # control points: (lon, lat, fof2, hmf2, mufd, muf)
    hops_muf: Optional[int] = None           # P.533 hop count the MUF is computed with (n0, or nM on long paths)


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
    # Fewest hops: in P.533 the basic MUF grows with hop length (eq. 3, Cd rises
    # from 0 at 0 km to 1 at dmax), so more, shorter hops never raise it. A path
    # above the MUF at the fewest hops is above it at any hop count.
    n, takeoff = _hops.likely_hops(d, height, min_takeoff_deg)
    cp = _p533_control_points(lon1, lat1, lon2, lat2, d, cond)
    if cp is None:
        return PathIono(STATUS_NO_DATA, n, takeoff)
    rows, n_muf = cp
    muf = min(r[5] for r in rows)
    return PathIono(STATUS_F2 if freq_mhz <= muf else STATUS_ABOVE, n, takeoff,
                    round(sum(r[3] for r in rows) / len(rows), 1), round(min(r[2] for r in rows), 2),
                    round(muf, 2), rows, n_muf)


def _sample(cond, lon, lat):
    """(foF2, hmF2, MUF(3000), M(3000)F2, fH, x) at a point, or None."""
    f2, h, mufd = cond.at(lon, lat)
    if f2 is None or h is None or mufd is None or f2 <= 0 or mufd <= 0:
        return None
    x = 2.0
    when = getattr(cond, "time", None)
    if when is not None:
        r12 = getattr(cond, "r12", None)
        fo_e = _ccir.foe(lat, lon, when, DEFAULT_R12 if r12 is None else r12)
        x = max(f2 / fo_e, 2.0)
    return f2, h, mufd, mufd / f2, gyro_mhz(lon, lat), x


def _p533_control_points(lon1, lat1, lon2, lat2, d, cond):
    """P.533-14 control points with their F2 basic MUF, and the hop count used:
    ([(lon, lat, foF2, hmF2, MUF(3000), MUF)], n), or None without data."""
    rows = []
    if d > SHORT_MODEL_MAX_KM:                                   # §5.3.1
        nm, dm = long_path_hops(d)
        for frac in (dm / 2 / d, 1 - dm / 2 / d):
            lon, lat = _along(lon1, lat1, lon2, lat2, frac)
            s = _sample(cond, lon, lat)
            if s is None:
                return None
            f2, h, mufd, m3, fh, _ = s
            rows.append((lon, lat, f2, h, mufd, f2_muf_long(dm, f2, m3, fh)))
        return rows, nm
    mlon, mlat = _along(lon1, lat1, lon2, lat2, 0.5)
    s = _sample(cond, mlon, mlat)
    if s is None:
        return None
    f2, h, mufd, m3, fh, x = s
    n0 = lowest_order_hops(d, mirror_height_km(m3))
    dmax = min(p533_dmax(m3, x), DMAX_CAP_KM)                     # at the mid-path control point
    if d <= dmax:                                                 # §3.5.1.1
        return [(mlon, mlat, f2, h, mufd, f2_muf(d / n0, f2, m3, fh, x, dmax))], n0
    d0 = d / n0                                                   # §3.5.1.2
    for frac in (d0 / 2 / d, 1 - d0 / 2 / d):
        lon, lat = _along(lon1, lat1, lon2, lat2, frac)
        s = _sample(cond, lon, lat)
        if s is None:
            return None
        f2, h, mufd, m3, fh, x = s
        rows.append((lon, lat, f2, h, mufd, f2_muf(dmax, f2, m3, fh, x, dmax)))
    return rows, n0


def _along(lon1, lat1, lon2, lat2, frac):
    """Point a fraction of the way along the great circle."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dd = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2
                                 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    a, b = math.sin((1 - frac) * dd) / math.sin(dd), math.sin(frac * dd) / math.sin(dd)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))
