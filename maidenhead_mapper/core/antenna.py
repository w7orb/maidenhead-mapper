"""Antenna A/B comparison from two receivers decoding the same signals.

Pure Python, no QGIS. When two receivers (two antennas, or two radios) decode
the same FT8/FT4 transmission in the same slot, the transmitter, its antenna
and the path are identical, so

    SNR(A) - SNR(B) = (antenna A gain - antenna B gain) towards that signal
                      + a fixed receiver offset (gain, noise floor)

Binned by direction, distance and estimated takeoff angle, the medians show
the *relative* pattern of the two antennas. The fixed offset shifts every
bin equally; the shape is what matters.

Decodes only one receiver got are counted too ("only_a", "only_b"). Below
the decode floor (about -20 dB) the weaker antenna doesn't report a low SNR,
it reports nothing, so a median over matched pairs alone understates its
disadvantage. decode_advantage = (only_a - only_b) / all decodes shows it.
Fading makes single pairs noisy (5 dB scatter is typical): trust bins with
about 100 pairs or more.

Takeoff angle is never observed: it is inferred from distance, layer height
and hop count. compare() takes an `angle_at` callable for that:

  geometric_angle      fixed F2 height (300 km), fewest geometric hops (default)
  iono_angle(...)      the layer height at the reflection points *at the decode
                       time* (KC2G or the CCIR model), hop count from it, and the
                       P.533 basic MUF. The fewest hops give the longest hops and
                       so the highest MUF: if they can't support the frequency,
                       more hops can't either. Then a 500-2,300 km path on 12m
                       and up is taken as sporadic-E (single hop off ~105 km) and
                       goes in its own "Es" takeoff bin; other paths above the
                       MUF keep their F2 angle (the basic MUF is conservative).

The angle is worked out per sender and 15-minute slot, not per station: the
same station is heard at different angles as hmF2 rises and falls through the
day, which is what eventually lets an elevation pattern be separated from
distance (TODO 15).
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import bands as _bands
from . import es as _es
from . import ft8msg
from . import hops as _hops
from . import iono as _iono
from . import maidenhead as mh

SECTORS = 16
DIST_EDGES = (0, 1000, 2000, 4000, 6000, 8000, 10000, 13000, 20100)
TAKEOFF_EDGES = (0, 3, 6, 10, 15, 25, 90)
ES_BIN = "Es (~105 km)"
GEOMETRIC, KC2G, MODEL = "geometric", "KC2G", "model"
MODE_F2, MODE_ABOVE, MODE_ES = "F2", "above MUF", "Es"
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


@dataclass
class Decode:
    slot: object          # hashable time key (datetime of the slot start)
    message: str
    snr: int
    sender: Optional[str] = None
    mhz: Optional[float] = None     # dial frequency, when known


@dataclass
class Match:
    sender: str
    snr_a: Optional[int]
    snr_b: Optional[int]
    slot: object = None
    mhz: Optional[float] = None

    @property
    def diff(self):
        return None if self.snr_a is None or self.snr_b is None else self.snr_a - self.snr_b


@dataclass
class Bin:
    label: str
    n_both: int = 0
    only_a: int = 0
    only_b: int = 0
    diffs: List[int] = field(default_factory=list)

    @property
    def median_db(self):
        return float(statistics.median(self.diffs)) if self.diffs else None

    @property
    def iqr_db(self):
        if len(self.diffs) < 4:
            return None
        q = statistics.quantiles(self.diffs, n=4)
        return q[0], q[2]

    @property
    def decode_advantage(self):
        total = self.n_both + self.only_a + self.only_b
        return (self.only_a - self.only_b) / total if total else None

    def add(self, m):
        if m.snr_a is not None and m.snr_b is not None:
            self.n_both += 1
            self.diffs.append(m.snr_a - m.snr_b)
        elif m.snr_a is not None:
            self.only_a += 1
        else:
            self.only_b += 1


def match(decodes_a, decodes_b):
    """Pair decodes of the same message in the same slot. Duplicate decodes of
    one message in a slot keep the strongest."""
    def index(ds):
        out = {}
        for d in ds:
            key = (d.slot, d.message.strip().upper())
            if key not in out or d.snr > out[key].snr:
                out[key] = d
        return out
    ia, ib = index(decodes_a), index(decodes_b)
    out = []
    for key in sorted(set(ia) | set(ib), key=lambda k: (str(k[0]), k[1])):
        da, db = ia.get(key), ib.get(key)
        sender = (da or db).sender
        if sender is None:
            p = ft8msg.parse_message(key[1])
            sender = p.sender if p else None
        if sender:
            out.append(Match(sender, da.snr if da else None, db.snr if db else None, key[0],
                             (da.mhz if da and da.mhz else None) or (db.mhz if db else None)))
    return out


def learn_grids(*decode_lists):
    """Callsign -> grid from every message carrying one, in either log."""
    grids = {}
    for ds in decode_lists:
        for d in ds:
            p = ft8msg.parse_message(d.message)
            if p and p.grid:
                grids[p.sender] = p.grid
    return grids


def _sector(bearing):
    width = 360.0 / SECTORS
    return int(((bearing + width / 2) % 360) // width)


def _band_label(edges, v, unit):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{lo:,}–{hi:,} {unit}"
    return f"{edges[-1]:,}+ {unit}"


@dataclass
class Angle:
    """Estimated takeoff for one path at one time."""
    takeoff_deg: Optional[float]
    hops: Optional[int]
    method: str = GEOMETRIC           # geometric / KC2G / model
    mode: str = MODE_F2               # F2 / above MUF / Es
    hmf2_km: Optional[float] = None
    muf_mhz: Optional[float] = None


def geometric_angle(lon1, lat1, lon2, lat2, distance_km, when=None, mhz=None):
    """Fixed 300 km F2, fewest hops. Sporadic-E only where it is near certain
    (6m and up at single-Es-hop range)."""
    band = _bands.band_for_mhz(mhz)
    if band in _es.VHF_BANDS and _es.classify(distance_km, band) == _es.HIGH:
        return Angle(round(_es.takeoff_deg(distance_km), 1), 1, GEOMETRIC, MODE_ES, _es.ES_HEIGHT_KM)
    n, t = _hops.likely_hops(distance_km)
    return Angle(t, n, GEOMETRIC)


def iono_angle(conditions_for):
    """An angle_at for compare() that uses the ionosphere at the decode time.

    conditions_for(when) -> (conditions, method): conditions has .at(lon, lat)
    -> (foF2, hmF2, MUF(3000)) and .time (iono.Conditions or
    ccir.ModelConditions), or is None when there is none for that time; those
    paths fall back to geometric_angle."""
    def angle_at(lon1, lat1, lon2, lat2, distance_km, when=None, mhz=None):
        cond, method = conditions_for(when) if when is not None else (None, None)
        if cond is None or not mhz:
            return geometric_angle(lon1, lat1, lon2, lat2, distance_km, when, mhz)
        p = _iono.analyse_path(lon1, lat1, lon2, lat2, mhz, cond)
        if p.status == _iono.STATUS_NO_DATA or p.takeoff_deg is None:
            return geometric_angle(lon1, lat1, lon2, lat2, distance_km, when, mhz)
        if _es.classify(distance_km, _bands.band_for_mhz(mhz), p.status) == _es.HIGH:
            return Angle(round(_es.takeoff_deg(distance_km), 1), 1, method, MODE_ES, _es.ES_HEIGHT_KM, p.muf_mhz)
        mode = MODE_F2 if p.status == _iono.STATUS_F2 else MODE_ABOVE
        return Angle(p.takeoff_deg, p.hops, method, mode, p.hmf2_km, p.muf_mhz)
    return angle_at


def _quarter_hour(t):
    try:
        return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)
    except (AttributeError, TypeError):
        return None


def _most_common(values):
    counts = defaultdict(int)
    for v in values:
        counts[v] += 1
    return max(counts, key=counts.get) if counts else None


@dataclass
class Comparison:
    total: Bin
    by_sector: Dict[int, Bin]
    by_distance: Dict[str, Bin]
    by_takeoff: Dict[str, Bin]
    by_station: Dict[str, Bin]
    placed: Dict[str, dict]     # call -> {grid, distance_km, bearing_deg, takeoff_deg, takeoff_300_deg, ...}
    unplaced: int
    methods: Dict[str, int] = field(default_factory=dict)   # matches per angle method
    modes: Dict[str, int] = field(default_factory=dict)     # matches per propagation mode


def compare(matches, grids, my_grid, angle_at=None):
    """Bin matched decodes by the sender's direction, distance and takeoff from my_grid.

    angle_at(lon1, lat1, lon2, lat2, distance_km, when, mhz) -> Angle; default
    geometric_angle. It is called once per sender, 15-minute slot and frequency.
    placed[call] gets the median takeoff over its decodes (and min/max), the
    old fixed-300 km value (takeoff_300_deg), hops, hmF2, method and mode."""
    angle_at = angle_at or geometric_angle
    total = Bin("all")
    sec = {k: Bin(COMPASS[k]) for k in range(SECTORS)}
    dist, take, stations = {}, {}, {}
    placed, unplaced = {}, 0
    angles = {}                                 # (sender, quarter hour, mhz) -> Angle
    per_station = defaultdict(list)             # sender -> [Angle], one per match
    methods, modes = defaultdict(int), defaultdict(int)
    home = mh.center(my_grid) if mh.is_valid(my_grid or "") else None
    for m in matches:
        total.add(m)
        stations.setdefault(m.sender, Bin(m.sender)).add(m)
        g = grids.get(m.sender)
        if not (g and home):
            unplaced += 1
            continue
        if m.sender not in placed:
            d, b = mh.distance_bearing(my_grid, g)
            placed[m.sender] = {"grid": g, "distance_km": round(d, 1), "bearing_deg": round(b, 1),
                                "takeoff_300_deg": _hops.likely_hops(d)[1]}
        info = placed[m.sender]
        key = (m.sender, _quarter_hour(m.slot), m.mhz)
        if key not in angles:
            angles[key] = angle_at(home[0], home[1], *mh.center(g), info["distance_km"], key[1], m.mhz)
        a = angles[key]
        per_station[m.sender].append(a)
        methods[a.method] += 1
        modes[a.mode] += 1
        sec[_sector(info["bearing_deg"])].add(m)
        dl = _band_label(DIST_EDGES, info["distance_km"], "km")
        dist.setdefault(dl, Bin(dl)).add(m)
        if a.mode == MODE_ES:
            take.setdefault(ES_BIN, Bin(ES_BIN)).add(m)
        elif a.takeoff_deg is not None:
            tl = _band_label(TAKEOFF_EDGES, a.takeoff_deg, "°")
            take.setdefault(tl, Bin(tl)).add(m)
    for call, info in placed.items():
        al = per_station.get(call) or []
        ts = [a.takeoff_deg for a in al if a.takeoff_deg is not None]
        hs = [a.hmf2_km for a in al if a.hmf2_km is not None and a.mode != MODE_ES]
        info["takeoff_deg"] = round(statistics.median(ts), 1) if ts else None
        info["takeoff_min_deg"] = min(ts) if ts else None
        info["takeoff_max_deg"] = max(ts) if ts else None
        info["hops"] = _most_common([a.hops for a in al if a.hops])
        info["hmf2_km"] = round(statistics.median(hs), 1) if hs else None
        info["method"] = _most_common([a.method for a in al]) or GEOMETRIC
        info["mode"] = _most_common([a.mode for a in al]) or MODE_F2
    order = lambda edges: {_band_label(edges, lo, "km" if edges is DIST_EDGES else "°"): i
                           for i, lo in enumerate(edges)}
    dist = dict(sorted(dist.items(), key=lambda kv: order(DIST_EDGES).get(kv[0], 99)))
    take = dict(sorted(take.items(), key=lambda kv: order(TAKEOFF_EDGES).get(kv[0], 99)))
    return Comparison(total, sec, dist, take, stations, placed, unplaced, dict(methods), dict(modes))


def decodes_from_rows(rows):
    """core.alltxt Rows (Rx only) -> [Decode]."""
    out = []
    for r in rows:
        if r.rxtx == "Rx":
            p = ft8msg.parse_message(r.message)
            out.append(Decode(r.time, r.message, r.snr, p.sender if p else None, r.mhz))
    return out


def decodes_from_log(log, client_id):
    """Decodes from one WSJT-X instance in a live/session log. The slot is the
    UTC date of reception plus the Decode's slot time (ms since midnight)."""
    import datetime as _dt
    from . import wsjtx_udp as w
    out = []
    dial = None                      # MHz, from this instance's latest Status
    for t, m in log:
        if isinstance(m, w.Status) and m.id == client_id and m.dial_hz:
            dial = m.dial_hz / 1e6
            continue
        if not isinstance(m, w.Decode) or m.id != client_id:
            continue
        if m.time_ms is not None:
            midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
            slot = midnight + _dt.timedelta(milliseconds=m.time_ms)
            if slot - t > _dt.timedelta(hours=12):      # decoded just after midnight
                slot -= _dt.timedelta(days=1)
        else:
            slot = t.replace(second=t.second - t.second % 15, microsecond=0)
        p = ft8msg.parse_message(m.message)
        out.append(Decode(slot, m.message, m.snr, p.sender if p else None, dial))
    return out


def sector_label(k):
    return COMPASS[k]


def destination(lon, lat, bearing_deg, distance_km):
    """Point distance_km from (lon, lat) along bearing (spherical Earth)."""
    R = _hops.R_EARTH
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    dr = distance_km / R
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(dr) * math.cos(p1), math.cos(dr) - math.sin(p1) * math.sin(p2))
    return (math.degrees(l2) + 540) % 360 - 180, math.degrees(p2)
