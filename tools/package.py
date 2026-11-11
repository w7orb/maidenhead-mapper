"""Build the zip that plugins.qgis.org (or Plugins -> Install from ZIP) takes.

    python tools/package.py            -> dist/maidenhead_mapper-<version>.zip

Packs maidenhead_mapper/ plus LICENSE, skipping caches. Standard library only.
"""

import configparser
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "maidenhead_mapper"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo", ".bak", ".orig"}


def main():
    meta = configparser.ConfigParser(interpolation=None)
    meta.read(os.path.join(ROOT, PKG, "metadata.txt"), encoding="utf-8")
    version = meta["general"]["version"]
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{PKG}-{version}.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PKG)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if os.path.splitext(name)[1] in SKIP_EXT or name.startswith("."):
                    continue
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
                count += 1
        z.write(os.path.join(ROOT, "LICENSE"), f"{PKG}/LICENSE")
    print(f"{out}: {count + 1} files")


if __name__ == "__main__":
    sys.exit(main())
