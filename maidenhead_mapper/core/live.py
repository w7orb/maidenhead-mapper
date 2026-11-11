"""State for the live WSJT-X map. Pure Python, no QGIS.

Feed it parsed UDP messages (`wsjtx_udp.parse`) with `handle(msg, now)`.
It keeps one StationState per (callsign, direction):

    heard   every station decoded (the message's sender)
    worked  stations logged via QSO Logged

and an ExchangeTracker (`exchanges`) that pairs directed messages into
exchanges between any two stations, with the reports each side sent.

A station is only *placed* once its grid is known: from the message
itself, or remembered from an earlier message (grid cache). Until then it
is counted as waiting. `take_dirty()` returns the keys changed since the
last call, so the map layer can update in batches.
"""

import dataclasses
import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import ft8msg
from .alltxt import Transmitted
from .exchange import ExchangeTracker
from . import maidenhead as mh
from . import wsjtx_udp as w
from .bands import BAND_NAMES, band_for_hz

# Decode.mode is one character; Status.mode has the full name.
MODE_CHARS = {"~": "FT8", "+": "FT4", "#": "JT65", "@": "JT9", "&": "MSK144", "$": "JT4", "`": "FST4", ":": "Q65"}

Key = Tuple[str, str]  # (call, direction)


@dataclass
class StationState:
    call: str
    direction: str
    grid: Optional[str] = None
    reports: int = 0
    snr_best: Optional[int] = None
    snr_last: Optional[int] = None
    bands: Set[str] = field(default_factory=set)
    modes: Set[str] = field(default_factory=set)
    first_utc: Optional[_dt.datetime] = None
    last_utc: Optional[_dt.datetime] = None
    called_me: int = 0          # messages from them addressed to my call

    def bands_str(self):
        order = {b: i for i, b in enumerate(BAND_NAMES)}
        return ",".join(sorted(self.bands, key=lambda b: order.get(b, 99)))


@dataclass
class ClientState:
    id: str
    version: str = ""
    dial_hz: int = 0
    mode: str = ""
    de_call: str = ""
    de_grid: str = ""
    last_seen: Optional[_dt.datetime] = None


class LiveModel:
    def __init__(self, my_call: str = "", my_grid: str = "", record: bool = False):
        self.my_call = my_call.upper()
        self.my_grid = my_grid if mh.is_valid(my_grid) else ""
        self.stations: Dict[Key, StationState] = {}
        self.grid_cache: Dict[str, str] = {}
        self.clients: Dict[str, ClientState] = {}
        self.decodes = 0
        self.last_packet: Optional[_dt.datetime] = None
        self._dirty: Set[Key] = set()
        self.exchanges = ExchangeTracker(self.my_call)
        # (time, message) for everything that changes the map, so any time
        # window can be rebuilt with replay(); also what sessions save.
        self.record = record
        self.log: List[Tuple[_dt.datetime, object]] = []
        self._last_status: Dict[str, tuple] = {}

    # -------------------------------------------------------------- input
    def handle(self, msg, now: Optional[_dt.datetime] = None):
        """Update state from one parsed message. Returns True if it was used."""
        if msg is None:
            return False
        now = now or _dt.datetime.now(_dt.timezone.utc)
        self.last_packet = now
        client = self.clients.setdefault(msg.id, ClientState(msg.id))
        client.last_seen = now

        if self.record and isinstance(msg, LOGGED_TYPES):
            if not isinstance(msg, w.Status) or self._status_changed(msg):
                self.log.append((now, msg))

        if isinstance(msg, w.Heartbeat):
            client.version = msg.version
        elif isinstance(msg, Transmitted):
            p = ft8msg.parse_message(msg.message)
            self.exchanges.on_decode(p, None, _band(client), client.mode, now)
        elif isinstance(msg, w.Status):
            client.dial_hz, client.mode = msg.dial_hz, msg.mode
            client.de_call, client.de_grid = msg.de_call.upper(), msg.de_grid
            if client.de_call and not self.my_call:
                self.my_call = self.exchanges.my_call = client.de_call
            if mh.is_valid(client.de_grid) and not self.my_grid:
                self.my_grid = client.de_grid
            if client.de_call and mh.is_valid(client.de_grid):
                self._learn_grid(_base(client.de_call), client.de_grid)  # each WSJT-X's own station
            if msg.dx_call and mh.is_valid(msg.dx_grid or ""):
                self._learn_grid(msg.dx_call.upper(), msg.dx_grid)
            self.exchanges.on_my_status(msg.dx_call, msg.report, msg.transmitting,
                                        _band(client), client.mode, now)
        elif isinstance(msg, w.Decode):
            self._on_decode(msg, client, now)
        elif isinstance(msg, w.QsoLogged):
            self._on_logged(msg, now)
        elif isinstance(msg, w.Close):
            self.clients.pop(msg.id, None)
        return True

    def _status_changed(self, msg):
        key = (msg.dial_hz, msg.mode, msg.dx_call, msg.report, msg.transmitting, msg.de_call, msg.de_grid, msg.dx_grid)
        if self._last_status.get(msg.id) == key:
            return False
        self._last_status[msg.id] = key
        return True

    def _on_decode(self, msg, client, now):
        self.decodes += 1
        p = ft8msg.parse_message(msg.message)
        if p is None:
            return
        if p.grid:
            self._learn_grid(p.sender, p.grid)
        st = self._station(p.sender, "heard")
        st.reports += 1
        st.snr_last = msg.snr
        st.snr_best = msg.snr if st.snr_best is None else max(st.snr_best, msg.snr)
        band = band_for_hz(client.dial_hz) if client.dial_hz else None
        if band:
            st.bands.add(band)
        mode = client.mode or MODE_CHARS.get(msg.mode, "")
        if mode:
            st.modes.add(mode)
        st.first_utc = st.first_utc or now
        st.last_utc = now
        if self.my_call and p.to and _base(p.to) == _base(self.my_call):
            st.called_me += 1
        self._dirty.add((st.call, st.direction))
        self.exchanges.on_decode(p, msg.snr, band, mode, now)

    def _on_logged(self, msg, now):
        call = ft8msg.clean_call(msg.dx_call)
        if not call:
            return
        if msg.dx_grid and mh.is_valid(msg.dx_grid):
            self._learn_grid(call, msg.dx_grid)
        st = self._station(call, "worked")
        st.reports += 1
        band = band_for_hz(msg.tx_hz)
        if band:
            st.bands.add(band)
        if msg.mode:
            st.modes.add(msg.mode)
        when = msg.time_off or now
        st.first_utc = st.first_utc or when
        st.last_utc = when
        try:
            st.snr_last = int(msg.report_rcvd)
            st.snr_best = st.snr_last if st.snr_best is None else max(st.snr_best, st.snr_last)
        except (TypeError, ValueError):
            pass
        self._dirty.add((st.call, st.direction))
        self.exchanges.on_logged(call, msg.report_sent, msg.report_rcvd, band, msg.mode, when)

    def _learn_grid(self, call, grid):
        """Remember a grid; a finer or changed grid updates every row for that call."""
        grid = mh.display(grid)
        if self.grid_cache.get(call) == grid:
            return
        self.grid_cache[call] = grid
        for direction in ("heard", "worked"):
            st = self.stations.get((call, direction))
            if st is not None:
                st.grid = grid
                self._dirty.add((call, direction))

    def _station(self, call, direction):
        key = (call, direction)
        st = self.stations.get(key)
        if st is None:
            st = self.stations[key] = StationState(call, direction, grid=self.grid_cache.get(call))
        elif st.grid is None and call in self.grid_cache:
            st.grid = self.grid_cache[call]
        return st

    # ------------------------------------------------------------- output
    def take_dirty(self):
        """Keys changed since the last call (placed or not)."""
        keys, self._dirty = self._dirty, set()
        return keys

    def placed_count(self):
        return sum(1 for s in self.stations.values() if s.grid)

    def waiting_count(self):
        return sum(1 for s in self.stations.values() if not s.grid)

    def grid_of(self, call):
        """Best known grid for a callsign (exact, then base call), or None."""
        return self.grid_cache.get(call) or self.grid_cache.get(_base(call))

    def distance_bearing(self, grid):
        if not (self.my_grid and grid):
            return None, None
        d, b = mh.distance_bearing(self.my_grid, grid)
        return round(d, 1), round(b, 1)


LOGGED_TYPES = (w.Status, w.Decode, w.QsoLogged, Transmitted)
_TYPES = {cls.__name__: cls for cls in LOGGED_TYPES}


def replay(log, my_call="", my_grid="", start=None, end=None):
    """A fresh LiveModel built from log entries with start <= time <= end.

    The latest Status per client from before `start` is applied first (as
    not transmitting) so dial frequency and mode are known.
    """
    m = LiveModel(my_call, my_grid)
    pending = {}
    for t, msg in log:
        if start is not None and t < start:
            if isinstance(msg, w.Status):
                pending[msg.id] = dataclasses.replace(msg, transmitting=False)
            continue
        if end is not None and t > end:
            break
        for st in pending.values():
            m.handle(st, t)
        pending = {}
        m.handle(msg, t)
    return m


def encode_entry(msg):
    """(kind, JSON text) for saving a log entry."""
    d = dataclasses.asdict(msg)
    for k, v in d.items():
        if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
            d[k] = {"__dt__": v.isoformat()}
    return type(msg).__name__, json.dumps(d)


def decode_entry(kind, text):
    """Inverse of encode_entry; None for unknown kinds."""
    cls = _TYPES.get(kind)
    if cls is None:
        return None
    d = json.loads(text)
    for k, v in d.items():
        if isinstance(v, dict) and "__dt__" in v:
            iso = v["__dt__"]
            d[k] = _dt.datetime.fromisoformat(iso) if "T" in iso else (
                _dt.time.fromisoformat(iso) if ":" in iso else _dt.date.fromisoformat(iso))
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def _band(client):
    return band_for_hz(client.dial_hz) if client.dial_hz else None


def _base(call):
    parts = [p for p in call.upper().split("/") if p]
    return max(parts, key=len) if parts else ""
