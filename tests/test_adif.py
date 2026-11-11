import datetime as dt

import pytest

from maidenhead_mapper.core import adif

# As WSJT-X writes wsjtx_log.adi
WSJTX = """WSJT-X ADIF Export<eoh>
<call:5>K1ABC <gridsquare:4>FN42 <mode:3>FT8 <rst_sent:3>-10 <rst_rcvd:3>+03 <qso_date:8>20261111 <time_on:6>205130 <qso_date_off:8>20261111 <time_off:6>205230 <band:3>20m <freq:9>14.075123 <station_callsign:5>W7ORB <my_gridsquare:6>CN85on <tx_pwr:3>100 <comments:14>FT8  Sent: -10 <eor>
<call:6>JA1XYZ <gridsquare:4>PM95 <mode:4>MFSK <submode:3>FT4 <rst_sent:3>-15 <rst_rcvd:3>-18 <qso_date:8>20261111 <time_on:6>235930 <qso_date_off:8>20261112 <time_off:6>000030 <band:3>15m <freq:9>21.140000 <station_callsign:5>W7ORB <my_gridsquare:6>CN85on <eor>
"""

# A LoTW download: upper-case tags, typed lengths, a confirmation, no grid
LOTW = """ARRL Logbook of the World Status Report
<PROGRAMID:4>LoTW
<EOH>
<CALL:5>K1ABC
<BAND:3>20M
<MODE:3>FT8
<QSO_DATE:8:D>20261111
<TIME_ON:6>205200
<QSL_RCVD:1>Y
<LOTW_QSL_RCVD:1>Y
<STATION_CALLSIGN:5>W7ORB
<EOR>
"""


def test_parse_wsjtx():
    recs = adif.parse_adi(WSJTX)
    assert len(recs) == 2 and recs[0]["call"] == "K1ABC" and recs[0]["comments"] == "FT8  Sent: -10"
    q = adif.to_qso(recs[0])
    assert (q.call, q.grid, q.my_grid, q.band, q.mode, q.my_call) == ("K1ABC", "FN42", "CN85on", "20m", "FT8", "W7ORB")
    assert q.time_on == dt.datetime(2026, 11, 11, 20, 51, 30, tzinfo=dt.timezone.utc)
    assert (q.sent_db, q.rcvd_db) == (-10, 3)
    ft4 = adif.to_qso(recs[1])
    assert ft4.mode == "FT4" and ft4.time_off.day == 12 and ft4.digital


def test_phone_rst_not_db():
    q = adif.to_qso(adif.parse_adi("<call:4>W1AW<qso_date:8>20240101<time_on:4>1200<mode:3>SSB<rst_sent:2>59<eor>")[0])
    assert q.rst_sent == "59" and q.sent_db is None and q.time_on.minute == 0


def test_merge_lotw_confirmation():
    qs = [adif.to_qso(r, "wsjtx_log.adi") for r in adif.parse_adi(WSJTX)]
    qs += [adif.to_qso(r, "lotw.adi") for r in adif.parse_adi(LOTW)]
    out, merged = adif.merge(qs)
    assert merged == 1 and len(out) == 2
    k = out[0]
    assert k.call == "K1ABC" and k.confirmed == "lotw,qsl" and k.grid == "FN42" and k.source == "wsjtx_log.adi,lotw.adi"


def test_utf8_byte_lengths():
    # length counted in UTF-8 bytes ("Zürich" = 7 bytes) must not swallow the next tag
    recs = adif.parse_adi("<call:5>HB9AA<qth:7>Zürich<qso_date:8>20240101<time_on:4>1200<eor>")
    assert recs[0]["qth"] == "Zürich" and recs[0]["qso_date"] == "20240101"


def test_latlon_and_vucc():
    assert adif.parse_latlon("N045 30.000") == pytest.approx(45.5)
    assert adif.parse_latlon("W122 45.000") == pytest.approx(-122.75)
    q = adif.to_qso({"call": "W7X", "qso_date": "20240101", "time_on": "1200",
                     "lat": "N045 30.000", "lon": "W122 45.000"})
    assert q.grid.upper().startswith("CN85")
    q = adif.to_qso({"call": "N6R", "qso_date": "20240101", "time_on": "1200", "vucc_grids": "DM04,DM14"})
    assert q.grid == "DM04"


def test_adx():
    xml = """<?xml version="1.0"?><ADX><HEADER><PROGRAMID>x</PROGRAMID></HEADER><RECORDS>
    <RECORD><CALL>K1ABC</CALL><QSO_DATE>20261111</QSO_DATE><TIME_ON>2051</TIME_ON><GRIDSQUARE>FN42</GRIDSQUARE>
    <MODE>FT8</MODE><RST_RCVD>-07</RST_RCVD></RECORD></RECORDS></ADX>"""
    q = adif.to_qso(adif.parse_adx(xml)[0])
    assert q.call == "K1ABC" and q.grid == "FN42" and q.rcvd_db == -7


def test_read_and_worked(tmp_path):
    a, b = tmp_path / "wsjtx_log.adi", tmp_path / "lotw.adi"
    a.write_text(WSJTX, encoding="utf-8")
    b.write_text(LOTW, encoding="utf-8")
    r = adif.read([str(a), str(b)], my_call="W7ORB/P")
    assert (r.records, r.unusable, r.merged, len(r.qsos)) == (3, 0, 1, 2)
    w = {s.call: s for s in adif.worked(r.qsos)}
    assert w["K1ABC"].confirmed == 1 and w["K1ABC"].sent_best_db == -10 and w["JA1XYZ"].modes == "FT4"
    r = adif.read([str(a)], start=dt.datetime(2026, 11, 11, 22, tzinfo=dt.timezone.utc))
    assert len(r.qsos) == 1 and r.filtered_out == 1
