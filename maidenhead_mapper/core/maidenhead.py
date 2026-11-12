"""Maidenhead locator math.

Pure Python: no QGIS imports, so it can be unit-tested with plain pytest.

Locator levels (pairs of characters):
    field              2 chars  A-R A-R   20 deg lon x 10 deg lat
    square             4 chars  0-9 0-9    2 deg lon x  1 deg lat
    subsquare          6 chars  a-x a-x    5' lon x 2.5' lat
    extended square    8 chars  0-9 0-9   30" lon x 15" lat
    extended subsq.   10 chars  a-x a-x   1.25" lon x 0.625" lat

The grid always covers exactly -180..180 lon / -90..90 lat. It is never
derived from a basemap extent: Web Mercator stops at +/-85.0511 deg, which
shifted the original hand-built grid ~5 deg south.
"""

from __future__ import annotations

import math
import re
from typing import Iterator, Optional, Tuple

WORLD = (-180.0, -90.0, 180.0, 90.0)  # west, south, east, north

# (lon size, lat size, number of divisions, alphabet) for each pair.
_DIGITS, _LETTERS24 = "0123456789", "ABCDEFGHIJKLMNOPQRSTUVWX"
_PAIRS = (
    (20.0, 10.0, 18, "ABCDEFGHIJKLMNOPQR"),
    (2.0, 1.0, 10, _DIGITS),
    (2.0 / 24, 1.0 / 24, 24, _LETTERS24),
    (2.0 / 240, 1.0 / 240, 10, _DIGITS),
    (2.0 / 5760, 1.0 / 5760, 24, _LETTERS24),
)

FIELD, SQUARE, SUBSQUARE, EXT_SQUARE, EXT_SUBSQUARE = 2, 4, 6, 8, 10
PRECISIONS = (FIELD, SQUARE, SUBSQUARE, EXT_SQUARE, EXT_SUBSQUARE)
_VALID = re.compile(r"^[A-R]{2}(?:[0-9]{2}(?:[A-X]{2}(?:[0-9]{2}(?:[A-X]{2})?)?)?)?$")

EARTH_RADIUS_KM = 6371.0088

Bounds = Tuple[float, float, float, float]  # west, south, east, north


def normalize(locator: str) -> str:
    """Upper-case and strip a locator (WSJT-X writes subsquares lower-case)."""
    return (locator or "").strip().upper()


def is_valid(locator: str) -> bool:
    """True for a well-formed 2, 4, 6, 8 or 10 character locator (any case)."""
    return bool(_VALID.match(normalize(locator)))


def truncate(locator: str, max_chars: Optional[int]) -> str:
    """Normalize and cap a valid locator at `max_chars` (None = keep all)."""
    loc = normalize(locator)
    if max_chars is not None:
        if max_chars not in PRECISIONS:
            raise ValueError(f"max_chars must be one of {PRECISIONS}")
        loc = loc[:max_chars]
    return loc


def display(locator: str) -> str:
    """Conventional display case: CN85pl, CN85pl12ab."""
    loc = normalize(locator)
    return loc[:4] + loc[4:6].lower() + loc[6:8] + loc[8:10].lower()


def bounds(locator: str) -> Bounds:
    """Return (west, south, east, north) of the cell named by `locator`."""
    loc = normalize(locator)
    if not _VALID.match(loc):
        raise ValueError(f"Invalid Maidenhead locator: {locator!r}")
    west, south = WORLD[0], WORLD[1]
    lon_size = lat_size = 0.0
    for i in range(len(loc) // 2):
        lon_size, lat_size, _, alphabet = _PAIRS[i]
        west += alphabet.index(loc[2 * i]) * lon_size
        south += alphabet.index(loc[2 * i + 1]) * lat_size
    return (west, south, west + lon_size, south + lat_size)


def center(locator: str) -> Tuple[float, float]:
    """Return (lon, lat) of the centre of the cell."""
    w, s, e, n = bounds(locator)
    return ((w + e) / 2.0, (s + n) / 2.0)


def to_locator(lat: float, lon: float, precision: int = SQUARE) -> str:
    """Convert a lat/lon to a locator of `precision` characters (2-10, even)."""
    if precision not in PRECISIONS:
        raise ValueError(f"precision must be one of {PRECISIONS}")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range: {lat}")
    # Wrap longitude into [-180, 180); the north pole belongs to the top row.
    lon = ((lon + 180.0) % 360.0) - 180.0
    x = lon - WORLD[0]
    y = min(lat - WORLD[1], 180.0 - 1e-9)
    out = []
    for i in range(precision // 2):
        lon_size, lat_size, n, alphabet = _PAIRS[i]
        xi = min(int(x // lon_size), n - 1)
        yi = min(int(y // lat_size), n - 1)
        out.append(alphabet[xi] + alphabet[yi])
        x -= xi * lon_size
        y -= yi * lat_size
    return display("".join(out))


SEAM_INSET_DEG = 1e-6   # ~0.1 m: keeps cell edges off any projection's seam meridian


def cell_ring(cell: Bounds, step_deg: float = 1.0, inset_deg: float = SEAM_INSET_DEG):
    """Closed ring [(lon, lat), ...] for a cell, for drawing it as a polygon.

    Edges get a vertex every `step_deg` so they follow curved meridians and
    parallels in other projections (4 corners give straight chords that fall
    short of the map edge). East and west edges move `inset_deg` inwards: a
    field boundary can be exactly a projection's seam (60°E for a map centred
    on 120°W), where PROJ may put the edge on either side of the map.
    """
    w, s, e, n = cell
    w, e = w + inset_deg, e - inset_deg

    def steps(a, b):
        k = max(1, int(math.ceil(abs(b - a) / step_deg - 1e-9)))
        return [a + (b - a) * i / k for i in range(k)]
    ring = [(lon, s) for lon in steps(w, e)]            # south edge, west to east
    ring += [(e, lat) for lat in steps(s, n)]           # east edge, south to north
    ring += [(lon, n) for lon in steps(e, w)]           # north edge, east to west
    ring += [(w, lat) for lat in steps(n, s)]           # west edge, north to south
    ring.append(ring[0])
    return ring


def iter_cells(precision: int, extent: Optional[Bounds] = None) -> Iterator[Tuple[str, Bounds]]:
    """Yield (locator, bounds) for every cell of `precision` (2 or 4) that
    intersects `extent` (west, south, east, north). Defaults to the world."""
    if precision not in (FIELD, SQUARE):
        raise ValueError("grid generation supports precision 2 or 4")
    w, s, e, n = extent or WORLD
    w, s = max(w, WORLD[0]), max(s, WORLD[1])
    e, n = min(e, WORLD[2]), min(n, WORLD[3])
    if w >= e or s >= n:
        return
    lon_size = 20.0 if precision == FIELD else 2.0
    lat_size = 10.0 if precision == FIELD else 1.0
    c0 = int((w - WORLD[0]) // lon_size)
    c1 = min(int(math.ceil((e - WORLD[0]) / lon_size)), int(360 / lon_size))
    r0 = int((s - WORLD[1]) // lat_size)
    r1 = min(int(math.ceil((n - WORLD[1]) / lat_size)), int(180 / lat_size))
    for c in range(c0, c1):
        for r in range(r0, r1):
            cw = WORLD[0] + c * lon_size
            cs = WORLD[1] + r * lat_size
            loc = to_locator(cs + lat_size / 2.0, cw + lon_size / 2.0, precision)
            yield loc, (cw, cs, cw + lon_size, cs + lat_size)


def distance_bearing(from_locator: str, to_locator_: str) -> Tuple[float, float]:
    """Great-circle distance (km) and initial bearing (deg, 0-360) between the
    centres of two locators."""
    lon1, lat1 = (math.radians(v) for v in center(from_locator))
    lon2, lat2 = (math.radians(v) for v in center(to_locator_))
    dlon = lon2 - lon1
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    dist = 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return dist, bearing


def great_circle(lon1: float, lat1: float, lon2: float, lat2: float, segments: int = 32):
    """Points along the great circle between two points, as a list of parts.

    Each part is a list of (lon, lat). There are two parts when the path
    crosses the antimeridian, so it doesn't streak across the whole map.
    """
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2
                                + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d < 1e-9:
        return [[(lon1, lat1), (lon2, lat2)]]
    pts = []
    for i in range(segments + 1):
        f = i / segments
        a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        pts.append((math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))))
    pts[0], pts[-1] = (lon1, lat1), (lon2, lat2)
    parts, part = [], [pts[0]]
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        if abs(xb - xa) > 180:  # crossing: interpolate the latitude at +/-180
            edge = 180.0 if xa > 0 else -180.0
            xb_unwrapped = xb + (360 if xa > 0 else -360)
            t = (edge - xa) / (xb_unwrapped - xa)
            ym = ya + t * (yb - ya)
            part.append((edge, ym))
            parts.append(part)
            part = [(-edge, ym)]
        part.append((xb, yb))
    parts.append(part)
    return parts
