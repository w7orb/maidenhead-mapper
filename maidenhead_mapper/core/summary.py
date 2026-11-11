"""Per-station summary of QSO records — placeholder for Phase 2.

Contract:

    summarize(records) -> list[StationSummary]

One StationSummary per callsign that has a valid grid. Its fields become
the marker layer's attributes; the draft set is below and will follow
the user's existing summarization code.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class StationSummary:
    call: str
    grid: str
    qso_count: int
    first_utc: str
    last_utc: str
    bands: str            # comma-separated, e.g. "20m,40m"
    modes: str            # comma-separated, e.g. "FT8,FT4"
    best_rpt_rcvd: Optional[int] = None
    best_rpt_sent: Optional[int] = None
    distance_km: Optional[float] = None
    bearing_deg: Optional[float] = None


def summarize(records):
    raise NotImplementedError("Summarization arrives in Phase 2")
