import datetime as dt
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from maidenhead_mapper.core import psk, pskfetch

NOW = dt.datetime(2026, 11, 11, 3, 4, 5, tzinfo=dt.timezone.utc)


def test_query_url():
    q = parse_qs(urlparse(pskfetch.query_url(" w7orb ", 3600, "ft8")).query)
    assert q["callsign"] == ["W7ORB"] and q["flowStartSeconds"] == ["-3600"] and q["mode"] == ["FT8"]
    assert q["callback"] == ["doNothing"]
    q = parse_qs(urlparse(pskfetch.query_url("W7ORB", 10 ** 6)).query)
    assert q["flowStartSeconds"] == ["-86400"] and "mode" not in q   # capped at 24 h


def test_parse_response():
    body = {"receptionReport": [{"senderCallsign": "K1ABC"}], "lastSequenceNumber": "1"}
    raw, data = pskfetch.parse_response("doNothing(" + json.dumps(body) + ");\n")
    assert data == body and json.loads(raw) == body
    xml = '<?xml version="1.0"?>\n<js><![CDATA[\ndoNothing(' + json.dumps(body) + ');\n]]></js>\n'
    assert pskfetch.parse_response(xml)[1] == body    # the shape the server actually sends
    with pytest.raises(pskfetch.FetchError, match="Unexpected"):
        pskfetch.parse_response("Your IP has made too many queries too often. Please slow down.")
    with pytest.raises(pskfetch.FetchError, match="invalid JSON"):
        pskfetch.parse_response("doNothing({oops);")


def test_save_round_trips_through_loader(tmp_path):
    raw = json.dumps({"receptionReport": [{"senderCallsign": "K1ABC", "receiverCallsign": "W7ORB",
                                           "flowStartSeconds": 1, "frequency": 14074000}]})
    path = pskfetch.save(raw, str(tmp_path / "new"), NOW)
    assert os.path.basename(path) == "psk_2026_11_11-03_04_05.json"
    assert os.listdir(tmp_path / "new") == ["psk_2026_11_11-03_04_05.json"]   # no .part left
    assert len(psk.load_reports(str(tmp_path / "new"))) == 1
    with pytest.raises(FileExistsError):
        pskfetch.save(raw, str(tmp_path / "new"), NOW)


def test_wait_seconds():
    assert pskfetch.wait_seconds(0, 1000) == 0
    assert pskfetch.wait_seconds(1000, 1100) == 200
    assert pskfetch.wait_seconds(1000, 1300) == 0
