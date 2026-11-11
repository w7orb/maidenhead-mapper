"""Who sent an FT8/FT4 message, and their grid if the message carries one.

Standard message shapes (WSJT-X 77-bit):
    CQ K1ABC FN42            CQ DX K1ABC FN42        CQ POTA K1ABC FN42
    CQ 290 K1ABC FN42        CQ K1ABC                (no grid)
    W7ORB K1ABC FN42         W7ORB K1ABC R FN42      (grid exchange)
    W7ORB K1ABC -12          W7ORB K1ABC R-12        W7ORB K1ABC RR73 / RRR / 73
    <W7ORB> K1ABC/P FN42     hashed or compound callsigns

The sender is the second callsign (or the one after CQ [modifier]). 'RR73'
looks like a grid square but is a sign-off; it is never treated as a grid.
Free-text and telemetry messages return None.

Directed messages also carry the exchange step: a signal report ('-12', or
'R-12' as an acknowledgement) is how the *sender* hears the *addressee*, in
dB; 'RR73', 'RRR' and '73' sign off.
"""

import re
from dataclasses import dataclass
from typing import Optional

_GRID4 = re.compile(r"^[A-R]{2}[0-9]{2}$")
# A callsign: letters/digits with at least one digit and one letter, optional /affixes.
_CALL = re.compile(r"^(?=[A-Z0-9/]*[0-9])(?=[A-Z0-9/]*[A-Z])[A-Z0-9]{1,4}(?:/?[A-Z0-9]{1,6})*(?:/[A-Z0-9]{1,4})?$")
_NOT_GRIDS = {"RR73"}
_REPORT = re.compile(r"^(R)?([+-]\d{2})$")
SIGNOFFS = ("RR73", "RRR", "73")


@dataclass
class Parsed:
    sender: str                 # upper-case, <> removed
    to: Optional[str]           # addressee, None for CQ
    grid: Optional[str]         # 4-char grid if the message carries one
    is_cq: bool
    report: Optional[int] = None    # dB: how the sender hears the addressee
    roger: bool = False             # 'R' acknowledgement ('R-12', 'R FN42', 'RRR', 'RR73')
    signoff: Optional[str] = None   # 'RR73' | 'RRR' | '73'


def clean_call(token: str) -> Optional[str]:
    """Strip hash brackets; None for '<...>' (unresolved hash) or non-callsigns."""
    t = token.strip().upper()
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1]
    if not t or t == "..." or not _CALL.match(t):
        return None
    return t


def is_grid(token: str) -> bool:
    t = token.upper()
    return bool(_GRID4.match(t)) and t not in _NOT_GRIDS


def parse_message(message: str) -> Optional[Parsed]:
    words = message.strip().upper().split()
    if len(words) < 2:
        return None

    if words[0] == "CQ" or words[0].startswith("CQ_"):
        rest = words[1:]
        # 'CQ DX K1ABC FN42', 'CQ POTA ...', 'CQ 290 ...': drop the modifier when
        # it isn't itself the callsign (a modifier has no digit, or is all digits).
        if len(rest) >= 2 and (rest[0].isdigit() or not any(c.isdigit() for c in rest[0])):
            rest = rest[1:]
        if not rest:
            return None
        sender = clean_call(rest[0])
        if sender is None:
            return None
        grid = rest[1] if len(rest) > 1 and is_grid(rest[1]) else None
        return Parsed(sender, None, grid, True)

    if len(words) < 2:
        return None
    to, sender = clean_call(words[0]), clean_call(words[1])
    if sender is None:
        return None
    tail = words[2:]
    roger = False
    if tail and tail[0] == "R" and len(tail) > 1:
        tail, roger = tail[1:], True
    grid = tail[0] if tail and is_grid(tail[0]) else None
    report = signoff = None
    if len(tail) == 1 and grid is None:
        m = _REPORT.match(tail[0])
        if m:
            report, roger = int(m.group(2)), roger or bool(m.group(1))
        elif tail[0] in SIGNOFFS:
            signoff = tail[0]
            roger = roger or tail[0] != "73"
    return Parsed(sender, to, grid, False, report, roger, signoff)
