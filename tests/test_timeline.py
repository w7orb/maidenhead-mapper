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


def test_summarize_window():
    from maidenhead_mapper.core.stations import StationSummary

    def s(call, direction, bands, dist, snr, reports=1):
        return StationSummary(call, "FN42", direction, reports, snr, snr, snr, bands, "FT8",
                              T0, T0, dist, 90.0, "psk", 0.0, 0.0)
    w = timeline.summarize_window([s("A", "heard", "20m,40m", 100.0, -5, 3), s("B", "heard_me", "20m", 9000.0, -20),
                                   s("C", "heard", "", None, None)])
    assert (w.heard, w.heard_me, w.reports) == (2, 1, 5)
    assert w.bands == [("40m", 1), ("20m", 2)]
    assert w.farthest.call == "B" and w.strongest.call == "A"
    assert timeline.summarize_window([]).farthest is None


def test_window_label():
    end = T0 + dt.timedelta(hours=12)
    assert timeline.window_label(T0, end) == "11-10 18:00 – 11-11 06:00 UTC"
    assert timeline.window_label(T0, T0 + dt.timedelta(hours=2)) == "11-10 18:00–20:00 UTC"


def test_hourly_from_log():
    from maidenhead_mapper.core import wsjtx_udp as w
    log = [(T0, w.Status("c", dial_hz=14074000, mode="FT8", de_call="W7ORB/P")),
           (T0 + dt.timedelta(minutes=1), w.Decode("c", message="CQ K1ABC FN42")),
           (T0 + dt.timedelta(minutes=2), w.Decode("c", message="W7ORB K1ABC -10")),
           (T0 + dt.timedelta(hours=2), w.Decode("c", message="K1ABC W9XYZ R-05"))]
    h = timeline.hourly_from_log(log)
    assert h.start == T0 and h.heard == [1, 0, 1] and h.heard_me == [1, 0, 0]
    assert timeline.hourly_from_log(log, my_call="N0CALL").heard_me == [0, 0, 0]
