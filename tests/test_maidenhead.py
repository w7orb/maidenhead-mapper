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


def test_extended_precision():
    w, s, e, n = mh.bounds("CN85pl12ab")
    assert e - w == pytest.approx(2 / 5760) and n - s == pytest.approx(1 / 5760)
    # each longer locator sits inside its prefix
    for loc in ("CN85pl12ab", "EM25tj70bd", "RR99xx99xx", "AA00aa00aa"):
        for k in (2, 4, 6, 8):
            pw, ps, pe, pn = mh.bounds(loc[:k])
            cw, cs, ce, cn = mh.bounds(loc)
            assert pw <= cw and ps <= cs and ce <= pe + 1e-12 and cn <= pn + 1e-12


def test_extended_round_trip_and_case():
    # lon remainder in the 8-char cell is 16.08 tenth-cells -> 'q'
    assert mh.to_locator(45.495115, -122.677748, 10) == "CN85pl88qt"
    assert mh.to_locator(45.495, -122.678, 10) == "CN85pl88pt"
    lon, lat = mh.center("cn85pl88pt")
    assert mh.to_locator(lat, lon, 10) == "CN85pl88pt"
    assert mh.display("cn85PL12AB") == "CN85pl12ab"


def test_truncate():
    assert mh.truncate("cn85pl12ab", 6) == "CN85PL"
    assert mh.truncate("CN85", 6) == "CN85"
    assert mh.truncate("CN85", None) == "CN85"
    with pytest.raises(ValueError):
        mh.truncate("CN85", 5)


@pytest.mark.parametrize("bad", ["CN85pl1", "CN85pl1a", "CN85plaa", "CN85pl12a", "CN85pl12yz", "CN85pl12ab1"])
def test_invalid_extended(bad):
    assert not mh.is_valid(bad)


def test_great_circle():
    (part,) = mh.great_circle(-122.6, 45.5, -71.0, 42.4)        # Portland -> Boston
    assert part[0] == (-122.6, 45.5) and part[-1] == (-71.0, 42.4)
    assert max(lat for _, lat in part) > 46                      # bows north of both ends
    parts = mh.great_circle(-122.6, 45.5, 139.7, 35.7)           # Portland -> Tokyo crosses 180
    assert len(parts) == 2
    assert parts[0][-1][0] == -180.0 and parts[1][0][0] == 180.0
    assert parts[0][-1][1] == parts[1][0][1]
    assert all(abs(x) <= 180 for p in parts for x, _ in p)
    assert mh.great_circle(1, 2, 1, 2) == [[(1, 2), (1, 2)]]
