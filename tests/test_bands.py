import pytest

from maidenhead_mapper.core.bands import BAND_NAMES, band_for_hz, band_for_mhz


@pytest.mark.parametrize("hz,band", [
    (14074852, "20m"), (7074000, "40m"), (28074000, "10m"), (50313000, "6m"),
    (1840000, "160m"), (474200, "630m"), (10136000, "30m"), (144174000, "2m"),
])
def test_ft8_frequencies(hz, band):
    assert band_for_hz(hz) == band


def test_edges_and_gaps():
    assert band_for_mhz(14.0) == "20m" and band_for_mhz(14.35) == "20m"
    assert band_for_mhz(15.0) is None
    assert band_for_hz(None) is None and band_for_hz("x") is None


def test_names_unique_and_ordered():
    assert len(set(BAND_NAMES)) == len(BAND_NAMES)
    assert BAND_NAMES.index("160m") < BAND_NAMES.index("20m") < BAND_NAMES.index("2m")
