"""Research dataset export: one row per observed SNR, from ALL.TXT. Pure Python, no QGIS.

Each row is one signal-to-noise measurement of one transmitter at one
receiver, so logs from many operators can be merged and shared (e.g. with
HamSCI or on Zenodo). Two kinds of measurement come out of ALL.TXT:

    exchange_report  the report one station sent another in an exchange
                     ("W4XYZ K1ABC -05": K1ABC hears W4XYZ at -5 dB), so
                     tx = the addressee, rx = the sender of the report.
                     A report repeated in the same exchange is one row
                     (n_decoded counts the repeats).
    my_decode        my receiver's SNR of a decoded message: tx = the
                     sender, rx = me. One row per decode.

exchange_id links both directions of an exchange and my decodes of either
side. Path features (distance, bearing, fewest hops, control points and
day/night there) come from the two grids' centres.

The column list, units and meanings are in FIELDS and docs/research-export-schema.md.
"""

import configparser
import csv
import datetime as _dt
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ccir as _ccir
from . import ft8msg
from . import hops as _hops
from . import maidenhead as mh
from .bands import band_for_mhz
from .exchange import ExchangeTracker
from .iono import _along

SCHEMA_VERSION = "1.0"
SOURCE_ALLTXT = "alltxt"
EXCHANGE_REPORT, MY_DECODE = "exchange_report", "my_decode"
DECODE_FLOOR_DB = -24          # FT8's usual lowest report; at or below it counts as censored
SHORT_PATH_KM = 2000.0         # up to this: one control point at the midpoint
CONTROL_KM = 1000.0            # longer paths: control points this far from each end
DAY_MAX_SZA = 90.0             # sun above the horizon at the control point (ground)
NIGHT_MIN_SZA = 108.0          # sun below the horizon even at the F2 layer (~300 km up sees to ~107 deg)
PSEUDONYM_HEX = 12             # characters of SHA-256 kept for a pseudonymous callsign
EXCHANGE_ID_HEX = 16
GENERATOR = "Maidenhead Mapper (QGIS plugin)"

# name, type, unit, meaning
FIELDS = [
    ("schema_version", "string", "", "Version of this schema (\"%s\")." % SCHEMA_VERSION),
    ("source", "string", "", "Where the row came from: \"alltxt\" (a WSJT-X ALL.TXT file)."),
    ("observer", "string", "", "Callsign of the operator whose log this is (pseudonymised if the export is)."),
    ("time_utc", "datetime", "ISO 8601 UTC", "Start of the decoded transmission (first decode of a repeated report)."),
    ("band", "string", "", "Amateur band, e.g. \"20m\"; empty outside the bands."),
    ("freq_mhz", "float", "MHz", "Dial frequency plus the audio offset: the signal's RF frequency."),
    ("mode", "string", "", "WSJT-X mode, e.g. FT8, FT4."),
    ("report_kind", "string", "", "\"exchange_report\": the SNR rx reported for tx in an exchange. "
                                  "\"my_decode\": the observer's receiver decoded tx."),
    ("exchange_id", "string", "", "Links rows of one exchange (both directions and the observer's decodes of each "
                                  "side). Empty for decodes outside an exchange (CQs)."),
    ("tx_call", "string", "", "Transmitting station (pseudonymised if the export is)."),
    ("tx_grid", "string", "Maidenhead", "Transmitter's grid; empty if never decoded."),
    ("rx_call", "string", "", "Receiving station: the one that measured the SNR (pseudonymised if the export is)."),
    ("rx_grid", "string", "Maidenhead", "Receiver's grid; empty if never decoded."),
    ("snr_db", "integer", "dB", "Signal-to-noise ratio in 2500 Hz, as WSJT-X reports it."),
    ("censored", "integer", "0/1", "1 if snr_db is at or below the decode floor (the true SNR may be lower)."),
    ("n_decoded", "integer", "", "Times this measurement was decoded (repeats of the same report in one exchange); "
                                 "1 for my_decode."),
    ("grid_precision", "integer", "characters", "Characters in the coarser of the two grids (4 or 6); empty "
                                                "without both grids."),
    ("distance_km", "float", "km", "Great-circle distance between the grid centres."),
    ("bearing_deg", "float", "degrees", "Initial bearing from tx to rx, 0-360 clockwise from true north."),
    ("n_min_hops", "integer", "", "Fewest F2 hops (layer at 300 km, takeoff at least 2 degrees)."),
    ("cp1_lat", "float", "degrees", "Control point 1: the path midpoint (up to 2000 km), else 1000 km from tx."),
    ("cp1_lon", "float", "degrees", ""),
    ("cp2_lat", "float", "degrees", "Control point 2, paths over 2000 km only: 1000 km from rx."),
    ("cp2_lon", "float", "degrees", ""),
    ("cp1_sza_deg", "float", "degrees", "Solar zenith angle at control point 1 at time_utc."),
    ("cp2_sza_deg", "float", "degrees", "Solar zenith angle at control point 2."),
    ("day_night", "string", "", "At the control points: \"day\" (zenith below 90), \"night\" (108 or more, "
                                "the F2 layer dark too), \"twilight\" (between), or \"mixed\" (two control "
                                "points that differ)."),
]
COLUMNS = [f[0] for f in FIELDS]


@dataclass
class Options:
    my_call: str = ""
    my_grid: str = ""
    start: Optional[_dt.datetime] = None     # rows from here (inclusive) ...
    end: Optional[_dt.datetime] = None       # ... to here (exclusive)
    band: Optional[str] = None
    floor_db: int = DECODE_FLOOR_DB
    pseudonymise: bool = False
    salt: str = ""
    grid_chars: int = 6                      # 4 or 6: cap on grid precision in the output


@dataclass
class Stats:
    rows: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    censored: int = 0
    with_both_grids: int = 0
    exchanges: int = 0
    stations: int = 0
    first_utc: Optional[_dt.datetime] = None
    last_utc: Optional[_dt.datetime] = None
    unparsed: int = 0          # Rx lines that weren't a callsign message (free text, telemetry)


# ---------------------------------------------------------------- helpers
def pseudonym(call: str, salt: str) -> str:
    """Salted SHA-256 of an upper-case callsign, truncated. Same call and salt -> same id."""
    h = hashlib.sha256((salt + "\n" + call.upper()).encode("utf-8")).hexdigest()
    return h[:PSEUDONYM_HEX]


def exchange_key(call_a: str, call_b: str, band: str, first_utc: _dt.datetime) -> str:
    """An id for an exchange that doesn't depend on how many others came before it."""
    a, b = sorted((call_a, call_b))
    text = f"{a}|{b}|{band}|{first_utc:%Y-%m-%dT%H:%M:%S}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:EXCHANGE_ID_HEX]


def solar_zenith_deg(lat: float, lon: float, when: _dt.datetime) -> float:
    """Solar zenith angle in degrees (the CCIR model's formula; good to a few degrees)."""
    cz = _ccir.cos_zenith(lat, lon, when)
    return math.degrees(math.acos(max(-1.0, min(1.0, cz))))


def light(sza: float) -> str:
    if sza < DAY_MAX_SZA:
        return "day"
    return "night" if sza >= NIGHT_MIN_SZA else "twilight"


def day_night(szas) -> str:
    kinds = {light(z) for z in szas if z is not None}
    if not kinds:
        return ""
    return kinds.pop() if len(kinds) == 1 else "mixed"


def control_points(lon1: float, lat1: float, lon2: float, lat2: float, distance_km: float):
    """[(lon, lat)]: the midpoint up to 2000 km, else points 1000 km from each end (P.533 style)."""
    if distance_km < 1.0:
        return [(lon1, lat1)]
    if distance_km <= SHORT_PATH_KM:
        return [_along(lon1, lat1, lon2, lat2, 0.5)]
    f = CONTROL_KM / distance_km
    return [_along(lon1, lat1, lon2, lat2, f), _along(lon1, lat1, lon2, lat2, 1 - f)]


def path_features(tx_grid: str, rx_grid: str, when: _dt.datetime) -> dict:
    """Distance, bearing, hops, control points and day/night for a tx->rx path."""
    d, brg = mh.distance_bearing(tx_grid, rx_grid)
    n, _ = _hops.likely_hops(d)
    (lon1, lat1), (lon2, lat2) = mh.center(tx_grid), mh.center(rx_grid)
    cps = control_points(lon1, lat1, lon2, lat2, d)
    szas = [solar_zenith_deg(lat, lon, when) for lon, lat in cps]
    out = {"grid_precision": min(len(tx_grid), len(rx_grid)), "distance_km": round(d, 1),
           "bearing_deg": round(brg, 1), "n_min_hops": n,
           "cp1_lon": round(cps[0][0], 3), "cp1_lat": round(cps[0][1], 3), "cp1_sza_deg": round(szas[0], 1),
           "day_night": day_night(szas)}
    if len(cps) > 1:
        out.update(cp2_lon=round(cps[1][0], 3), cp2_lat=round(cps[1][1], 3), cp2_sza_deg=round(szas[1], 1))
    return out


def _base(call):
    parts = [p for p in call.upper().split("/") if p]
    return max(parts, key=len) if parts else ""


# ---------------------------------------------------------------- building
@dataclass
class _Obs:
    time: _dt.datetime
    mhz: float
    mode: str
    band: str
    kind: str
    tx: str
    rx: str
    snr: int
    ex: object = None          # exchange.Exchange, or None
    n: int = 1


def observations(rows, opts: Options):
    """(observations in the window, grid cache, Stats) from time-sorted alltxt.Row's.

    Every row is fed to the exchange tracker (and teaches grids), including
    rows before opts.start, so exchanges and grids that began earlier are
    known; only measurements from opts.start to opts.end are kept."""
    me = opts.my_call.strip().upper()
    tracker = ExchangeTracker(me)
    grids: Dict[str, str] = {}
    if me and mh.is_valid(opts.my_grid.strip()):
        grids[me] = mh.display(opts.my_grid.strip())
    out: List[_Obs] = []
    reports: Dict[tuple, _Obs] = {}
    stats = Stats()

    def inside(t):
        return (opts.start is None or t >= opts.start) and (opts.end is None or t < opts.end)

    for r in rows:
        band = band_for_mhz(r.mhz) or ""
        if opts.band and band != opts.band:
            continue
        p = ft8msg.parse_message(r.message)
        if p is None:
            if r.rxtx == "Rx" and inside(r.time):
                stats.unparsed += 1
            continue
        if p.grid:
            old = grids.get(p.sender, "")
            if not old.upper().startswith(p.grid.upper()):     # keep a finer grid (e.g. my 6-char one)
                grids[p.sender] = p.grid
        is_rx = r.rxtx == "Rx"
        ex = tracker.on_decode(p, r.snr if is_rx else None, band, r.mode, r.time)
        if not inside(r.time):
            continue
        mhz = r.mhz + r.df / 1e6
        if is_rx and me:
            out.append(_Obs(r.time, mhz, r.mode, band, MY_DECODE, p.sender, me, r.snr, ex))
        if ex is not None and p.report is not None:
            key = (ex.id, p.sender, p.report)
            o = reports.get(key)
            if o is None:
                o = reports[key] = _Obs(r.time, mhz, r.mode, band, EXCHANGE_REPORT, p.to, p.sender, p.report, ex)
                out.append(o)
            else:
                o.n += 1
    return out, grids, stats


def build(rows, opts: Options):
    """(list of row dicts with COLUMNS keys, Stats)."""
    obs, grids, stats = observations(rows, opts)
    salt = opts.salt
    names: Dict[str, str] = {}

    def name(call):
        if not call or not opts.pseudonymise:
            return call
        v = names.get(call)
        if v is None:
            v = names[call] = pseudonym(call, salt)
        return v

    def grid_of(call):
        g = grids.get(call) or grids.get(_base(call)) or ""
        return mh.display(mh.truncate(g, opts.grid_chars)) if g else ""

    me = name(opts.my_call.strip().upper())
    ex_ids: Dict[int, str] = {}
    out = []
    calls = set()
    for o in obs:
        row = dict.fromkeys(COLUMNS, "")
        exid = ""
        if o.ex is not None:
            exid = ex_ids.get(o.ex.id)
            if exid is None:
                exid = ex_ids[o.ex.id] = exchange_key(name(o.ex.a), name(o.ex.b), o.ex.band, o.ex.first_utc)
        tg, rg = grid_of(o.tx), grid_of(o.rx)
        censored = 1 if o.snr <= opts.floor_db else 0
        row.update(schema_version=SCHEMA_VERSION, source=SOURCE_ALLTXT, observer=me,
                   time_utc=o.time.strftime("%Y-%m-%dT%H:%M:%SZ"), band=o.band, freq_mhz=round(o.mhz, 6),
                   mode=o.mode, report_kind=o.kind, exchange_id=exid, tx_call=name(o.tx), tx_grid=tg,
                   rx_call=name(o.rx), rx_grid=rg, snr_db=o.snr, censored=censored, n_decoded=o.n)
        if tg and rg:
            row.update(path_features(tg, rg, o.time))
            stats.with_both_grids += 1
        out.append(row)
        calls.update((o.tx, o.rx))
        stats.by_kind[o.kind] = stats.by_kind.get(o.kind, 0) + 1
        stats.censored += censored
        stats.first_utc = o.time if stats.first_utc is None else min(stats.first_utc, o.time)
        stats.last_utc = o.time if stats.last_utc is None else max(stats.last_utc, o.time)
    out.sort(key=lambda r: (r["time_utc"], r["report_kind"], r["tx_call"], r["rx_call"]))
    stats.rows = len(out)
    stats.exchanges = len(ex_ids)
    stats.stations = len(calls)
    return out, stats


# ---------------------------------------------------------------- writing
def schema_path(csv_path: str) -> str:
    """'<name>.schema.json' next to '<name>.csv'."""
    root, ext = os.path.splitext(csv_path)
    return (root if ext.lower() == ".csv" else csv_path) + ".schema.json"


def write_csv(path: str, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def schema_doc(stats: Stats, opts: Options, plugin_version: str = "", csv_name: str = "",
               now: Optional[_dt.datetime] = None) -> dict:
    """The sidecar description. The salt itself is never written."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "data_file": csv_name,
        "format": "CSV, UTF-8, comma-separated, one header row; empty cell = unknown",
        "generator": GENERATOR,
        "plugin_version": plugin_version,
        "created_utc": _iso(now),
        "source": SOURCE_ALLTXT,
        "time_span": {"requested_start_utc": _iso(opts.start), "requested_end_utc": _iso(opts.end),
                      "first_row_utc": _iso(stats.first_utc), "last_row_utc": _iso(stats.last_utc)},
        "band_filter": opts.band or None,
        "row_counts": {"total": stats.rows, EXCHANGE_REPORT: stats.by_kind.get(EXCHANGE_REPORT, 0),
                       MY_DECODE: stats.by_kind.get(MY_DECODE, 0), "censored": stats.censored,
                       "with_both_grids": stats.with_both_grids, "exchanges": stats.exchanges,
                       "stations": stats.stations},
        "decode_floor_db": opts.floor_db,
        "grid_chars_max": opts.grid_chars,
        "pseudonymised": bool(opts.pseudonymise),
        "pseudonym_method": ("SHA-256 of salt + newline + upper-case callsign, first %d hex characters; "
                             "the salt is not included" % PSEUDONYM_HEX) if opts.pseudonymise else None,
        "control_points": "midpoint if distance_km <= 2000, else 1000 km from each end",
        "day_night_rule": "day: zenith < 90; twilight: 90 to 108; night: >= 108 degrees",
        "fields": [{"name": n, "type": t, "unit": u, "description": d} for n, t, u, d in FIELDS],
    }


def write_schema(path: str, doc: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def plugin_version(metadata_path: Optional[str] = None) -> str:
    """The plugin's version from metadata.txt, or '' if it can't be read."""
    path = metadata_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metadata.txt")
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
        return cp.get("general", "version", fallback="")
    except (configparser.Error, OSError):
        return ""
