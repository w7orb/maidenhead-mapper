import datetime as dt

import pytest

from maidenhead_mapper.core import hops, iono
from maidenhead_mapper.core.bands import band_mhz


def contours(*levels_lats):
    """Parallels of latitude as contour lines: [(level, lat), ...]."""
    feats = []
    for level, lat in levels_lats:
        feats.append({"type": "Feature", "properties": {"level-value": level},
                      "geometry": {"type": "LineString", "coordinates": [[x, lat] for x in range(-180, 181, 5)]}})
    return {"type": "FeatureCollection", "features": feats}


def flat(value):
    return iono.ContourField(contours((value, 0)))


def test_contour_interpolation():
    f = iono.ContourField(contours((10, 0), (20, 10), (30, 20)))
    assert f.levels == [10, 20, 30]
    assert f.value_at(0, 5) == pytest.approx(15, abs=0.01)
    assert f.value_at(100, 2.5) == pytest.approx(12.5, abs=0.01)
    assert f.value_at(-179, 17.5) == pytest.approx(27.5, abs=0.01)
    assert f.value_at(0, 10) == pytest.approx(20, abs=0.01)
    assert flat(7).value_at(50, 60) == 7                       # one level everywhere
    assert iono.ContourField({}).value_at(0, 0) is None and not iono.ContourField({})


def test_hop_muf_scaling():
    assert iono.hop_muf(3000, 21.0, 300) == pytest.approx(21.0)
    assert iono.hop_muf(1000, 21.0, 300) < 21.0 * 0.7           # short hops: much lower MUF
    assert iono.hop_muf(3400, 21.0, 300) > 21.0                 # longer hop, lower angle: higher
    # sec(i) at 3000 km off ~300 km is about the familiar M(3000)F2 of ~3.3
    assert iono.sec_incidence(3000, 300) == pytest.approx(3.28, abs=0.05)


def cond(mufd=21.0, fof2=7.0, hmf2=300.0):
    return iono.Conditions(dt.datetime(2026, 11, 11, tzinfo=dt.timezone.utc), flat(mufd), flat(fof2), flat(hmf2))


def test_analyse_path_status():
    # ~3,000 km east-west path near the equator, F2 MUF(3000) = 21 MHz everywhere
    lon2 = 3000 / 111.195
    r = iono.analyse_path(0, 0, lon2, 0, band_mhz("20m"), cond())
    assert r.status == iono.STATUS_F2 and r.hops == 1 and r.muf_mhz == pytest.approx(21.0, abs=0.2)
    assert r.hmf2_km == 300 and r.fof2_mhz == 7 and len(r.points) == 1
    assert iono.analyse_path(0, 0, lon2, 0, band_mhz("10m"), cond()).status == iono.STATUS_ABOVE
    # 1,000 km on 10m: far above the short-hop MUF -> sporadic-E candidate
    short = iono.analyse_path(0, 0, 1000 / 111.195, 0, 28.074, cond(mufd=28.5))
    assert short.status == iono.STATUS_ABOVE and short.muf_mhz < 20


def test_analyse_path_uses_layer_height_for_hops():
    lon2 = 3600 / 111.195      # beyond one hop off 300 km (3,416 km at 2 deg) ...
    assert iono.analyse_path(0, 0, lon2, 0, 14.074, cond(hmf2=300)).hops == 2
    assert iono.analyse_path(0, 0, lon2, 0, 14.074, cond(hmf2=380)).hops == 1   # ... but not off a higher layer
    assert hops.max_hop_km(380, 2) > 3600


def test_no_data():
    empty = iono.Conditions(None, iono.ContourField({}), iono.ContourField({}), iono.ContourField({}))
    assert iono.analyse_path(0, 0, 30, 0, 14.0, empty).status == iono.STATUS_NO_DATA


def test_parse_stations():
    now = dt.datetime(2026, 11, 11, 19, 0, tzinfo=dt.timezone.utc)
    data = [
        {"station": {"code": "MHJ45", "name": "Millstone Hill", "latitude": "42.6", "longitude": "288.5"},
         "time": "2026-11-11T18:50:00", "fof2": 9.95, "hmf2": 257.8, "mufd": 32.5, "md": "3.271", "cs": 70.0},
        {"station": {"code": "AU930", "name": "Austin", "latitude": "30.4", "longitude": "262.3"},
         "time": "2026-03-19T22:10:05", "fof2": 8.6, "md": "3.352", "cs": 100.0},
        {"station": {}, "time": "bad"},
    ]
    fresh, stale = iono.parse_stations(data, now, max_age_h=1)
    assert stale == 1 and len(fresh) == 1
    s = fresh[0]
    assert s.code == "MHJ45" and s.lon == pytest.approx(-71.5) and s.md == pytest.approx(3.271)
    assert s.hmf2 == pytest.approx(257.8) and s.foes is None


def test_band_mhz():
    assert band_mhz("20m") == 14.074 and band_mhz("23cm") == 1270.0 and band_mhz("nope") is None


def test_ionosonde_nudges_map_nearby_only():
    s = iono.Ionosonde("X", "x", 0.0, 0.0, None, fof2=9.0, hmf2=280.0, mufd=30.0, md=None, foes=None, confidence=90)
    c = iono.Conditions(None, flat(21.0), flat(7.0), flat(300.0), [s])
    f2, h, m = c.at(0, 0)
    assert (f2, h, m) == pytest.approx((9.0, 280.0, 30.0))         # at the ionosonde: its values
    f2, h, m = c.at(0, 5)                                          # ~556 km away: partly
    assert 7.0 < f2 < 9.0 and 21.0 < m < 30.0
    assert c.at(0, 30) == pytest.approx((7.0, 300.0, 21.0), abs=1e-6)   # far away: the map
    low = iono.Ionosonde("Y", "y", 0.0, 0.0, None, 9.0, 280.0, 30.0, None, None, confidence=10)
    assert iono.Conditions(None, flat(21.0), flat(7.0), flat(300.0), [low]).at(0, 0) == (7.0, 300.0, 21.0)


def test_long_paths_use_p533_control_points():
    # 10,000 km along the equator, MUF(3000) 20 MHz everywhere
    lon2 = 10000 / 111.195
    middle = iono.Conditions(None, flat(20.0), flat(7.0), flat(300.0))
    r = iono.analyse_path(0, 0, lon2, 0, 21.0, middle)
    assert len(r.points) == 2 and r.muf_mhz == pytest.approx(22.0)          # 1.1 x MUF(3000)
    xs = sorted(round(x) for x, _, *_ in r.points)
    assert xs == [round(2000 / 111.195), round(8000 / 111.195)]              # 2,000 km from each end
    assert r.status == iono.STATUS_F2 and r.hops == 3
    assert iono.analyse_path(0, 0, lon2, 0, 28.0, middle).status == iono.STATUS_ABOVE
