"""QGIS must not crash on exit while the live map is running and saving a session.

Run by tests/qgis_smoke.py in a separate process (the crash, if any, happens as
the process ends):  python-qgis.bat tests/qgis_live_exit.py <mode>

  quit   : start, feed data, then quit the event loop like closing QGIS
           (aboutToQuit), clear the project and exit
  unload : clear the project, then the plugin's unload (dock.shutdown()), then exit

Also checks that Stop then Start keeps the session's log (it is reopened, not
overwritten). Prints OK and exits 0 on success.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from qgis.core import QgsApplication, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QTimer  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "quit"
app = QgsApplication([], True)
app.initQgis()

from maidenhead_mapper.core import alltxt  # noqa: E402
from maidenhead_mapper.live.dock import LiveDock  # noqa: E402
import sample_alltxt  # noqa: E402

tmp = tempfile.mkdtemp()
log = alltxt.to_log(alltxt.read([sample_alltxt.write(os.path.join(tmp, "ALL.TXT"))]), "N0CALL", "CN85")
half = len(log) // 2

dock = LiveDock(None)
dock.save.setChecked(True)
dock.folder.setFilePath(os.path.join(tmp, "sessions"))
dock.mode.setCurrentIndex(dock.mode.findData("unicast"))
dock.address.setText("127.0.0.1")
import socket  # noqa: E402

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:   # a free port, never WSJT-X's 2237
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
dock.port.setValue(free_port)
ctl = dock.ctl


def feed(entries):
    for t, msg in entries:
        ctl.model.handle(msg, t)
    ctl.flush()


dock.button.setChecked(True)               # Start
assert ctl.listener.is_listening() and ctl.store is not None
feed(log[:half])
dock.button.setChecked(False)              # Stop: closes the session's log table
dock.button.setChecked(True)               # Start again, same file
feed(log[half:])
gpkg = ctl.store.path
assert ctl.store.written == len(ctl.model.log), (ctl.store.written, len(ctl.model.log))

app.processEvents()
if mode == "quit":
    QTimer.singleShot(0, app.quit)
    app.exec()                             # emits aboutToQuit, as closing QGIS does
    QgsProject.instance().clear()
elif mode == "unload":
    QgsProject.instance().clear()
    dock.shutdown()
    dock.deleteLater()
else:
    raise SystemExit(f"unknown mode {mode}")
app.processEvents()

rows = QgsVectorLayer(f"{gpkg}|layername=log", "log", "ogr").featureCount()
assert rows == len(ctl.model.log), f"session log has {rows} rows, expected {len(ctl.model.log)}"
app.exitQgis()
print("OK", mode, rows)
