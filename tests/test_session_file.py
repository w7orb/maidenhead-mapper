import sqlite3

from maidenhead_mapper.core import session_file


def _gpkg(path, tables):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT)")
    con.executemany("INSERT INTO gpkg_contents VALUES (?, ?)", [(t, "features") for t in tables])
    con.commit()
    con.close()


def test_infer_session(tmp_path):
    p = str(tmp_path / "wsjtx_session_20261111_194233.gpkg")
    _gpkg(p, ["stations", "exchanges", "log"])
    assert session_file.gpkg_tables(p) == {"stations", "exchanges", "log"}
    s, e = session_file.infer(p, "stations"), session_file.infer(p, "EXCHANGES")
    assert (s["kind"], s["role"], e["role"], s["gpkg"]) == ("session", "stations", "exchanges", p)
    assert s["group"] == e["group"] == session_file.group_for(p)       # one dataset per file
    assert session_file.infer(p, "log") is None                        # the log isn't drawn


def test_not_a_session(tmp_path):
    p = str(tmp_path / "other.gpkg")
    _gpkg(p, ["stations", "exchanges"])                                # no log table
    assert session_file.infer(p, "stations") is None
    assert session_file.infer(str(tmp_path / "missing.gpkg"), "stations") is None
    assert session_file.infer("x.shp", "stations", tables={"stations", "exchanges", "log"}) is None
    bad = tmp_path / "bad.gpkg"
    bad.write_bytes(b"not sqlite")
    assert session_file.gpkg_tables(str(bad)) == set()


def test_group_is_stable_and_distinct(tmp_path):
    a, b = str(tmp_path / "a.gpkg"), str(tmp_path / "b.gpkg")
    assert session_file.group_for(a) == session_file.group_for(a) != session_file.group_for(b)
