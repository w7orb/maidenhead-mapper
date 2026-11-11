"""Optional real-data checks. Set MM_REAL_DATA to a folder holding

    data/misc2/psk_*.json                  PSK Reporter pulls
    wsjtx/data/2022_12_13/*_ALL.TXT        WSJT-X logs (DXCommander1-3, loop1-3)

to run them; without it those tests are skipped."""

import os

ROOT = os.environ.get("MM_REAL_DATA", "")
REASON = "set MM_REAL_DATA to run real-data checks"


def path(*parts):
    return os.path.join(ROOT, *parts) if ROOT else os.path.join("__no_real_data__", *parts)
