import pytest

from maidenhead_mapper.core import hops


def test_max_hop_matches_table():
    assert hops.max_hop_km(300, 0) == pytest.approx(3836, abs=2)
    assert hops.max_hop_km(300, 3) == pytest.approx(3225, abs=2)
    assert hops.max_hop_km(110, 0) == pytest.approx(2351, abs=2)


def test_takeoff_examples():
    # 3,000 km off a 300 km layer: ~4 deg in one hop, ~18 in two, ~28 in three
    assert hops.takeoff_deg(3000, 1) == pytest.approx(4.3, abs=0.1)
    assert hops.takeoff_deg(3000, 2) == pytest.approx(18.0, abs=0.1)
    assert hops.takeoff_deg(3000, 3) == pytest.approx(28.1, abs=0.1)
    assert hops.takeoff_deg(5000, 1) < 0                       # beyond one hop
    assert hops.takeoff_deg(100, 1) > 75                       # near-vertical


def test_hop_options_and_likely():
    opts = hops.hop_options(3000, max_takeoff_deg=35)
    assert [n for n, _ in opts] == [1, 2, 3]
    assert hops.likely_hops(16704) == (5, pytest.approx(hops.takeoff_deg(16704, 5), abs=0.06))
    assert hops.max_hop_km(300, 2) == pytest.approx(3416, abs=2)
    assert hops.likely_hops(3400)[0] == 1 and hops.likely_hops(3450)[0] == 2
    assert hops.likely_hops(3000, height_km=hops.E_HEIGHT_KM)[0] == 2   # E layer: shorter hops
    assert hops.likely_hops(None) == (None, None) and hops.likely_hops(0) == (None, None)


def test_reflection_points():
    (mid,) = hops.reflection_points(0, 0, 90, 0, 1)
    assert mid == pytest.approx((45, 0), abs=1e-9)
    pts = hops.reflection_points(0, 0, 90, 0, 3)
    assert [round(lon) for lon, _ in pts] == [15, 45, 75]
    assert hops.reflection_points(5, 5, 5, 5, 2) == [(5, 5), (5, 5)]
