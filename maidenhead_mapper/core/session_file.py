"""Recognising a saved live-session GeoPackage from the file itself. Pure Python.

A session file (live/session.py) holds the tables `stations`, `exchanges`
and `log`. Layers loaded from one (drag and drop, a new project) carry no
plugin tags, so the timeline and tools infer what they are from the file:
same tables -> a session, role from the table, and a group id derived from
the file path so both tables of one file form one dataset.
"""

import hashlib
import os
import sqlite3

SESSION_TABLES = ("stations", "exchanges", "log")
ROLES = {"stations": "stations", "exchanges": "exchanges"}


def gpkg_tables(path):
    """Table names listed in a GeoPackage's gpkg_contents, or an empty set."""
    if not (path and os.path.isfile(path)):
        return set()
    try:
        uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            return {r[0].lower() for r in con.execute("SELECT table_name FROM gpkg_contents")}
        finally:
            con.close()
    except sqlite3.Error:
        return set()


def group_for(path):
    """A stable dataset id for one session file."""
    norm = os.path.normcase(os.path.abspath(path))
    return "gpkg-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def infer(path, layername, tables=None):
    """Log properties ({kind, gpkg, role, group}) for the table `layername` of
    the GeoPackage at `path` if the file is a saved session, else None."""
    role = ROLES.get((layername or "").lower())
    if role is None or not (path or "").lower().endswith(".gpkg"):
        return None
    tables = gpkg_tables(path) if tables is None else {t.lower() for t in tables}
    if not set(SESSION_TABLES) <= tables:
        return None
    return {"kind": "session", "gpkg": path, "role": role, "group": group_for(path), "inferred": True}
