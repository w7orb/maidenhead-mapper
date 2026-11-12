from datetime import datetime, timedelta, timezone

from maidenhead_mapper.core import corridors as cor

T0 = datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc)


def rec(a, ga, b, gb, ab=None, ba=None, band="20m", minutes=0, **kw):
    return cor.Record(a, ga, b, gb, band, ab, ba, T0 + timedelta(minutes=minutes), **kw)


def test_cells():
    assert cor.cell("EM78ab", cor.FIELD) == "EM"
    assert cor.cell("em78", cor.SQUARE) == "EM78"
    assert cor.cell("EM78", cor.BLOCK) == "EM68"
    assert cor.cell("FN31", cor.BLOCK) == "FN20"
    assert cor.cell("EM", cor.SQUARE) is None
    assert cor.cell("RR73", cor.SQUARE) == "RR73" and cor.cell("XX", cor.FIELD) is None
    assert cor.cell_center("EM68", cor.BLOCK) == (-86.0, 39.0)   # corner shared by EM68/69/78/79


def test_direction_is_kept_when_order_is_swapped():
    # A in FN31 hears B (EM78) at -10, B hears A at +2: signal FN->EM = +2, EM->FN = -10
    r1 = rec("W1AA", "FN31", "K4BB", "EM78", ab=-10, ba=2)
    # stored the other way round: EM station first
    r2 = rec("K4CC", "EM78", "W1DD", "FN31", ab=4, ba=-8, minutes=70)
    s = cor.summarize([r1, r2], cor.SQUARE)
    (c,) = s.corridors
    assert (c.cell_a, c.cell_b) == ("EM78", "FN31")
    assert sorted(c.a_to_b) == [-10, -8]       # EM -> FN
    assert sorted(c.b_to_a) == [2, 4]          # FN -> EM
    assert c.diffs == [-12, -12]
    assert c.diff_db == -12.0 and c.snr_db == -3.0
    assert len(c.calls_a) == 2 and len(c.calls_b) == 2
    assert c.hours_active == 2
    assert c.label() == "2×  -9/+3"


def test_filters_and_counts():
    rs = [rec("A1A", "FN31", "B1B", "FN31", ab=1, ba=2),                 # same cell
          rec("A1A", "FN31", "B1B", "", ab=1),                            # no grid
          rec("A1A", "FN31", "B1B", "EM78", ab=1, minutes=600),           # outside
          rec("A1A", "FN31", "B1B", "EM78", ab=1, band="40m"),
          rec("A1A", "FN31", "B1B", "EM78", ab=1, band="20m")]
    s = cor.summarize(rs, cor.SQUARE, end=T0 + timedelta(hours=1))
    assert (s.same_cell, s.no_grid, s.outside) == (1, 1, 1)
    assert len(s.corridors) == 2
    s = cor.summarize(rs, cor.SQUARE, per_band=False, end=T0 + timedelta(hours=1))
    assert len(s.corridors) == 1 and s.corridors[0].exchanges == 2
    assert cor.summarize(rs, cor.SQUARE, per_band=False, min_exchanges=4).corridors == []
    s = cor.summarize(rs, cor.FIELD)
    assert s.same_cell == 1 and s.corridors[0].cell_a == "EM"


def test_mode_counts():
    rs = [rec("A", "FN31", "B", "EM78", mode_guess="Es"),
          rec("A", "FN31", "B", "EM78", f2_status="above F2 MUF"),
          rec("A", "FN31", "B", "EM78", f2_status="F2", mode_guess="F2")]
    (c,) = cor.summarize(rs).corridors
    assert dict(c.status) == {"Es": 1, "above MUF": 1, "F2": 1}


def test_station_facts():
    rs = [rec("W1AA", "FN31", "K4BB", "EM78", ab=-10, ba=2),
          rec("W1AA", "FN31", "K4BB", "EM78", ab=-12, ba=0),
          rec("W1AA", "FN31", "JA1CC", "PM95", ab=-20, ba=-15, band="15m")]
    f = cor.station_facts(rs, per_band=False)
    w = f[("", "W1AA")]
    assert w.partners == 2 and w.farthest == "JA1CC"
    assert w.bands == "20m,15m"
    assert w.heard_db == 0.0          # others heard W1AA: +2, 0, -15
    assert w.hears_db == -12.0        # W1AA heard: -10, -12, -20
    assert 1000 < w.median_km < w.max_km
    k = cor.station_facts(rs)[("20m", "K4BB")]
    assert k.partners == 1 and k.heard_db == -11.0 and k.hears_db == 1.0


def test_bin_floor():
    t = datetime(2026, 11, 11, 21, 47, 15, tzinfo=timezone.utc)
    assert cor.bin_floor(t, 1) == datetime(2026, 11, 11, 21, tzinfo=timezone.utc)
    assert cor.bin_floor(t, 3) == datetime(2026, 11, 11, 21, tzinfo=timezone.utc)
    assert cor.bin_floor(t, 6) == datetime(2026, 11, 11, 18, tzinfo=timezone.utc)
    assert cor.bin_floor(t, 24) == datetime(2026, 11, 11, tzinfo=timezone.utc)


def test_over_time():
    rs = [rec("W1AA", "FN31", "K4BB", "EM78", ab=-10, ba=2),                   # 21:00
          rec("W1AA", "FN31", "K4CC", "EM79", ab=-3, minutes=20),              # 21:20, A->B? a_hears_b only
          rec("W1AA", "FN31", "JA1DD", "PM95", ba=-15, minutes=65),            # 22:05
          rec("W1AA", "FN31", "W1EE", "FN31", ab=1, ba=1, minutes=70),         # same cell
          rec("W1AA", "FN31", "K4BB", "EM78", minutes=-120)]                   # before start
    bins = cor.over_time(rs, cor.FIELD, 1, start=T0)
    assert [b.start.hour for b in bins] == [21, 22]
    b21, b22 = bins
    (c,) = b21.summary.corridors
    assert (c.cell_a, c.cell_b, c.exchanges) == ("EM", "FN", 2)
    assert cor.seen(c) == cor.BOTH            # FN->EM reported (-10, -3), EM->FN reported (+2)
    assert b21.cells["FN"].exchanges == 2 and b21.cells["FN"].partners == {"EM"}
    assert len(b21.cells["EM"].calls) == 2
    assert b22.summary.same_cell == 1 and b22.cells["FN"].exchanges == 3     # same-cell counts twice
    (c,) = b22.summary.corridors
    assert cor.seen(c) == cor.A_TO_B          # FN->PM: JA1DD reported W1AA
    assert b22.end - b22.start == timedelta(hours=1)
    days = cor.over_time(rs, cor.FIELD, 24)
    assert len(days) == 1 and days[0].records == 5
