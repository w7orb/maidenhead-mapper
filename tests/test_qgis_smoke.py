"""Runs tests/qgis_smoke.py with QGIS's own Python when MM_QGIS_PYTHON points to it,
e.g. "C:\Program Files\QGIS 3.44.2\bin\python-qgis.bat". Skipped otherwise."""

import os
import subprocess

import pytest

QGIS_PYTHON = os.environ.get("MM_QGIS_PYTHON", "")
HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(not QGIS_PYTHON, reason="set MM_QGIS_PYTHON to QGIS's python launcher to run the QGIS smoke test")
def test_qgis_smoke():
    r = subprocess.run([QGIS_PYTHON, os.path.join(HERE, "qgis_smoke.py")], capture_output=True, text=True,
                       timeout=600, cwd=os.path.dirname(HERE))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "\nOK" in out, out[-4000:]
