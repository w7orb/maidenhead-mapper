"""WSJT-X UDP message protocol: decode and encode. Pure Python (struct).

WSJT-X (and JTDX/MSHV) send Qt QDataStream-encoded datagrams, big-endian,
described in WSJT-X's NetworkMessage.hpp:

    quint32 magic 0xADBCCBDA | quint32 schema (2 or 3) | quint32 type | utf8 id | payload

Only the messages the map needs are decoded: Heartbeat (0), Status (1),
Decode (2), Clear (3), QSO Logged (5), Close (6). Others parse to None.
Newer WSJT-X versions append fields; trailing fields this module doesn't
know are ignored, and fields missing from older versions get defaults.

The encoder is used by the tests and tools/replay_alltxt.py.
"""

import datetime as _dt
import struct
from dataclasses import dataclass
from typing import Optional

MAGIC = 0xADBCCBDA
SCHEMA = 2
HEARTBEAT, STATUS, DECODE, CLEAR, REPLY, QSO_LOGGED, CLOSE = 0, 1, 2, 3, 4, 5, 6
_NULL = 0xFFFFFFFF
_JD_OFFSET = 1721425  # Julian day number = date.toordinal() + this


class ProtocolError(ValueError):
    """Not a WSJT-X datagram, or a malformed one."""


@dataclass
class Heartbeat:
    id: str
    max_schema: int = 0
    version: str = ""
    revision: str = ""


@dataclass
class Status:
    id: str
    dial_hz: int = 0
    mode: str = ""
    dx_call: str = ""
    report: str = ""
    tx_mode: str = ""
    tx_enabled: bool = False
    transmitting: bool = False
    decoding: bool = False
    rx_df: int = 0
    tx_df: int = 0
    de_call: str = ""
    de_grid: str = ""
    dx_grid: str = ""


@dataclass
class Decode:
    id: str
    new: bool = True
    time_ms: Optional[int] = None   # ms since UTC midnight (slot start)
    snr: int = 0
    dt: float = 0.0
    df: int = 0                      # audio offset, Hz
    mode: str = ""                   # single character: ~ FT8, + FT4, ...
    message: str = ""
    low_confidence: bool = False
    off_air: bool = False


@dataclass
class Clear:
    id: str
    window: int = 0


@dataclass
class QsoLogged:
    id: str
    time_off: Optional[_dt.datetime] = None
    dx_call: str = ""
    dx_grid: str = ""
    tx_hz: int = 0
    mode: str = ""
    report_sent: str = ""
    report_rcvd: str = ""
    tx_power: str = ""
    comments: str = ""
    name: str = ""
    time_on: Optional[_dt.datetime] = None
    operator_call: str = ""
    my_call: str = ""
    my_grid: str = ""


@dataclass
class Close:
    id: str


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def _take(self, fmt):
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise EOFError
        (v,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return v

    def u8(self):
        return self._take(">B")

    def bool(self):
        return self._take(">B") != 0

    def u32(self):
        return self._take(">I")

    def i32(self):
        return self._take(">i")

    def u64(self):
        return self._take(">Q")

    def i64(self):
        return self._take(">q")

    def f64(self):
        return self._take(">d")

    def utf8(self):
        n = self.u32()
        if n == _NULL:
            return ""
        if self.pos + n > len(self.data):
            raise EOFError
        s = self.data[self.pos:self.pos + n].decode("utf-8", errors="replace")
        self.pos += n
        return s

    def qtime(self):
        ms = self.u32()
        return None if ms == _NULL else ms

    def qdatetime(self):
        jd = self.i64()
        ms = self.u32()
        spec = self.u8()
        offset = 0
        if spec == 2:
            offset = self.i32()
        elif spec == 3:
            raise ProtocolError("QDateTime with a named time zone is not supported")
        if jd == 0 or ms == _NULL:
            return None
        day = _dt.date.fromordinal(jd - _JD_OFFSET)
        t = _dt.datetime.combine(day, _dt.time()) + _dt.timedelta(milliseconds=ms)
        if spec == 0:  # local time: WSJT-X uses UTC; treat as naive UTC
            return t.replace(tzinfo=_dt.timezone.utc)
        return (t - _dt.timedelta(seconds=offset)).replace(tzinfo=_dt.timezone.utc)


def _fill(msg, reader, readers):
    """Read fields in order; stop quietly when an older sender has fewer."""
    for name, read in readers:
        try:
            setattr(msg, name, read(reader))
        except EOFError:
            break
    return msg


_U8, _B, _U32, _I32, _U64, _F64, _S = (_Reader.u8, _Reader.bool, _Reader.u32, _Reader.i32,
                                       _Reader.u64, _Reader.f64, _Reader.utf8)

_FIELDS = {
    HEARTBEAT: (Heartbeat, [("max_schema", _U32), ("version", _S), ("revision", _S)]),
    STATUS: (Status, [("dial_hz", _U64), ("mode", _S), ("dx_call", _S), ("report", _S), ("tx_mode", _S),
                      ("tx_enabled", _B), ("transmitting", _B), ("decoding", _B), ("rx_df", _U32),
                      ("tx_df", _U32), ("de_call", _S), ("de_grid", _S), ("dx_grid", _S)]),
    DECODE: (Decode, [("new", _B), ("time_ms", _Reader.qtime), ("snr", _I32), ("dt", _F64), ("df", _U32),
                      ("mode", _S), ("message", _S), ("low_confidence", _B), ("off_air", _B)]),
    CLEAR: (Clear, [("window", _U8)]),
    QSO_LOGGED: (QsoLogged, [("time_off", _Reader.qdatetime), ("dx_call", _S), ("dx_grid", _S),
                             ("tx_hz", _U64), ("mode", _S), ("report_sent", _S), ("report_rcvd", _S),
                             ("tx_power", _S), ("comments", _S), ("name", _S),
                             ("time_on", _Reader.qdatetime), ("operator_call", _S), ("my_call", _S),
                             ("my_grid", _S)]),
    CLOSE: (Close, []),
}


def parse(data: bytes):
    """Decode one datagram. Returns a message object, or None for message
    types the map doesn't use. Raises ProtocolError for non-WSJT-X data."""
    r = _Reader(bytes(data))
    try:
        if r.u32() != MAGIC:
            raise ProtocolError("bad magic number")
        schema = r.u32()
        mtype = r.u32()
        client_id = r.utf8()
    except EOFError:
        raise ProtocolError("datagram too short") from None
    if schema < 1 or schema > 3:
        raise ProtocolError(f"unsupported schema {schema}")
    if mtype not in _FIELDS:
        return None
    cls, readers = _FIELDS[mtype]
    return _fill(cls(id=client_id), r, readers)


# ---------------------------------------------------------------- encoding

class _Writer:
    def __init__(self):
        self.parts = []

    def put(self, fmt, v):
        self.parts.append(struct.pack(fmt, v))
        return self

    def utf8(self, s):
        if s is None:
            return self.put(">I", _NULL)
        b = s.encode("utf-8")
        self.put(">I", len(b))
        self.parts.append(b)
        return self

    def qdatetime(self, t):
        if t is None:
            return self.put(">q", 0).put(">I", _NULL).put(">B", 1)
        t = t.astimezone(_dt.timezone.utc)
        ms = ((t.hour * 60 + t.minute) * 60 + t.second) * 1000 + t.microsecond // 1000
        return self.put(">q", t.date().toordinal() + _JD_OFFSET).put(">I", ms).put(">B", 1)

    def bytes(self):
        return b"".join(self.parts)


def _header(mtype, client_id):
    return _Writer().put(">I", MAGIC).put(">I", SCHEMA).put(">I", mtype).utf8(client_id)


def encode_heartbeat(client_id="WSJT-X", max_schema=3, version="2.7.0", revision=""):
    return _header(HEARTBEAT, client_id).put(">I", max_schema).utf8(version).utf8(revision).bytes()


def encode_status(client_id="WSJT-X", dial_hz=14074000, mode="FT8", de_call="", de_grid="", **kw):
    w = _header(STATUS, client_id).put(">Q", dial_hz).utf8(mode).utf8(kw.get("dx_call", ""))
    w.utf8(kw.get("report", "")).utf8(kw.get("tx_mode", mode))
    for b in (kw.get("tx_enabled", False), kw.get("transmitting", False), kw.get("decoding", False)):
        w.put(">B", int(b))
    w.put(">I", kw.get("rx_df", 1500)).put(">I", kw.get("tx_df", 1500))
    w.utf8(de_call).utf8(de_grid).utf8(kw.get("dx_grid", ""))
    return w.bytes()


def encode_decode(message, snr=-10, time_ms=0, client_id="WSJT-X", dt=0.0, df=1500, mode="~",
                  new=True, low_confidence=False, off_air=False):
    w = _header(DECODE, client_id).put(">B", int(new)).put(">I", time_ms).put(">i", snr)
    w.put(">d", dt).put(">I", df).utf8(mode).utf8(message)
    return w.put(">B", int(low_confidence)).put(">B", int(off_air)).bytes()


def encode_clear(client_id="WSJT-X", window=0):
    return _header(CLEAR, client_id).put(">B", window).bytes()


def encode_close(client_id="WSJT-X"):
    return _header(CLOSE, client_id).bytes()


def encode_qso_logged(dx_call, dx_grid="", tx_hz=14074000, mode="FT8", time_off=None, time_on=None,
                      client_id="WSJT-X", report_sent="-10", report_rcvd="-10", my_call="", my_grid=""):
    w = _header(QSO_LOGGED, client_id).qdatetime(time_off).utf8(dx_call).utf8(dx_grid).put(">Q", tx_hz)
    for s in (mode, report_sent, report_rcvd, "", "", ""):
        w.utf8(s)
    w.qdatetime(time_on).utf8("").utf8(my_call).utf8(my_grid).utf8("").utf8("").utf8("")
    return w.bytes()
