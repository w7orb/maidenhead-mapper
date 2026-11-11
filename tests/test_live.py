import datetime as dt

import pytest

from maidenhead_mapper.core import ft8msg, live
from maidenhead_mapper.core import wsjtx_udp as w

T0 = dt.datetime(2026, 11, 10, 20, 0, tzinfo=dt.timezone.utc)


# ------------------------------------------------------------ UDP protocol

def test_round_trip_every_type():
    hb = w.parse(w.encode_heartbeat("WSJT-X", 3, "2.7.0", "abc"))
    assert (hb.id, hb.max_schema, hb.version, hb.revision) == ("WSJT-X", 3, "2.7.0", "abc")

    st = w.parse(w.encode_status("WSJT-X", 14074000, "FT8", de_call="W7ORB", de_grid="CN85on", dx_grid="FN42"))
    assert (st.dial_hz, st.mode, st.de_call, st.de_grid, st.dx_grid) == (14074000, "FT8", "W7ORB", "CN85on", "FN42")

    d = w.parse(w.encode_decode("CQ K1ABC FN42", snr=-17, time_ms=75_600_000, dt=0.3, df=1234, mode="~"))
    assert (d.message, d.snr, d.time_ms, d.df, d.mode, d.new) == ("CQ K1ABC FN42", -17, 75_600_000, 1234, "~", True)
    assert d.dt == pytest.approx(0.3)

    q = w.parse(w.encode_qso_logged("K1ABC", "FN42", 7074000, "FT8", time_off=T0, time_on=T0, report_rcvd="-05"))
    assert (q.dx_call, q.dx_grid, q.tx_hz, q.mode, q.report_rcvd, q.time_off) == ("K1ABC", "FN42", 7074000, "FT8", "-05", T0)

    assert isinstance(w.parse(w.encode_close()), w.Close)
    assert w.parse(w.encode_clear(window=2)).window == 2


def test_unknown_type_and_bad_data():
    other = w._header(7, "WSJT-X").bytes()  # Replay: not used by the map
    assert w.parse(other) is None
    with pytest.raises(w.ProtocolError):
        w.parse(b"hello world, not wsjt-x")
    with pytest.raises(w.ProtocolError):
        w.parse(b"\xad\xbc")


def test_older_status_with_fewer_fields_still_parses():
    full = w.encode_status("WSJT-X", 7074000, "FT4", de_call="W7ORB")
    cut = full[: len(full) - 20]  # drop trailing fields an old version wouldn't send
    st = w.parse(cut)
    assert st.dial_hz == 7074000 and st.mode == "FT4"


def test_newer_extra_trailing_fields_ignored():
    d = w.parse(w.encode_decode("CQ K1ABC FN42") + b"\x00\x00\x00\x05extra")
    assert d.message == "CQ K1ABC FN42"


def test_null_strings():
    raw = w._header(w.HEARTBEAT, "X").put(">I", 3).utf8(None).utf8(None).bytes()
    hb = w.parse(raw)
    assert hb.version == "" and hb.revision == ""


# ------------------------------------------------------------ messages

@pytest.mark.parametrize("msg,sender,to,grid,cq", [
    ("CQ K1ABC FN42", "K1ABC", None, "FN42", True),
    ("CQ DX K1ABC FN42", "K1ABC", None, "FN42", True),
    ("CQ POTA K1ABC FN42", "K1ABC", None, "FN42", True),
    ("CQ 290 K1ABC FN42", "K1ABC", None, "FN42", True),
    ("CQ K1ABC", "K1ABC", None, None, True),
    ("W7ORB K1ABC FN42", "K1ABC", "W7ORB", "FN42", False),
    ("W7ORB K1ABC R FN42", "K1ABC", "W7ORB", "FN42", False),
    ("W7ORB K1ABC -12", "K1ABC", "W7ORB", None, False),
    ("W7ORB K1ABC R-12", "K1ABC", "W7ORB", None, False),
    ("W7ORB K1ABC RR73", "K1ABC", "W7ORB", None, False),   # RR73 is not a grid
    ("W7ORB K1ABC 73", "K1ABC", "W7ORB", None, False),
    ("<W7ORB> K1ABC/P FN42", "K1ABC/P", "W7ORB", "FN42", False),
    ("<...> K1ABC -12", "K1ABC", None, None, False),
    ("KH6KG/W5 AH6U RR73", "AH6U", "KH6KG/W5", None, False),
])
def test_parse_message(msg, sender, to, grid, cq):
    p = ft8msg.parse_message(msg)
    assert (p.sender, p.to, p.grid, p.is_cq) == (sender, to, grid, cq)


@pytest.mark.parametrize("msg", ["ALOHA BOB", "TNX", "", "CQ", "TU; <...> BJ2IAF 529 4676"])
def test_free_text_ignored(msg):
    assert ft8msg.parse_message(msg) is None


# ------------------------------------------------------------ live model

def feed(model, *packets, now=T0):
    for p in packets:
        model.handle(w.parse(p), now)


def test_decodes_build_stations_and_cache_grids():
    m = live.LiveModel()
    feed(m, w.encode_status(dial_hz=14074000, mode="FT8", de_call="W7ORB", de_grid="CN85on"))
    assert (m.my_call, m.my_grid) == ("W7ORB", "CN85on")

    feed(m, w.encode_decode("CQ K1ABC FN42", snr=-15))
    feed(m, w.encode_decode("W7ORB K1ABC -10", snr=-8), now=T0 + dt.timedelta(seconds=30))
    feed(m, w.encode_decode("W7ORB N0GRID -10", snr=-3))
    k = m.stations[("K1ABC", "heard")]
    assert (k.grid, k.reports, k.snr_best, k.snr_last, k.called_me) == ("FN42", 2, -8, -8, 1)
    assert k.bands == {"20m"} and k.modes == {"FT8"}
    assert (k.first_utc, k.last_utc) == (T0, T0 + dt.timedelta(seconds=30))
    assert (m.placed_count(), m.waiting_count(), m.decodes) == (1, 1, 3)
    assert m.take_dirty() == {("K1ABC", "heard"), ("N0GRID", "heard")}
    assert m.take_dirty() == set()

    # a grid arriving later places the waiting station
    feed(m, w.encode_decode("CQ N0GRID EN34"))
    assert m.stations[("N0GRID", "heard")].grid == "EN34"
    assert m.waiting_count() == 0
    d, b = m.distance_bearing("EN34")
    assert d > 2000 and 60 < b < 120


def test_band_follows_status_and_mode_char_fallback():
    m = live.LiveModel()
    feed(m, w.encode_decode("CQ K1ABC FN42", mode="+"))            # no Status yet
    feed(m, w.encode_status(dial_hz=7047500, mode="FT4"), w.encode_decode("CQ K1ABC FN42"))
    k = m.stations[("K1ABC", "heard")]
    assert k.bands == {"40m"} and k.modes == {"FT4"}


def test_qso_logged_marks_worked():
    m = live.LiveModel()
    feed(m, w.encode_decode("CQ K1ABC FN42"))
    feed(m, w.encode_qso_logged("K1ABC", "", 7074000, "FT8", time_off=T0, report_rcvd="-07"))
    wk = m.stations[("K1ABC", "worked")]
    assert (wk.grid, wk.bands, wk.snr_best, wk.last_utc) == ("FN42", {"40m"}, -7, T0)  # grid from cache
    assert ("K1ABC", "heard") in m.stations


def test_close_forgets_client_and_none_ignored():
    m = live.LiveModel()
    feed(m, w.encode_heartbeat("A"), w.encode_heartbeat("B"), w.encode_close("A"))
    assert set(m.clients) == {"B"}
    assert m.handle(None) is False
