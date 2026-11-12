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
    not_decoded      (schema 1.1) a transmission that was made but not
                     decoded at rx: S sent T a message, T's answer to it
                     was on the air (I decoded it, or sent it myself) and
                     S sent the same message again in its next slot. No
                     SNR (empty), censored = 1. See not_decoded().

exchange_id links both directions of an exchange and my decodes of either
side. Path features (distance, bearing, fewest hops, control points and
day/night there) come from the two grids' centres. With a model source
(schema 1.1), each row also gets the path's basic MUF from the CCIR model
at its time (Check paths' CCIR method), f/MUF, and the sporadic-E rule
of the plugin's detector.

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
from . import es as _es
from . import ft8msg
from . import hops as _hops
from . import iono as _iono
from . import maidenhead as mh
from .bands import band_for_mhz
from .exchange import ExchangeTracker
from .iono import _along

SCHEMA_VERSION = "1.1"
SOURCE_ALLTXT = "alltxt"
EXCHANGE_REPORT, MY_DECODE, NOT_DECODED = "exchange_report", "my_decode", "not_decoded"
SLOT_S = {"FT8": 15.0, "FT4": 7.5}   # T/R period; not_decoded needs one of these modes
SLOT_TOLERANCE_S = 1.5
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
                                  "\"my_decode\": the observer's receiver decoded tx. \"not_decoded\" (1.1): "
                                  "tx transmitted to rx and rx then repeated its previous message, so rx did not "
                                  "decode (or act on) it; no SNR."),
    ("exchange_id", "string", "", "Links rows of one exchange (both directions and the observer's decodes of each "
                                  "side). Empty for decodes outside an exchange (CQs)."),
    ("tx_call", "string", "", "Transmitting station (pseudonymised if the export is)."),
    ("tx_grid", "string", "Maidenhead", "Transmitter's grid; empty if never decoded."),
    ("rx_call", "string", "", "Receiving station: the one that measured the SNR (pseudonymised if the export is)."),
    ("rx_grid", "string", "Maidenhead", "Receiver's grid; empty if never decoded."),
    ("snr_db", "integer", "dB", "Signal-to-noise ratio in 2500 Hz, as WSJT-X reports it. Empty for not_decoded."),
    ("censored", "integer", "0/1", "1 if snr_db is at or below the decode floor (the true SNR may be lower); "
                                   "always 1 for not_decoded."),
    ("n_decoded", "integer", "", "Times this measurement was decoded (repeats of the same report in one exchange); "
                                 "1 for my_decode, 0 for not_decoded."),
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
    # ---- added in schema 1.1 (appended, so 1.0 readers keep working)
    ("muf_basic_mhz", "float", "MHz", "Basic MUF of the path at time_utc from the CCIR (ITU-R P.1239) monthly-"
                                      "median model, by ITU-R P.533 (lowest control point, fewest hops), as Check "
                                      "paths computes it. Empty without both grids or a model value."),
    ("f_over_muf", "float", "", "freq_mhz / muf_basic_mhz. Over 1: above the basic MUF (Es, scatter, or a day "
                                "better than the monthly median)."),
    ("es_candidate", "integer", "0/1", "1 if the plugin's sporadic-E rule says Es (high confidence): 12m-2m at "
                                       "single-Es-hop distance (500 km to one hop off 105 km), and on 12m/10m "
                                       "above the basic MUF. Empty if it can't be told (no grids; 12m/10m in range "
                                       "without a MUF)."),
    ("foes_min_mhz", "float", "MHz", "For es_candidate = 1: the lowest Es critical frequency that returns "
                                     "freq_mhz at this distance (secant law, Es at 105 km)."),
]
COLUMNS = [f[0] for f in FIELDS]
COLUMNS_1_0 = COLUMNS[:COLUMNS.index("muf_basic_mhz")]     # schema 1.0: a prefix of COLUMNS


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
    muf_source: object = None                # conditions(when) -> (cond, label, note), e.g. ccir.ModelSource;
                                             # None: muf_basic_mhz / f_over_muf empty


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
    with_muf: int = 0
    es_candidates: int = 0
    muf_labels: Dict[str, int] = field(default_factory=dict)   # model label (R12 used) -> rows
    muf_missing: Dict[str, int] = field(default_factory=dict)  # why rows with both grids got no MUF -> rows


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
    snr: Optional[int]         # None for not_decoded
    ex: object = None          # exchange.Exchange, or None
    n: int = 1


@dataclass
class _Msg:
    """A directed message in an exchange, as decoded by me (or sent by me)."""
    time: _dt.datetime
    sender: str
    to: str
    stage: Optional[int]
    text: str
    mhz: float
    mode: str
    band: str


def _stage(p, message) -> Optional[int]:
    """Place of a message in the standard sequence: 0 call (with or without grid),
    1 report, 2 roger report, 3 RR73/RRR, 4 73; None for anything else."""
    if p.signoff == "73":
        return 4
    if p.signoff in ("RR73", "RRR"):
        return 3
    if p.report is not None:
        return 2 if p.roger else 1
    if not p.roger and (p.grid or len(message.split()) == 2):
        return 0
    return None


def not_decoded(msgs, me=""):
    """not_decoded evidence in one exchange's messages (time order): (rx S, the _Msg from T) where

    * S sent T a message at stage 0-2 (call/grid, report, roger report),
    * in the next slot T sent S the answer to it (the next stage), and
    * in the slot after that S sent T exactly the same message again.

    WSJT-X repeats a message until it decodes the answer, so S did not decode
    (or did not act on) T's answer. Needs FT8/FT4 and the three messages in
    consecutive slots, one period apart; S must not be me (I may have decoded
    it and repeated anyway). It says nothing about why: weak signal, QRM,
    S's operator, or a decode that came too late."""
    me_base = _base(me) if me else ""
    seq = []
    for m in msgs:          # one message per sender per slot
        if seq and seq[-1].sender == m.sender and abs((m.time - seq[-1].time).total_seconds()) < SLOT_TOLERANCE_S:
            continue
        seq.append(m)
    out = []
    for s1, m, s2 in zip(seq, seq[1:], seq[2:]):
        period = SLOT_S.get(s1.mode)
        if period is None or not (s1.mode == m.mode == s2.mode):
            continue
        if not (s1.sender == s2.sender == m.to and m.sender == s1.to == s2.to and s1.sender != m.sender):
            continue
        if s1.text != s2.text or s1.stage is None or s1.stage > 2 or m.stage != s1.stage + 1:
            continue
        if me_base and _base(s1.sender) == me_base:
            continue
        if (abs((m.time - s1.time).total_seconds() - period) > SLOT_TOLERANCE_S
                or abs((s2.time - m.time).total_seconds() - period) > SLOT_TOLERANCE_S):
            continue
        out.append((s1.sender, m))
    return out


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
    msgs: Dict[int, List[_Msg]] = {}
    ex_by_id = {}
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
        mhz = r.mhz + r.df / 1e6
        if ex is not None:
            msgs.setdefault(ex.id, []).append(_Msg(r.time, p.sender, p.to, _stage(p, r.message),
                                                   " ".join(r.message.upper().split()), mhz, r.mode, band))
            ex_by_id[ex.id] = ex
        if not inside(r.time):
            continue
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
    for exid, seq in msgs.items():
        for s, m in not_decoded(seq, me):
            if inside(m.time):
                out.append(_Obs(m.time, m.mhz, m.mode, m.band, NOT_DECODED, m.sender, s, None, ex_by_id[exid], 0))
    return out, grids, stats


class PathMuf:
    """Basic MUF of a path at a time, from a model source (Check paths' CCIR option):
    iono.analyse_path() with the source's conditions for that time. Cached per
    (grid pair, model time and R12); the conditions cache values per 0.5 degree cell."""

    def __init__(self, source):
        self.source = source
        self._cond = {}       # time -> (conditions or None, label or why not)
        self._muf = {}

    def muf(self, grid_a, grid_b, when):
        """(MUF in MHz, model label), or (None, why not)."""
        c = self._cond.get(when)
        if c is None:
            cond, label, note = self.source.conditions(when)
            c = self._cond[when] = (cond, label if cond is not None else (note or "no model data"))
        cond, label = c
        if cond is None:
            return None, label
        a, b = sorted((grid_a, grid_b))
        key = (a, b, id(cond))
        v = self._muf.get(key)
        if v is None:
            (x1, y1), (x2, y2) = mh.center(a), mh.center(b)
            v = self._muf[key] = (_iono.analyse_path(x1, y1, x2, y2, 0.0, cond).muf_mhz,)
        return v[0], (label if v[0] else "no MUF for the path (e.g. same grid)")


def muf_features(freq_mhz, band, distance_km, muf_mhz):
    """muf_basic_mhz, f_over_muf, es_candidate, foes_min_mhz for a path with both grids."""
    out = {}
    status = None
    if muf_mhz:
        out.update(muf_basic_mhz=round(muf_mhz, 2), f_over_muf=round(freq_mhz / muf_mhz, 3))
        status = _iono.status_for(freq_mhz, muf_mhz)
    conf = _es.classify(distance_km, band, status)
    if conf == _es.HIGH:
        out.update(es_candidate=1, foes_min_mhz=round(_es.foes_min_mhz(freq_mhz, distance_km), 2))
    elif conf is None:
        out["es_candidate"] = 0
    return out


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
    pm = PathMuf(opts.muf_source) if opts.muf_source is not None else None
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
        censored = 1 if o.snr is None or o.snr <= opts.floor_db else 0
        row.update(schema_version=SCHEMA_VERSION, source=SOURCE_ALLTXT, observer=me,
                   time_utc=o.time.strftime("%Y-%m-%dT%H:%M:%SZ"), band=o.band, freq_mhz=round(o.mhz, 6),
                   mode=o.mode, report_kind=o.kind, exchange_id=exid, tx_call=name(o.tx), tx_grid=tg,
                   rx_call=name(o.rx), rx_grid=rg, snr_db="" if o.snr is None else o.snr, censored=censored,
                   n_decoded=o.n)
        if tg and rg:
            row.update(path_features(tg, rg, o.time))
            stats.with_both_grids += 1
            muf = None
            if pm is not None:
                muf, label = pm.muf(tg, rg, o.time)
                if muf:
                    stats.with_muf += 1
                    stats.muf_labels[label] = stats.muf_labels.get(label, 0) + 1
                else:
                    stats.muf_missing[label] = stats.muf_missing.get(label, 0) + 1
            row.update(muf_features(o.mhz, o.band, row["distance_km"], muf))
            stats.es_candidates += row["es_candidate"] == 1
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
                       "stations": stats.stations, NOT_DECODED: stats.by_kind.get(NOT_DECODED, 0),
                       "with_muf": stats.with_muf, "es_candidate": stats.es_candidates},
        "decode_floor_db": opts.floor_db,
        "grid_chars_max": opts.grid_chars,
        "pseudonymised": bool(opts.pseudonymise),
        "pseudonym_method": ("SHA-256 of salt + newline + upper-case callsign, first %d hex characters; "
                             "the salt is not included" % PSEUDONYM_HEX) if opts.pseudonymise else None,
        "control_points": "midpoint if distance_km <= 2000, else 1000 km from each end",
        "day_night_rule": "day: zenith < 90; twilight: 90 to 108; night: >= 108 degrees",
        "muf_model": ({"model": "CCIR (ITU-R P.1239) monthly-median foF2 and M(3000)F2 at the path's time "
                                "(rounded down to 15 min); path basic MUF by ITU-R P.533-14, as Check paths. "
                                "Not KC2G: it only has the present.",
                       "r12_used": dict(sorted(stats.muf_labels.items())),
                       "rows_without_muf": dict(sorted(stats.muf_missing.items()))}
                      if opts.muf_source is not None else None),
        "es_rule": ("es_candidate = 1 where the plugin's sporadic-E detector says 'high': bands %s at %d km to one "
                    "Es hop (%d km, layer at %d km); on 12m/10m only above the basic MUF"
                    % ("/".join(_es.ES_BANDS), _es.MIN_KM, round(_es.max_single_hop_km()), _es.ES_HEIGHT_KM)),
        "not_decoded_rule": ("S sent T a message (call/grid, report or roger report); in the next slot T sent S "
                             "the answer (the next step); in the slot after, S sent the same message again. "
                             "Row: tx = T, rx = S, time = T's message. FT8/FT4 only; S is never the observer. "
                             "Evidence that S did not decode (or act on) that transmission, not that the "
                             "path was closed."),
        "changes": CHANGES,
        "fields": [{"name": n, "type": t, "unit": u, "description": d} for n, t, u, d in FIELDS],
    }


CHANGES = {
    "1.1": "Added columns muf_basic_mhz, f_over_muf, es_candidate, foes_min_mhz (at the end); report_kind "
           "not_decoded (snr_db empty, censored 1, n_decoded 0); row_counts not_decoded, with_muf, es_candidate "
           "(row_counts.censored now includes the not_decoded rows); muf_model, es_rule, not_decoded_rule, "
           "changes. Rows and columns of 1.0 are otherwise unchanged.",
    "1.0": "First version.",
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
