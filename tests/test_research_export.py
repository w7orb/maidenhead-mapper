import csv
import datetime as dt
import json
import math
import os

import pytest

from tests import sample_alltxt as sample

from maidenhead_mapper.core import alltxt
from maidenhead_mapper.core import ccir
from maidenhead_mapper.core import es
from maidenhead_mapper.core import iono
from maidenhead_mapper.core import maidenhead as mh
from maidenhead_mapper.core import research_export as rx
from tests.test_ccir import CACHE, constant_month

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


# ------------------------------------------------------------------ schema 1.1
COLUMNS_1_0 = ["schema_version", "source", "observer", "time_utc", "band", "freq_mhz", "mode", "report_kind",
               "exchange_id", "tx_call", "tx_grid", "rx_call", "rx_grid", "snr_db", "censored", "n_decoded",
               "grid_precision", "distance_km", "bearing_deg", "n_min_hops", "cp1_lat", "cp1_lon", "cp2_lat",
               "cp2_lon", "cp1_sza_deg", "cp2_sza_deg", "day_night"]


def test_schema_1_1_appends_columns():
    assert rx.SCHEMA_VERSION == "1.1"
    assert rx.COLUMNS[:len(COLUMNS_1_0)] == COLUMNS_1_0 == rx.COLUMNS_1_0     # 1.0 readers keep working
    assert rx.COLUMNS[len(COLUMNS_1_0):] == ["muf_basic_mhz", "f_over_muf", "es_candidate", "foes_min_mhz"]


def model_source(fof2=5.0, m3000=3.0, r12=100):
    """CCIR model with the same foF2 / M(3000)F2 everywhere, at a given R12."""
    model = ccir.Model({m: constant_month(fof2, fof2, m3000, m3000) for m in range(1, 13)})
    return ccir.ModelSource(model, r12)


def expected_muf(model_src, grid_a, grid_b, when):
    """The basic MUF Check paths gets with the CCIR option: analyse_path on the model's conditions."""
    cond, _, _ = model_src.conditions(when)
    (x1, y1), (x2, y2) = mh.center(grid_a), mh.center(grid_b)
    return iono.analyse_path(x1, y1, x2, y2, 14.0, cond).muf_mhz


def parse_time(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def test_muf_per_row_matches_the_ccir_model():
    src = model_source(fof2=7.0)
    rows, st = build(muf_source=src)
    with_grids = [r for r in rows if r["distance_km"] != ""]
    assert st.with_muf == len(with_grids) == 16
    check = model_source(fof2=7.0)          # a fresh source: no shared cache
    for r in with_grids:
        muf = expected_muf(check, r["tx_grid"], r["rx_grid"], parse_time(r["time_utc"]))
        assert r["muf_basic_mhz"] == pytest.approx(muf, abs=0.006)
        assert r["f_over_muf"] == pytest.approx(r["freq_mhz"] / muf, abs=0.002)
    # the same path either way round
    a = expected_muf(check, "FN31", "PM95", sample.T0)
    b = expected_muf(check, "PM95", "FN31", sample.T0)
    assert a == pytest.approx(b, abs=0.01)
    # no grids: empty
    r = next(r for r in rows if r["tx_call"] == "W9NOG")
    assert r["muf_basic_mhz"] == r["f_over_muf"] == r["es_candidate"] == ""
    assert sum(st.muf_labels.values()) == 16 and list(st.muf_labels) == ["CCIR median, R12 100 (given)"]


def test_muf_follows_the_model_time_and_strength():
    weak, strong = build(muf_source=model_source(fof2=4.0))[0], build(muf_source=model_source(fof2=10.0))[0]
    pick = lambda rows: next(r for r in rows if (r["tx_call"], r["rx_call"]) == ("W4XYZ", "K1ABC")
                             and r["report_kind"] == rx.EXCHANGE_REPORT)
    # roughly in proportion to foF2 (not exactly: P.533's correction uses foF2 / foE)
    assert 2.2 < pick(strong)["muf_basic_mhz"] / pick(weak)["muf_basic_mhz"] < 2.6
    assert pick(weak)["f_over_muf"] > pick(strong)["f_over_muf"]


@pytest.mark.skipif(not os.path.isfile(os.path.join(CACHE, "ccir11.asc")), reason="no cached CCIR coefficients")
def test_muf_with_real_coefficients():
    src = ccir.ModelSource(ccir.load_dir(CACHE), 60)
    rows, st = build(muf_source=src)
    check = ccir.ModelSource(ccir.load_dir(CACHE), 60)
    r = next(r for r in rows if (r["tx_call"], r["rx_call"]) == ("K1ABC", "JA1AAA"))
    muf = expected_muf(check, "FN31", "PM95", parse_time(r["time_utc"]))
    assert 5 < muf < 40 and r["muf_basic_mhz"] == pytest.approx(muf, abs=0.006)


def test_muf_empty_without_model_or_sunspot_number():
    rows, st = build()
    assert {r["muf_basic_mhz"] for r in rows} == {""} and st.with_muf == 0
    src = ccir.ModelSource(model_source().model)       # no R12 given, none to look up
    rows, st = build(muf_source=src)
    assert {r["muf_basic_mhz"] for r in rows} == {""}
    assert list(st.muf_missing) == ["no sunspot number for 2026-11; enter one"]
    assert sum(st.muf_missing.values()) == 16


def test_es_candidate_rule():
    f = rx.muf_features
    # 6m at single-Es-hop range: Es whatever the F2 MUF
    out = f(50.313, "6m", 1500.0, None)
    assert out["es_candidate"] == 1 and out["foes_min_mhz"] == pytest.approx(es.foes_min_mhz(50.313, 1500.0), abs=0.01)
    # 10m: above the basic MUF -> Es; below -> F2, not a candidate; no MUF -> unknown (empty)
    assert f(28.076, "10m", 1500.0, 20.0)["es_candidate"] == 1
    assert f(28.076, "10m", 1500.0, 30.0) == {"muf_basic_mhz": 30.0, "f_over_muf": 0.936, "es_candidate": 0}
    assert "es_candidate" not in f(28.076, "10m", 1500.0, None)
    # outside the single-hop range, or not an Es band: 0, MUF or not
    assert f(28.076, "10m", 3000.0, 20.0)["es_candidate"] == 0
    assert f(28.076, "10m", 300.0, 20.0)["es_candidate"] == 0
    assert f(14.076, "20m", 1500.0, 10.0)["es_candidate"] == 0
    assert f(14.076, "20m", 1500.0, None) == {"es_candidate": 0}
    # same rule as the detector's mode_guess
    for band, mhz, d, muf in (("10m", 28.076, 1500.0, 20.0), ("10m", 28.076, 1500.0, 30.0), ("6m", 50.3, 900.0, None)):
        status = iono.status_for(mhz, muf) if muf else None
        assert (f(mhz, band, d, muf)["es_candidate"] == 1) == (es.mode_guess(d, band, status) == es.MODE_ES)


def repeat_rows():
    return sample_rows(sample.repeat_lines())


def test_es_candidate_in_rows():
    low = build(repeat_rows(), muf_source=model_source(fof2=4.0))[0]
    high = build(repeat_rows(), muf_source=model_source(fof2=14.0))[0]
    pick = lambda rows: [r for r in rows if {r["tx_call"], r["rx_call"]} == {"K2DEF", "W5GHI"}]
    d = pick(low)[0]["distance_km"]
    assert 500 < d < 2298
    assert {r["es_candidate"] for r in pick(low)} == {1} and all(r["f_over_muf"] > 1 for r in pick(low))
    assert {r["es_candidate"] for r in pick(high)} == {0} and all(r["f_over_muf"] <= 1 for r in pick(high))
    assert all(r["foes_min_mhz"] == "" for r in pick(high))
    rows, st = build(repeat_rows(), muf_source=model_source(fof2=4.0))
    assert st.es_candidates == sum(r["es_candidate"] == 1 for r in rows) > len(pick(low))   # + W6MNO -> me
    # 10m in range but no model: can't tell
    assert {r["es_candidate"] for r in pick(build(repeat_rows())[0])} == {""}


def test_not_decoded_rows():
    rows, st = build(repeat_rows())
    nd = [r for r in rows if r["report_kind"] == rx.NOT_DECODED]
    got = sorted((r["time_utc"], r["tx_call"], r["rx_call"]) for r in nd)
    assert got == [("2026-11-11T23:00:30Z", "K2DEF", "W5GHI"),     # W5GHI missed the -07
                   ("2026-11-11T23:01:30Z", "K2DEF", "W5GHI"),     # ... and the RR73
                   ("2026-11-11T23:05:15Z", ME, "W6MNO")]          # W6MNO missed my report
    assert st.by_kind[rx.NOT_DECODED] == 3
    for r in nd:
        assert r["snr_db"] == "" and r["censored"] == 1 and r["n_decoded"] == 0
        assert r["exchange_id"] and r["band"] == "10m" and r["mode"] == "FT8"
    first = nd[0] if nd[0]["time_utc"] < nd[1]["time_utc"] else nd[1]
    assert (first["tx_grid"], first["rx_grid"]) == ("FN20", "EM12") and first["distance_km"] != ""
    # same exchange as the reports and my decodes of it
    ex = {r["exchange_id"] for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT
          and {r["tx_call"], r["rx_call"]} == {"K2DEF", "W5GHI"}}
    assert {r["exchange_id"] for r in nd if r["rx_call"] == "W5GHI"} == ex
    # 1.0 rows are unchanged by the new kind
    old, _ = build(sample_rows())
    assert kinds(old) == {rx.MY_DECODE: 12, rx.EXCHANGE_REPORT: 5}
    # the repeated report is still one exchange_report row with n_decoded 2
    rep = next(r for r in rows if r["report_kind"] == rx.EXCHANGE_REPORT and r["rx_call"] == "W5GHI")
    assert rep["n_decoded"] == 2 and rep["snr_db"] == -10


def test_not_decoded_window_and_censored_count():
    rows, st = build(repeat_rows(), start=sample.T_REPEATS + dt.timedelta(seconds=60))
    assert sorted(r["time_utc"] for r in rows if r["report_kind"] == rx.NOT_DECODED) == \
        ["2026-11-11T23:01:30Z", "2026-11-11T23:05:15Z"]
    assert st.censored == sum(r["censored"] for r in rows) == 2


def msg(secs, sender, to, text, mode="FT8"):
    p = rx.ft8msg.parse_message(f"{to} {sender} {text}".strip())
    return rx._Msg(sample.T0 + dt.timedelta(seconds=secs), sender, to, rx._stage(p, f"{to} {sender} {text}".strip()),
                   f"{to} {sender} {text}".strip(), 14.075, mode, "20m")


def test_not_decoded_rule_details():
    nd = rx.not_decoded
    # FT4: 7.5 s slots (ALL.TXT rounds to whole seconds)
    seq = [msg(0, "S1", "T1", "FN31", "FT4"), msg(7, "T1", "S1", "-03", "FT4"), msg(15, "S1", "T1", "FN31", "FT4")]
    assert [(s, m.sender) for s, m in nd(seq)] == [("S1", "T1")]
    # FT8 timing on FT4 messages: no
    seq = [msg(0, "S1", "T1", "FN31", "FT4"), msg(15, "T1", "S1", "-03", "FT4"), msg(30, "S1", "T1", "FN31", "FT4")]
    assert nd(seq) == []
    # T's message is not the answer (a repeat of its own earlier step, or a jump): no
    seq = [msg(0, "S1", "T1", "-05"), msg(15, "T1", "S1", "FN31"), msg(30, "S1", "T1", "-05")]
    assert nd(seq) == []
    seq = [msg(0, "S1", "T1", "FN31"), msg(15, "T1", "S1", "RR73"), msg(30, "S1", "T1", "FN31")]
    assert nd(seq) == []
    # RR73 repeated after a 73: no (stages 0-2 only)
    seq = [msg(0, "S1", "T1", "RR73"), msg(15, "T1", "S1", "73"), msg(30, "S1", "T1", "RR73")]
    assert nd(seq) == []
    # S changed its message: it decoded T
    seq = [msg(0, "S1", "T1", "-05"), msg(15, "T1", "S1", "R-07"), msg(30, "S1", "T1", "RR73")]
    assert nd(seq) == []
    # roger report answered with RRR, then repeated: yes; the observer as S: no
    seq = [msg(0, "S1", "T1", "R-05"), msg(15, "T1", "S1", "RRR"), msg(30, "S1", "T1", "R-05")]
    assert len(nd(seq)) == 1 and nd(seq, me="S1") == [] and nd(seq, me="S1/P") == []
    # a duplicate decode in the same slot doesn't break the pattern
    seq = [msg(0, "S1", "T1", "-05"), msg(0, "S1", "T1", "-05"), msg(15, "T1", "S1", "R-07"), msg(30, "S1", "T1", "-05")]
    assert len(nd(seq)) == 1


def test_schema_doc_1_1(tmp_path):
    rows, st = build(repeat_rows(), muf_source=model_source())
    doc = rx.schema_doc(st, rx.Options(my_call=ME, muf_source=model_source()), "9.9.9", "x.csv", now=sample.T0)
    assert doc["schema_version"] == "1.1" and set(doc["changes"]) == {"1.0", "1.1"}
    assert doc["row_counts"]["not_decoded"] == 3 and doc["row_counts"]["with_muf"] == st.with_muf > 0
    assert doc["row_counts"]["es_candidate"] == st.es_candidates
    assert doc["muf_model"]["r12_used"] == {"CCIR median, R12 100 (given)": st.with_muf}
    assert "not_decoded" in doc["not_decoded_rule"] or "S sent T" in doc["not_decoded_rule"]
    assert [f["name"] for f in doc["fields"]] == rx.COLUMNS
    assert rx.schema_doc(st, rx.Options(my_call=ME))["muf_model"] is None
    path = str(tmp_path / "x.csv")
    rx.write_csv(path, rows)
    with open(path, encoding="utf-8", newline="") as f:
        back = list(csv.DictReader(f))
    nd = [r for r in back if r["report_kind"] == "not_decoded"]
    assert len(nd) == 3 and {r["snr_db"] for r in nd} == {""} and {r["censored"] for r in nd} == {"1"}
