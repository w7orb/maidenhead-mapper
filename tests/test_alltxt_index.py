import datetime as dt

from maidenhead_mapper.core import alltxt, alltxt_index as ai

UTC = dt.timezone.utc
LINES = [
    "261111_235945    14.074 Rx FT8    -12  0.3 2462 CQ K1ABC FN31",
    "261111_235945    14.074 Tx FT8      0  0.0 1500 K1ABC N0CALL CN85",
    "261112_000015    21.074 Rx FT8     -5  0.1 1200 N0CALL K1ABC -05",
    "garbage line",
    "261112_010015    21.074 Rx FT8     -7  0.1 1200 K1ABC N0CALL R-07",
]


def write(path, lines, mode="w"):
    with open(path, mode, encoding="utf-8", newline="\n") as f:
        f.write("".join(line + "\n" for line in lines))


def test_index_counts_and_offsets(tmp_path):
    p = tmp_path / "ALL.TXT"
    write(p, LINES)
    ix = ai.index([str(p)], str(tmp_path / "cache"))
    f = ix.files[0]
    assert (f.rx, f.tx, f.other) == (3, 1, 1)
    assert f.in_order
    assert ix.first_time == dt.datetime(2026, 11, 11, 23, 59, 45, tzinfo=UTC)
    assert ix.last_time == dt.datetime(2026, 11, 12, 1, 0, 15, tzinfo=UTC)
    assert ix.bands() == {"20m": 1, "15m": 2}
    h = ix.hourly()
    assert len(h) == 3 and h.heard == [1, 1, 1]
    start = dt.datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
    off = ix.offsets(start)[f.path]
    assert off == len("".join(line + "\n" for line in LINES[:2]).encode())
    rows = alltxt.read([f.path], start, None, ix.offsets(start), {f.path})
    assert [r.message for r in rows] == ["K1ABC N0CALL R-07"]
    rows = alltxt.read([f.path], None, dt.datetime(2026, 11, 12, 0, 30, tzinfo=UTC), None, {f.path})
    assert len(rows) == 3


def test_incremental_and_replaced(tmp_path):
    p = tmp_path / "ALL.TXT"
    cache = str(tmp_path / "cache")
    write(p, LINES[:3])
    assert ai.index([str(p)], cache).rx == 2
    write(p, LINES[3:], "a")                       # WSJT-X appended
    f = ai.index([str(p)], cache).files[0]
    assert (f.rx, f.other) == (3, 1)
    assert ai.load_cached(cache, str(p)).rx == 3   # cache updated
    write(p, LINES[2:3])                           # replaced by a different, shorter file
    assert ai.index([str(p)], cache).rx == 1


def test_out_of_order_disables_seeking(tmp_path):
    p = tmp_path / "ALL.TXT"
    write(p, [LINES[2], LINES[0]])
    f = ai.index([str(p)], None).files[0]
    assert not f.in_order
    assert f.offset_for(dt.datetime(2026, 11, 12, tzinfo=UTC)) == 0
