"""Modelled antenna patterns (gain in dBi against azimuth and elevation).

Pure Python, no QGIS. Used by the A/B comparison's reference-antenna mode:
antenna B (or A) is a simple, well-modelled antenna, so

    A's absolute gain = B's modelled gain (at the path's bearing and takeoff)
                        + the measured A - B

Formats
-------
CSV (comma, semicolon or tab), one row per point, header row required:

    azimuth_deg, elevation_deg, gain_dbi [, frequency_mhz]

Headers are matched loosely ("Azimuth (deg)", "az", "El", "Gain dBi", "dBi",
"MHz" all work). Lines starting with # are skipped. With semicolons, a
decimal comma ("-2,5") is accepted. azimuth_deg is measured *clockwise*,
like a compass, from the antenna's reference direction; elevation_deg is
above the horizon. A frequency column lets one file hold several bands.

NEC-2 output (4nec2 .out files, nec2c, nec2dxs): every "RADIATION PATTERNS"
table, one per frequency. Columns THETA, PHI, then three gains (VERT., HOR.,
TOTAL, or MAJOR, MINOR, TOTAL); TOTAL is used by default. NEC's angles are
not compass angles:

    elevation = 90 - THETA          (THETA is measured from the zenith)
    azimuth   = -PHI (mod 360)      (PHI is counter-clockwise from +X;
                                     compass azimuth is clockwise)

so a pattern's azimuth 0 is the model's +X axis. NEC prints -999.99 dB for
no radiation; gains below MIN_DBI are raised to MIN_DBI.

Orientation: the compass bearing that the pattern's azimuth 0 (the model's
+X axis, or the CSV's reference direction) points to. For a model drawn with
+X east, use 90. Then

    pattern azimuth = (compass bearing - orientation) mod 360

EZNEC's own export formats are not read: export or copy its far-field
table into the CSV layout above.

Interpolation is bilinear in azimuth (wrapping through 360) and elevation.
A pattern with one elevation is taken as the same at every elevation; one
with one azimuth as the same in every direction (an omnidirectional
vertical). Both give a warning. Elevations outside the pattern's range get
no value (no extrapolation).
"""

import bisect
import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import List, Optional

MIN_DBI = -40.0          # floor for NEC's -999.99 and other deep nulls
FREQ_TOLERANCE = 0.10    # a decode more than 10 % from every pattern's frequency gets no gain
_TOL = 1e-6

NO_ELEVATION = "takeoff angle outside the pattern's elevations"
NO_FREQUENCY = "no pattern within 10% of the decode frequency"


class PatternError(ValueError):
    """A pattern file that can't be read."""


@dataclass
class Pattern:
    """One frequency's pattern on a full azimuth x elevation grid.

    azimuths: pattern azimuths (clockwise from the reference direction), sorted, in [0, 360)
    elevations: sorted, degrees above the horizon
    gains[i][j]: dBi at elevations[i], azimuths[j]"""
    azimuths: List[float]
    elevations: List[float]
    gains: List[List[float]]
    mhz: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def max_dbi(self):
        return max(max(row) for row in self.gains)

    def gain(self, azimuth_deg, elevation_deg):
        """Gain in dBi at a pattern azimuth (clockwise from the reference
        direction) and elevation, or None outside the elevation range."""
        els, azs = self.elevations, self.azimuths
        if len(els) == 1:
            i0 = i1 = 0
            te = 0.0
        else:
            if elevation_deg < els[0] - _TOL or elevation_deg > els[-1] + _TOL:
                return None
            e = min(max(elevation_deg, els[0]), els[-1])
            i1 = min(max(bisect.bisect_right(els, e), 1), len(els) - 1)
            i0 = i1 - 1
            te = (e - els[i0]) / (els[i1] - els[i0])
        if len(azs) == 1:
            j0 = j1 = 0
            ta = 0.0
        else:
            a = azimuth_deg % 360.0
            j = bisect.bisect_right(azs, a)
            if j == 0 or j == len(azs):       # between the last azimuth and the first + 360
                j0, j1 = len(azs) - 1, 0
                lo, hi = azs[-1], azs[0] + 360.0
                if a < azs[0]:
                    a += 360.0
            else:
                j0, j1 = j - 1, j
                lo, hi = azs[j0], azs[j1]
            ta = (a - lo) / (hi - lo)
        g = self.gains
        low = g[i0][j0] + (g[i0][j1] - g[i0][j0]) * ta
        high = g[i1][j0] + (g[i1][j1] - g[i1][j0]) * ta
        return low + (high - low) * te


def build(points, mhz=None, source="pattern"):
    """Pattern from (azimuth_cw_deg, elevation_deg, gain_dbi) points on a full grid."""
    grid = {}
    floored = 0
    for az, el, g in points:
        if not all(math.isfinite(v) for v in (az, el, g)):
            raise PatternError(f"{source}: a value isn't a number ({az}, {el}, {g})")
        if el < -90 - _TOL or el > 90 + _TOL:
            raise PatternError(f"{source}: elevation {el:g}° is outside −90…90°")
        a = round(az % 360.0, 6) % 360.0
        e = round(el, 6)
        if g < MIN_DBI:
            g, floored = MIN_DBI, floored + 1
        grid.setdefault((a, e), g)          # 0 and 360 are the same direction: keep the first
    if not grid:
        raise PatternError(f"{source}: no pattern points")
    azs = sorted({k[0] for k in grid})
    els = sorted({k[1] for k in grid})
    for e in (-90.0, 90.0):                 # at the zenith (nadir) every azimuth is the same direction
        known = [g for (a, e2), g in grid.items() if e2 == e]
        if known:
            for a in azs:
                grid.setdefault((a, e), known[0])
    missing = [(a, e) for e in els for a in azs if (a, e) not in grid]
    if missing:
        a, e = missing[0]
        raise PatternError(f"{source}: not a full azimuth × elevation grid: {len(missing)} of "
                           f"{len(azs) * len(els)} points missing (first: azimuth {a:g}°, elevation {e:g}°)")
    if len(azs) > 2:
        steps = [b - a for a, b in zip(azs, azs[1:])]
        wrap = azs[0] + 360.0 - azs[-1]
        if wrap > max(steps) + 1e-3:
            raise PatternError(f"{source}: azimuths cover only {azs[0]:g}–{azs[-1]:g}°. Give the full circle "
                               "(symmetry isn't assumed), or a single azimuth for an omnidirectional antenna.")
    warnings = []
    if len(els) == 1:
        warnings.append(f"only one elevation ({els[0]:g}°): gain is taken as the same at every elevation")
    if len(azs) == 1:
        warnings.append(f"only one azimuth ({azs[0]:g}°): gain is taken as the same in every direction")
    if floored:
        warnings.append(f"{floored} gain(s) below {MIN_DBI:g} dBi (NEC prints −999.99 for none) set to {MIN_DBI:g} dBi")
    gains = [[grid[(a, e)] for a in azs] for e in els]
    return Pattern(azs, els, gains, mhz, warnings)


@dataclass
class PatternSet:
    """Patterns for one antenna (one per frequency), and how it is oriented."""
    patterns: List[Pattern]
    orientation_deg: float = 0.0
    source: str = ""
    kind: str = ""                      # "CSV" or "NEC"

    @property
    def warnings(self):
        out = []
        for p in self.patterns:
            pre = f"{p.mhz:g} MHz: " if p.mhz and len(self.patterns) > 1 else ""
            out.extend(pre + w for w in p.warnings)
        return out

    def for_mhz(self, mhz=None):
        """The pattern nearest mhz (the first when mhz is unknown or the
        patterns carry no frequency), or None if none is within 10 %."""
        with_f = [p for p in self.patterns if p.mhz]
        if not mhz or not with_f:
            return self.patterns[0]
        p = min(with_f, key=lambda q: abs(q.mhz - mhz))
        return p if abs(p.mhz - mhz) <= FREQ_TOLERANCE * p.mhz else None

    def lookup(self, bearing_deg, elevation_deg, mhz=None):
        """(gain dBi or None, reason when None) towards a compass bearing at an elevation."""
        p = self.for_mhz(mhz)
        if p is None:
            return None, NO_FREQUENCY
        if elevation_deg is None:
            return None, NO_ELEVATION
        g = p.gain((bearing_deg - self.orientation_deg) % 360.0, elevation_deg)
        return (None, NO_ELEVATION) if g is None else (g, None)

    def gain(self, bearing_deg, elevation_deg, mhz=None):
        return self.lookup(bearing_deg, elevation_deg, mhz)[0]


# -- CSV ---------------------------------------------------------------------------
def _role(header):
    h = re.sub(r"[^a-z]", "", header.lower())
    if h.startswith("az") or h.startswith("bearing"):
        return "az"
    if h.startswith("el") or h.startswith("alt"):
        return "el"
    if "gain" in h or h.startswith("dbi"):
        return "gain"
    if h.startswith("freq") or h.startswith("mhz"):
        return "mhz"
    return None


def _number(text, decimal_comma):
    t = text.strip()
    if decimal_comma:
        t = t.replace(",", ".")
    return float(t)


def read_csv_text(text, source="CSV"):
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        raise PatternError(f"{source}: the file is empty")
    head = lines[0]
    delim = max(("\t", ";", ","), key=head.count)
    if head.count(delim) == 0:
        raise PatternError(f"{source}: needs a header row with columns azimuth_deg, elevation_deg, gain_dbi "
                           "separated by commas, semicolons or tabs")
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    cols = {}
    for i, name in enumerate(rows[0]):
        r = _role(name)
        if r and r not in cols:
            cols[r] = i
    need = [n for n, r in (("azimuth_deg", "az"), ("elevation_deg", "el"), ("gain_dbi", "gain")) if r not in cols]
    if need:
        raise PatternError(f"{source}: no column for {', '.join(need)} (found: {', '.join(rows[0])})")
    decimal_comma = delim != ","
    by_f = {}
    for n, row in enumerate(rows[1:], start=2):
        try:
            az, el, g = (_number(row[cols[k]], decimal_comma) for k in ("az", "el", "gain"))
            f = _number(row[cols["mhz"]], decimal_comma) if "mhz" in cols and row[cols["mhz"]].strip() else None
        except (ValueError, IndexError):
            raise PatternError(f"{source}: row {n} isn't three numbers: {delim.join(row)!r}") from None
        by_f.setdefault(f, []).append((az, el, g))
    pats = [build(pts, f, f"{source}" + (f" ({f:g} MHz)" if f else "")) for f, pts in by_f.items()]
    return PatternSet(pats, source=source, kind="CSV")


# -- NEC-2 output --------------------------------------------------------------------
_FREQ = re.compile(r"FREQUENCY\s*[=:]\s*([-+0-9.Ee]+)\s*MHZ", re.I)
_COLUMNS = {"total": 4, "vert": 2, "hor": 3}


def _floats(tokens):
    try:
        return [float(t) for t in tokens]
    except ValueError:
        return None


def read_nec_text(text, column="total", source="NEC output"):
    """Every RADIATION PATTERNS table in NEC-2 output, one Pattern per table."""
    if column not in _COLUMNS:
        raise PatternError(f"column must be one of {', '.join(_COLUMNS)}")
    lines = text.splitlines()
    mhz = None
    tables = []                                  # (mhz, header text, [(theta, phi, gain)])
    i = 0
    while i < len(lines):
        m = _FREQ.search(lines[i])
        if m:
            try:
                mhz = float(m.group(1))
            except ValueError:
                pass
        if "RADIATION PATTERNS" in lines[i].upper():
            header, pts = [], []
            i += 1
            while i < len(lines):
                tok = lines[i].split()
                vals = _floats(tok[:5]) if len(tok) >= 5 else None
                if vals is not None:
                    th, ph = vals[0], vals[1]
                    pts.append((th, ph, vals[_COLUMNS[column]]))
                elif pts:
                    break                            # end of the table
                else:
                    header.append(lines[i].upper())
                    if len(header) > 12:
                        break
                i += 1
            if pts:
                tables.append((mhz, " ".join(header), pts))
            continue
        i += 1
    if not tables:
        raise PatternError(f"{source}: no RADIATION PATTERNS table found. Is this NEC-2 output "
                           "(4nec2 .out, nec2c)? Add an RP card to the model.")
    pats = []
    for f, header, pts in tables:
        if column != "total" and "VERT" not in header:
            raise PatternError(f"{source}: this table has no VERT./HOR. gains (polarisation is major/minor axis)")
        conv = []
        for th, ph, g in pts:
            if th < 0:                           # theta -30, phi 0 is theta 30, phi 180
                th, ph = -th, ph + 180.0
            conv.append(((-ph) % 360.0, 90.0 - th, g))
        p = build(conv, f, f"{source}" + (f" ({f:g} MHz)" if f else ""))
        if "DIRECTIVE" in header:
            p.warnings.append("directive gains: losses (ground, wire, matching) are not included")
        pats.append(p)
    return PatternSet(pats, source=source, kind="NEC")


# -- any file ----------------------------------------------------------------------
def read_pattern(path, orientation_deg=0.0, column="total"):
    """A CSV or NEC-2 output file -> PatternSet. Raises PatternError for bad files."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise PatternError(f"can't read the pattern file: {exc}") from None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "RADIATION PATTERNS" in text.upper():
        ps = read_nec_text(text, column, name)
    else:
        if "\x00" in text:
            raise PatternError(f"{name}: a binary file, not a CSV or NEC output")
        ps = read_csv_text(text, name)
    ps.orientation_deg = float(orientation_deg or 0.0) % 360.0
    return ps
