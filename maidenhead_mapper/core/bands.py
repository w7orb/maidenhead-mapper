"""Amateur band lookup from frequency. Pure Python.

Edges are the widest ITU allocation across regions, so a spot that is legal
anywhere is named. Anything outside these ranges returns None.
"""

from typing import Optional

# (name, low MHz, high MHz), in frequency order
BANDS = (
    ("2200m", 0.1357, 0.1378),
    ("630m", 0.472, 0.479),
    ("160m", 1.8, 2.0),
    ("80m", 3.5, 4.0),
    ("60m", 5.25, 5.45),
    ("40m", 7.0, 7.3),
    ("30m", 10.1, 10.15),
    ("20m", 14.0, 14.35),
    ("17m", 18.068, 18.168),
    ("15m", 21.0, 21.45),
    ("12m", 24.89, 24.99),
    ("10m", 28.0, 29.7),
    ("6m", 50.0, 54.0),
    ("4m", 70.0, 71.0),
    ("2m", 144.0, 148.0),
    ("1.25m", 222.0, 225.0),
    ("70cm", 420.0, 450.0),
    ("23cm", 1240.0, 1300.0),
)
BAND_NAMES = tuple(b[0] for b in BANDS)

# Standard FT8 dial frequency per band (MHz): the frequency a band name stands
# for when a path has only a band (e.g. for MUF checks).
FT8_MHZ = {"160m": 1.840, "80m": 3.573, "60m": 5.357, "40m": 7.074, "30m": 10.136, "20m": 14.074,
           "17m": 18.100, "15m": 21.074, "12m": 24.915, "10m": 28.074, "6m": 50.313, "4m": 70.154,
           "2m": 144.174}


def band_mhz(name) -> Optional[float]:
    """Representative frequency for a band name: its FT8 frequency, else its middle."""
    if name in FT8_MHZ:
        return FT8_MHZ[name]
    for n, lo, hi in BANDS:
        if n == name:
            return (lo + hi) / 2
    return None


def band_for_hz(freq_hz) -> Optional[str]:
    """Band name for a frequency in Hz (PSK Reporter's unit), else None."""
    try:
        return band_for_mhz(float(freq_hz) / 1e6)
    except (TypeError, ValueError):
        return None


def band_for_mhz(freq_mhz) -> Optional[str]:
    """Band name for a frequency in MHz (WSJT-X's ALL.TXT unit), else None."""
    try:
        f = float(freq_mhz)
    except (TypeError, ValueError):
        return None
    for name, lo, hi in BANDS:
        if lo <= f <= hi:
            return name
    return None
