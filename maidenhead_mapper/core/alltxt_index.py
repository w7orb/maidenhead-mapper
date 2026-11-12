"""A quick index of WSJT-X ALL.TXT files: what time span and bands they hold,
decodes per UTC hour, and where each day starts, so a date range can be read
without parsing the whole file. Pure Python.

The index is cached as JSON (keyed by the file's path) and brought up to date
incrementally: WSJT-X only appends, so when a file has grown, only the new
part is scanned. A file that shrank or changed at the start is re-indexed.
"""

import datetime as _dt
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .bands import band_for_mhz
from .timeline import Hourly

VERSION = 1
UTC = _dt.timezone.utc
HEAD_BYTES = 256            # first bytes, to tell an appended file from a replaced one


@dataclass
class FileIndex:
    path: str
    size: int = 0                       # bytes scanned so far
    head: str = ""                      # hash of the first HEAD_BYTES
    rx: int = 0                         # decode (Rx) lines
    tx: int = 0                         # my transmissions (Tx lines)
    other: int = 0                      # lines in another format
    first: Optional[int] = None         # epoch seconds of the first / last line
    last: Optional[int] = None
    in_order: bool = True               # times never go backwards
    hours: Dict[str, int] = field(default_factory=dict)        # hour epoch -> Rx lines
    days: Dict[str, int] = field(default_factory=dict)         # day epoch -> byte offset of its first line
    bands: Dict[str, int] = field(default_factory=dict)        # band -> Rx lines
    version: int = VERSION

    @property
    def first_time(self):
        return None if self.first is None else _dt.datetime.fromtimestamp(self.first, UTC)

    @property
    def last_time(self):
        return None if self.last is None else _dt.datetime.fromtimestamp(self.last, UTC)

    def offset_for(self, start: Optional[_dt.datetime]) -> int:
        """Byte offset to start reading at for times >= start (0 if unknown or out of order)."""
        if start is None or not self.in_order or not self.days:
            return 0
        day = int(_dt.datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        best = 0
        for d, off in self.days.items():
            if int(d) <= day and off > best:
                best = off
        return best


def _head(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read(HEAD_BYTES)).hexdigest()


def _scan(idx: FileIndex, path, start_offset, progress=None, cancelled=None):
    """Scan from start_offset to the end of the file, adding to idx."""
    hour_cache = {}                     # b"YYMMDD_HH" -> hour epoch
    total = max(1, os.path.getsize(path) - start_offset)
    last = idx.last
    with open(path, "rb") as f:
        f.seek(start_offset)
        offset = start_offset
        n = 0
        for line in f:
            here = offset
            offset += len(line)
            n += 1
            if progress and n % 200000 == 0:
                progress(100.0 * (offset - start_offset) / total)
                if cancelled and cancelled():
                    return False
            if len(line) < 20 or line[6:7] != b"_":
                if line.strip():
                    idx.other += 1
                continue
            key = line[:9]
            hour = hour_cache.get(key)
            if hour is None:
                try:
                    t = _dt.datetime(2000 + int(key[0:2]), int(key[2:4]), int(key[4:6]), int(key[7:9]), tzinfo=UTC)
                except ValueError:
                    idx.other += 1
                    continue
                hour = hour_cache[key] = int(t.timestamp())
                day = str(hour - hour % 86400)
                if day not in idx.days:
                    idx.days[day] = here
            try:
                secs = hour + int(line[9:11]) * 60 + int(line[11:13])
            except ValueError:
                idx.other += 1
                continue
            parts = line.split(None, 3)
            if len(parts) < 4 or parts[2] not in (b"Rx", b"Tx"):
                idx.other += 1
                continue
            if last is not None and secs < last:
                idx.in_order = False
            last = secs if last is None else max(last, secs)
            if idx.first is None or secs < idx.first:
                idx.first = secs
            if parts[2] == b"Tx":
                idx.tx += 1
                continue
            idx.rx += 1
            h = str(hour)
            idx.hours[h] = idx.hours.get(h, 0) + 1
            try:
                band = band_for_mhz(float(parts[1]))
            except ValueError:
                band = None
            if band:
                idx.bands[band] = idx.bands.get(band, 0) + 1
    idx.last = last
    idx.size = offset
    return True


def cache_file(cache_dir, path):
    key = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"alltxt_{key}.json")


def load_cached(cache_dir, path) -> Optional[FileIndex]:
    if not cache_dir:
        return None
    try:
        with open(cache_file(cache_dir, path), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != VERSION:
            return None
        return FileIndex(**d)
    except (OSError, ValueError, TypeError):
        return None


def save_cached(cache_dir, idx: FileIndex):
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cache_file(cache_dir, idx.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(idx), f)
        os.replace(tmp, cache_file(cache_dir, idx.path))
    except OSError:
        pass


def index_file(path, cache_dir=None, progress=None, cancelled=None) -> Optional[FileIndex]:
    """Index one file, reusing and extending a cached index when the file was only appended to.
    None if cancelled."""
    size = os.path.getsize(path)
    head = _head(path)
    idx = load_cached(cache_dir, path)
    if idx is not None and idx.head == head and idx.size == size:
        return idx
    if idx is None or idx.head != head or idx.size > size:
        idx = FileIndex(os.path.abspath(path), head=head)
    if not _scan(idx, path, idx.size, progress, cancelled):
        return None
    save_cached(cache_dir, idx)
    return idx


@dataclass
class Index:
    files: List[FileIndex]

    @property
    def rx(self):
        return sum(f.rx for f in self.files)

    @property
    def first_time(self):
        ts = [f.first for f in self.files if f.first is not None]
        return _dt.datetime.fromtimestamp(min(ts), UTC) if ts else None

    @property
    def last_time(self):
        ts = [f.last for f in self.files if f.last is not None]
        return _dt.datetime.fromtimestamp(max(ts), UTC) if ts else None

    def bands(self):
        out = {}
        for f in self.files:
            for b, n in f.bands.items():
                out[b] = out.get(b, 0) + n
        return out

    def hourly(self) -> Hourly:
        """Decodes per hour for every hour first..last (as `heard`; `heard_me` is zero)."""
        counts = {}
        for f in self.files:
            for h, n in f.hours.items():
                counts[int(h)] = counts.get(int(h), 0) + n
        if not counts:
            return Hourly()
        h0, h1 = min(counts), max(counts)
        n = (h1 - h0) // 3600 + 1
        heard = [counts.get(h0 + 3600 * i, 0) for i in range(n)]
        return Hourly(_dt.datetime.fromtimestamp(h0, UTC), heard, [0] * n)

    def offsets(self, start):
        return {f.path: f.offset_for(start) for f in self.files}

    def summary(self):
        b = self.bands()
        bands = ", ".join(k for k, _ in sorted(b.items(), key=lambda kv: -kv[1]))
        if self.first_time is None:
            return "No decodes found."
        return (f"{self.rx:,} decodes, {self.first_time:%Y-%m-%d %H:%M} – {self.last_time:%Y-%m-%d %H:%M} UTC"
                + (f"; {bands}" if bands else ""))


def index(paths, cache_dir=None, progress=None, cancelled=None) -> Optional[Index]:
    files = []
    for i, p in enumerate(paths):
        def prog(pct, i=i):
            if progress:
                progress((i + pct / 100.0) * 100.0 / len(paths))
        fi = index_file(p, cache_dir, prog, cancelled)
        if fi is None:
            return None
        files.append(fi)
    return Index(files)
