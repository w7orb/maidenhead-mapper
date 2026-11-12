"""A small synthetic ALL.TXT: two hours, three bands' worth of known exchanges.

21:00 UTC, 20m: K1ABC (FN31) and W4XYZ (EM78) complete a QSO, reports both ways.
22:00 UTC, 15m: K1ABC and JA1AAA (PM95) exchange reports both ways;
                W4XYZ calls VK2BBB (QF56), no answer decoded (no reports);
                K1ABC sends a report to W9NOG, who never sends a grid (left out).
Decoded at N0CALL (CN85), who transmits once (a Tx line).
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


def lines():
    out = []
    for secs, mhz, rxtx, snr, msg in _SCRIPT:
        t = T0 + dt.timedelta(seconds=secs)
        out.append(f"{t:%y%m%d_%H%M%S} {mhz:9.3f} {rxtx} FT8 {snr:6d}  0.1 1500 {msg}")
    return out


def write(path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines()) + "\n")
    return str(path)
