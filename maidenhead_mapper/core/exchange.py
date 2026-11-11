"""Reconstruct FT8/FT4 exchanges between any two stations from decodes.

Pure Python, no QGIS. A standard exchange, as decoded off the air:

    AH6U N5ER EM34      N5ER answers AH6U, sends grid
    N5ER AH6U +07       AH6U hears N5ER at +07 dB
    AH6U N5ER R-04      N5ER: roger; hears AH6U at -04 dB
    N5ER AH6U RR73      AH6U signs off
    AH6U N5ER 73

Every directed message between the same two calls on the same band joins
one Exchange until the pair falls silent for IDLE; then a new one starts.
Each report is stored as "how the sender hears the addressee", so a full
exchange gives the SNR both ways, and my own decodes add how *I* hear each
side: a triangle of signal reports.

My own transmissions are never decoded back to me, so my side of my own
QSOs comes from WSJT-X's Status (who I'm working, the report I send) and
QSO Logged (both reports, confirmed).
"""

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

IDLE = _dt.timedelta(minutes=3)
MAX_STEPS = 12          # transcript length kept per exchange

# Stages, in order
STARTED, ONE_REPORT, BOTH_REPORTS, CONFIRMED = "started", "one report", "both reports", "confirmed"
STAGES = (STARTED, ONE_REPORT, BOTH_REPORTS, CONFIRMED)


@dataclass
class Exchange:
    id: int
    a: str                          # first station seen sending (usually the one answering a CQ)
    b: str
    band: str = ""
    mode: str = ""
    first_utc: Optional[_dt.datetime] = None
    last_utc: Optional[_dt.datetime] = None
    a_hears_b: Optional[int] = None     # dB, as reported by A to B
    b_hears_a: Optional[int] = None     # dB, as reported by B to A
    me_hears_a: Optional[int] = None    # dB, my last decode of A in this exchange
    me_hears_b: Optional[int] = None
    signed_off: bool = False
    involves_me: bool = False
    logged: bool = False                # my QSO, logged in WSJT-X
    messages: int = 0
    steps: List[str] = field(default_factory=list)   # "N5ER: EM34", ...

    @property
    def stage(self):
        reports = (self.a_hears_b is not None) + (self.b_hears_a is not None)
        if self.logged or (reports == 2 and self.signed_off):
            return CONFIRMED
        return (STARTED, ONE_REPORT, BOTH_REPORTS)[reports]

    def contrasts(self):
        """(report_diff, b_vs_me, a_vs_me) in dB, None where a number is missing.

        report_diff = how B heard A minus how A heard B: path and antennas
                      cancel, leaving A's (power + noise floor) minus B's
        b_vs_me     = how B heard A minus how I heard A
        a_vs_me     = how A heard B minus how I heard B
        Positive *_vs_me: they heard that signal better than I did.
        """
        def diff(x, y):
            return None if x is None or y is None else x - y
        return (diff(self.b_hears_a, self.a_hears_b), diff(self.b_hears_a, self.me_hears_a),
                diff(self.a_hears_b, self.me_hears_b))

    def other(self, call):
        return self.b if call == self.a else self.a

    def set_report(self, sender, db):
        if sender == self.a:
            self.a_hears_b = db
        else:
            self.b_hears_a = db

    def set_my_snr(self, sender, db):
        if sender == self.a:
            self.me_hears_a = db
        else:
            self.me_hears_b = db

    def transcript(self):
        return " | ".join(self.steps)

    def _step(self, sender, text):
        """Add to the transcript; a repeat of the previous step becomes 'x2', 'x3'..."""
        self.messages += 1
        step = f"{sender}: {text}"
        if self.steps:
            last, _, n = self.steps[-1].partition(" ×")
            if last == step:
                self.steps[-1] = f"{step} ×{int(n or 1) + 1}"
                return
        if len(self.steps) < MAX_STEPS:
            self.steps.append(step)


PairKey = Tuple[frozenset, str]   # ({call1, call2}, band)


class ExchangeTracker:
    """Feed decodes (and my Status / QSO Logged) in time order."""

    def __init__(self, my_call: str = ""):
        self.my_call = my_call.upper()
        self.exchanges: Dict[int, Exchange] = {}
        self._open: Dict[PairKey, int] = {}
        self._next_id = 1
        self._dirty = set()

    # -------------------------------------------------------------- input
    def on_decode(self, parsed, snr, band, mode, now):
        """A decoded message (ft8msg.Parsed) heard at `snr` dB. Returns the
        Exchange it joined, or None for CQs and undirected messages.
        snr=None for my own transmissions (ALL.TXT Tx lines)."""
        if parsed is None or parsed.is_cq or not parsed.to or parsed.to == parsed.sender:
            return None
        ex = self._get(parsed.sender, parsed.to, band, mode, now)
        if snr is not None:
            ex.set_my_snr(parsed.sender, snr)
        if parsed.report is not None:
            ex.set_report(parsed.sender, parsed.report)
        if parsed.signoff:
            ex.signed_off = True
        ex._step(parsed.sender, _text(parsed))
        self._dirty.add(ex.id)
        return ex

    def on_my_status(self, dx_call, report, transmitting, band, mode, now):
        """WSJT-X Status while I transmit to dx_call: my side of my exchange."""
        if not (transmitting and dx_call and self.my_call):
            return None
        ex = self._get(self.my_call, dx_call.upper(), band, mode, now)
        db = _db(report)
        if db is not None and (ex.a_hears_b if ex.a == self.my_call else ex.b_hears_a) != db:
            ex.set_report(self.my_call, db)
            ex._step(self.my_call, f"{db:+03d} (sent)")
        self._dirty.add(ex.id)
        return ex

    def on_logged(self, dx_call, report_sent, report_rcvd, band, mode, now):
        """QSO Logged: my exchange with dx_call is confirmed."""
        if not (dx_call and self.my_call):
            return None
        dx = dx_call.upper()
        ex = self._get(self.my_call, dx, band, mode, now)
        sent, rcvd = _db(report_sent), _db(report_rcvd)
        if sent is not None:
            ex.set_report(self.my_call, sent)       # how I hear them
        if rcvd is not None:
            ex.set_report(dx, rcvd)                 # how they hear me
        ex.logged = ex.signed_off = True
        ex._step(self.my_call, "logged")
        self._dirty.add(ex.id)
        return ex

    # ------------------------------------------------------------- output
    def take_dirty(self):
        ids, self._dirty = self._dirty, set()
        return ids

    def counts(self):
        out = {s: 0 for s in STAGES}
        for ex in self.exchanges.values():
            out[ex.stage] += 1
        return out

    # ------------------------------------------------------------ helpers
    def _get(self, sender, to, band, mode, now):
        key = (frozenset((sender, to)), band or "")
        ex = self.exchanges.get(self._open.get(key, 0))
        if ex is None or (ex.last_utc and now - ex.last_utc > IDLE):
            ex = Exchange(self._next_id, sender, to, band or "", mode or "", first_utc=now)
            ex.involves_me = bool(self.my_call) and _base(self.my_call) in (_base(sender), _base(to))
            self.exchanges[ex.id] = ex
            self._open[key] = ex.id
            self._next_id += 1
        ex.last_utc = now
        if mode and not ex.mode:
            ex.mode = mode
        return ex


def _text(p):
    if p.report is not None:
        return ("R" if p.roger else "") + f"{p.report:+03d}"
    if p.signoff:
        return p.signoff
    if p.grid:
        return ("R " if p.roger else "") + p.grid
    return "…"


def _db(report):
    """'-12', 'R-12', '+05', -12 -> int dB; anything else -> None."""
    if isinstance(report, int):
        return report
    t = (report or "").strip().upper().lstrip("R").strip()
    try:
        v = int(t)
    except ValueError:
        return None
    return v if -50 <= v <= 60 else None


def _base(call):
    parts = [p for p in call.upper().split("/") if p]
    return max(parts, key=len) if parts else ""
