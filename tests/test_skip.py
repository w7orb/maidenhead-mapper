import datetime as dt
import os

import pytest

from tests import realdata

from maidenhead_mapper.core import alltxt, skip
from maidenhead_mapper.core import wsjtx_udp as w

T0 = dt.datetime(2022, 11, 24, 18, 0, tzinfo=dt.timezone.utc)
MISC2 = realdata.path("data", "misc2")
ALLTXT = realdata.path("wsjtx", "data",
                      "2022_12_13", "loop1_ALL.TXT")


def test_stats():
    d = [50, 120] + list(range(2000, 4000, 100))          # 2 local + 20 sky-wave from 2,000 km
    s = skip.stats(d, "10m")
    assert s.local_n == 2 and s.n == 20
    assert s.skip_km == pytest.approx(2190, abs=1)          # 10th percentile
    assert s.median_km == pytest.approx(2950, abs=1)
    assert 6 < s.fof2_est_mhz < 10                          # 28 MHz with a ~2,000 km skip
    few = skip.stats([1000, 2000], "10m")
    assert few.skip_km is None and few.fof2_est_mhz is None and few.n == 2


def test_by_hour_and_summary():
    samples = [(T0 + dt.timedelta(minutes=k), "20m", 600 + 50 * k) for k in range(25)]
    samples += [(T0 + dt.timedelta(hours=1), "20m", 900)] * 3
    r = skip.by_hour(samples)
    assert list(r) == [("20m", T0), ("20m", T0 + dt.timedelta(hours=1))]
    assert r[("20m", T0)].skip_km is not None and r[("20m", T0 + dt.timedelta(hours=1))].skip_km is None
    assert skip.summary_line([("10m", d) for d in range(2000, 4000, 100)] + [("20m", 500)]) == "10m ~2,190 km"
    closing = skip.stats(list(range(5000, 9000, 100)), "10m")       # only long multi-hop paths left
    assert closing.skip_km == 5390 and closing.fof2_est_mhz is None


def test_log_samples_learn_grids_from_anywhere_in_log():
    log = [(T0, w.Status("c", dial_hz=28074000, mode="FT8", de_grid="CN85on")),
           (T0, w.Decode("c", message="W7ORB K1ABC -10")),             # K1ABC's grid only sent later
           (T0 + dt.timedelta(minutes=1), w.Decode("c", message="CQ K1ABC FN42")),
           (T0 + dt.timedelta(minutes=2), w.Decode("c", message="CQ NOGRID"))]
    s = skip.log_samples(log)
    assert [(b, round(d)) for _, b, d in s] == [("10m", round(s[0][2]))] * 2 and s[0][2] > 3500


@pytest.mark.skipif(not os.path.isdir(MISC2), reason=realdata.REASON)
def test_real_misc2_10m_skip():
    import warnings
    from maidenhead_mapper.core import psk
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reports = psk.add_remote_station(psk.load_reports(MISC2), "W7ORB")
    pairs = [(b, d) for _, b, d in skip.psk_samples(reports, "CN85on")]
    s = skip.stats([d for b, d in pairs if b == "10m"], "10m")
    assert 1500 < s.skip_km < 3000 and 6 < s.fof2_est_mhz < 11   # skip zone seen earlier; foF2 ~7-10 MHz


@pytest.mark.skipif(not os.path.isfile(ALLTXT), reason=realdata.REASON)
def test_real_alltxt_samples():
    log = alltxt.to_log(alltxt.read([ALLTXT]), de_grid="CN85on")
    s = skip.log_samples(log)
    assert len(s) > 300 and all(b == "10m" for _, b, _ in s)
