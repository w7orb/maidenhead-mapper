import datetime as dt
import os

import pytest

from maidenhead_mapper.core import voacap as vc

DATA = os.path.join(os.path.dirname(__file__), "data", "voacap")
UTC = dt.timezone.utc

# The circuits behind mm_batch3_voacapw.out (made with Settings())
BATCH3 = [vc.Circuit(35.8, -5.9, 44.9, 20.5, 2026, 11, 120, 22, (7.074, 14.074, 21.074, 28.074)),
          vc.Circuit(47.5, -122.5, 51.5, -0.1, 2026, 11, 120, 23, (14.074,)),
          vc.Circuit(-33.9, 151.2, 40.7, -74.0, 2026, 11, 120, 24, (14.074, 10.136))]


def read(name):
    with open(os.path.join(DATA, name), encoding="latin-1") as f:
        return f.read()


def echoed_deck(out_text):
    """The input deck as VOACAP echoes it at the top of its output."""
    lines = out_text.replace("\r", "").split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith(" COMMENT"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith(" QUIT"))
    return [ln[1:].rstrip() for ln in lines[start:end + 1]]


# --- deck ------------------------------------------------------------------------

def test_deck_matches_what_voacap_read():
    """Golden: our deck is exactly what the real program echoed back."""
    ours = [ln.rstrip() for ln in vc.deck(BATCH3, vc.Settings()).splitlines()]
    assert ours == echoed_deck(read("mm_batch3_voacapw.out"))


def _f(text, w, d=0):
    """Read a Fortran Fw.d field: an explicit decimal point wins, else d implied decimals."""
    t = text.strip()
    if not t:
        return 0.0
    return float(t) if "." in t else int(t) / 10 ** d


def test_cards_read_back_with_voacaps_formats():
    """Fixed-width fields as decred.for reads them (formats 1506, 1507, 1508, 1510)."""
    s = vc.Settings(power_w=50, tx_gain_dbi=2.15, rx_gain_dbi=-1.5, noise_dbw=-150, req_snr_ft8=-24)
    d = vc.deck([vc.Circuit(-33.87, 151.21, 47.61, -122.33, 2026, 3, 87.4, 0, (3.573, 28.074))], s, "/")
    cards = {ln[:10].strip(): ln for ln in d.splitlines()}
    c = cards["CIRCUIT"]                                   # 10X,F5.2,A1,3(F9.2,A1),4X,I5
    assert (_f(c[10:15], 5, 2), c[15], _f(c[16:25], 9, 2), c[25]) == (33.87, "S", 151.21, "E")
    assert (_f(c[26:35], 9, 2), c[35], _f(c[36:45], 9, 2), c[45]) == (47.61, "N", 122.33, "W")
    assert int(c[50:55]) == 0                              # short path
    y = cards["SYSTEM"]                                    # 10X,F5.2,F5.0,F5.2,F5.0,3F5.2
    vals = [_f(y[10:15], 5, 2), _f(y[15:20], 5), _f(y[20:25], 5, 2), _f(y[25:30], 5),
            _f(y[30:35], 5, 2), _f(y[35:40], 5, 2), _f(y[40:45], 5, 2)]
    assert vals == pytest.approx([1.0, 150.0, 3.0, 90.0, 10.0, 3.0, 0.1], abs=0.05)
    assert _f(cards["SUNSPOT"][10:15], 5, 2) == 87.0      # not 0.87: the point is written
    assert cards["TIME"][10:30] == "   24   24    1    1"  # 0 UT is hour 24
    assert cards["MONTH"][10:20] == " 2026 3.00"
    f = cards["FREQUENCY"]
    assert [_f(f[10 + 5 * i:15 + 5 * i], 5, 2) for i in range(3)] == [3.57, 28.07, 0.0]
    tx = [ln for ln in d.splitlines() if ln.startswith("ANTENNA       1")][0]   # 10X,4I5,F10.3,1X,A21,1X,F5.1,F10.4
    assert (_f(tx[30:40], 10, 3), tx[41:62].strip(), _f(tx[63:68], 5, 1), _f(tx[68:78], 10, 4)) == \
        (2.15, "default/isotrope", 0.0, 0.05)
    rx = [ln for ln in d.splitlines() if ln.startswith("ANTENNA       2")][0]
    assert _f(rx[30:40], 10, 3) == -1.5
    assert all(len(ln) <= 80 for ln in d.splitlines())


def test_antenna_cards_only_before_the_first_execute():
    d = vc.deck(BATCH3, vc.Settings()).splitlines()
    first_exec = d.index("EXECUTE")
    assert [i for i, ln in enumerate(d) if ln.startswith("ANTENNA")] == [first_exec - 4, first_exec - 3]
    assert d.count("EXECUTE") == 3 and d[-1] == "QUIT"


# --- output ------------------------------------------------------------------------

def test_parse_voacapl_sample_output():
    blocks = vc.parse_output(read("voacapl_test01.out"))
    assert len(blocks) == 24
    b = blocks[0]
    assert (b.label, b.hour, b.muf) == ("TANGIER, Morocco", 1.0, 16.2)
    assert b.freqs[:3] == [6.1, 7.2, 9.7]
    p = vc.prediction(b, 0)                       # 6.07 MHz at 01 UT
    assert p.ok and (p.mode, p.tangle, p.snr_hz, p.rel, p.sdbw, p.loss) == ("1F2", 7.8, 106.0, 1.0, -43.0, 100.0)
    assert b.rows["MODE"][0] == "1F2" and b.rows["TANGLE"][0] == 13.7   # the MUF column
    assert vc.prediction(b, 9).ok is False        # an unused (0.00) frequency
    assert [x.hour for x in blocks] == [float(h) for h in range(1, 25)]
    last = blocks[-1]
    p = vc.prediction(last, 8)                    # 25.89 MHz at 24 UT, above the 17.4 MHz MUF
    assert (p.rel, p.snr_hz, p.sdbw, p.mufday) == (0.01, 26.0, -145.0, 0.0)


def test_parse_batch_output_labels_and_na():
    blocks = vc.parse_output(read("mm_batch3_voacapw.out"))
    assert [(b.label, b.hour) for b in blocks] == [("MM000000", 22.0), ("MM000001", 23.0), ("MM000002", 24.0)]
    assert all(b.header["ssn"] == 120.0 for b in blocks)
    p = vc.prediction(blocks[0], 1)               # 14.074 MHz, 2,440 km at 22 UT
    assert (p.mode, p.snr_hz, p.rel, blocks[0].muf) == ("1F2", 38.0, 0.88, 13.6)
    assert blocks[2].rows["MODE"][0] == "NANA"    # no mode at the MUF itself on that long path
    assert vc.prediction(blocks[2], 0).mode == "EF2"


def test_db_conversion():
    assert vc.BW_FT8_DB == pytest.approx(33.98, abs=0.01)
    assert vc.ft8_snr(13.0) == pytest.approx(-20.98, abs=0.01)
    assert vc.snr_hz_from_ft8(-21) == pytest.approx(12.98, abs=0.01)
    assert vc.Prediction(True, snr_hz=38.0).snr_ft8 == 4.0
    assert vc.Settings().req_snr_hz == pytest.approx(12.98, abs=0.01)


# --- batches and cache ----------------------------------------------------------------

def _requests():
    out = []
    for c in BATCH3:
        out += [vc.Request(c.tx_lat, c.tx_lon, c.rx_lat, c.rx_lon, c.year, c.month, c.ssn, c.hour, f)
                for f in c.freqs]
    return out


def test_group_circuits_rebuilds_the_batch():
    assert [c for c, _ in vc.group_circuits(_requests())] == [
        vc.Circuit(c.tx_lat, c.tx_lon, c.rx_lat, c.rx_lon, c.year, c.month, c.ssn, c.hour,
                   tuple(round(f, 2) for f in c.freqs)) for c in BATCH3]
    many = [vc.Request(1, 2, 3, 4, 2026, 1, 50, 5, 3 + i) for i in range(13)]
    assert [len(c.freqs) for c, _ in vc.group_circuits(many)] == [11, 2]


def test_predict_uses_output_then_cache(tmp_path, monkeypatch):
    calls = []

    def fake_run(install, deck_text, timeout, keep_dir=None):
        calls.append(deck_text)
        return read("mm_batch3_voacapw.out")

    monkeypatch.setattr(vc, "run_deck", fake_run)
    s = vc.Settings()
    reqs = _requests() + [vc.Request(1, 2, 3, 4, 2026, 11, 120, 22, 50.313)]
    cache = vc.Cache(str(tmp_path / "p.json"))
    got = vc.predict(reqs, s, vc.Install("windows", "x.exe", "C:\\itshfbc"), cache)
    assert len(calls) == 1 and calls[0] == vc.deck(BATCH3, s)
    p = got[reqs[1].key(s)]
    assert p.ok and p.snr_hz == 38.0 and p.muf == 13.6
    assert got[reqs[-1].key(s)].error.startswith("50.313 MHz is outside")
    # second run: all from the cache file, no VOACAP needed
    again = vc.predict(reqs, s, None, vc.Cache(str(tmp_path / "p.json")))
    assert len(calls) == 1 and again[reqs[1].key(s)] == p
    # other settings are not served from the cache
    with pytest.raises(vc.NotInstalled):
        vc.predict(reqs[:1], vc.Settings(power_w=5), None, vc.Cache(str(tmp_path / "p.json")))


def test_ssn_mismatch_is_an_error(monkeypatch):
    monkeypatch.setattr(vc, "run_deck", lambda *a, **k: read("mm_batch3_voacapw.out"))
    r = vc.Request(35.8, -5.9, 44.9, 20.5, 2026, 11, 80, 22, 7.074)
    p = vc.predict([r], vc.Settings(), vc.Install("windows", "x", "y"))[r.key(vc.Settings())]
    assert not p.ok and "SSN 120" in p.error


def test_request_key_rounds_like_the_deck():
    s = vc.Settings()
    k1 = vc.request_key(35.801, -5.9, 44.9, 20.5, 2026, 11, 120.2, 0, 14.074, s)
    k2 = vc.request_key(35.80, -5.9, 44.9, 20.5, 2026, 11, 120, 24, 14.07, s)
    assert k1 == k2
    assert k1 != vc.request_key(35.80, -5.9, 44.9, 20.5, 2026, 11, 120, 24, 14.07, vc.Settings(noise_dbw=-150))


# --- install -------------------------------------------------------------------------

def test_not_installed_messages(tmp_path):
    with pytest.raises(vc.NotInstalled) as e:
        vc.find_install(str(tmp_path), platform="win32")
    assert "its.ntia.gov" in str(e.value) and "does not include VOACAP" in str(e.value)
    with pytest.raises(vc.NotInstalled) as e:
        vc.find_install("", platform="linux", which=lambda name: None)
    assert "voacapl" in str(e.value)
    (tmp_path / "bin_win").mkdir()
    (tmp_path / "bin_win" / "voacapw.exe").write_bytes(b"")
    (tmp_path / "run").mkdir()
    inst = vc.find_install(str(tmp_path), platform="win32")
    assert inst.kind == "windows" and inst.run_dir == os.path.join(str(tmp_path), "run")
    (tmp_path / "antennas").mkdir()
    inst = vc.find_install(str(tmp_path), platform="linux", which=lambda name: "/usr/bin/voacapl")
    assert inst.kind == "voacapl"


def test_predict_without_install_only_fails_when_needed():
    with pytest.raises(vc.NotInstalled) as e:
        vc.predict(_requests()[:1], vc.Settings(), None)
    assert "VOACAP was not found" in str(e.value)
    assert vc.predict([], vc.Settings(), None) == {}


# --- paths ---------------------------------------------------------------------------

def t(h, m=0, day=11):
    return dt.datetime(2026, 11, day, h, m, tzinfo=UTC)


def test_path_time():
    assert vc.path_time({"bin_start": t(22), "bin_end": t(23)}) == (t(22), "")
    when, note = vc.path_time({"bin_start": t(21), "bin_end": t(23, 59) + dt.timedelta(minutes=1)})
    assert when == t(21) and "3 h" in note
    assert vc.path_time({"first_utc": t(22, 31), "last_utc": t(22, 40)})[0] == t(23)    # exchange: start
    when, note = vc.path_time({"cell_a": "FN", "first_utc": t(18), "last_utc": t(23)})
    assert when == t(21) and "5 h" in note                                               # corridor: middle
    assert vc.path_time({"first_utc": t(23, 40)})[0] == t(0, day=12)
    assert vc.path_time({}) == (None, "no time")


def test_path_band():
    assert vc.path_band({"band": "20m"}) == ("20m", 14.074, "")
    band, f, note = vc.path_band({"bands": "40m,20m,15m"})
    assert (band, f) == ("15m", 21.074) and "several" in note
    assert vc.path_band({"freq_mhz": 7.0741}) == ("40m", 7.0741, "")
    assert vc.path_band({"band": None, "bands": ""})[1] is None


def test_observed_and_gaps():
    assert vc.observed({"a_to_b_db": -12.0, "b_to_a_db": -8.0, "snr_db": -10.5}) == (-12.0, -8.0, -10.5)
    assert vc.observed({"a_hears_b": -3, "b_hears_a": -9}) == (-9.0, -3.0, -6.0)    # b_hears_a is A->B
    assert vc.observed({"a_hears_b": None, "b_hears_a": -9}) == (-9.0, None, -9.0)
    ab = vc.Prediction(True, muf=13.6, mode="1F2", tangle=12.3, rel=0.88, snr_hz=38.0, sdbw=-120)
    ba = vc.Prediction(True, muf=13.6, mode="1F2", tangle=12.3, rel=0.92, snr_hz=40.0)
    out = vc.combine(ab, ba, (-12.0, -8.0, -10.5))
    assert out["voacap_snr_ab_db"] == 4.0 and out["voacap_snr_ba_db"] == 6.0
    assert out["voacap_snr_hz"] == 39.0 and out["voacap_snr_db"] == 5.0 and out["voacap_rel"] == 0.9
    assert (out["gap_a_to_b_db"], out["gap_b_to_a_db"], out["snr_gap_db"]) == (-16.0, -14.0, -15.5)
    bad = vc.combine(vc.Prediction(False, "x MHz is outside VOACAP's 2-30 MHz"), None, (None, None, None))
    assert bad["voacap_ok"] == 0 and "outside" in bad["voacap_note"]


def test_summary():
    rows = [{"voacap_ok": 1, "voacap_rel": 0.05, "obs_snr_db": -15, "snr_gap_db": -10},
            {"voacap_ok": 1, "voacap_rel": 0.6, "obs_snr_db": -5, "snr_gap_db": 2},
            {"voacap_ok": 1, "voacap_rel": 0.9, "obs_snr_db": None, "snr_gap_db": None},
            {"voacap_ok": 0}]
    s = vc.summary(rows)
    assert (s["paths"], s["predicted"], s["with_gap"], s["observed"], s["rel_below_10"]) == (4, 3, 2, 2, 1)
    assert s["median_gap"] == -4.0 and s["share_rel_below_10"] == 0.5


def test_ssn_for_falls_back_to_latest():
    smoothed = {(2026, 4): 101.5, (2026, 5): 99.0}
    assert vc.ssn_for(t(22), smoothed, {}, None) == (99.0, "SILSO smoothed 2026-05 (latest)")
    assert vc.ssn_for(t(22), {(2026, 11): 90.0}, {}, None) == (90.0, "SILSO smoothed")
    # a smoothed value from the last 12 months wins over a newer single month
    assert vc.ssn_for(t(22), smoothed, {(2026, 10): 45.7}, None) == (99.0, "SILSO smoothed 2026-05 (latest)")
    assert vc.ssn_for(t(22), {}, {(2026, 10): 45.7}, None) == (45.7, "SILSO monthly 2026-10 (latest)")
    assert vc.ssn_for(t(22), {}, {}, None) == (None, "")


# --- the real program, if installed ----------------------------------------------------

def _install():
    try:
        return vc.find_install(os.environ.get("MM_VOACAP_DIR", ""))
    except vc.NotInstalled:
        return None


@pytest.mark.skipif(_install() is None, reason="VOACAP is not installed")
def test_real_voacap_reproduces_fixture():
    inst = _install()
    before = set(os.listdir(inst.run_dir)) if inst.kind == "windows" else set()
    text = vc.run_deck(inst, vc.deck(BATCH3, vc.Settings(), "\\" if inst.kind == "windows" else "/"), 60)
    got = [(b.label, b.hour, b.muf, b.rows["SNR"], b.rows["REL"]) for b in vc.parse_output(text)]
    want = [(b.label, b.hour, b.muf, b.rows["SNR"], b.rows["REL"])
            for b in vc.parse_output(read("mm_batch3_voacapw.out"))]
    assert got == want
    if inst.kind == "windows":
        assert set(os.listdir(inst.run_dir)) == before      # deck, output and gain files cleaned up


def test_combine_flags_paths_above_the_muf():
    p = vc.Prediction(True, muf=12.0, mode="1F2", tangle=10.0, rel=0.0, snr_hz=-120.0, mufday=0.02)
    out = vc.combine(p, p, (-10.0, None, -10.0))
    assert out["snr_gap_db"] == pytest.approx(-10 - (-120 - vc.BW_FT8_DB), abs=0.1)
    assert "above VOACAP's MUF" in out["voacap_note"]
