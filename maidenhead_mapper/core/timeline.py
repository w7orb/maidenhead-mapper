"""Hourly activity counts for the timeline panel. Pure Python."""

import datetime as _dt
from dataclasses import dataclass, field
from typing import List

HOUR = _dt.timedelta(hours=1)


def hour_floor(t):
    return t.replace(minute=0, second=0, microsecond=0)


@dataclass
class Hourly:
    """Reports per UTC hour, split by direction, for every hour first..last.

    Hours with no reports are present with zero counts, so bin i covers
    [start + i h, start + (i+1) h).
    """
    start: _dt.datetime = None
    heard: List[int] = field(default_factory=list)
    heard_me: List[int] = field(default_factory=list)

    def __len__(self):
        return len(self.heard)

    def total(self, i):
        return self.heard[i] + self.heard_me[i]

    def hour(self, i):
        return self.start + i * HOUR

    def span(self, i0, i1):
        """(start, end) datetimes covering bins i0..i1 inclusive; end is the
        last instant of bin i1, for inclusive filters."""
        return self.hour(i0), self.hour(i1 + 1) - _dt.timedelta(microseconds=1)

    def index(self, t):
        """Bin index containing time t, clamped to the range."""
        if not len(self):
            return 0
        i = int((t - self.start) // HOUR)
        return max(0, min(len(self) - 1, i))

    def reports(self, i0=0, i1=None):
        i1 = len(self) - 1 if i1 is None else i1
        return sum(self.heard[i0:i1 + 1]) + sum(self.heard_me[i0:i1 + 1])


def hourly(reports):
    """Hourly counts of report dicts with a `time` and `direction`."""
    times = [(r["time"], r.get("direction")) for r in reports if r.get("time") is not None]
    if not times:
        return Hourly()
    start = hour_floor(min(t for t, _ in times))
    n = int((hour_floor(max(t for t, _ in times)) - start) // HOUR) + 1
    out = Hourly(start, [0] * n, [0] * n)
    for t, d in times:
        i = int((t - start) // HOUR)
        if d == "heard_me":
            out.heard_me[i] += 1
        else:
            out.heard[i] += 1
    return out


@dataclass
class WindowSummary:
    """What a set of StationSummary rows (one time window) adds up to."""
    heard: int = 0
    heard_me: int = 0
    reports: int = 0
    bands: List[tuple] = field(default_factory=list)    # (band, stations), low to high
    farthest: object = None                             # StationSummary or None
    strongest: object = None


def summarize_window(rows):
    from .stations import _band_order  # local: stations imports nothing from here
    out = WindowSummary()
    counts = {}
    for s in rows:
        if s.direction == "heard_me":
            out.heard_me += 1
        else:
            out.heard += 1
        out.reports += s.reports
        for b in filter(None, s.bands.split(",")):
            counts[b] = counts.get(b, 0) + 1
        if s.distance_km is not None and (out.farthest is None or s.distance_km > out.farthest.distance_km):
            out.farthest = s
        if s.snr_best is not None and (out.strongest is None or s.snr_best > out.strongest.snr_best):
            out.strongest = s
    out.bands = sorted(counts.items(), key=lambda kv: _band_order(kv[0]))
    return out


def window_label(start, end):
    """Short legend text for a window, e.g. '11-24 08:00–20:00 UTC'."""
    if start.date() == end.date():
        return f"{start:%m-%d %H:%M}–{end:%H:%M} UTC"
    return f"{start:%m-%d %H:%M} – {end:%m-%d %H:%M} UTC"
