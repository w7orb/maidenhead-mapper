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
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ft8msg
from . import hops as _hops
from . import maidenhead as mh

SECTORS = 16
DIST_EDGES = (0, 1000, 2000, 4000, 6000, 8000, 10000, 13000, 20100)
TAKEOFF_EDGES = (0, 3, 6, 10, 15, 25, 90)
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


@dataclass
class Decode:
    slot: object          # hashable time key (datetime of the slot start)
    message: str
    snr: int
    sender: Optional[str] = None


@dataclass
class Match:
    sender: str
    snr_a: Optional[int]
    snr_b: Optional[int]

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
            out.append(Match(sender, da.snr if da else None, db.snr if db else None))
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
class Comparison:
    total: Bin
    by_sector: Dict[int, Bin]
    by_distance: Dict[str, Bin]
    by_takeoff: Dict[str, Bin]
    by_station: Dict[str, Bin]
    placed: Dict[str, dict]     # call -> {grid, distance_km, bearing_deg, takeoff_deg}
    unplaced: int


def compare(matches, grids, my_grid):
    """Bin matched decodes by the sender's direction, distance and takeoff from my_grid."""
    total = Bin("all")
    sec = {k: Bin(COMPASS[k]) for k in range(SECTORS)}
    dist, take, stations = {}, {}, {}
    placed, unplaced = {}, 0
    for m in matches:
        total.add(m)
        stations.setdefault(m.sender, Bin(m.sender)).add(m)
        g = grids.get(m.sender)
        if not (g and mh.is_valid(my_grid or "")):
            unplaced += 1
            continue
        if m.sender not in placed:
            d, b = mh.distance_bearing(my_grid, g)
            _, t = _hops.likely_hops(d)
            placed[m.sender] = {"grid": g, "distance_km": round(d, 1), "bearing_deg": round(b, 1),
                                "takeoff_deg": t}
        info = placed[m.sender]
        sec[_sector(info["bearing_deg"])].add(m)
        dl = _band_label(DIST_EDGES, info["distance_km"], "km")
        dist.setdefault(dl, Bin(dl)).add(m)
        if info["takeoff_deg"] is not None:
            tl = _band_label(TAKEOFF_EDGES, info["takeoff_deg"], "°")
            take.setdefault(tl, Bin(tl)).add(m)
    order = lambda edges: {_band_label(edges, lo, "km" if edges is DIST_EDGES else "°"): i
                           for i, lo in enumerate(edges)}
    dist = dict(sorted(dist.items(), key=lambda kv: order(DIST_EDGES).get(kv[0], 99)))
    take = dict(sorted(take.items(), key=lambda kv: order(TAKEOFF_EDGES).get(kv[0], 99)))
    return Comparison(total, sec, dist, take, stations, placed, unplaced)


def decodes_from_rows(rows):
    """core.alltxt Rows (Rx only) -> [Decode]."""
    out = []
    for r in rows:
        if r.rxtx == "Rx":
            p = ft8msg.parse_message(r.message)
            out.append(Decode(r.time, r.message, r.snr, p.sender if p else None))
    return out


def decodes_from_log(log, client_id):
    """Decodes from one WSJT-X instance in a live/session log. The slot is the
    UTC date of reception plus the Decode's slot time (ms since midnight)."""
    import datetime as _dt
    from . import wsjtx_udp as w
    out = []
    for t, m in log:
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
        out.append(Decode(slot, m.message, m.snr, p.sender if p else None))
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
