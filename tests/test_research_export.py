import csv
import datetime as dt
import json
import math

import pytest

from tests import sample_alltxt as sample

from maidenhead_mapper.core import alltxt
from maidenhead_mapper.core import maidenhead as mh
from maidenhead_mapper.core import research_export as rx

UTC = dt.timezone.utc
ME, MY_GRID = "N0CALL", "CN85"


def sample_rows(extra=()):
    lines = sample.lines() + list(extra)
    rows = [alltxt.parse_line(s) for s in lines]
    return sorted((r for r in rows if r), key=lambda r: r.time)


def line(secs, mhz, rxtx, snr, msg):
    t = sample.T0 + dt.timedelta(seconds=secs)
    return f"{t:%y%m%d_%H%M%S} {mhz:9.3f} {rxtx} FT8 {snr:6d}  0.1 1500 {msg}"


def build(rows=None, **kw):
    opts = rx.Options(my_call=kw.pop("my_call", ME), my_grid=kw.pop("my_grid", MY_GRID), **kw)
    return rx.build(sample_rows() if rows is None else rows, opts)


def kinds(rows):
    out = {}
    for r in rows:
        out[r["report_kind"]] = out.get(r["report_kind"], 0) + 1
    return out


def test_one_row_per_observed_snr():
    rows, st = build()
    # 12 Rx decodes; 5 reports: -05, R-08 (20m), -12, R-15, -03 (15m)
    assert kinds(rows) == {rx.MY_DECODE: 12, rx.EXCHANGE_REPORT: 5}
    assert st.rows == 17 and st.by_kind[rx.EXCHANGE_REPORT] == 5
    assert st.exchanges == 4          # K1ABC-W4XYZ, JA1AAA-K1ABC, VK2BBB-W4XYZ, W9NOG-K1ABC
    assert all(set(r) == set(rx.COLUMNS) for r in rows)
    assert {r["schema_version"] for r in rows} == {rx.SCHEMA_VERSION}
    assert {r["source"] for r in rows} == {"alltxt"}
    assert {r["observer"] for r in rows} == {ME}


def test_report_direction_rx_is_the_reporter():
    rows, _ = build()
    rep = {(r["tx_call"], r["rx_call"]): r for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT}
    # "W4XYZ K1ABC -05": K1ABC hears W4XYZ at -5
    assert rep[("W4XYZ", "K1ABC")]["snr_db"] == -5
    assert rep[("K1ABC", "W4XYZ")]["snr_db"] == -8
    assert rep[("K1ABC", "JA1AAA")]["snr_db"] == -12
    assert rep[("JA1AAA", "K1ABC")]["snr_db"] == -15
    r = rep[("W4XYZ", "K1ABC")]
    assert (r["tx_grid"], r["rx_grid"], r["band"], r["mode"]) == ("EM78", "FN31", "20m", "FT8")
    assert r["time_utc"] == "2026-11-11T21:00:30Z"
    assert r["freq_mhz"] == pytest.approx(14.0755)       # dial + 1500 Hz


def test_my_decodes_are_received_by_me():
    rows, _ = build()
    mine = [r for r in rows if r["report_kind"] == rx.MY_DECODE]
    assert {r["rx_call"] for r in mine} == {ME}
    assert {r["rx_grid"] for r in mine} == {MY_GRID}
    cq = next(r for r in mine if r["time_utc"] == "2026-11-11T21:00:00Z")
    assert (cq["tx_call"], cq["snr_db"], cq["exchange_id"]) == ("K1ABC", -10, "")


def test_exchange_id_links_both_directions_and_my_decodes():
    rows, _ = build()
    ex = {r["exchange_id"] for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT
          and {r["tx_call"], r["rx_call"]} == {"K1ABC", "W4XYZ"}}
    assert len(ex) == 1
    linked = [r for r in rows if r["exchange_id"] in ex]
    assert kinds(linked) == {rx.EXCHANGE_REPORT: 2, rx.MY_DECODE: 4}
    assert len({r["exchange_id"] for r in rows if r["exchange_id"]}) == 4


def test_missing_grid_leaves_path_fields_empty():
    rows, st = build()
    r = next(r for r in rows if r["tx_call"] == "W9NOG")
    assert r["tx_grid"] == "" and r["distance_km"] == "" and r["day_night"] == "" and r["grid_precision"] == ""
    assert st.with_both_grids == 16


def test_path_features():
    rows, _ = build()
    r = next(r for r in rows if (r["tx_call"], r["rx_call"]) == ("W4XYZ", "K1ABC"))
    d, b = mh.distance_bearing("EM78", "FN31")
    assert r["distance_km"] == pytest.approx(d, abs=0.1) and r["bearing_deg"] == pytest.approx(b, abs=0.1)
    assert r["n_min_hops"] == 1 and r["grid_precision"] == 4
    assert r["cp2_lat"] == "" and r["cp1_sza_deg"] != ""        # short: midpoint only
    far = next(r for r in rows if (r["tx_call"], r["rx_call"]) == ("K1ABC", "JA1AAA"))
    assert far["distance_km"] > 10000 and far["n_min_hops"] >= 3
    assert far["cp2_lat"] != "" and far["day_night"] in ("day", "night", "twilight", "mixed")


def test_control_points_1000_km_from_each_end():
    (lon1, lat1), (lon2, lat2) = mh.center("FN31"), mh.center("PM95")
    d, _ = mh.distance_bearing("FN31", "PM95")
    cps = rx.control_points(lon1, lat1, lon2, lat2, d)
    assert len(cps) == 2
    assert _km(lon1, lat1, *cps[0]) == pytest.approx(1000, abs=1)
    assert _km(lon2, lat2, *cps[1]) == pytest.approx(1000, abs=1)
    (mlon, mlat), = rx.control_points(-100, 40, -90, 40, 850)
    assert mlat > 40 and mlon == pytest.approx(-95, abs=0.01)     # great-circle midpoint bulges north
    assert rx.control_points(1, 2, 1, 2, 0) == [(1, 2)]


def _km(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * mh.EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def test_solar_zenith_and_day_night():
    noon = dt.datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert rx.solar_zenith_deg(23.4, 0.0, noon) < 3
    assert rx.solar_zenith_deg(-23.4, 180.0, noon) > 170
    assert rx.light(45) == "day" and rx.light(100) == "twilight" and rx.light(120) == "night"
    assert rx.day_night([45, 50]) == "day" and rx.day_night([45, 120]) == "mixed" and rx.day_night([]) == ""


def test_censored_flag_and_floor():
    rows, st = build()
    assert st.censored == 0
    rows, st = build(floor_db=-14)
    low = sorted(r["snr_db"] for r in rows if r["censored"] == 1)
    assert low == [-20, -18, -17, -15, -14]
    assert st.censored == 5


def test_window_and_band_filter():
    rows, _ = build(band="20m")
    assert kinds(rows) == {rx.MY_DECODE: 5, rx.EXCHANGE_REPORT: 2}
    rows, _ = build(start=sample.T0 + dt.timedelta(hours=1))
    assert {r["band"] for r in rows} == {"15m"}
    assert kinds(rows) == {rx.MY_DECODE: 7, rx.EXCHANGE_REPORT: 3}
    # W4XYZ's grid was sent at 21:00, before the window, and is still known
    r = next(r for r in rows if r["tx_call"] == "W4XYZ")
    assert r["tx_grid"] == "EM78"
    rows, _ = build(end=sample.T0 + dt.timedelta(seconds=30))          # end is exclusive
    assert len(rows) == 2


def test_repeated_report_is_one_row():
    extra = [line(31, 14.074, "Rx", -12, "W4XYZ K1ABC -05"), line(32, 14.074, "Rx", -12, "W4XYZ K1ABC -05")]
    rows, _ = build(sample_rows(extra))
    rep = [r for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT and r["tx_call"] == "W4XYZ"]
    assert len(rep) == 1 and rep[0]["n_decoded"] == 3 and rep[0]["time_utc"] == "2026-11-11T21:00:30Z"
    assert kinds(rows)[rx.MY_DECODE] == 14


def test_my_transmitted_report():
    extra = [line(90, 14.074, "Rx", -6, "N0CALL W4XYZ EM78"), line(105, 14.074, "Tx", 0, "W4XYZ N0CALL -07"),
             line(120, 14.074, "Rx", -6, "N0CALL W4XYZ R-11")]
    rows, _ = build(sample_rows(extra), my_grid="CN85ab")
    rep = {(r["tx_call"], r["rx_call"]): r for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT}
    assert rep[("W4XYZ", ME)]["snr_db"] == -7          # I sent: I hear W4XYZ at -7
    assert rep[(ME, "W4XYZ")]["snr_db"] == -11
    # my 6-char grid is kept although my CQ only carries CN85
    assert rep[("W4XYZ", ME)]["rx_grid"] == "CN85ab" and rep[("W4XYZ", ME)]["grid_precision"] == 4
    rows, _ = build(sample_rows(extra), my_grid="CN85ab", grid_chars=4)
    assert {r["rx_grid"] for r in rows if r["rx_call"] == ME} == {"CN85"}


def test_pseudonymise():
    plain, _ = build()
    rows, _ = build(pseudonymise=True, salt="s1")
    text = json.dumps(rows)
    for call in ("K1ABC", "W4XYZ", "JA1AAA", "VK2BBB", "W9NOG", ME):
        assert call not in text
    assert [r["tx_grid"] for r in rows] == [r["tx_grid"] for r in plain]  # grids kept (same order)
    k = rx.pseudonym("K1ABC", "s1")
    assert len(k) == rx.PSEUDONYM_HEX and k == rx.pseudonym("k1abc", "s1") != rx.pseudonym("K1ABC", "s2")
    assert any(r["tx_call"] == k for r in rows)
    assert {r["observer"] for r in rows} == {rx.pseudonym(ME, "s1")}
    assert {r["exchange_id"] for r in rows} != {r["exchange_id"] for r in plain}


def test_write_csv_and_schema(tmp_path):
    rows, st = build()
    path = str(tmp_path / "out.csv")
    rx.write_csv(path, rows)
    with open(path, encoding="utf-8", newline="") as f:
        back = list(csv.DictReader(f))
    assert len(back) == 17 and list(back[0]) == rx.COLUMNS
    assert rx.schema_path(path) == str(tmp_path / "out.schema.json")
    opts = rx.Options(my_call=ME, start=sample.T0)
    doc = rx.schema_doc(st, opts, "9.9.9", "out.csv", now=sample.T0)
    rx.write_schema(rx.schema_path(path), doc)
    with open(rx.schema_path(path), encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["schema_version"] == rx.SCHEMA_VERSION and doc["plugin_version"] == "9.9.9"
    assert doc["row_counts"]["total"] == 17 and doc["row_counts"]["exchange_report"] == 5
    assert doc["decode_floor_db"] == -24 and doc["pseudonymised"] is False
    assert [f["name"] for f in doc["fields"]] == rx.COLUMNS
    assert doc["time_span"]["first_row_utc"] == "2026-11-11T21:00:00Z"


def test_plugin_version_from_metadata():
    v = rx.plugin_version()
    assert v and v[0].isdigit()
