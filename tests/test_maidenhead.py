import pytest

from maidenhead_mapper.core import maidenhead as mh


def test_field_bounds():
    assert mh.bounds("CN") == (-140.0, 40.0, -120.0, 50.0)
    assert mh.bounds("AA") == (-180.0, -90.0, -160.0, -80.0)
    assert mh.bounds("RR") == (160.0, 80.0, 180.0, 90.0)


def test_square_and_subsquare_bounds():
    assert mh.bounds("CN85") == (-124.0, 45.0, -122.0, 46.0)
    assert mh.bounds("RR99") == (178.0, 89.0, 180.0, 90.0)
    w, s, e, n = mh.bounds("cn85pl")
    assert e - w == pytest.approx(5 / 60)
    assert n - s == pytest.approx(2.5 / 60)


def test_portland_regression():
    # The hand-built grid (extent taken from Web Mercator) put Portland in CO.
    assert mh.to_locator(45.495115, -122.677748, 2) == "CN"
    assert mh.to_locator(45.495115, -122.677748, 4) == "CN85"


@pytest.mark.parametrize("lat,lon,expected", [
    (51.5, -0.13, "IO91"),        # London
    (-33.87, 151.21, "QF56"),     # Sydney
    (90.0, 180.0, "AR09"),        # pole + antimeridian wrap to -180
    (-90.0, -180.0, "AA00"),
    (89.99, 179.99, "RR99"),
])
def test_known_locations(lat, lon, expected):
    assert mh.to_locator(lat, lon, 4) == expected


def test_round_trip():
    for loc in ("CN85", "IO91", "QF56", "AA00", "RR99", "JJ00"):
        lon, lat = mh.center(loc)
        assert mh.to_locator(lat, lon, 4) == loc
    lon, lat = mh.center("CN85pl")
    assert mh.to_locator(lat, lon, 6) == "CN85pl"


@pytest.mark.parametrize("bad", ["", "C", "SN", "CN8", "CN8A", "CNAA", "CN85YA", "CN85p", None])
def test_invalid(bad):
    assert not mh.is_valid(bad)
    with pytest.raises(ValueError):
        mh.bounds(bad)


def test_valid_any_case():
    assert mh.is_valid("cn85pl") and mh.is_valid(" CN85 ") and mh.is_valid("io")


def test_iter_cells_world_counts_and_extent():
    fields = list(mh.iter_cells(2))
    squares = list(mh.iter_cells(4))
    assert len(fields) == 324 and len({f[0] for f in fields}) == 324
    assert len(squares) == 32400 and len({s[0] for s in squares}) == 32400
    for cells in (fields, squares):
        assert min(b[0] for _, b in cells) == -180.0
        assert max(b[2] for _, b in cells) == 180.0
        assert min(b[1] for _, b in cells) == -90.0
        assert max(b[3] for _, b in cells) == 90.0
    # every label matches its own polygon
    for loc, b in fields + squares:
        assert mh.bounds(loc) == pytest.approx(b)


def test_iter_cells_partial_extent():
    cells = dict(mh.iter_cells(4, (-123.5, 45.2, -121.5, 45.8)))
    assert set(cells) == {"CN85", "CN95"}
    assert list(mh.iter_cells(2, (200, 0, 210, 10))) == []


def test_distance_bearing():
    d, b = mh.distance_bearing("CN85", "CN86")  # one square north = 1 deg lat
    assert d == pytest.approx(111.2, abs=0.2)
    assert b == pytest.approx(0.0, abs=0.01)
    d, b = mh.distance_bearing("CN85", "CN85")
    assert d == 0.0
    d, _ = mh.distance_bearing("CN85", "IO91")  # Portland -> London ~7900 km
    assert 7700 < d < 8100
