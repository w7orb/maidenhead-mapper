"""A small synthetic ALL.TXT: two hours, three bands' worth of known exchanges.

21:00 UTC, 20m: K1ABC (FN31) and W4XYZ (EM78) complete a QSO, reports both ways.
22:00 UTC, 15m: K1ABC and JA1AAA (PM95) exchange reports both ways;
                W4XYZ calls VK2BBB (QF56), no answer decoded (no reports);
                K1ABC sends a report to W9NOG, who never sends a grid (left out).
Decoded at N0CALL (CN85), who transmits once (a Tx line).

repeat_lines() (separate, so the counts above stay as they are) adds, on 10m
from 23:00 UTC, exchanges with repeated messages for the not_decoded rows:
  K2DEF (FN20) / W5GHI (EM12): W5GHI repeats its call after K2DEF's report
    and its R-report after K2DEF's RR73: two transmissions W5GHI missed;
  K2DEF / W7PQR (DN17): W7PQR repeats its call, but a slot later (not the
    next slot): not counted;
  N0CALL / W6MNO (DM04): W6MNO repeats its call after my report (my Tx
    line): W6MNO missed me;
  K3JKL (FN21) / N0CALL: I repeat my call after K3JKL's report: not counted
    (I decoded it; the observer is never the station that missed).
"""

import datetime as dt

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 11, 21, 0, tzinfo=UTC)

_SCRIPT = [
    # (seconds after T0, MHz, rx/tx, snr, message)
    (0, 14.074, "Rx", -10, "CQ K1ABC FN31"),
    (15, 14.074, "Rx", -14, "K1ABC W4XYZ EM78"),
    (30, 14.074, "Rx", -11, "W4XYZ K1ABC -05"),
    (45, 14.074, "Rx", -13, "K1ABC W4XYZ R-08"),
    (60, 14.074, "Rx", -10, "W4XYZ K1ABC RR73"),
    (75, 14.074, "Tx", 0, "CQ N0CALL CN85"),
    (3600, 21.074, "Rx", -18, "CQ JA1AAA PM95"),
    (3615, 21.074, "Rx", -9, "JA1AAA K1ABC FN31"),
    (3630, 21.074, "Rx", -17, "K1ABC JA1AAA -12"),
    (3645, 21.074, "Rx", -8, "JA1AAA K1ABC R-15"),
    (3660, 21.074, "Rx", -20, "CQ VK2BBB QF56"),
    (3675, 21.074, "Rx", -12, "VK2BBB W4XYZ EM78"),
    (3690, 21.074, "Rx", -7, "W9NOG K1ABC -03"),
]


T_REPEATS = T0 + dt.timedelta(hours=2)
_REPEATS = [
    # (seconds after T_REPEATS, MHz, rx/tx, snr, message)
    (0, 28.074, "Rx", -9, "CQ K2DEF FN20"),
    (15, 28.074, "Rx", -12, "K2DEF W5GHI EM12"),
    (30, 28.074, "Rx", -8, "W5GHI K2DEF -07"),      # W5GHI misses this ...
    (45, 28.074, "Rx", -12, "K2DEF W5GHI EM12"),    # ... and calls again
    (60, 28.074, "Rx", -8, "W5GHI K2DEF -07"),
    (75, 28.074, "Rx", -11, "K2DEF W5GHI R-10"),
    (90, 28.074, "Rx", -9, "W5GHI K2DEF RR73"),     # missed too ...
    (105, 28.074, "Rx", -11, "K2DEF W5GHI R-10"),   # ... R-10 again
    (120, 28.074, "Rx", -9, "W5GHI K2DEF RR73"),
    (135, 28.074, "Rx", -13, "K2DEF W5GHI 73"),
    (150, 28.074, "Rx", -10, "K2DEF W7PQR DN17"),
    (165, 28.074, "Rx", -9, "W7PQR K2DEF -05"),
    (195, 28.074, "Rx", -10, "K2DEF W7PQR DN17"),   # two slots later: not evidence
    (300, 28.074, "Rx", -6, "N0CALL W6MNO DM04"),
    (315, 28.074, "Tx", 0, "W6MNO N0CALL -04"),      # I send; W6MNO misses it ...
    (330, 28.074, "Rx", -6, "N0CALL W6MNO DM04"),   # ... and calls me again
    (400, 28.076, "Rx", -15, "CQ K3JKL FN21"),
    (405, 28.076, "Tx", 0, "K3JKL N0CALL CN85"),
    (420, 28.076, "Rx", -15, "N0CALL K3JKL -18"),
    (435, 28.076, "Tx", 0, "K3JKL N0CALL CN85"),     # I repeat: not counted
]


def _fmt(t0, script):
    out = []
    for secs, mhz, rxtx, snr, msg in script:
        t = t0 + dt.timedelta(seconds=secs)
        out.append(f"{t:%y%m%d_%H%M%S} {mhz:9.3f} {rxtx} FT8 {snr:6d}  0.1 1500 {msg}")
    return out


def repeat_lines():
    return _fmt(T_REPEATS, _REPEATS)


def lines():
    out = []
    for secs, mhz, rxtx, snr, msg in _SCRIPT:
        t = T0 + dt.timedelta(seconds=secs)
        out.append(f"{t:%y%m%d_%H%M%S} {mhz:9.3f} {rxtx} FT8 {snr:6d}  0.1 1500 {msg}")
    return out


def write(path, repeats=False):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines() + (repeat_lines() if repeats else [])) + "\n")
    return str(path)
