"""WSJT-X ALL.TXT reading, and turning it into the live map's message log.

Pure Python, no QGIS. ALL.TXT lines (WSJT-X 2.1 and later) look like

    221213_205315    28.074 Rx FT8    -12  0.3 2462 AH6U N5ER EM34
    221213_205330    28.074 Tx FT8      0  0.0 1500 N5ER W7ORB R-05

Rx lines are decodes; Tx lines are my own transmissions. `to_log` converts
rows into the same (time, message) log the live map records, so an ALL.TXT
file can be replayed through `live.replay` exactly like a live session.
"""

import datetime as _dt
from collections import namedtuple
from dataclasses import dataclass

from . import wsjtx_udp as w

Row = namedtuple("Row", "time mhz mode snr dt df message rxtx")

# Decode.mode character for each mode name (the reverse of live.MODE_CHARS)
MODE_CHAR = {"FT8": "~", "FT4": "+", "JT65": "#", "JT9": "@", "MSK144": "&", "JT4": "$", "FST4": "`", "Q65": ":"}


@dataclass
class Transmitted:
    """One of my own transmissions (an ALL.TXT Tx line). Not a UDP message."""
    id: str
    message: str = ""


class Rows(list):
    skipped = 0          # lines that weren't Rx/Tx in the current format


def parse_line(line):
    p = line.split(None, 7)
    if len(p) < 8 or p[2] not in ("Rx", "Tx"):
        return None
    try:
        t = _dt.datetime.strptime(p[0], "%y%m%d_%H%M%S").replace(tzinfo=_dt.timezone.utc)
        return Row(t, float(p[1]), p[3], int(p[4]), float(p[5]), int(p[6]), p[7].strip(), p[2])
    except ValueError:
        return None


def read(paths, start=None, end=None, offsets=None, in_order=()):
    """Rows from one or more ALL.TXT files, sorted by time (optionally limited).

    offsets: {path: byte offset} to start reading at (from an index);
    in_order: paths whose times never go backwards, so reading can stop at `end`.
    """
    rows = Rows()
    offsets = offsets or {}
    for path in paths:
        with open(path, "rb") as f:
            f.seek(offsets.get(path, 0))
            stop = path in in_order
            for raw in f:
                line = raw.decode("utf-8", errors="replace")
                r = parse_line(line)
                if r is None:
                    if line.strip():
                        rows.skipped += 1
                    continue
                if end and r.time > end:
                    if stop:
                        break
                    continue
                if start and r.time < start:
                    continue
                rows.append(r)
    rows.sort(key=lambda r: r.time)
    return rows


def to_log(rows, de_call="", de_grid="", client_id="ALL.TXT"):
    """[(time, message)] for live.replay: a Status whenever frequency or mode
    changes, a Decode per Rx line, a Transmitted per Tx line."""
    log, status = [], None
    for r in rows:
        if status != (r.mhz, r.mode):
            status = (r.mhz, r.mode)
            log.append((r.time, w.Status(client_id, dial_hz=int(round(r.mhz * 1e6)), mode=r.mode,
                                         de_call=de_call, de_grid=de_grid)))
        if r.rxtx == "Tx":
            log.append((r.time, Transmitted(client_id, r.message)))
        else:
            ms = ((r.time.hour * 60 + r.time.minute) * 60 + r.time.second) * 1000
            log.append((r.time, w.Decode(client_id, time_ms=ms, snr=r.snr, dt=r.dt, df=r.df,
                                         mode=MODE_CHAR.get(r.mode, "~"), message=r.message)))
    return log
