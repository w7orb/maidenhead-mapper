import datetime as dt
import os

import pytest

from maidenhead_mapper.core import ccir

# Where the plugin caches the downloaded coefficients (if QGIS has run it)
CACHE = os.path.join(os.environ.get("APPDATA", ""), "QGIS", "QGIS3", "profiles", "default", "maidenhead_mapper", "ccir")
SCRATCH = os.environ.get("MM_CCIR_DIR", "")


def constant_month(f2_lo, f2_hi, m3_lo, m3_hi):
    """Coefficients that give the same value everywhere and always."""
    def arr(n, v):
        a = [0.0] * n
        a[0] = v
        return a
    return ccir.MonthCoeffs((arr(ccir.N_F2, f2_lo), arr(ccir.N_F2, f2_hi)),
                            (arr(ccir.N_M3, m3_lo), arr(ccir.N_M3, m3_hi)))


def test_term_counts_match_iri():
    for _, nq, k1, m, _ in (ccir._FOF2, ccir._M3000):
        assert 1 + nq[0] + 2 * sum(q + 1 for q in nq[1:k1]) == m


def test_constant_maps_and_solar_interpolation():
    model = ccir.Model({m: constant_month(5.0, 10.0, 3.5, 3.0) for m in range(1, 13)})
    when = dt.datetime(2022, 6, 20, 14, tzinfo=dt.timezone.utc)
    f2, m3, h = model.values(-100, 40, when, 0)
    assert (f2, m3) == pytest.approx((5.0, 3.5))
    assert model.values(10, -30, when, 50)[0] == pytest.approx(7.5)
    assert model.values(10, -30, when, 300)[0] == pytest.approx(12.5)     # capped at R12 = 150
    assert 200 < h < 400
    c = model.conditions(when, 100).at(0, 0)
    assert c[0] == pytest.approx(10.0) and c[2] == pytest.approx(30.0)    # MUF(3000) = foF2 * M


def test_month_interpolation():
    months = {m: constant_month(float(m), float(m), 3.0, 3.0) for m in range(1, 13)}
    model = ccir.Model(months)
    mid_june = dt.datetime(2022, 6, 15, 12, tzinfo=dt.timezone.utc)
    assert model.values(0, 0, mid_june, 0)[0] == pytest.approx(6.0)
    july_1 = dt.datetime(2022, 7, 1, 0, tzinfo=dt.timezone.utc)
    assert 6.0 < model.values(0, 0, july_1, 0)[0] < 7.0
    dec_31 = dt.datetime(2022, 12, 31, 0, tzinfo=dt.timezone.utc)
    assert 1.0 < model.values(0, 0, dec_31, 0)[0] < 12.0                   # wraps to January


def test_modip_grid():
    assert ccir.modip(89.9, 0) > 80 and ccir.modip(-89.9, 0) < -80
    # the dip equator crosses 45 W between 20 S and 20 N
    vals = [ccir.modip(lat, -45.0, 2022) for lat in range(-20, 21, 5)]
    assert vals[0] < 0 < vals[-1] and vals == sorted(vals)
    assert abs(ccir.modip(45.5, -122.6, 2022) - ccir.dipole_modip(45.5, -122.6)) < 6
    with pytest.raises(ValueError):
        ccir.parse("1.0 2.0")


def test_sunspot_sources():
    sm = ccir.parse_silso("2022;11;2022.873; 101.2; 17.7; 1047;1\n2022;12;2022.958; 106.7; 18.1;  860;1\n"
                          "2026;10;2026.790;  -1.0; -1.0; 1126;0\nbad line\n")
    mo = ccir.parse_silso("2026;10;2026.790;  45.7; 10.2; 1126;0\n")
    assert sm == {(2022, 11): 101.2, (2022, 12): 106.7}
    when = dt.datetime(2022, 12, 13, 21, tzinfo=dt.timezone.utc)
    assert ccir.r12_for(when, sm, mo) == (106.7, "SILSO smoothed")
    recent = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
    essn = {"24h": [{"time": recent.timestamp() - 3600, "ssn": 60.0}, {"time": recent.timestamp(), "ssn": 62.0}]}
    assert ccir.r12_for(recent, sm, mo, essn) == (61.0, "KC2G effective")
    assert ccir.r12_for(recent, sm, mo) == (45.7, "SILSO monthly")
    assert ccir.r12_for(dt.datetime(1990, 1, 1, tzinfo=dt.timezone.utc), sm, mo) == (None, "")


def _real_dir():
    for d in (SCRATCH, CACHE):
        if d and os.path.isfile(os.path.join(d, "ccir11.asc")):
            return d
    return None


@pytest.mark.skipif(_real_dir() is None, reason="CCIR coefficient files not downloaded")
def test_matches_pyiri_reference_values():
    d = _real_dir()
    model = ccir.Model({m: ccir.parse(open(os.path.join(d, f"ccir{m + 10}.asc")).read()) for m in range(1, 13)})
    # PyIRI 0.1.7 IRI_monthly_mean_par(2022, ...): foF2 / M(3000) at solar min (R12=0) and max (R12=100)
    ref = [((-122.6, 45.5, 20.0, 12), (5.665, 10.117), (3.569, 3.208)),
           ((-47.9, -15.8, 15.0, 6), (7.329, 10.648), (3.633, 3.231)),
           ((139.7, 35.7, 3.0, 3), (7.678, 11.851), (3.177, 2.920))]
    for (lon, lat, ut, month), fo, m3 in ref:
        when = dt.datetime(2022, month, 15, int(ut), int(ut % 1 * 60), tzinfo=dt.timezone.utc)
        # mid-month exactly: no blending with the next month
        when = when.replace(day=15) if when.hour >= 12 else when.replace(day=16)
        for k, r12 in enumerate((0, 100)):
            f, m, _ = model.values(lon, lat, when, r12)
            assert f == pytest.approx(fo[k], rel=0.03) and m == pytest.approx(m3[k], rel=0.03)
