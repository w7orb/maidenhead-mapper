import datetime as dt
import json
import os

import pytest

from maidenhead_mapper.core import psk, stations

ME = "W7ORB"
MISC2 = os.path.join(os.path.dirname(__file__), "..", "..", "pskreporter-analytics", "data", "misc2")


def rep(sender, receiver, t, snr=-10, freq=14074000, sloc="CN85on", rloc="CN85on", mode="FT8"):
    return {"senderCallsign": sender, "receiverCallsign": receiver, "senderLocator": sloc,
            "receiverLocator": rloc, "flowStartSeconds": t, "frequency": freq, "sNR": snr, "mode": mode}


def utc(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)


def load(folder):
    return psk.add_remote_station(psk.load_reports(str(folder)), ME)


def write(folder, name, reports):
    (folder / name).write_text(json.dumps({"receptionReport": reports}), encoding="utf-8")


@pytest.fixture
def folder(tmp_path):
    write(tmp_path, "psk_1.json", [
        rep("K1ABC", ME, 1000, snr=-15, sloc="FN42"),                     # heard
        rep("K1ABC", ME, 2000, snr=-5, sloc="FN42ab", freq=7074000),      # heard again, 40m, finer grid
        rep(ME, "DL1XYZ", 1500, snr=-20, rloc="JO62qm"),                  # heard_me
        rep("w7orb/p", "JA1AAA", 1600, snr=-18, rloc="PM95"),             # heard_me, portable me
        rep(ME, ME, 1700),                                                # self-spot
        rep("X1X", "Y1Y", 1800),                                          # neither
        rep("N0LOC", ME, 1900, sloc=None),                                # no locator
    ])
    write(tmp_path, "psk_2.json", [rep("K1ABC", ME, 2000, snr=-5, sloc="FN42ab", freq=7074000)])  # duplicate pull
    (tmp_path / "psk_3.json").write_text("", encoding="utf-8")                                   # empty file
    (tmp_path / "notes.txt").write_text("ignore me")
    return tmp_path


def test_load_dedupes_and_lists_skipped(folder):
    with pytest.warns(UserWarning, match="psk_3.json"):
        reports = psk.load_reports(str(folder))
    assert len(reports) == 7
    assert reports.skipped_files == ["psk_3.json"] and reports.files_read == 2
    dup = [r for r in reports if r["flowStartSeconds"] == 2000]
    assert len(dup) == 1 and dup[0]["copies"] == 2
    assert dup[0]["firstSeenFile"] == "psk_1.json" and dup[0]["sourceFile"] == "psk_2.json"
    assert dup[0]["time"] == utc(2000)


def test_non_ascii_file_loads(tmp_path):
    write(tmp_path, "psk_1.json", [dict(rep("F1ABC", ME, 1), senderRegion="Île-de-France")])
    assert psk.load_reports(str(tmp_path))[0]["senderRegion"] == "Île-de-France"


def test_directions(folder):
    with pytest.warns(UserWarning):
        reports = load(folder)
    by_t = {r["flowStartSeconds"]: r for r in reports}
    assert by_t[1000]["direction"] == "heard" and by_t[1000]["remoteCallsign"] == "K1ABC"
    assert by_t[1000]["myLocator"] == "CN85on"
    assert by_t[1500]["direction"] == "heard_me" and by_t[1500]["remoteLocator"] == "JO62qm"
    assert by_t[1600]["direction"] == "heard_me"
    assert by_t[1700]["direction"] == "self" and by_t[1700]["remoteCallsign"] == ME
    assert by_t[1800]["direction"] is None and by_t[1800]["remoteCallsign"] is None


def test_base_callsign():
    assert psk.base_callsign("w7orb/p") == psk.base_callsign("VE7/W7ORB") == "W7ORB"
    assert psk.base_callsign("W7ORBX") != "W7ORB"
    assert psk.base_callsign("") is None and psk.base_callsign(None) is None


def test_summarize(folder):
    with pytest.warns(UserWarning):
        reports = load(folder)
    rows, st = stations.summarize(reports)
    assert (st.self_spots, st.no_direction, st.no_locator, st.stations) == (1, 1, 1, 3)
    by = {(r.call, r.direction): r for r in rows}
    k = by[("K1ABC", "heard")]
    assert k.reports == 2 and k.grid == "FN42ab"           # latest locator wins
    assert (k.snr_best, k.snr_median, k.snr_last) == (-5, -10.0, -5)
    assert k.bands == "40m,20m" and k.modes == "FT8"
    assert k.first_utc == utc(1000) and k.last_utc == utc(2000)
    assert k.distance_km > 3000 and 50 < k.bearing_deg < 90  # Portland -> New England
    assert stations.guess_my_grid(reports) == "CN85on"
    assert by[("DL1XYZ", "heard_me")].grid == "JO62qm"
    assert by[("JA1AAA", "heard_me")].lon == pytest.approx(139.0)


def test_summarize_filters_and_precision(folder):
    with pytest.warns(UserWarning):
        reports = load(folder)
    rows, st = stations.summarize(reports, directions=("heard",), precision=4)
    assert [(r.call, r.grid) for r in rows] == [("K1ABC", "FN42")]
    rows, _ = stations.summarize(reports, bands=["40m"])
    assert [(r.call, r.reports, r.bands) for r in rows] == [("K1ABC", 1, "40m")]
    rows, _ = stations.summarize(reports, start=utc(1550))
    assert {r.call for r in rows} == {"JA1AAA", "K1ABC"}


def test_summarize_empty():
    rows, st = stations.summarize([])
    assert rows == [] and st.stations == 0


@pytest.mark.skipif(not os.path.isdir(MISC2), reason="pskreporter-analytics data not checked out alongside")
def test_real_misc2():
    reports = psk.add_remote_station(psk.load_reports(MISC2), ME)
    from collections import Counter
    assert Counter(r["direction"] for r in reports) == {"heard": 12371, "heard_me": 737, "self": 2}
    rows, st = stations.summarize(reports)
    assert st.stations == len(rows) == 5336
    assert stations.guess_my_grid(reports) == "CN85on"
    # Reports of my own signal are placed at the receiver, never all at my square
    assert not any(r.direction == "heard_me" and r.grid.upper().startswith("CN85ON") for r in rows)
