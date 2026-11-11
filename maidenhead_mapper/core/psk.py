"""PSK Reporter file loading and direction labelling.

Vendored from pskreporter-analytics `src/psktools.py` at commit 4a94e80
(https://github.com/cwtews/pskreporter-analytics). Changes from the original:
  - `add_remote_station` no longer adds Lat/Lon; positions come from
    `maidenhead.center` in `stations.py`, so the plugin has one locator
    implementation.
  - `load_reports` collects skipped file names on the returned frame's
    `attrs['skipped_files']` instead of only warning, so the Processing log
    can report them.

Requires pandas (bundled with QGIS 3.34+ on Windows, macOS and most Linux
packages). No QGIS imports.
"""

import json
import os
import warnings

import pandas as pd

# Identifies one reception report. Pulls whose time windows overlap return the
# same report again; those copies are identical apart from occasional
# corrections PSK Reporter makes to details like the sender's locator.
REPORT_KEY = ["senderCallsign", "receiverCallsign", "flowStartSeconds", "frequency"]


def read_psk_file(path):
    """Load one saved PSK Reporter query (as written by pskreporter-pull.py)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_psk_files(data_dir):
    """Sorted psk_*.json file names in a folder (names sort by pull time)."""
    return [n for n in sorted(os.listdir(data_dir)) if n.startswith("psk_") and n.endswith(".json")]


def load_reports(data_dir, dedupe=True, progress=None):
    """Load receptionReport entries from every psk_*.json file in a folder.

    Empty or corrupted files are skipped (listed in df.attrs['skipped_files']).
    With dedupe (default) each report appears once, from the latest file that
    has it, with `copies` and `firstSeenFile` columns.
    `progress(i, n)` is called per file if given.
    """
    names = iter_psk_files(data_dir)
    frames, bad_files = [], []
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names))
        try:
            reports = read_psk_file(os.path.join(data_dir, name)).get("receptionReport", [])
        except ValueError:  # includes JSONDecodeError and UnicodeDecodeError
            bad_files.append(name)
            continue
        if reports:
            df = pd.DataFrame(reports)
            df["sourceFile"] = name
            frames.append(df)

    if bad_files:
        warnings.warn(f"{data_dir}: skipped {len(bad_files)} unreadable file(s): {', '.join(bad_files)}")

    if not frames:
        df = pd.DataFrame(columns=["sourceFile", "time"] + (["copies", "firstSeenFile"] if dedupe else []))
    else:
        df = pd.concat(frames, ignore_index=True)
        df["time"] = pd.to_datetime(df["flowStartSeconds"], unit="s", utc=True)
        if dedupe:
            copies = df.groupby(REPORT_KEY, dropna=False)["sourceFile"]
            df["copies"] = copies.transform("size")
            df["firstSeenFile"] = copies.transform("first")
            df = df.drop_duplicates(REPORT_KEY, keep="last").reset_index(drop=True)

    df.attrs["files_read"] = len(names) - len(bad_files)
    df.attrs["skipped_files"] = bad_files
    return df


def base_callsign(call):
    """The base callsign with portable prefixes/suffixes removed, upper-cased.

    Split on '/' and keep the longest part (the first on a tie), so 'W7ORB/P',
    'VE7/W7ORB' and 'w7orb/qrp' all give 'W7ORB'. None for non-strings/empty.
    """
    if not isinstance(call, str) or not call.strip():
        return None
    parts = [p for p in call.strip().upper().split("/") if p]
    return max(parts, key=len) if parts else None


def add_remote_station(df, mycall):
    """Label each report by direction relative to `mycall`; name the other end.

    Adds:
      direction       'heard' (I received them), 'heard_me' (they received me),
                      'self' (my receiver decoded my signal), None (neither side)
      remoteCallsign  the other station's callsign
      remoteLocator   the other station's locator ('self': the receiver's)
      myLocator       my locator as reported on my side of the report
    """
    me = base_callsign(mycall)
    if me is None:
        raise ValueError(f"mycall must be a callsign, not {mycall!r}")

    def side(col):
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        return df[col].map(base_callsign) == me

    rx_is_me = side("receiverCallsign")
    tx_is_me = side("senderCallsign")
    i_received = rx_is_me & ~tx_is_me
    i_self = rx_is_me & tx_is_me
    i_sent = tx_is_me & ~rx_is_me

    df["direction"] = None
    df.loc[i_received, "direction"] = "heard"
    df.loc[i_sent, "direction"] = "heard_me"
    df.loc[i_self, "direction"] = "self"
    i_sent = i_sent | i_self  # the "other" end of a self-spot is the receiver

    def pick(when_received, when_sent):
        out = pd.Series(None, index=df.index, dtype=object)
        if when_received in df.columns:
            out[i_received] = df.loc[i_received, when_received]
        if when_sent in df.columns:
            out[i_sent] = df.loc[i_sent, when_sent]
        return out

    df["remoteCallsign"] = pick("senderCallsign", "receiverCallsign")
    df["remoteLocator"] = pick("senderLocator", "receiverLocator")
    df["myLocator"] = pick("receiverLocator", "senderLocator")
    return df
