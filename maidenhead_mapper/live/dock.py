"""Dock panel and controller for the live WSJT-X map."""

import datetime as _dt
import json
import uuid
import weakref

from qgis.core import QgsSettings
from qgis.gui import QgsFileWidget
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.bands import band_for_hz
from ..core.live import LiveModel
from ..core import exchange as x
from ..algorithms.import_alltxt import LOG_PROPERTY
from .exchange_layer import ExchangeLayer
from .layer import LiveLayer
from .listener import WsjtxListener
from .session import SessionStore

S = "maidenhead_mapper/live/"
DEFAULTS = {"mode": "multicast", "address": "239.255.0.1", "port": 2237, "fade": 30}
BATCH_MS = 2000      # apply queued station changes this often
REPAINT_MS = 30000   # repaint so fading progresses even when quiet


# Running controllers by session group id, so the Activity timeline can find
# the live log behind a layer.
CONTROLLERS = weakref.WeakValueDictionary()


class LiveController:
    """Glue: listener -> model -> layers (+ optional GeoPackage), with timers. No widgets."""

    def __init__(self):
        self.model = LiveModel(record=True)
        self.group = uuid.uuid4().hex
        CONTROLLERS[self.group] = self
        self.listener = WsjtxListener()
        self.listener.message.connect(self._on_message)
        self.layer = LiveLayer(self.model)
        self.exchange_layer = ExchangeLayer(self.model)
        self.store = None
        self.save_folder = None
        self.paused = False          # True while the timeline shows a past window
        self.save_error = None
        self._set_properties()
        self.batch = QTimer()
        self.batch.timeout.connect(self.flush)
        self.fader = QTimer()
        self.fader.timeout.connect(self.layer.repaint)
        self.fader.timeout.connect(self.exchange_layer.repaint)
        self.decode_times = []
        self.on_update = None  # callback for the dock's status

    def _new_group(self):
        CONTROLLERS.pop(self.group, None)
        self.group = uuid.uuid4().hex
        CONTROLLERS[self.group] = self

    def _layers(self):
        return (self.layer, self.exchange_layer)

    def _set_properties(self):
        base = {"kind": "session" if self.store else "live", "group": self.group,
                "gpkg": self.store.path if self.store else None}
        for lyr, role in ((self.layer, "stations"), (self.exchange_layer, "exchanges")):
            lyr.properties = {LOG_PROPERTY: json.dumps(dict(base, role=role))}

    def set_fade(self, minutes):
        for lyr in self._layers():
            lyr.set_fade(minutes)

    def start(self, address, port, multicast, fade_minutes, save_folder=None):
        if save_folder != self.save_folder:
            self.save_folder, self.store = save_folder, None
        if save_folder and self.store is None:
            # New file; the current layers stay as they are and new ones are
            # created in the GeoPackage with everything heard so far.
            self.store = SessionStore(save_folder)
            for lyr in self._layers():
                lyr.make_layer = self.store.make_layer
                lyr.detach()
        elif not save_folder:
            from .synced import memory_layer
            for lyr in self._layers():
                if lyr.make_layer is not memory_layer:
                    lyr.make_layer = memory_layer
                    lyr.detach()
        if any(lyr.layer is None for lyr in self._layers()):
            self._new_group()  # detached layers keep the old group; new ones get their own
        self._set_properties()
        self.set_fade(fade_minutes)
        self.listener.start(address, port, multicast)
        self.batch.start(BATCH_MS)
        self.fader.start(REPAINT_MS)

    def stop(self):
        self.listener.stop()
        self.batch.stop()
        self.fader.stop()
        self.flush()

    def _on_message(self, msg):
        now = _dt.datetime.now(_dt.timezone.utc)
        before = self.model.decodes
        self.model.handle(msg, now)
        if self.model.decodes != before:
            self.decode_times.append(now)

    def flush(self):
        if not self.paused:
            keys = self.model.take_dirty()
            needs_layer = self.layer.layer is None and self.model.stations
            if keys or needs_layer:
                self.layer.sync(keys)
            ids = self.model.exchanges.take_dirty()
            if ids or self.exchange_layer.unplaced or (self.exchange_layer.layer is None
                                                       and self.model.exchanges.exchanges):
                self.exchange_layer.sync(ids)
        if self.store is not None:
            try:
                self.store.append_log(self.model.log)
                self.save_error = None
            except Exception as exc:  # disk full, file locked...: keep the live map going
                self.save_error = str(exc)
        if self.on_update:
            self.on_update()

    # -- Activity timeline hooks ------------------------------------------
    def show_window(self, model):
        """Show a past window's model; live updates are held until resume()."""
        self.paused = True
        for lyr in self._layers():
            lyr.rebuild(model)

    def resume(self):
        if not self.paused:
            return
        self.paused = False
        self.model.take_dirty()
        self.model.exchanges.take_dirty()
        for lyr in self._layers():
            lyr.rebuild(self.model)
        if self.on_update:
            self.on_update()

    def decodes_per_minute(self):
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1)
        self.decode_times = [t for t in self.decode_times if t >= cutoff]
        return len(self.decode_times)


class LiveDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("WSJT-X live map", parent)
        self.setObjectName("MaidenheadMapperLiveDock")
        self.ctl = LiveController()
        self.ctl.on_update = self.refresh_status
        s = QgsSettings()

        w = QWidget()
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Multicast (shares with JTAlert/GridTracker)", "multicast")
        self.mode.addItem("Unicast (WSJT-X → QGIS only)", "unicast")
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.value(S + "mode", DEFAULTS["mode"]))))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.address = QLineEdit(s.value(S + "address", DEFAULTS["address"]))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(s.value(S + "port", DEFAULTS["port"])))
        self.fade = QSpinBox()
        self.fade.setRange(1, 24 * 60)
        self.fade.setSuffix(" min")
        self.fade.setValue(int(s.value(S + "fade", DEFAULTS["fade"])))
        self.fade.valueChanged.connect(self.ctl.set_fade)
        form.addRow("Mode", self.mode)
        form.addRow("Address", self.address)
        form.addRow("Port", self.port)
        form.addRow("Fade over", self.fade)
        self.save = QCheckBox("Save session to a GeoPackage in")
        self.save.setChecked(s.value(S + "save", False, type=bool))
        self.save.setToolTip("Each Start (after changing this) begins a new file, "
                             "wsjtx_session_YYYYMMDD_HHMMSS.gpkg, written every 2 s.")
        self.folder = QgsFileWidget()
        self.folder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.folder.setFilePath(s.value(S + "folder", "", type=str))
        form.addRow(self.save)
        form.addRow(self.folder)

        self.button = QPushButton("Start")
        self.button.setCheckable(True)
        self.button.toggled.connect(self._toggle)
        self.status = QLabel("Stopped")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop)

        lay = QVBoxLayout()
        lay.addLayout(form)
        lay.addWidget(self.button)
        lay.addWidget(self.status, 1)
        w.setLayout(lay)
        self.setWidget(w)

    def _mode_changed(self):
        # Swap the stock address when switching modes (keep custom ones)
        multicast = self.mode.currentData() == "multicast"
        if self.address.text() in ("239.255.0.1", "127.0.0.1"):
            self.address.setText("239.255.0.1" if multicast else "127.0.0.1")

    def _toggle(self, on):
        if on:
            multicast = self.mode.currentData() == "multicast"
            try:
                folder = self.folder.filePath().strip() if self.save.isChecked() else None
                if self.save.isChecked() and not folder:
                    raise ValueError("Choose a folder to save the session in, or untick saving.")
                self.ctl.start(self.address.text().strip(), self.port.value(), multicast, self.fade.value(),
                               save_folder=folder)
            except (OSError, ValueError) as exc:
                self.status.setText(f"<b>Not started:</b> {exc}")
                self.button.blockSignals(True)
                self.button.setChecked(False)
                self.button.blockSignals(False)
                return
            s = QgsSettings()
            s.setValue(S + "mode", self.mode.currentData())
            s.setValue(S + "address", self.address.text().strip())
            s.setValue(S + "port", self.port.value())
            s.setValue(S + "fade", self.fade.value())
            s.setValue(S + "save", self.save.isChecked())
            s.setValue(S + "folder", self.folder.filePath().strip())
            self.button.setText("Stop")
        else:
            self.ctl.stop()
            self.button.setText("Start")
        for wdg in (self.mode, self.address, self.port, self.save, self.folder):
            wdg.setEnabled(not on)
        self.refresh_status()

    def refresh_status(self):
        c, m = self.ctl, self.ctl.model
        lines = []
        if c.listener.is_listening():
            where = f"{self.address.text()}:{self.port.value()}"
            if c.listener.joined:
                where += f" (joined on {len(c.listener.joined)} interface(s))"
            lines.append(f"<b>Listening</b> on {where}")
        else:
            lines.append("<b>Stopped</b>")
        if not m.clients:
            lines.append("No WSJT-X heard yet. In WSJT-X: Settings → Reporting → UDP Server "
                         f"{self.address.text()} port {self.port.value()}.")
        for cl in m.clients.values():
            band = band_for_hz(cl.dial_hz) or "?"
            dial = f"{cl.dial_hz / 1e6:.3f} MHz" if cl.dial_hz else "?"
            lines.append(f"{cl.id} {cl.version}: {dial} {cl.mode} ({band}), {cl.de_call} {cl.de_grid}")
        lines.append(f"Decodes: {m.decodes} ({c.decodes_per_minute()}/min)")
        lines.append(f"Stations on map: {m.placed_count()}; waiting for a grid: {m.waiting_count()}")
        ex = m.exchanges
        if ex.exchanges:
            n = ex.counts()
            mine = sum(1 for e in ex.exchanges.values() if e.involves_me)
            lines.append(f"Exchanges: {len(ex.exchanges)} ({c.exchange_layer.placed_count()} on map): "
                         f"{n[x.CONFIRMED]} confirmed, {n[x.BOTH_REPORTS]} both reports, "
                         f"{n[x.ONE_REPORT]} one report, {n[x.STARTED]} started; {mine} mine")
        if m.last_packet:
            lines.append(f"Last packet: {m.last_packet.strftime('%H:%M:%S')} UTC")
        if c.store is not None:
            lines.append(f"Saving to {c.store.path} ({c.store.written:,} log entries)")
        if c.save_error:
            lines.append(f"<b>Saving failed:</b> {c.save_error}")
        if c.paused:
            lines.append("<b>Map shows a past time window</b> (Activity timeline); still listening. "
                         "Choose <i>All hours</i> there to return to live.")
        if c.listener.bad_datagrams:
            lines.append(f"Ignored non-WSJT-X datagrams: {c.listener.bad_datagrams}")
        self.status.setText("<br>".join(lines))

    def shutdown(self):
        self.ctl.stop()
