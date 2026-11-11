import datetime as dt
import os

import pytest

from tests import realdata

from maidenhead_mapper.core import alltxt, exchange, live
from maidenhead_mapper.core import wsjtx_udp as w

ALLTXT = realdata.path("wsjtx", "data",
                      "2022_12_13", "loop1_ALL.TXT")

SAMPLE = """\
221213_205300    28.074 Rx FT8      7  0.3  488 CQ WB2UBW EM67
221213_205315    28.074 Rx FT8    -12  0.3 2462 AH6U N5ER EM34
221213_205330    28.074 Tx FT8      0  0.0 1500 CQ W7ORB CN85
221213_205345    28.074 Rx FT8     -9  0.1  900 W7ORB K1ABC FN42
221213_205400    28.074 Rx FT8    -13  0.3 1000 N5ER AH6U +07
221213_205400    28.074 Tx FT8      0  0.0 1500 K1ABC W7ORB -09
221213_205415    28.074 Rx FT8     -9  0.1  900 W7ORB K1ABC R-14
221213_205430    28.074 Tx FT8      0  0.0 1500 K1ABC W7ORB RR73
some old-format line
221213_210000    14.074 Rx FT8    -20  0.1  900 CQ JA1XYZ PM95
"""


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "ALL.TXT"
    p.write_text(SAMPLE, encoding="utf-8")
    return str(p)


def test_read_rows(path):
    rows = alltxt.read([path])
    assert len(rows) == 9 and rows.skipped == 1
    assert rows[2].rxtx == "Tx" and rows[2].message == "CQ W7ORB CN85"
    assert rows[-1].mhz == 14.074


def test_log_and_replay_with_my_tx(path):
    log = alltxt.to_log(alltxt.read([path]), de_call="W7ORB", de_grid="CN85on")
    kinds = [type(m).__name__ for _, m in log]
    assert kinds.count("Status") == 2 and kinds.count("Transmitted") == 3 and kinds.count("Decode") == 6
    m = live.replay(log)
    assert m.my_call == "W7ORB" and m.my_grid == "CN85on"
    mine = [e for e in m.exchanges.exchanges.values() if e.involves_me]
    assert len(mine) == 1
    ex = mine[0]
    assert (ex.a, ex.b) == ("K1ABC", "W7ORB")
    assert ex.b_hears_a == -9 and ex.a_hears_b == -14 and ex.signed_off      # my Tx lines fill my side
    assert ex.stage == exchange.CONFIRMED and ex.me_hears_a == -9
    assert ("W7ORB", "heard") not in m.stations                               # I don't hear myself
    assert m.stations[("JA1XYZ", "heard")].bands == {"20m"}


def test_replay_window(path):
    log = alltxt.to_log(alltxt.read([path]), de_call="W7ORB")
    t = lambda hms: dt.datetime(2022, 12, 13, *hms, tzinfo=dt.timezone.utc)
    m = live.replay(log, start=t((21, 0, 0)))
    assert set(m.stations) == {("JA1XYZ", "heard")}
    assert m.clients["ALL.TXT"].dial_hz == 14074000
    m = live.replay(log, end=t((20, 53, 20)))
    assert set(m.stations) == {("WB2UBW", "heard"), ("N5ER", "heard")}
    assert m.clients["ALL.TXT"].dial_hz == 28074000


def test_live_model_records_log_without_repeating_status():
    m = live.LiveModel(record=True)
    now = dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc)
    st = w.parse(w.encode_status("c", 14074000, "FT8", de_call="W7ORB", de_grid="CN85"))
    for _ in range(3):
        m.handle(st, now)
    m.handle(w.parse(w.encode_heartbeat("c")), now)
    m.handle(w.parse(w.encode_decode("CQ K1ABC FN42", client_id="c")), now)
    assert [type(x).__name__ for _, x in m.log] == ["Status", "Decode"]


def test_entry_round_trip():
    when = dt.datetime(2026, 11, 11, 3, 4, 5, tzinfo=dt.timezone.utc)
    msgs = [w.parse(w.encode_status("c", 14074000, "FT8", de_call="W7ORB", transmitting=True, dx_call="K1ABC")),
            w.parse(w.encode_decode("CQ K1ABC FN42", snr=-7, client_id="c")),
            w.parse(w.encode_qso_logged("K1ABC", "FN42", 14074000, "FT8", time_off=when, time_on=when,
                                        client_id="c", my_call="W7ORB", my_grid="CN85")),
            alltxt.Transmitted("c", "K1ABC W7ORB -05")]
    for msg in msgs:
        kind, text = live.encode_entry(msg)
        assert live.decode_entry(kind, text) == msg
    assert live.decode_entry("Nope", "{}") is None


@pytest.mark.skipif(not os.path.isfile(ALLTXT), reason=realdata.REASON)
def test_real_file_matches_tracker_counts():
    m = live.replay(alltxt.to_log(alltxt.read([ALLTXT])))
    assert len(m.exchanges.exchanges) == 131 and m.decodes == 474
