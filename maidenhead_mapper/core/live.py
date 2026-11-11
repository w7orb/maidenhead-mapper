"""State for the live WSJT-X map. Pure Python, no QGIS.

Feed it parsed UDP messages (`wsjtx_udp.parse`) with `handle(msg, now)`.
It keeps one StationState per (callsign, direction):

    heard   every station decoded (the message's sender)
    worked  stations logged via QSO Logged

A station is only *placed* once its grid is known: from the message
itself, or remembered from an earlier message (grid cache). Until then it
is counted as waiting. `take_dirty()` returns the keys changed since the
last call, so the map layer can update in batches.
"""

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

from . import ft8msg
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
    def __init__(self, my_call: str = "", my_grid: str = ""):
        self.my_call = my_call.upper()
        self.my_grid = my_grid if mh.is_valid(my_grid) else ""
        self.stations: Dict[Key, StationState] = {}
        self.grid_cache: Dict[str, str] = {}
        self.clients: Dict[str, ClientState] = {}
        self.decodes = 0
        self.last_packet: Optional[_dt.datetime] = None
        self._dirty: Set[Key] = set()

    # -------------------------------------------------------------- input
    def handle(self, msg, now: Optional[_dt.datetime] = None):
        """Update state from one parsed message. Returns True if it was used."""
        if msg is None:
            return False
        now = now or _dt.datetime.now(_dt.timezone.utc)
        self.last_packet = now
        client = self.clients.setdefault(msg.id, ClientState(msg.id))
        client.last_seen = now

        if isinstance(msg, w.Heartbeat):
            client.version = msg.version
        elif isinstance(msg, w.Status):
            client.dial_hz, client.mode = msg.dial_hz, msg.mode
            client.de_call, client.de_grid = msg.de_call.upper(), msg.de_grid
            if client.de_call and not self.my_call:
                self.my_call = client.de_call
            if mh.is_valid(client.de_grid) and not self.my_grid:
                self.my_grid = client.de_grid
        elif isinstance(msg, w.Decode):
            self._on_decode(msg, client, now)
        elif isinstance(msg, w.QsoLogged):
            self._on_logged(msg, now)
        elif isinstance(msg, w.Close):
            self.clients.pop(msg.id, None)
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

    def distance_bearing(self, grid):
        if not (self.my_grid and grid):
            return None, None
        d, b = mh.distance_bearing(self.my_grid, grid)
        return round(d, 1), round(b, 1)


def _base(call):
    parts = [p for p in call.upper().split("/") if p]
    return max(parts, key=len) if parts else ""
