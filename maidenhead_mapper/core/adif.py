"""ADIF logs (.adi and .adx): QSOs as records. Pure Python.

Reads what WSJT-X (wsjtx_log.adi), JTDX, MSHV, N1MM, Log4OM, QRZ and LoTW
write. Each QSO becomes a Qso with my side (station callsign, my grid) and
theirs (call, grid), time, band, mode and both reports.

Reports: RST_SENT is how I heard them, RST_RCVD how they heard me. For
digital modes (FT8, FT4, JT65, ...) these are SNR in dB ("-10", "+03") and
are kept as numbers; phone/CW RST ("59", "599") is kept as text only.

Several files (e.g. wsjtx_log.adi plus a LoTW download) can hold the same
QSO. merge() joins QSOs with the same call, band and mode within a few
minutes, keeping every non-empty field and any confirmation.
"""

import datetime as _dt
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import maidenhead as mh
from .bands import band_for_mhz

_TAG = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+))?(?::[A-Za-z])?>")
_DB = re.compile(r"^[+-]?\d{1,2}$")
DIGITAL = {"FT8", "FT4", "JT65", "JT9", "JT4", "Q65", "MSK144", "FST4", "FST4W", "JS8", "WSPR", "ISCAT",
           "FT2", "Q65A", "Q65B", "Q65C", "Q65D", "Q65E", "JT65A", "JT65B", "JT65C"}
MERGE_MINUTES = 5


@dataclass
class Qso:
    call: str
    time_on: _dt.datetime                       # UTC, aware
    time_off: Optional[_dt.datetime] = None
    band: Optional[str] = None
    freq_mhz: Optional[float] = None
    mode: Optional[str] = None                  # SUBMODE if given (FT4 is MODE=MFSK SUBMODE=FT4)
    grid: Optional[str] = None
    my_call: Optional[str] = None
    my_grid: Optional[str] = None
    rst_sent: Optional[str] = None              # how I heard them
    rst_rcvd: Optional[str] = None              # how they heard me
    confirmed: str = ""                         # "lotw", "qsl", "eqsl" joined by ","
    prop_mode: Optional[str] = None
    tx_pwr: Optional[str] = None
    country: Optional[str] = None
    comment: Optional[str] = None
    source: str = ""                            # file name(s)
    extra: Dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def digital(self):
        return (self.mode or "").upper() in DIGITAL

    @property
    def sent_db(self):
        return _db(self.rst_sent) if self.digital else None

    @property
    def rcvd_db(self):
        return _db(self.rst_rcvd) if self.digital else None


def _db(v):
    v = (v or "").strip()
    return int(v) if _DB.match(v) else None


class Records(list):
    """Raw records (dicts of lower-case field -> text) with the header."""

    def __init__(self, items=(), header=None):
        super().__init__(items)
        self.header = header or {}


def _value(text, pos, length):
    """The field value at pos. ADIF counts length in characters for ASCII, but
    some writers count UTF-8 bytes: never take more characters than bytes allow."""
    v = text[pos:pos + length]
    while v and len(v.encode("utf-8")) > length:
        v = v[:-1]
    return v


def parse_adi(text):
    """ADI text -> Records. Tags are case-insensitive; text before <EOH> is
    the header (or there is none if the file starts with a tag)."""
    records, current, header = [], {}, {}
    in_header = not text.lstrip().startswith("<")
    pos = 0
    while True:
        m = _TAG.search(text, pos)
        if not m:
            break
        name = m.group(1).lower()
        if m.group(2) is not None:
            n = int(m.group(2))
            value = _value(text, m.end(), n)
            pos = m.end() + len(value)
            (header if in_header else current)[name] = value
            continue
        pos = m.end()
        if name == "eoh":
            in_header = False
            current = {}
        elif name == "eor":
            if current:
                records.append(current)
            current = {}
    return Records(records, header)


def parse_adx(text):
    """ADX (XML) text -> Records."""
    root = ET.fromstring(text)
    def tag(e):
        return e.tag.split("}")[-1].lower()
    header, records = {}, []
    for child in root:
        if tag(child) == "header":
            header = {tag(e): (e.text or "") for e in child}
        elif tag(child) == "records":
            for rec in child:
                d = {}
                for e in rec:
                    name = tag(e)
                    if name in ("userdef", "app"):
                        name = (e.get("FIELDNAME") or e.get("fieldname") or name).lower()
                    d[name] = (e.text or "").strip()
                if d:
                    records.append(d)
    return Records(records, header)


def read_file(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if path.lower().endswith(".adx") or text.lstrip().startswith("<?xml"):
        return parse_adx(text)
    return parse_adi(text)


def _time(date, hhmm):
    date, hhmm = (date or "").strip(), (hhmm or "").strip()
    if len(date) != 8 or not date.isdigit():
        return None
    hhmm = (hhmm + "000000")[:6] if hhmm.isdigit() and len(hhmm) in (4, 6) else "000000"
    try:
        return _dt.datetime.strptime(date + hhmm, "%Y%m%d%H%M%S").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def parse_latlon(v):
    """ADIF location 'N045 30.000' -> signed degrees, or None."""
    m = re.match(r"^\s*([NSEW])\s*(\d{1,3})\s+(\d{1,2}(?:\.\d*)?)\s*$", v or "", re.I)
    if not m:
        return None
    deg = int(m.group(2)) + float(m.group(3)) / 60
    return -deg if m.group(1).upper() in "SW" else deg


def _grid(rec, prefix=""):
    """The best locator for a side: GRIDSQUARE (with GRIDSQUARE_EXT), else the
    first VUCC grid, else LAT/LON."""
    g = (rec.get(prefix + "gridsquare") or "").strip()
    ext = (rec.get(prefix + "gridsquare_ext") or "").strip()
    if g and ext and len(g) == 8:
        g += ext
    if mh.is_valid(g):
        return mh.display(g)
    vucc = (rec.get(prefix + "vucc_grids") or "").split(",")[0].strip()
    if mh.is_valid(vucc):
        return mh.display(vucc)
    lat, lon = parse_latlon(rec.get(prefix + "lat")), parse_latlon(rec.get(prefix + "lon"))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        return mh.display(mh.to_locator(lat, lon, mh.SUBSQUARE))
    return None


def _confirmed(rec):
    out = []
    if (rec.get("lotw_qsl_rcvd") or rec.get("app_lotw_qsl_rcvd") or "").upper() in ("Y", "V"):
        out.append("lotw")
    if (rec.get("qsl_rcvd") or "").upper() in ("Y", "V"):
        out.append("qsl")
    if (rec.get("eqsl_qsl_rcvd") or "").upper() in ("Y", "V"):
        out.append("eqsl")
    return ",".join(out)


def to_qso(rec, source=""):
    """Record dict -> Qso, or None without a call or a valid date/time."""
    call = (rec.get("call") or "").strip().upper()
    t_on = _time(rec.get("qso_date"), rec.get("time_on"))
    if not call or t_on is None:
        return None
    t_off = _time(rec.get("qso_date_off") or rec.get("qso_date"), rec.get("time_off"))
    if t_off is not None and t_off < t_on:
        t_off += _dt.timedelta(days=1)           # past midnight without QSO_DATE_OFF
    try:
        freq = float(rec.get("freq") or "") or None
    except ValueError:
        freq = None
    band = (rec.get("band") or "").strip().lower() or band_for_mhz(freq)
    mode = (rec.get("submode") or rec.get("mode") or "").strip().upper() or None
    my_call = (rec.get("station_callsign") or rec.get("operator") or rec.get("owner_callsign") or "").strip().upper()
    known = {"call", "qso_date", "time_on", "qso_date_off", "time_off", "freq", "band", "mode", "submode",
             "gridsquare", "my_gridsquare", "station_callsign", "operator", "rst_sent", "rst_rcvd",
             "prop_mode", "tx_pwr", "country", "comment", "comments"}
    return Qso(
        call=call, time_on=t_on, time_off=t_off, band=band or None, freq_mhz=freq, mode=mode,
        grid=_grid(rec), my_call=my_call or None, my_grid=_grid(rec, "my_"),
        rst_sent=(rec.get("rst_sent") or "").strip() or None, rst_rcvd=(rec.get("rst_rcvd") or "").strip() or None,
        confirmed=_confirmed(rec), prop_mode=(rec.get("prop_mode") or "").strip().upper() or None,
        tx_pwr=(rec.get("tx_pwr") or "").strip() or None, country=(rec.get("country") or "").strip() or None,
        comment=(rec.get("comment") or rec.get("comments") or "").strip() or None, source=source,
        extra={k: v for k, v in rec.items() if k not in known and v})


def _fill(a, b):
    """Merge b into a: keep a's values, fill blanks from b, union confirmations."""
    for name in ("time_off", "band", "freq_mhz", "mode", "grid", "my_call", "my_grid", "rst_sent", "rst_rcvd",
                 "prop_mode", "tx_pwr", "country", "comment"):
        if getattr(a, name) in (None, "") and getattr(b, name) not in (None, ""):
            setattr(a, name, getattr(b, name))
    # a longer locator from the other file is more precise
    if b.grid and a.grid and len(b.grid) > len(a.grid) and b.grid.upper().startswith(a.grid.upper()):
        a.grid = b.grid
    conf = [c for c in (a.confirmed + "," + b.confirmed).split(",") if c]
    a.confirmed = ",".join(dict.fromkeys(conf))
    if b.source and b.source not in a.source.split(","):
        a.source = ",".join(s for s in (a.source, b.source) if s)


def merge(qsos, minutes=MERGE_MINUTES):
    """Join duplicates: same call, band and mode (when both have one) within
    `minutes` of each other. Returns (qsos sorted by time, number merged)."""
    out, merged = [], 0
    recent = {}                     # (call, band) -> [indexes into out]
    window = _dt.timedelta(minutes=minutes)
    for q in sorted(qsos, key=lambda q: q.time_on):
        key = (q.call, q.band)
        hit = None
        for i in recent.get(key, []):
            o = out[i]
            if abs(q.time_on - o.time_on) <= window and (not q.mode or not o.mode or q.mode == o.mode):
                hit = o
                break
        if hit is not None:
            _fill(hit, q)
            merged += 1
            continue
        recent.setdefault(key, []).append(len(out))
        out.append(q)
    return out, merged


@dataclass
class ReadResult:
    qsos: List[Qso]
    records: int = 0
    unusable: int = 0               # no call or no date/time
    merged: int = 0
    filtered_out: int = 0
    files: List[str] = field(default_factory=list)


def read(paths, start=None, end=None, my_call=None):
    """Read and merge ADIF files. start/end bound time_on (aware datetimes);
    my_call keeps only QSOs whose station callsign has that base call (QSOs
    without a station callsign are kept)."""
    from .psk import base_callsign
    qsos, n_rec, bad = [], 0, 0
    for p in paths:
        recs = read_file(p)
        n_rec += len(recs)
        for r in recs:
            q = to_qso(r, os.path.basename(p))
            if q is None:
                bad += 1
            else:
                qsos.append(q)
    want = base_callsign(my_call) if my_call else None
    kept, filtered = [], 0
    for q in qsos:
        if ((start is not None and q.time_on < start) or (end is not None and q.time_on > end)
                or (want and q.my_call and base_callsign(q.my_call) != want)):
            filtered += 1
        else:
            kept.append(q)
    kept, merged = merge(kept)
    return ReadResult(kept, n_rec, bad, merged, filtered, list(paths))


@dataclass
class Worked:
    """One worked station, over all its QSOs."""
    call: str
    grid: Optional[str]
    qsos: int
    bands: str
    modes: str
    first_utc: _dt.datetime
    last_utc: _dt.datetime
    sent_best_db: Optional[int]
    rcvd_best_db: Optional[int]
    confirmed: int                  # QSOs confirmed by any QSL route
    country: Optional[str]


def worked(qsos):
    """Group QSOs by call. The grid is the most recent (longest on ties) one logged."""
    from .bands import BAND_NAMES
    groups = {}
    for q in sorted(qsos, key=lambda q: q.time_on):
        groups.setdefault(q.call, []).append(q)
    out = []
    order = lambda b: BAND_NAMES.index(b) if b in BAND_NAMES else 99
    for call, g in sorted(groups.items()):
        grids = [q.grid for q in g if q.grid]
        grid = grids[-1] if grids else None
        if grid:
            longer = [x for x in grids if len(x) > len(grid) and x.upper().startswith(grid.upper())]
            grid = longer[-1] if longer else grid
        sent = [q.sent_db for q in g if q.sent_db is not None]
        rcvd = [q.rcvd_db for q in g if q.rcvd_db is not None]
        out.append(Worked(
            call=call, grid=grid, qsos=len(g),
            bands=",".join(sorted({q.band for q in g if q.band}, key=order)),
            modes=",".join(sorted({q.mode for q in g if q.mode})),
            first_utc=g[0].time_on, last_utc=g[-1].time_off or g[-1].time_on,
            sent_best_db=max(sent) if sent else None, rcvd_best_db=max(rcvd) if rcvd else None,
            confirmed=sum(1 for q in g if q.confirmed),
            country=next((q.country for q in reversed(g) if q.country), None)))
    return out
