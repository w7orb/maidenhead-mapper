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


class FlatIono:
    """Uniform ionosphere: (foF2, hmF2, MUF(3000)) everywhere."""
    def __init__(self, when, fof2=9.0, hmf2=300.0, mufd=27.0):
        self.time, self.r12, self.ionosondes = when, 100.0, []
        self._v = (fof2, hmf2, mufd)

    def at(self, lon, lat):
        return self._v


def test_match_keeps_slot_and_frequency():
    a = [antenna.Decode(T0, "CQ K1ABC FN42", -5, None, 28.074)]
    b = [antenna.Decode(T0, "CQ K1ABC FN42", -9, None, None)]
    m = antenna.match(a, b)[0]
    assert m.slot == T0 and m.mhz == 28.074


def test_geometric_default_keeps_the_300km_angle():
    a = [antenna.Decode(T0, "CQ K1ABC FN42", -5, None, 14.074)]
    b = [antenna.Decode(T0, "CQ K1ABC FN42", -9, None, 14.074)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), "CN85on")
    p = c.placed["K1ABC"]
    assert p["method"] == "geometric" and p["takeoff_deg"] == p["takeoff_300_deg"]
    assert c.methods == {"geometric": 1}


def test_iono_angle_follows_layer_height_through_the_day():
    # same station, two times: a low night layer gives a lower angle than a high day layer
    t1, t2 = T0, T0 + dt.timedelta(hours=6)
    heights = {t1: 230.0, t2: 350.0}
    angle = antenna.iono_angle(lambda when: (FlatIono(when, 12.0, heights[when], 40.0), "model"))
    msgs = [(t1, -5, -9), (t2, -6, -8)]
    a = [antenna.Decode(t, "CQ K1ABC FN42", sa, None, 14.074) for t, sa, _ in msgs]
    b = [antenna.Decode(t, "CQ K1ABC FN42", sb, None, 14.074) for t, _, sb in msgs]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), "CN85on", angle)
    p = c.placed["K1ABC"]
    assert p["method"] == "model" and p["takeoff_min_deg"] < p["takeoff_max_deg"]
    assert sum(b_.n_both for b_ in c.by_takeoff.values()) == 2
    assert c.modes == {"F2": 2}


def test_iono_angle_sends_es_paths_to_their_own_bin():
    # 10m, ~1,500 km (Portland -> Colorado), foF2 far too low for F2: sporadic-E
    angle = antenna.iono_angle(lambda when: (FlatIono(when, 4.0, 300.0, 12.0), "KC2G"))
    a = [antenna.Decode(T0, "CQ K0XYZ DM79", -5, None, 28.074)]
    b = [antenna.Decode(T0, "CQ K0XYZ DM79", -8, None, 28.074)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), "CN85on", angle)
    assert list(c.by_takeoff) == [antenna.ES_BIN]
    p = c.placed["K0XYZ"]
    assert p["mode"] == "Es" and p["hops"] == 1 and p["method"] == "KC2G"
    assert p["takeoff_deg"] < p["takeoff_300_deg"]          # one hop off 105 km is lower than off 300 km
