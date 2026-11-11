import datetime as dt

from maidenhead_mapper.core import timeline

T0 = dt.datetime(2026, 11, 10, 18, 0, tzinfo=dt.timezone.utc)


def r(minutes, direction="heard"):
    return {"time": T0 + dt.timedelta(minutes=minutes), "direction": direction}


def test_hourly_bins_include_empty_hours():
    h = timeline.hourly([r(5), r(59, "heard_me"), r(60), r(185), {"time": None, "direction": "heard"}])
    assert h.start == T0 and len(h) == 4
    assert h.heard == [1, 1, 0, 1] and h.heard_me == [1, 0, 0, 0]
    assert h.total(0) == 2 and h.reports() == 4 and h.reports(1, 2) == 1


def test_span_and_index():
    h = timeline.hourly([r(0), r(150)])
    start, end = h.span(1, 2)
    assert start == T0 + dt.timedelta(hours=1)
    assert end < T0 + dt.timedelta(hours=3) and end > T0 + dt.timedelta(hours=2, minutes=59, seconds=59)
    assert h.index(T0 + dt.timedelta(minutes=150)) == 2
    assert h.index(T0 - dt.timedelta(days=1)) == 0 and h.index(T0 + dt.timedelta(days=1)) == 2


def test_empty():
    h = timeline.hourly([])
    assert len(h) == 0 and h.reports() == 0 and h.index(T0) == 0
