import datetime as dt

import pytest

from maidenhead_mapper.core import es
from maidenhead_mapper.core.iono import STATUS_ABOVE, STATUS_F2, STATUS_NO_DATA

T0 = dt.datetime(2026, 6, 15, 18, 0, tzinfo=dt.timezone.utc)


def test_classify():
    assert es.max_single_hop_km() == pytest.approx(2310, abs=20)
    assert es.classify(1500, "6m") == es.HIGH
    assert es.classify(1500, "10m") == es.POSSIBLE
    assert es.classify(1500, "10m", STATUS_NO_DATA) == es.POSSIBLE
    assert es.classify(1500, "10m", STATUS_ABOVE) == es.HIGH
    assert es.classify(1500, "10m", STATUS_F2) is None          # F2 explains it
    assert es.classify(1500, "20m") is None                     # not an Es band
    assert es.classify(300, "6m") is None and es.classify(3000, "6m") is None
    assert es.classify(None, "6m") is None


def test_foes_and_takeoff():
    # 50 MHz over 2,000 km needs foEs of roughly 50 / 5 = 10 MHz
    f = es.foes_min_mhz(50.313, 2000)
    assert 7 < f < 12
    assert es.foes_min_mhz(50.313, 1000) > f                    # shorter skip needs a denser layer
    assert 0 < es.takeoff_deg(2000) < es.takeoff_deg(1000)


def test_cluster():
    m = lambda minutes: T0 + dt.timedelta(minutes=minutes)
    pts = [(-100, 40, m(0)), (-98, 41, m(10)), (-99, 39, m(20)),     # one cloud
           (10, 50, m(0)), (12, 51, m(5)),                           # another
           (-100, 40, m(300)),                                       # same place, hours later
           (150, -30, None)]
    labels = es.cluster(pts)
    assert labels[0] == labels[1] == labels[2] == 1
    assert labels[3] == labels[4] == 2
    assert len({labels[5], labels[6], 1, 2}) == 4
    assert es.cluster([]) == []


def test_mode_guess():
    from maidenhead_mapper.core import iono
    assert es.mode_guess(1500, "6m", iono.STATUS_F2) == es.MODE_ES            # 6m at Es range: Es regardless
    assert es.mode_guess(1500, "10m", iono.STATUS_ABOVE) == es.MODE_ES        # 10m above F2 MUF at Es range
    assert es.mode_guess(1500, "10m", iono.STATUS_F2) == es.MODE_F2
    assert es.mode_guess(4000, "10m", iono.STATUS_ABOVE) == es.MODE_ABOVE     # beyond one Es hop
    assert es.mode_guess(1500, "20m", iono.STATUS_ABOVE) == es.MODE_ABOVE     # not an Es band
    assert es.mode_guess(1500, "10m", iono.STATUS_NO_DATA) == es.MODE_NO_DATA
