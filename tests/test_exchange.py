import datetime as dt
import glob
import os
import sys

import pytest

from maidenhead_mapper.core import exchange as x
from maidenhead_mapper.core import ft8msg

T0 = dt.datetime(2022, 12, 13, 20, 53, 15, tzinfo=dt.timezone.utc)
ALLTXT = os.path.join(os.path.dirname(__file__), "..", "..", "pskreporter-analytics", "wsjtx", "data", "2022_12_13")


def feed(tr, lines, band="10m", start=T0, step=15):
    out = None
    for i, (msg, snr) in enumerate(lines):
        out = tr.on_decode(ft8msg.parse_message(msg), snr, band, "FT8", start + dt.timedelta(seconds=step * i))
    return out


def test_parse_steps():
    p = ft8msg.parse_message("AH6U N5ER R-04")
    assert (p.sender, p.to, p.report, p.roger) == ("N5ER", "AH6U", -4, True)
    assert ft8msg.parse_message("N5ER AH6U +07").report == 7
    assert ft8msg.parse_message("N5ER AH6U RR73").signoff == "RR73"
    assert ft8msg.parse_message("AH6U N5ER 73").signoff == "73"
    p = ft8msg.parse_message("W7ORB K1ABC R FN42")
    assert p.grid == "FN42" and p.roger and p.report is None
    assert ft8msg.parse_message("K1ABC W9XYZ R 559 0013").report is None   # contest exchange: not a dB report


def test_full_exchange_between_others():
    tr = x.ExchangeTracker("W7ORB")
    ex = feed(tr, [("AH6U N5ER EM34", -12), ("N5ER AH6U +07", -13), ("AH6U N5ER R-04", -12),
                   ("N5ER AH6U RR73", -13), ("AH6U N5ER 73", -12)])
    assert len(tr.exchanges) == 1
    assert (ex.a, ex.b) == ("N5ER", "AH6U")
    assert ex.b_hears_a == 7 and ex.a_hears_b == -4          # AH6U hears N5ER +07; N5ER hears AH6U -04
    assert (ex.me_hears_a, ex.me_hears_b) == (-12, -13)
    assert ex.stage == x.CONFIRMED and not ex.involves_me and ex.messages == 5
    assert ex.transcript() == "N5ER: EM34 | AH6U: +07 | N5ER: R-04 | AH6U: RR73 | N5ER: 73"


def test_stages_and_repeats():
    tr = x.ExchangeTracker()
    ex = feed(tr, [("K1ABC W9XYZ FN42", -10)])
    assert ex.stage == x.STARTED
    feed(tr, [("W9XYZ K1ABC -15", -9), ("W9XYZ K1ABC -15", -9)], start=T0 + dt.timedelta(seconds=15))
    assert ex.stage == x.ONE_REPORT and ex.steps[-1] == "K1ABC: -15 ×2"
    feed(tr, [("K1ABC W9XYZ R-11", -10)], start=T0 + dt.timedelta(seconds=60))
    assert ex.stage == x.BOTH_REPORTS                        # no sign-off heard yet
    assert len(tr.exchanges) == 1 and tr.counts()[x.BOTH_REPORTS] == 1


def test_new_exchange_after_idle_or_other_band():
    tr = x.ExchangeTracker()
    a = feed(tr, [("K1ABC W9XYZ -10", -5)])
    b = feed(tr, [("K1ABC W9XYZ -10", -5)], start=T0 + x.IDLE + dt.timedelta(seconds=30))
    c = feed(tr, [("K1ABC W9XYZ -10", -5)], band="20m", start=T0 + dt.timedelta(seconds=30))
    assert len({a.id, b.id, c.id}) == 3


def test_cq_and_undirected_ignored():
    tr = x.ExchangeTracker()
    assert feed(tr, [("CQ K1ABC FN42", -5)]) is None
    assert tr.on_decode(None, -5, "20m", "FT8", T0) is None
    assert not tr.exchanges


def test_my_qso_from_status_and_logged():
    tr = x.ExchangeTracker("W7ORB")
    feed(tr, [("W7ORB K1ABC FN42", -8)])                      # K1ABC answers my CQ
    ex = tr.on_my_status("K1ABC", "-08", True, "10m", "FT8", T0 + dt.timedelta(seconds=15))
    assert ex.involves_me and (ex.a, ex.b) == ("K1ABC", "W7ORB")
    assert ex.b_hears_a == -8                                 # I (B) hear K1ABC at -08
    feed(tr, [("W7ORB K1ABC R-14", -9)], start=T0 + dt.timedelta(seconds=30))
    assert ex.stage == x.BOTH_REPORTS
    tr.on_logged("K1ABC", "-08", "-14", "10m", "FT8", T0 + dt.timedelta(seconds=60))
    assert ex.stage == x.CONFIRMED and ex.logged and len(tr.exchanges) == 1
    k1 = ex.a if ex.a == "K1ABC" else ex.b
    me_heard_by_k1 = ex.a_hears_b if ex.a == k1 else ex.b_hears_a
    assert me_heard_by_k1 == -14
    # Not transmitting: Status alone doesn't open an exchange
    assert tr.on_my_status("N0CALL", "-10", False, "10m", "FT8", T0) is None


@pytest.mark.skipif(not os.path.isdir(ALLTXT), reason="pskreporter-analytics WSJT-X logs not checked out alongside")
def test_real_alltxt():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    from replay_alltxt import read_lines
    from maidenhead_mapper.core.bands import band_for_mhz
    tr = x.ExchangeTracker()
    for t, mhz, mode, snr, _dt, _df, msg in read_lines(glob.glob(os.path.join(ALLTXT, "loop1_ALL.TXT"))):
        tr.on_decode(ft8msg.parse_message(msg), snr, band_for_mhz(mhz), mode, t)
    c = tr.counts()
    assert len(tr.exchanges) == 131 and c[x.CONFIRMED] == 18
    pair = [e for e in tr.exchanges.values() if {e.a, e.b} == {"AH6U", "N5ER"}][0]
    assert pair.stage == x.CONFIRMED and pair.b_hears_a == 7 and pair.a_hears_b == -4
