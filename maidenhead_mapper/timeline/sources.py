"""What the Activity timeline can drive.

A Source groups the layer(s) one dataset is drawn on and knows how to
(1) load its raw data (in a background task where that is slow),
(2) count it per hour, and (3) redraw its layers for a time window.

    PskSource      a PSK Reporter station layer (Fetch / Import)
    LogSource      an ALL.TXT import or a saved live session: both layers,
                   rebuilt by replaying the log for the window
    LiveSource     the running live map: its in-memory log; live updates
                   pause while a past window is shown
"""

import json
import os
import warnings
from types import SimpleNamespace

from qgis.core import QgsFeature, QgsProject, QgsVectorDataProvider

from ..algorithms.import_alltxt import LOG_PROPERTY, station_features
from ..algorithms.import_psk import LAYER_PROPERTY, station_feature, station_fields
from ..core import alltxt, live, psk, stations, timeline
from ..core import exchange as x
from ..live.exchange_layer import exchange_features


class SourceError(Exception):
    pass


def rewrite_layer(layer, features):
    """Replace a layer's features (matching fields by name) in an edit session,
    so attribute tables, statistics and labels refresh."""
    if layer.isEditable():
        raise SourceError(f"Stop editing '{layer.name()}' first; the timeline replaces its features.")
    need = QgsVectorDataProvider.AddFeatures | QgsVectorDataProvider.DeleteFeatures
    if (int(layer.dataProvider().capabilities()) & int(need)) != int(need):
        raise SourceError(f"'{layer.name()}' can't be rewritten; use a temporary layer or GeoPackage.")
    dst = layer.fields()
    out = []
    for src in features:
        names = [n for n in src.fields().names() if dst.indexOf(n) >= 0 and n != "fid"]
        f = QgsFeature(dst)
        f.setGeometry(src.geometry())
        for n in names:
            f.setAttribute(n, src[n])
        out.append(f)
    layer.startEditing()
    layer.deleteFeatures(list(layer.allFeatureIds()))
    layer.addFeatures(out)
    if not layer.commitChanges():
        errors = "; ".join(layer.commitErrors())
        layer.rollBack()
        raise SourceError(f"Could not update '{layer.name()}': {errors}")
    layer.updateExtents()
    layer.triggerRepaint()


def _mtimes(paths):
    try:
        return tuple((p, os.path.getmtime(p)) for p in paths)
    except OSError:
        return None


class Source:
    key = ""
    name = ""
    persistent = True          # stores the chosen window on the layers

    def layers(self):
        return []

    def signature(self):
        """Changes when the underlying data changes (None = missing)."""
        return ()

    def load(self, task=None):
        """Raw data; may run in a background thread (no QGIS layer access)."""
        raise NotImplementedError

    def set_data(self, data):
        self.data = data
        self.hourly = self.count(data)

    def count(self, data):
        raise NotImplementedError

    def stored_window(self):
        return None, None

    def apply(self, start, end):
        """Redraw the layers for [start, end] (None = open); returns HTML lines."""
        raise NotImplementedError

    def reset(self):
        """Back to everything (default: apply the whole range)."""
        return self.apply(None, None)


class PskSource(Source):
    def __init__(self, layer):
        self.layer = layer
        self.key = "psk:" + layer.id()
        self.opts = json.loads(layer.customProperty(LAYER_PROPERTY))
        self.opts.setdefault("name", layer.name().split(" · ")[0])
        self.name = self.opts["name"]

    def layers(self):
        return [self.layer]

    def signature(self):
        folder = self.opts["folder"]
        try:
            return _mtimes([os.path.join(folder, n) for n in psk.iter_psk_files(folder)])
        except OSError:
            return None

    def load(self, task=None):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reports = psk.load_reports(self.opts["folder"], progress=(
                (lambda i, n: task.setProgress(100.0 * i / max(n, 1))) if task else None))
        return psk.add_remote_station(reports, self.opts["mycall"])

    def count(self, reports):
        kept, _ = stations.select(reports, self.opts["directions"], self.opts["bands"])
        return timeline.hourly(kept)

    def stored_window(self):
        return psk.report_time(self.opts.get("start")), psk.report_time(self.opts.get("end"))

    def apply(self, start, end):
        o = self.opts
        summary, _ = stations.summarize(self.data, precision=o["precision"], my_grid=o["my_grid"],
                                        directions=o["directions"], bands=o["bands"], start=start, end=end)
        fields = station_fields()
        rewrite_layer(self.layer, [station_feature(fields, s) for s in summary])
        o["start"] = None if start is None else int(start.timestamp())
        o["end"] = None if end is None else int(end.timestamp())
        self.layer.setCustomProperty(LAYER_PROPERTY, json.dumps(o))
        w = timeline.summarize_window(summary)
        lines = [f"<b>{w.heard + w.heard_me:,}</b> station(s): "
                 f"<span style='color:#1f77b4'>{w.heard:,} heard</span>, "
                 f"<span style='color:#ff7f0e'>{w.heard_me:,} heard me</span>"]
        return lines + _common_lines(w) + _skip_line(summary)


def _skip_line(rows):
    """'Skip edge: 10m ~2,450 km, ...' from station rows (distance_km, bands)."""
    from ..core import skip
    pairs = [(b, r.distance_km) for r in rows if r.distance_km is not None
             for b in str(r.bands or "").split(",") if b]
    line = skip.summary_line(pairs)
    return [f"Skip edge: {line}"] if line else []


def _common_lines(w):
    lines = []
    if w.bands:
        lines.append("Bands: " + ", ".join(f"{b} {n:,}" for b, n in w.bands))
    if w.farthest is not None:
        f = w.farthest
        lines.append(f"Farthest: {f.call} {f.grid}, {f.distance_km:,.0f} km"
                     + (f", likely {f.hops} hop(s)" if getattr(f, "hops", None) else ""))
    if w.strongest is not None:
        lines.append(f"Strongest: {w.strongest.call} {w.strongest.snr_best:+d} dB")
    return lines


def _model_lines(model):
    """Window summary lines for a replayed LiveModel."""
    rows = []
    for st in model.stations.values():
        if not st.grid:
            continue
        dist, _ = model.distance_bearing(st.grid)
        from ..core import hops as _hops
        rows.append(SimpleNamespace(call=st.call, grid=st.grid, direction="heard", reports=st.reports,
                                    bands=st.bands_str(), distance_km=dist, snr_best=st.snr_best,
                                    hops=_hops.likely_hops(dist)[0]))
    w = timeline.summarize_window(rows)
    n = model.exchanges.counts()
    mine = sum(1 for e in model.exchanges.exchanges.values() if e.involves_me)
    lines = [f"<b>{len(rows):,}</b> station(s) placed, {model.decodes:,} decode(s)",
             f"Exchanges: {len(model.exchanges.exchanges):,}: {n[x.CONFIRMED]} confirmed, "
             f"{n[x.BOTH_REPORTS]} both reports, {n[x.ONE_REPORT]} one report, {n[x.STARTED]} started"
             + (f"; {mine} mine" if mine else "")]
    return lines + _common_lines(w) + _skip_line(rows)


class LogSource(Source):
    """ALL.TXT import or saved session: a group of tagged layers."""

    def __init__(self, layers):
        self._layers = layers
        self.props = json.loads(layers[0].customProperty(LOG_PROPERTY))
        self.key = "log:" + self.props["group"]
        self.name = (layers[0].customProperty("maidenhead_mapper/base_name")
                     or layers[0].name().split(" · ")[0]) + (" + exchanges" if len(layers) > 1 else "")

    def layers(self):
        return self._layers

    def _paths(self):
        return self.props["files"] if self.props["kind"] == "alltxt" else [self.props["gpkg"]]

    def signature(self):
        return _mtimes(self._paths())

    def load(self, task=None):
        p = self.props
        if p["kind"] == "alltxt":
            return alltxt.to_log(alltxt.read(p["files"]), p.get("mycall", ""), p.get("mygrid", ""))
        from ..live.session import read_log
        return read_log(p["gpkg"])

    def count(self, log):
        return timeline.hourly_from_log(log, self.props.get("mycall", ""))

    def stored_window(self):
        return psk.report_time(self.props.get("start")), psk.report_time(self.props.get("end"))

    def apply(self, start, end):
        p = self.props
        model = live.replay(self.data, p.get("mycall", ""), p.get("mygrid", ""), start, end)
        for lyr in self._layers:
            role = json.loads(lyr.customProperty(LOG_PROPERTY)).get("role")
            feats = station_features(model) if role == "stations" else exchange_features(model)[0]
            rewrite_layer(lyr, feats)
            props = json.loads(lyr.customProperty(LOG_PROPERTY))
            props["start"] = None if start is None else int(start.timestamp())
            props["end"] = None if end is None else int(end.timestamp())
            lyr.setCustomProperty(LOG_PROPERTY, json.dumps(props))
        return _model_lines(model)


class LiveSource(Source):
    """The running live map (its layers may be memory or a saved session)."""

    persistent = False

    def __init__(self, controller, layers):
        self.ctl = controller
        self._layers = layers
        self.key = "log:" + controller.group
        self.name = "WSJT-X live map (running)"

    def layers(self):
        return [lyr for lyr in (self.ctl.layer.layer, self.ctl.exchange_layer.layer) if lyr is not None]

    def signature(self):
        return len(self.ctl.model.log)   # new messages -> recount

    def load(self, task=None):
        return list(self.ctl.model.log)

    def count(self, log):
        return timeline.hourly_from_log(log, self.ctl.model.my_call)

    def apply(self, start, end):
        if start is None and end is None:
            return self.reset()
        m = self.ctl.model
        model = live.replay(self.data, m.my_call, m.my_grid, start, end)
        self.ctl.show_window(model)
        return _model_lines(model) + ["<i>Live updates held while showing this window.</i>"]

    def reset(self):
        self.ctl.resume()
        return _model_lines(self.ctl.model) + ["<i>Live.</i>"]


def discover():
    """Every Source in the current project, in layer order."""
    from ..live.dock import CONTROLLERS

    out, groups = [], {}
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.customProperty(LAYER_PROPERTY):
            try:
                out.append(PskSource(lyr))
            except (TypeError, ValueError, KeyError):
                pass
            continue
        raw = lyr.customProperty(LOG_PROPERTY)
        if not raw:
            continue
        try:
            props = json.loads(raw)
            group = props["group"]
        except (TypeError, ValueError, KeyError):
            continue
        groups.setdefault(group, (props, []))[1].append(lyr)
    for group, (props, layers) in groups.items():
        layers.sort(key=lambda lyr: json.loads(lyr.customProperty(LOG_PROPERTY)).get("role") != "stations")
        ctl = CONTROLLERS.get(group)
        if ctl is not None:
            out.append(LiveSource(ctl, layers))
        elif props.get("kind") in ("alltxt", "session"):
            out.append(LogSource(layers))
    return out
