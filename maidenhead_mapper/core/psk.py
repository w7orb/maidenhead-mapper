"""PSK Reporter file loading and direction labelling. Pure Python.

Adapted from pskreporter-analytics `src/psktools.py` at commit 4a94e80
(https://github.com/cwtews/pskreporter-analytics, GPLv3, same author).
Changes from the original:
  - No pandas: reports are plain dicts (the JSON objects as PSK Reporter sends
    them) plus a few added keys, so the plugin has no outside dependencies.
  - `add_remote_station` adds no Lat/Lon; positions come from
    `maidenhead.center`, so the plugin has one locator implementation.
  - `load_reports` returns the skipped file names (`.skipped_files`) instead
    of only warning, so the Processing log can list them.
No QGIS imports.
"""

import datetime as _dt
import json
import os
import warnings

# Identifies one reception report. Pulls whose time windows overlap return the
# same report again; those copies are identical apart from occasional
# corrections PSK Reporter makes to details like the sender's locator.
REPORT_KEY = ("senderCallsign", "receiverCallsign", "flowStartSeconds", "frequency")

# Saved pulls are named psk_YYYY_MM_DD-HH_MM_SS.json (pull time, UTC)
FILE_PREFIX, FILE_SUFFIX = "psk_", ".json"


class Reports(list):
    """A list of report dicts, plus what happened while loading them."""

    files_read = 0
    skipped_files = ()


def read_psk_file(path):
    """Load one saved PSK Reporter query (as written by pskreporter-pull.py)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_psk_files(data_dir):
    """Sorted psk_*.json file names in a folder (names sort by pull time)."""
    return [n for n in sorted(os.listdir(data_dir)) if n.startswith(FILE_PREFIX) and n.endswith(FILE_SUFFIX)]


def report_time(seconds):
    """flowStartSeconds -> tz-aware UTC datetime, or None."""
    try:
        return _dt.datetime.fromtimestamp(int(seconds), _dt.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def load_reports(data_dir, dedupe=True, progress=None):
    """Load receptionReport entries from every psk_*.json file in a folder.

    Each report is the JSON dict plus `sourceFile` and `time` (UTC datetime or
    None). With dedupe (default) each report appears once, taken from the
    latest file that has it, with `copies` and `firstSeenFile` added.
    Empty or corrupt files are skipped and listed in `.skipped_files`.
    `progress(i, n)` is called per file if given.
    """
    names = iter_psk_files(data_dir)
    rows, bad_files = [], []
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names))
        try:
            data = read_psk_file(os.path.join(data_dir, name))
        except ValueError:  # includes JSONDecodeError and UnicodeDecodeError
            bad_files.append(name)
            continue
        for r in (data.get("receptionReport") or []) if isinstance(data, dict) else []:
            if isinstance(r, dict):
                r["sourceFile"] = name
                r["time"] = report_time(r.get("flowStartSeconds"))
                rows.append(r)

    if bad_files:
        warnings.warn(f"{data_dir}: skipped {len(bad_files)} unreadable file(s): {', '.join(bad_files)}")

    if dedupe:
        latest, copies, first_file = {}, {}, {}
        for r in rows:
            key = tuple(r.get(k) for k in REPORT_KEY)
            copies[key] = copies.get(key, 0) + 1
            first_file.setdefault(key, r["sourceFile"])
            latest.pop(key, None)  # re-insert so order follows the last copy
            latest[key] = r
        rows = []
        for key, r in latest.items():
            r["copies"], r["firstSeenFile"] = copies[key], first_file[key]
            rows.append(r)

    out = Reports(rows)
    out.files_read = len(names) - len(bad_files)
    out.skipped_files = bad_files
    return out


def base_callsign(call):
    """The base callsign with portable prefixes/suffixes removed, upper-cased.

    Split on '/' and keep the longest part (the first on a tie), so 'W7ORB/P',
    'VE7/W7ORB' and 'w7orb/qrp' all give 'W7ORB'. None for non-strings/empty.
    """
    if not isinstance(call, str) or not call.strip():
        return None
    parts = [p for p in call.strip().upper().split("/") if p]
    return max(parts, key=len) if parts else None


def add_remote_station(reports, mycall):
    """Label each report by direction relative to `mycall`; name the other end.

    Adds to every report dict (and returns the same list):
      direction       'heard' (I received them), 'heard_me' (they received me),
                      'self' (my receiver decoded my signal), None (neither side)
      remoteCallsign  the other station's callsign
      remoteLocator   the other station's locator ('self': the receiver's)
      myLocator       my locator as reported on my side of the report
    """
    me = base_callsign(mycall)
    if me is None:
        raise ValueError(f"mycall must be a callsign, not {mycall!r}")

    for r in reports:
        rx_me = base_callsign(r.get("receiverCallsign")) == me
        tx_me = base_callsign(r.get("senderCallsign")) == me
        if rx_me and not tx_me:
            r["direction"] = "heard"
            remote, mine = "sender", "receiver"
        elif tx_me:
            # A self-spot's "other end" is the receiver, so it sits where heard
            r["direction"] = "self" if rx_me else "heard_me"
            remote, mine = "receiver", "sender"
        else:
            r["direction"] = r["remoteCallsign"] = r["remoteLocator"] = r["myLocator"] = None
            continue
        r["remoteCallsign"] = r.get(remote + "Callsign")
        r["remoteLocator"] = r.get(remote + "Locator")
        r["myLocator"] = r.get(mine + "Locator")
    return reports
