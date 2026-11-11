"""PSK Reporter query helpers. Pure Python; the HTTP request itself is made by
the caller (QGIS's network stack, so proxy and SSL settings apply).

Adapted from pskreporter-analytics `src/pskreporter-pull.py`
(https://github.com/cwtews/pskreporter-analytics, GPLv3, same author).
Files are written in the same psk_YYYY_MM_DD-HH_MM_SS.json format, so folders
are interchangeable between the two projects.

PSK Reporter asks that a client query no more often than once every 5
minutes, and keeps about 24 hours of reports.
"""

import datetime as _dt
import json
import os
import re
from urllib.parse import urlencode

from .psk import FILE_PREFIX, FILE_SUFFIX

API_URL = "https://pskreporter.info/cgi-bin/pskquery5.pl"
MIN_INTERVAL_S = 5 * 60
MAX_WINDOW_S = 24 * 3600
_CALLBACK = "doNothing"
# The reply is JSONP wrapped in XML: <js><![CDATA[ doNothing({...}); ]]></js>
_JSONP = re.compile(r"doNothing\((.*)\);", re.S)


class FetchError(Exception):
    pass


def query_url(callsign, seconds, mode=None):
    """URL for reports sent or received by `callsign` in the last `seconds`."""
    seconds = max(60, min(int(abs(seconds)), MAX_WINDOW_S))
    params = {
        "encap": "1",
        "callback": _CALLBACK,
        "statistics": "1",
        "noactive": "1",
        "nolocator": "1",
        "flowStartSeconds": str(-seconds),
        "callsign": callsign.strip().upper(),
    }
    if mode:
        params["mode"] = mode.strip().upper()
    return API_URL + "?" + urlencode(params)


def parse_response(text):
    """JSONP response text -> (raw JSON text, parsed dict). Raises FetchError."""
    m = _JSONP.search(text.strip())
    if m is None:
        raise FetchError("Unexpected response from PSK Reporter: " + text.strip()[:300])
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise FetchError(f"PSK Reporter returned invalid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise FetchError("PSK Reporter returned an unexpected JSON value")
    return raw, data


def file_name(now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return FILE_PREFIX + now.strftime("%Y_%m_%d-%H_%M_%S") + FILE_SUFFIX


def save(raw, folder, now=None):
    """Write the raw JSON to folder/psk_<UTC time>.json atomically; return the path.

    Never replaces an existing file, and an interrupted write leaves no
    partial psk_*.json behind.
    """
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, file_name(now))
    if os.path.exists(path):
        raise FileExistsError(path)
    part = path + ".part"
    try:
        with open(part, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(part, path)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    return path


def wait_seconds(last_fetch_epoch, now_epoch):
    """Seconds still to wait before PSK Reporter's 5-minute limit allows a query."""
    if not last_fetch_epoch:
        return 0
    return max(0, int(round(last_fetch_epoch + MIN_INTERVAL_S - now_epoch)))
