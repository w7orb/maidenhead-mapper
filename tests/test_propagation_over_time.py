"""ALL.TXT -> index -> read a window -> rebuild exchanges -> corridors per time bin,
as the Propagation over time panel / Corridors over time algorithm do it."""

import datetime as dt
import os

import sample_alltxt as sample

from maidenhead_mapper.core import alltxt, alltxt_index as ai, live
from maidenhead_mapper.core import corridors as cor
from maidenhead_mapper.core.bands import band_for_mhz

UTC = dt.timezone.utc
H21 = dt.datetime(2026, 11, 11, 21, tzinfo=UTC)
H22 = dt.datetime(2026, 11, 11, 22, tzinfo=UTC)


def pipeline(path, start=None, end=None, hours=1, size=cor.FIELD, band=None, min_n=1, cache=None):
    ix = ai.index([path], cache)
    rows = alltxt.read([path], start, end, ix.offsets(start), {f.path for f in ix.files if f.in_order})
    if band:
        rows = [r for r in rows if band_for_mhz(r.mhz) == band]
    model = live.replay(alltxt.to_log(rows, "N0CALL", "CN85"))
    records, unplaced = cor.records_from_model(model)
    return cor.over_time(records, size, hours, start, end, min_n), unplaced, ix


def test_index_of_sample(tmp_path):
    ix = ai.index([sample.write(tmp_path / "ALL.TXT")], str(tmp_path))
    assert ix.rx == 12 and ix.files[0].tx == 1
    assert ix.bands() == {"20m": 5, "15m": 7}
    h = ix.hourly()
    assert h.start == H21 and h.heard == [5, 7]
    assert "12 decodes" in ix.summary() and "15m, 20m" in ix.summary()


def test_hourly_frames(tmp_path):
    bins, unplaced, _ = pipeline(sample.write(tmp_path / "ALL.TXT"))
    assert unplaced == 1                                   # W9NOG never sent a grid
    assert [b.start for b in bins] == [H21, H22]
    assert all(b.end - b.start == dt.timedelta(hours=1) for b in bins)
    (c21,) = bins[0].summary.corridors
    assert (c21.cell_a, c21.cell_b, cor.seen(c21)) == ("EM", "FN", cor.BOTH)
    assert (c21.a_to_b_db, c21.b_to_a_db) == (-5.0, -8.0)  # K1ABC heard W4XYZ -05; W4XYZ heard K1ABC -08
    assert c21.bands == {"20m"}
    by_pair = {(c.cell_a, c.cell_b): c for c in bins[1].summary.corridors}
    assert set(by_pair) == {("FN", "PM"), ("EM", "QF")}
    fp = by_pair[("FN", "PM")]
    assert (fp.a_to_b_db, fp.b_to_a_db) == (-12.0, -15.0)  # JA1AAA heard K1ABC -12; K1ABC heard JA1AAA -15
    assert cor.seen(by_pair[("EM", "QF")]) == cor.NO_REPORTS
    assert set(bins[1].cells) == {"FN", "PM", "EM", "QF"}
    assert bins[1].cells["FN"].partners == {"PM"}


def test_window_and_day_bins(tmp_path):
    p = sample.write(tmp_path / "ALL.TXT")
    bins, _, _ = pipeline(p, start=H22, end=H22 + dt.timedelta(hours=1))
    assert [b.start for b in bins] == [H22]
    days, _, _ = pipeline(p, hours=24)
    assert len(days) == 1 and days[0].start == dt.datetime(2026, 11, 11, tzinfo=UTC)
    assert days[0].records == 3 and len(days[0].summary.corridors) == 3
    three, _, _ = pipeline(p, hours=3)
    assert [b.start.hour for b in three] == [21]


def test_band_filter_and_min_exchanges(tmp_path):
    p = sample.write(tmp_path / "ALL.TXT")
    bins, _, _ = pipeline(p, band="20m")
    assert [b.start for b in bins] == [H21]
    bins, _, _ = pipeline(p, min_n=2)
    assert all(not b.summary.corridors for b in bins)       # every corridor has one exchange
    assert sum(len(b.cells) for b in bins) == 6             # active cells still listed


def test_block_cells(tmp_path):
    bins, _, _ = pipeline(sample.write(tmp_path / "ALL.TXT"), size=cor.BLOCK)
    assert set(bins[0].cells) == {"EM68", "FN20"}           # EM78 -> EM68 block, FN31 -> FN20 block


def test_multiple_files_and_offsets(tmp_path):
    a = sample.write(tmp_path / "a.txt")
    later = [ln.replace("261111_", "261113_") for ln in sample.lines()]
    b = tmp_path / "b.txt"
    b.write_text("\n".join(later) + "\n", encoding="utf-8")
    ix = ai.index([a, str(b)], str(tmp_path / "cache"))
    assert ix.rx == 24
    assert len(ix.hourly()) == 2 * 24 + 2                  # 21:00 on the 11th .. 22:00 on the 13th
    start = dt.datetime(2026, 11, 13, tzinfo=UTC)
    offs = ix.offsets(start)
    assert offs[os.path.abspath(a)] == os.path.getsize(a)       # all before start: skipped
    assert offs[os.path.abspath(str(b))] == 0
    rows = alltxt.read([os.path.abspath(a), os.path.abspath(str(b))], start, None, offs,
                       {f.path for f in ix.files})
    assert len(rows) == 13 and all(r.time >= start for r in rows)


def test_index_cancel(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("\n".join(sample.lines() * 40000) + "\n", encoding="utf-8")   # > 200,000 lines
    assert ai.index([str(p)], None, cancelled=lambda: True) is None
    seen = []
    assert ai.index([str(p)], None, progress=seen.append).rx == 12 * 40000
    assert seen and 0 < seen[-1] <= 100
