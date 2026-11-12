"""Summaries of many exchanges: grid-pair corridors and per-station facts. Pure Python.

A corridor is every exchange between two grid cells (a field, a square or a
block of 2x2 squares) on one band. Single FT8 reports fade by 20-30 dB, so
the useful numbers are medians over many exchanges:

* a_to_b_db: median SNR of signals sent from cell A and heard in cell B
  (the b_hears_a reports, and a_hears_b of exchanges stored the other way
  round);
* b_to_a_db: the reverse;
* diff_db: median, over exchanges with both reports, of (A->B minus B->A).
  Path and antennas cancel within an exchange (see core/balance.py), so a
  corridor that is lopsided over many exchanges says the stations at one
  end are louder and/or deafer than at the other.

Cells are put in alphabetical order (cell_a < cell_b), which for different
fields is roughly west to east. Exchanges within one cell have no line to
draw and are counted separately.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from . import maidenhead as mh
from .es import MODE_ABOVE, MODE_ES, MODE_F2  # noqa: F401  (re-exported for the algorithm)
from .iono import STATUS_ABOVE

FIELD, SQUARE, BLOCK = "field", "square", "2x2"
CELL_SIZES = (FIELD, BLOCK, SQUARE)


@dataclass
class Record:
    """One exchange, as read from an exchange layer."""
    call_a: str
    grid_a: str
    call_b: str
    grid_b: str
    band: str = ""
    a_hears_b: Optional[int] = None     # SNR of B's signal at A
    b_hears_a: Optional[int] = None     # SNR of A's signal at B
    first_utc: Optional[datetime] = None
    f2_status: Optional[str] = None
    mode_guess: Optional[str] = None


def cell(grid: str, size: str) -> Optional[str]:
    """Cell name for a locator; None if it is too short for the size.

    2x2 blocks are named by their south-west square (EM78 -> EM68)."""
    g = mh.normalize(grid)
    if not mh.is_valid(g):
        return None
    if size == FIELD:
        return g[:2]
    if len(g) < 4:
        return None
    if size == SQUARE:
        return g[:4]
    if size == BLOCK:
        return g[:2] + str(int(g[2]) // 2 * 2) + str(int(g[3]) // 2 * 2)
    raise ValueError(f"unknown cell size {size!r}")


def cell_center(name: str, size: str) -> Tuple[float, float]:
    if size == BLOCK:
        w, s, _, _ = mh.bounds(name)
        return w + 2.0, s + 1.0
    return mh.center(name)


def cell_distance_km(a: str, b: str, size: str) -> float:
    import math
    (lon1, lat1), (lon2, lat2) = cell_center(a, size), cell_center(b, size)
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * mh.EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _median(values):
    return round(float(statistics.median(values)), 1) if values else None


@dataclass
class Corridor:
    cell_a: str
    cell_b: str
    band: str
    exchanges: int = 0
    calls_a: set = field(default_factory=set)
    calls_b: set = field(default_factory=set)
    a_to_b: List[int] = field(default_factory=list)
    b_to_a: List[int] = field(default_factory=list)
    diffs: List[int] = field(default_factory=list)
    times: List[datetime] = field(default_factory=list)
    status: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bands: set = field(default_factory=set)

    @property
    def a_to_b_db(self):
        return _median(self.a_to_b)

    @property
    def b_to_a_db(self):
        return _median(self.b_to_a)

    @property
    def diff_db(self):
        return _median(self.diffs)

    @property
    def snr_db(self):
        """Median of every report on the corridor, both directions."""
        return _median(self.a_to_b + self.b_to_a)

    @property
    def first_utc(self):
        return min(self.times) if self.times else None

    @property
    def last_utc(self):
        return max(self.times) if self.times else None

    @property
    def hours_active(self):
        return len({t.replace(minute=0, second=0, microsecond=0) for t in self.times})

    def label(self):
        def db(v):
            return "–" if v is None else f"{v:+.0f}"
        return f"{self.exchanges}×  {db(self.a_to_b_db)}/{db(self.b_to_a_db)}"


@dataclass
class Summary:
    corridors: List[Corridor]
    same_cell: int = 0          # exchanges within one cell (no line)
    no_grid: int = 0            # exchanges without a usable grid at either end
    outside: int = 0            # outside the time window


def summarize(records: Iterable[Record], size: str = SQUARE, per_band: bool = True,
              start: Optional[datetime] = None, end: Optional[datetime] = None,
              min_exchanges: int = 1) -> Summary:
    """Group exchanges into corridors (see module docstring)."""
    groups: Dict[tuple, Corridor] = {}
    same = no_grid = outside = 0
    for r in records:
        if r.first_utc is not None and ((start and r.first_utc < start) or (end and r.first_utc > end)):
            outside += 1
            continue
        ca, cb = cell(r.grid_a or "", size), cell(r.grid_b or "", size)
        if not (ca and cb):
            no_grid += 1
            continue
        if ca == cb:
            same += 1
            continue
        a_to_b, b_to_a = r.b_hears_a, r.a_hears_b
        calls_a, calls_b = r.call_a, r.call_b
        if cb < ca:
            ca, cb = cb, ca
            a_to_b, b_to_a = b_to_a, a_to_b
            calls_a, calls_b = calls_b, calls_a
        band = (r.band or "") if per_band else ""
        c = groups.get((ca, cb, band))
        if c is None:
            c = groups[(ca, cb, band)] = Corridor(ca, cb, band)
        c.exchanges += 1
        if r.band:
            c.bands.add(r.band)
        c.calls_a.add(calls_a.upper())
        c.calls_b.add(calls_b.upper())
        if a_to_b is not None:
            c.a_to_b.append(a_to_b)
        if b_to_a is not None:
            c.b_to_a.append(b_to_a)
        if a_to_b is not None and b_to_a is not None:
            c.diffs.append(a_to_b - b_to_a)
        if r.first_utc is not None:
            c.times.append(r.first_utc)
        mode = _mode(r)
        if mode:
            c.status[mode] += 1
    out = [c for c in groups.values() if c.exchanges >= min_exchanges]
    out.sort(key=lambda c: (-c.exchanges, c.band, c.cell_a, c.cell_b))
    return Summary(out, same, no_grid, outside)


def _mode(r: Record) -> Optional[str]:
    """'F2', 'Es', 'above MUF' or 'no data' from Check paths' fields (mode_guess preferred)."""
    if r.mode_guess:
        return r.mode_guess
    if r.f2_status:
        return {STATUS_ABOVE: MODE_ABOVE}.get(r.f2_status, r.f2_status)
    return None


@dataclass
class StationFacts:
    call: str
    partners: int
    median_km: Optional[float]
    max_km: Optional[float]
    farthest: Optional[str]
    bands: str
    heard_db: Optional[float]       # median SNR others reported for this station's signal
    hears_db: Optional[float]       # median SNR this station reported for others


def station_facts(records: Iterable[Record], per_band: bool = True) -> Dict[Tuple[str, str], StationFacts]:
    """Per (band key, call): partners, distances, bands and median reports both ways."""
    partners = defaultdict(set)
    dist = defaultdict(list)
    far = {}
    bands = defaultdict(set)
    heard = defaultdict(list)
    hears = defaultdict(list)
    for r in records:
        key = (r.band or "") if per_band else ""
        a, b = r.call_a.upper(), r.call_b.upper()
        d = None
        if r.grid_a and r.grid_b and mh.is_valid(r.grid_a) and mh.is_valid(r.grid_b):
            d = mh.distance_bearing(r.grid_a, r.grid_b)[0]
        for me, other, heard_v, hears_v in ((a, b, r.b_hears_a, r.a_hears_b), (b, a, r.a_hears_b, r.b_hears_a)):
            k = (key, me)
            if other not in partners[k] and d is not None:
                dist[k].append(d)
                if d > far.get(k, (-1, None))[0]:
                    far[k] = (d, other)
            partners[k].add(other)
            if r.band:
                bands[k].add(r.band)
            if heard_v is not None:
                heard[k].append(heard_v)
            if hears_v is not None:
                hears[k].append(hears_v)
    out = {}
    for k in partners:
        ds = dist.get(k, [])
        out[k] = StationFacts(
            k[1], len(partners[k]), round(statistics.median(ds)) if ds else None,
            round(max(ds)) if ds else None, far.get(k, (None, None))[1],
            ",".join(sorted(bands[k], key=_band_order)), _median(heard[k]), _median(hears[k]))
    return out


def _band_order(name):
    from .bands import BAND_NAMES
    return BAND_NAMES.index(name) if name in BAND_NAMES else len(BAND_NAMES)


# --- over time ---------------------------------------------------------------

BOTH, A_TO_B, B_TO_A, NO_REPORTS = "both ways", "A→B only", "B→A only", "no reports"


def seen(c: Corridor) -> str:
    """Which directions the reports show working: B reported A (A->B) and/or A reported B."""
    ab, ba = bool(c.a_to_b), bool(c.b_to_a)
    return BOTH if ab and ba else A_TO_B if ab else B_TO_A if ba else NO_REPORTS


def bin_floor(t: datetime, hours: int) -> datetime:
    """Start of the bin holding t: whole UTC hours, or UTC days when hours == 24."""
    t = t.replace(minute=0, second=0, microsecond=0)
    if hours >= 24:
        return t.replace(hour=0)
    return t.replace(hour=t.hour - t.hour % hours)


@dataclass
class CellActivity:
    cell: str
    exchanges: int = 0
    calls: set = field(default_factory=set)
    partners: set = field(default_factory=set)      # other cells it exchanged with


@dataclass
class TimeBin:
    start: datetime
    end: datetime
    summary: Summary
    cells: Dict[str, CellActivity]
    records: int = 0


def over_time(records: Iterable[Record], size: str = FIELD, hours: int = 1,
              start: Optional[datetime] = None, end: Optional[datetime] = None,
              min_exchanges: int = 1, per_band: bool = False) -> List[TimeBin]:
    """Corridors and active cells per time bin (bins of `hours`, 24 = days).

    Exchanges are binned by first_utc; those without a time or outside
    start..end are left out. Bins without exchanges are left out too.
    """
    from datetime import timedelta
    groups = defaultdict(list)
    for r in records:
        if r.first_utc is None or (start and r.first_utc < start) or (end and r.first_utc > end):
            continue
        groups[bin_floor(r.first_utc, hours)].append(r)
    out = []
    step = timedelta(hours=24 if hours >= 24 else hours)
    for b in sorted(groups):
        rs = groups[b]
        cells: Dict[str, CellActivity] = {}
        for r in rs:
            ca, cb = cell(r.grid_a or "", size), cell(r.grid_b or "", size)
            for c, other, call in ((ca, cb, r.call_a), (cb, ca, r.call_b)):
                if not c:
                    continue
                act = cells.get(c)
                if act is None:
                    act = cells[c] = CellActivity(c)
                act.exchanges += 1
                act.calls.add(call.upper())
                if other and other != c:
                    act.partners.add(other)
        out.append(TimeBin(b, b + step, summarize(rs, size, per_band, min_exchanges=min_exchanges), cells, len(rs)))
    return out
