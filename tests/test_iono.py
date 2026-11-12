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


def test_p533_f2_muf_formulas():
    # sec(i) at 3000 km off ~300 km is about the familiar M(3000)F2 of ~3.3
    assert iono.sec_incidence(3000, 300) == pytest.approx(3.28, abs=0.05)
    fh, x = 0.8, 2.0
    dmax = min(iono.p533_dmax(3.0, x), iono.DMAX_CAP_KM)
    assert dmax == iono.DMAX_CAP_KM                                  # eq. (5) ~5,400 km, capped
    b = iono.p533_b(3.0, x)
    assert b == pytest.approx(3.0, abs=0.05)                         # eq. (6): B ~ M(3000)F2
    # eq. (3) at 3,000 km: Cd/C3000 = 1, so MUF = B foF2 + fH/2 (1 - 3000/dmax) ~ MUF(3000)
    assert iono.f2_muf(3000, 7.0, 3.0, fh, x) == pytest.approx(b * 7.0 + fh / 2 * 0.25)
    assert iono.f2_muf(0, 7.0, 3.0, fh, x) == pytest.approx(7.0 + fh / 2, abs=0.01)   # vertical: foF2 + fH/2
    assert iono.f2_muf(1000, 7.0, 3.0, fh, x) < 0.7 * 21.0           # short hops: much lower MUF
    assert iono.f2_muf(4000, 7.0, 3.0, fh, x) > 21.0                 # longest hop: higher
    assert iono.f2_muf(6000, 7.0, 3.0, fh, x) == iono.f2_muf(4000, 7.0, 3.0, fh, x)   # clipped to dmax
    assert iono.mirror_height_km(3.0) == pytest.approx(1490 / 3 - 176) and iono.mirror_height_km(1.5) == 500
    # eq. (30): fD 0 at 0 km rising to ~1 at 4,000 km; eq. (29) between fz and f4
    assert iono.distance_factor(0) == 0 and iono.distance_factor(4000) == pytest.approx(1.0, abs=0.05)
    assert iono.f2_muf_long(0, 7.0, 3.0, fh) == pytest.approx(7.0 + fh / 2)
    assert iono.f2_muf_long(4000, 7.0, 3.0, fh) == pytest.approx(1.1 * 21.0, abs=0.8)
    assert iono.long_path_hops(10000) == (4, 2500)                  # 3 x 3,333 km is under 3 deg off 300 km
    # gyrofrequency at 300 km: ~0.73 MHz at the dipole equator, twice that at the pole
    assert iono.gyro_mhz(0, 0) == pytest.approx(0.73, abs=0.05)
    assert iono.gyro_mhz(-72.68, 80.65) == pytest.approx(2 * iono.gyro_mhz(-72.68 + 180, -80.65 + 90), rel=0.01)


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


KM_DEG = 111.195          # km per degree along the equator


def _xs_km(r):
    return sorted(round(x * KM_DEG) for x, _, *_ in r.points)


def _sample(lon, lat, c):
    f2, h, mufd = c.at(lon, lat)
    _, _, _, m3, fh, x = iono._sample(c, lon, lat)
    return f2, m3, fh, x


def test_p533_short_paths_use_mid_point():
    # D <= dmax (Table 1a, §3.5.1.1): one control point, the mid-point; eq. (3) for d = D / n0
    c = cond()
    r = iono.analyse_path(0, 0, 1000 / KM_DEG, 0, 28.074, c)
    assert len(r.points) == 1 and _xs_km(r) == [500]
    f2, m3, fh, x = _sample(*r.points[0][:2], c)
    assert r.muf_mhz == pytest.approx(iono.f2_muf(1000, f2, m3, fh, x), abs=0.01)
    assert r.status == iono.STATUS_ABOVE and r.muf_mhz < 15


def test_p533_paths_beyond_dmax_use_d0_control_points():
    # dmax < D <= 9,000 km (§3.5.1.2): lower F2(dmax)MUF at T + d0/2 and R - d0/2.
    # hr = 1490/3 - 176 = 321 km -> 3 deg hops up to ~3,290 km, so n0 = 2 for 5,000 km
    c = cond()
    r = iono.analyse_path(0, 0, 5000 / KM_DEG, 0, 21.0, c)
    assert len(r.points) == 2 and _xs_km(r) == [1250, 3750]
    f2, m3, fh, x = _sample(*r.points[0][:2], c)
    assert r.points[0][5] == pytest.approx(iono.f2_muf(iono.DMAX_CAP_KM, f2, m3, fh, x))
    assert r.muf_mhz > 21.0 and r.status == iono.STATUS_F2
    # 7,000-9,000 km (§5.4): the basic MUF is still eq. (3) at the Table 1a points (n0 = 3 here)
    r8 = iono.analyse_path(0, 0, 8000 / KM_DEG, 0, 21.0, c)
    assert _xs_km(r8) == [1333, 6667]
    assert r8.muf_mhz == pytest.approx(r.muf_mhz, abs=0.1) and r8.status == iono.STATUS_F2


def test_long_paths_use_p533_control_points():
    # D > 9,000 km (§5.3.1): hops of dM <= 4,000 km at >= 3 deg off 300 km (4 x 2,500 km for
    # 10,000 km), control points dM/2 from each end, fBM = fz + (f4 - fz) fD
    lon2 = 10000 / KM_DEG
    middle = iono.Conditions(None, flat(20.0), flat(7.0), flat(300.0))
    r = iono.analyse_path(0, 0, lon2, 0, 18.0, middle)
    assert len(r.points) == 2 and _xs_km(r) == [1250, 8750]
    fh = iono.gyro_mhz(r.points[0][0], 0)
    assert r.points[0][5] == pytest.approx(iono.f2_muf_long(2500, 7.0, 20.0 / 7.0, fh))
    assert 18.0 < r.muf_mhz < 20.0
    assert r.status == iono.STATUS_F2 and r.hops == 3        # hops: likely count, 2 deg off hmF2
    assert iono.analyse_path(0, 0, lon2, 0, 21.0, middle).status == iono.STATUS_ABOVE


def test_more_hops_never_raise_the_basic_muf():
    # P.533 eq. (3): the basic MUF grows with hop length up to dmax, so splitting a
    # path into more, shorter hops can only lower it. This is why analyse_path keeps
    # the fewest hops: a path above the MUF there is above it at any hop count.
    for m3 in (2.6, 3.0, 3.6):
        for x in (2.0, 3.5):
            dmax = min(iono.p533_dmax(m3, x), iono.DMAX_CAP_KM)
            prev = None
            for d in range(100, int(dmax) + 1, 50):     # (dips ~0.02 MHz below 50 km: fH term)
                muf = iono.f2_muf(d, 7.0, m3, 0.8, x, dmax)
                assert prev is None or muf >= prev - 1e-9
                prev = muf
    for d in (3000, 6000, 12000):     # and the long-path model: fewer, longer hops are higher too
        n, dm = iono.long_path_hops(d)
        assert iono.f2_muf_long(dm, 7.0, 3.0, 0.8) >= iono.f2_muf_long(d / (n + 1), 7.0, 3.0, 0.8)


def test_hops_muf_is_the_p533_hop_count():
    r = iono.analyse_path(0, 0, 3000 / 111.195, 0, 14.074, cond())
    assert r.hops == 1 and r.hops_muf == 1
    r = iono.analyse_path(0, 0, 10000 / 111.195, 0, 18.0, iono.Conditions(None, flat(20.0), flat(7.0), flat(300.0)))
    assert (r.hops, r.hops_muf) == (3, 4)        # 2 deg geometric minimum vs P.533's 3 deg / 4,000 km rule
    assert iono.analyse_path(0, 0, 0.001, 0, 14.0, cond()).hops_muf is None
