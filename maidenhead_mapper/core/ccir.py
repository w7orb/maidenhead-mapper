"""CCIR (ITU-R P.1239) monthly-median F2 layer model. Pure Python, no QGIS.

The same foF2 / M(3000)F2 maps IRI uses: for each month, spherical-harmonic
style coefficients (Jones & Gallet) at sunspot numbers R12 = 0 and 100,
interpolated linearly in R12 (capped at 150, where foF2 saturates). The
evaluation is a port of IRI's GAMMA1. Coefficient files are IRI's
ccir11.asc .. ccir22.asc (month 1..12), read as given.

Also:
  modip    modified dip latitude from a 2.5 deg grid of IGRF values for
           2000 and 2025 (modip_grid.json, made with PyIRI), interpolated in
           space and time. A centred dipole is the fallback (up to ~25 deg
           off near the South Atlantic anomaly).
  hmF2     from M(3000)F2 by Bilitza et al. (1979):
           hmF2 = 1490 / (M + dM) - 176,
           dM = 0.18 / (foF2/foE - 1.4) + 0.096 (R12 - 25) / 150
  foE      simple CCIR daytime formula 0.9 [(180 + 1.44 R12) cos chi]^0.25,
           floored at night.

These are *monthly medians*: typical conditions for that month, time and
solar activity, not that day's weather (day-to-day foF2 varies ~20%).
"""

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

UMR = math.pi / 180
# GAMMA1 shapes: (harmonics, NQ, K1, M, MM)
_FOF2 = (6, (11, 11, 8, 4, 1, 0, 0, 0, 0), 9, 76, 13)
_M3000 = (4, (6, 7, 5, 2, 1, 0, 0), 7, 49, 9)
# check: terms = 1 + NQ[0] + 2 * sum(NQ[j] + 1 for j >= 1) must equal M (76 and 49)
N_F2, N_M3 = 13 * 76, 9 * 49
POLE_LAT, POLE_LON = 80.65, -72.68
R12_CAP = 150.0


@dataclass
class MonthCoeffs:
    f2: Tuple[List[float], List[float]]     # foF2 coefficients at R12 = 0, 100
    m3: Tuple[List[float], List[float]]     # M(3000)F2 coefficients at R12 = 0, 100


def parse(text) -> MonthCoeffs:
    """One ccirNN.asc file (Fortran 4E15.8; numbers can touch)."""
    vals = [float(x) for x in re.findall(r"[-+]?\d*\.\d+(?:[EeDd][-+]?\d+)?", text.replace("D", "E"))]
    need = 2 * N_F2 + 2 * N_M3
    if len(vals) < need:
        raise ValueError(f"CCIR file has {len(vals)} numbers, expected {need}")
    f2 = (vals[:N_F2], vals[N_F2:2 * N_F2])
    o = 2 * N_F2
    m3 = (vals[o:o + N_M3], vals[o + N_M3:o + 2 * N_M3])
    return MonthCoeffs(f2, m3)


def _gamma1(smodip, slat, slong, hour, shape, sfe):
    """IRI GAMMA1: evaluate a CCIR map at a place and UT hour."""
    iharm, nq, k1, m, mm = shape
    hou = (15.0 * hour - 180.0) * UMR
    s, c = [math.sin(hou)], [math.cos(hou)]
    for i in range(1, iharm):
        c.append(c[0] * c[i - 1] - s[0] * s[i - 1])
        s.append(c[0] * s[i - 1] + s[0] * c[i - 1])
    coef = []
    for i in range(m):
        mi = i * mm
        v = sfe[mi]
        for j in range(iharm):
            v += sfe[mi + 2 * j + 1] * s[j] + sfe[mi + 2 * j + 2] * c[j]
        coef.append(v)
    total = coef[0]
    ss = math.sin(smodip * UMR)
    s3 = ss
    xsinx = [1.0]
    for j in range(nq[0]):
        total += coef[1 + j] * ss
        xsinx.append(ss)
        ss *= s3
    xsinx.append(ss)
    np_ = nq[0]                      # 0-based index of the last coefficient used
    ss = math.cos(slat * UMR)
    s3 = ss
    for j in range(1, k1):
        s0 = slong * j * UMR
        s1, s2 = math.cos(s0), math.sin(s0)
        for l in range(nq[j] + 1):
            np_ += 1
            total += coef[np_] * xsinx[l] * ss * s1
            np_ += 1
            total += coef[np_] * xsinx[l] * ss * s2
        ss *= s3
    return total


_GRID = None


def _grid():
    global _GRID
    if _GRID is None:
        import json
        import os
        try:
            with open(os.path.join(os.path.dirname(__file__), "modip_grid.json"), encoding="utf-8") as f:
                _GRID = json.load(f)
        except (OSError, ValueError):
            _GRID = {}
    return _GRID


def modip(lat, lon, year=None):
    """Modified dip latitude (deg): IGRF grid, else dipole."""
    g = _grid()
    if g:
        step, nlon, nlat = g["step_deg"], g["nlon"], g["nlat"]
        x = ((lon + 180.0) % 360.0) / step
        y = (max(-90.0, min(90.0, lat)) + 90.0) / step
        i, j = min(int(x), nlon - 2), min(int(y), nlat - 2)
        fx, fy = x - i, y - j

        def at(grid):
            return ((grid[j][i] * (1 - fx) + grid[j][i + 1] * fx) * (1 - fy)
                    + (grid[j + 1][i] * (1 - fx) + grid[j + 1][i + 1] * fx) * fy)
        epochs = sorted((int(k), v) for k, v in g["epochs"].items())
        (y0, g0), (y1, g1) = epochs[0], epochs[-1]
        yr = y0 if year is None else max(y0, min(y1, year))
        w = (yr - y0) / (y1 - y0) if y1 != y0 else 0.0
        return at(g0) * (1 - w) + at(g1) * w
    return dipole_modip(lat, lon)


def dipole_modip(lat, lon):
    """Modified dip latitude (deg), tan(mu) = I / sqrt(cos(lat)), I from a centred dipole."""
    p, l, pp, lp = lat * UMR, lon * UMR, POLE_LAT * UMR, POLE_LON * UMR
    sin_m = math.sin(p) * math.sin(pp) + math.cos(p) * math.cos(pp) * math.cos(l - lp)
    mlat = math.asin(max(-1.0, min(1.0, sin_m)))
    dip = math.atan(2 * math.tan(mlat))                       # radians
    return math.degrees(math.atan(dip / math.sqrt(max(math.cos(p), 1e-6))))


def cos_zenith(lat, lon, when):
    doy = when.timetuple().tm_yday
    decl = 23.44 * UMR * math.sin(2 * math.pi * (284 + doy) / 365.0)
    ut = when.hour + when.minute / 60 + when.second / 3600
    ha = (ut + lon / 15.0 - 12.0) * 15.0 * UMR
    return math.sin(lat * UMR) * math.sin(decl) + math.cos(lat * UMR) * math.cos(decl) * math.cos(ha)


def foe(lat, lon, when, r12):
    cz = cos_zenith(lat, lon, when)
    return max(0.4, 0.9 * ((180 + 1.44 * r12) * max(cz, 0.0)) ** 0.25)


def hmf2_from_m3000(m3000, fof2, fo_e, r12):
    x = max(fof2 / fo_e, 1.7) if fo_e > 0 else 10.0
    dm = 0.18 / (x - 1.4) + 0.096 * (r12 - 25) / 150
    return 1490.0 / (m3000 + dm) - 176.0


def _month_weights(when):
    """Two (month 1..12, weight) pairs: linear between mid-month points."""
    mid = 15.5
    day = when.day + when.hour / 24
    m = when.month
    if day >= mid:
        nxt = m % 12 + 1
        days = 30.5
        w = (day - mid) / days
        return [(m, 1 - w), (nxt, w)]
    prv = (m - 2) % 12 + 1
    w = (mid - day) / 30.5
    return [(m, 1 - w), (prv, w)]


class Model:
    """CCIR maps for a set of months (dict month -> MonthCoeffs)."""

    def __init__(self, months: Dict[int, MonthCoeffs]):
        self.months = months

    def values(self, lon, lat, when, r12):
        """(foF2 MHz, M(3000)F2, hmF2 km) at a place and UTC time."""
        r = max(0.0, min(r12, R12_CAP)) / 100.0
        mu = modip(lat, lon, when.year + (when.timetuple().tm_yday - 1) / 365.25)
        slong = lon % 360
        hour = when.hour + when.minute / 60 + when.second / 3600
        f2 = m3 = 0.0
        for month, wgt in _month_weights(when):
            if wgt <= 0:
                continue
            mc = self.months[month]
            ff = [a * (1 - r) + b * r for a, b in zip(*mc.f2)]
            mm = [a * (1 - r) + b * r for a, b in zip(*mc.m3)]
            f2 += wgt * _gamma1(mu, lat, slong, hour, _FOF2, ff)
            m3 += wgt * _gamma1(mu, lat, slong, hour, _M3000, mm)
        h = hmf2_from_m3000(m3, f2, foe(lat, lon, when, r12), r12)
        return f2, m3, h

    def conditions(self, when, r12):
        """Something analyse_path() can use: .time and .at(lon, lat)."""
        return ModelConditions(self, when, r12)


class ModelConditions:
    def __init__(self, model, when, r12):
        self.model, self.time, self.r12 = model, when, r12
        self.ionosondes = []
        self._cache = {}

    def at(self, lon, lat):
        """(foF2, hmF2, MUF(3000)), like iono.Conditions.at."""
        key = (round(lon * 2), round(lat * 2))       # 0.5 deg
        if key not in self._cache:
            f2, m3, h = self.model.values(lon, lat, self.time, self.r12)
            self._cache[key] = (f2, h, f2 * m3)
        return self._cache[key]


def parse_silso(text):
    """SILSO CSV (SN_ms_tot / SN_m_tot: 'YYYY;MM;decimal year;value;...') -> {(y, m): value}.
    Missing values (-1) are left out."""
    out = {}
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 4:
            continue
        try:
            y, m, v = int(parts[0]), int(parts[1]), float(parts[3])
        except ValueError:
            continue
        if v >= 0:
            out[(y, m)] = v
    return out


def essn_at(data, when, window_h=24.0):
    """KC2G effective sunspot number near `when` (mean within window_h), or None.
    data: essn.json ({'24h': [{'time': epoch, 'ssn': ...}], ...})."""
    rows = (data or {}).get("24h") or []
    t = when.timestamp()
    vals = [r["ssn"] for r in rows if isinstance(r, dict) and r.get("ssn") is not None
            and abs(r.get("time", 0) - t) <= window_h * 3600]
    return sum(vals) / len(vals) if vals else None


def r12_for(when, smoothed=None, monthly=None, essn=None):
    """(R12, source) for a time: SILSO smoothed, else KC2G effective SSN (recent
    days), else SILSO monthly, else (None, '')."""
    key = (when.year, when.month)
    if smoothed and key in smoothed:
        return smoothed[key], "SILSO smoothed"
    e = essn_at(essn, when) if essn else None
    if e is not None:
        return e, "KC2G effective"
    if monthly and key in monthly:
        return monthly[key], "SILSO monthly"
    return None, ""
