import datetime as dt
import os

import pytest

from maidenhead_mapper.core import alltxt, antenna
from maidenhead_mapper.core import wsjtx_udp as w

T0 = dt.datetime(2022, 12, 13, 21, 0, tzinfo=dt.timezone.utc)
DATA = os.path.join(os.path.dirname(__file__), "..", "..", "pskreporter-analytics", "wsjtx", "data", "2022_12_13")


def d(sec, msg, snr):
    return antenna.Decode(T0 + dt.timedelta(seconds=sec), msg, snr)


def test_match_pairs_and_singles():
    a = [d(0, "CQ K1ABC FN42", -5), d(0, "CQ JA1XYZ PM95", -18), d(15, "W7ORB K1ABC -10", -6),
         d(15, "W7ORB K1ABC -10", -9)]                            # duplicate: keep the strongest
    b = [d(0, "CQ K1ABC FN42", -9), d(30, "CQ VK2AA QF56", -12)]
    ms = antenna.match(a, b)
    by = {(m.sender, m.snr_a, m.snr_b) for m in ms}
    assert by == {("K1ABC", -5, -9), ("JA1XYZ", -18, None), ("K1ABC", -6, None), ("VK2AA", None, -12)}
    assert [m.diff for m in ms if m.sender == "K1ABC" and m.snr_b is not None] == [4]


def test_compare_bins():
    a = [d(0, "CQ K1ABC FN42", -5), d(15, "CQ K1ABC FN42", -4), d(0, "CQ JA1XYZ PM95", -18)]
    b = [d(0, "CQ K1ABC FN42", -9), d(15, "CQ K1ABC FN42", -10), d(30, "CQ JA1XYZ PM95", -15)]
    ms = antenna.match(a, b)
    c = antenna.compare(ms, antenna.learn_grids(a, b), "CN85on")
    assert c.total.n_both == 2 and c.total.only_a == 1 and c.total.only_b == 1
    assert c.total.median_db == 5.0
    east = [b_ for b_ in c.by_sector.values() if b_.n_both]
    assert len(east) == 1 and east[0].label in ("ENE", "E")               # Portland -> Boston
    jp = c.by_station["JA1XYZ"]
    assert (jp.only_a, jp.only_b, jp.median_db, jp.decode_advantage) == (1, 1, None, 0.0)
    assert c.placed["K1ABC"]["distance_km"] > 4000 and c.placed["K1ABC"]["takeoff_deg"] is not None
    assert c.unplaced == 0


def test_decodes_from_log_by_client():
    log = [(T0 + dt.timedelta(seconds=14), w.Decode("A", time_ms=21 * 3600 * 1000, snr=-5, message="CQ K1ABC FN42")),
           (T0 + dt.timedelta(seconds=14), w.Decode("B", time_ms=21 * 3600 * 1000, snr=-9, message="CQ K1ABC FN42"))]
    da, db = antenna.decodes_from_log(log, "A"), antenna.decodes_from_log(log, "B")
    assert da[0].slot == T0 and len(da) == len(db) == 1
    assert antenna.match(da, db)[0].diff == 4


def test_destination():
    lon, lat = antenna.destination(0, 0, 90, 111.195)
    assert lon == pytest.approx(1.0, abs=1e-3) and lat == pytest.approx(0, abs=1e-6)


@pytest.mark.skipif(not os.path.isdir(DATA), reason="2022 A/B recordings not checked out alongside")
def test_real_recordings_match_prototype():
    da = antenna.decodes_from_rows(alltxt.read([os.path.join(DATA, f"DXCommander{k}_ALL.TXT") for k in (1, 2, 3)]))
    db = antenna.decodes_from_rows(alltxt.read([os.path.join(DATA, f"loop{k}_ALL.TXT") for k in (1, 2, 3)]))
    c = antenna.compare(antenna.match(da, db), antenna.learn_grids(da, db), "CN85on")
    assert 3800 < c.total.n_both < 4000                      # the prototype matched 3,923
    assert c.total.median_db == 1.0
    nw = c.by_sector[14]                                    # NW
    assert nw.median_db >= 3
