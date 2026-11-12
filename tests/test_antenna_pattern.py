"""Reference-antenna mode: pattern files and absolute gain from A/B pairs.
All patterns and calls here are synthetic."""

import datetime as dt

import pytest

from maidenhead_mapper.core import antenna
from maidenhead_mapper.core import antenna_pattern as ap
from maidenhead_mapper.core import maidenhead as mh

T0 = dt.datetime(2026, 1, 10, 18, 0, tzinfo=dt.timezone.utc)
HOME = "FN31pr"


def csv_grid(fn, azs=range(0, 360, 30), els=(0, 10, 20, 30, 60, 90), sep=","):
    rows = [sep.join(("azimuth_deg", "elevation_deg", "gain_dbi"))]
    rows += [sep.join(str(v) for v in (a, e, fn(a, e))) for a in azs for e in els]
    return "\n".join(rows) + "\n"


# -- CSV ---------------------------------------------------------------------------
def test_csv_reads_and_interpolates_bilinearly():
    ps = ap.read_csv_text(csv_grid(lambda a, e: a / 30 + e / 10))      # plane: exact under bilinear
    assert ps.kind == "CSV" and not ps.warnings
    assert ps.gain(45, 15) == pytest.approx(1.5 + 1.5)
    assert ps.gain(0, 0) == pytest.approx(0)
    assert ps.gain(330, 90) == pytest.approx(11 + 9)


def test_azimuth_wraps_through_north():
    # 330° -> 0° gap: gain 11 at 330, 0 at 0; 345° is halfway
    ps = ap.read_csv_text(csv_grid(lambda a, e: a / 30))
    assert ps.gain(345, 10) == pytest.approx(5.5)
    assert ps.gain(-15, 10) == pytest.approx(5.5)
    assert ps.gain(360 + 15, 10) == pytest.approx(0.5)


def test_tolerant_headers_semicolons_and_decimal_commas():
    text = "# made-up dipole\nAzimuth (deg);El;Gain dBi\n0;10;-2,5\n0;20;1,5\n180;10;-2,5\n180;20;1,5\n"
    ps = ap.read_csv_text(text)
    assert ps.gain(90, 15) == pytest.approx(-0.5)
    tabbed = "az\televation\tdbi\n0\t5\t3\n120\t5\t3\n240\t5\t3\n"
    assert ap.read_csv_text(tabbed).gain(60, 5) == pytest.approx(3)


def test_single_elevation_cut_is_constant_with_warning():
    ps = ap.read_csv_text(csv_grid(lambda a, e: 2.0 if a == 0 else -1.0, els=(10,)))
    assert any("one elevation" in w for w in ps.warnings)
    assert ps.gain(0, 2) == ps.gain(0, 45) == pytest.approx(2.0)


def test_single_azimuth_is_omnidirectional():
    ps = ap.read_csv_text(csv_grid(lambda a, e: e / 10, azs=(0,)))
    assert any("one azimuth" in w for w in ps.warnings)
    assert ps.gain(0, 15) == ps.gain(217, 15) == pytest.approx(1.5)


def test_elevation_outside_the_pattern_has_no_value():
    ps = ap.read_csv_text(csv_grid(lambda a, e: 0, els=(5, 10, 20)))
    assert ps.gain(0, 4) is None and ps.gain(0, 25) is None
    assert ps.lookup(0, 2) == (None, ap.NO_ELEVATION)


def test_orientation_rotates_the_pattern():
    # beam with its peak at pattern azimuth 0, pointed at compass 90 (east)
    text = csv_grid(lambda a, e: 6.0 if a == 0 else -10.0)
    ps = ap.read_csv_text(text)
    ps.orientation_deg = 90
    assert ps.gain(90, 10) == pytest.approx(6.0)
    assert ps.gain(0, 10) == pytest.approx(-10.0)


def test_csv_frequency_column_picks_the_nearest_pattern():
    rows = ["freq_mhz,az,el,gain"] + [f"{f},{a},10,{g}" for f, g in ((7.1, 1.0), (14.1, 5.0)) for a in (0, 120, 240)]
    ps = ap.read_csv_text("\n".join(rows))
    assert ps.gain(0, 10, 7.074) == pytest.approx(1.0)
    assert ps.gain(0, 10, 14.074) == pytest.approx(5.0)
    assert ps.lookup(0, 10, 28.074) == (None, ap.NO_FREQUENCY)
    assert ps.gain(0, 10) == pytest.approx(1.0)           # unknown frequency: the first


@pytest.mark.parametrize("text, msg", [
    ("", "empty"),
    ("just some words\n", "header row"),
    ("azimuth_deg,gain_dbi\n0,1\n", "elevation_deg"),
    ("az,el,gain\n0,10,x\n", "row 2"),
    ("az,el,gain\n0,10,1\n90,10,1\n90,20,1\n", "not a full"),
    ("az,el,gain\n" + "".join(f"{a},10,0\n" for a in range(0, 100, 10)), "cover only"),
    ("az,el,gain\n0,95,1\n", "outside"),
])
def test_bad_csv_files_say_why(text, msg):
    with pytest.raises(ap.PatternError, match=msg):
        ap.read_csv_text(text)


def test_read_pattern_from_file(tmp_path):
    p = tmp_path / "dipole.csv"
    p.write_text(csv_grid(lambda a, e: 1.0), encoding="utf-8")
    ps = ap.read_pattern(str(p), orientation_deg=450)
    assert ps.orientation_deg == 90 and ps.gain(10, 10) == pytest.approx(1.0)
    with pytest.raises(ap.PatternError, match="can't read"):
        ap.read_pattern(str(tmp_path / "missing.csv"))


# -- NEC-2 output --------------------------------------------------------------------
# A hand-made, synthetic table in the NEC-2 / 4nec2 .out layout (numbers invented).
NEC = """
                               - - - - - - FREQUENCY - - - - - -

                                    FREQUENCY= 1.4100E+01 MHZ
                                    WAVELENGTH= 2.1262E+01 METERS

                               - - - RADIATION PATTERNS - - -

 - - ANGLES - -           - POWER GAINS -       - - - POLARIZATION - - -    - - - E(THETA) - - -    - - - E(PHI) - - -
  THETA     PHI        VERT.   HOR.    TOTAL      AXIAL     TILT  SENSE    MAGNITUDE    PHASE      MAGNITUDE    PHASE
 DEGREES   DEGREES       DB      DB       DB        RATIO     DEG.            VOLTS/M    DEGREES      VOLTS/M    DEGREES
   90.00      0.00    -999.99 -999.99 -999.99    0.00000     0.00            0.00000E+00     0.00  0.00000E+00     0.00
   60.00      0.00     -20.00    1.00    1.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   30.00      0.00     -20.00    4.00    4.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
    0.00      0.00     -20.00    2.00    2.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   90.00     90.00    -999.99 -999.99 -999.99    0.00000     0.00            0.00000E+00     0.00  0.00000E+00     0.00
   60.00     90.00     -20.00   -9.00   -9.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   30.00     90.00     -20.00   -6.00   -6.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
    0.00     90.00     -20.00    2.00    2.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   90.00    180.00    -999.99 -999.99 -999.99    0.00000     0.00            0.00000E+00     0.00  0.00000E+00     0.00
   60.00    180.00     -20.00    1.00    1.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   30.00    180.00     -20.00    4.00    4.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
    0.00    180.00     -20.00    2.00    2.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   90.00    270.00    -999.99 -999.99 -999.99    0.00000     0.00            0.00000E+00     0.00  0.00000E+00     0.00
   60.00    270.00     -20.00   -3.00   -3.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   30.00    270.00     -20.00    0.00    0.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
    0.00    270.00     -20.00    2.00    2.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   90.00    360.00    -999.99 -999.99 -999.99    0.00000     0.00            0.00000E+00     0.00  0.00000E+00     0.00
   60.00    360.00     -20.00    1.00    1.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
   30.00    360.00     -20.00    4.00    4.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00
    0.00    360.00     -20.00    2.00    2.00    0.00000    90.00  LINEAR   1.00000E-02     0.00  1.00000E+00     0.00

 DATA CARD No:   7 EN   0     0     0      0  0.00000E+00  0.00000E+00
"""


def test_nec_table_conventions():
    ps = ap.read_nec_text(NEC)
    p = ps.patterns[0]
    assert ps.kind == "NEC" and p.mhz == pytest.approx(14.1)
    assert p.elevations == [0, 30, 60, 90]                  # elevation = 90 - theta
    assert p.azimuths == [0, 90, 180, 270]                  # phi 360 = phi 0; azimuth = -phi
    assert p.gain(0, 0) == ap.MIN_DBI                       # -999.99 raised to the floor
    assert any("-40" in w or "−40" in w for w in p.warnings)
    assert ps.gain(0, 60) == pytest.approx(4.0)             # theta 30, phi 0 = +X
    # phi 90 (+Y, counter-clockwise from +X) is azimuth 270; with +X north that is west
    assert ps.gain(270, 30) == pytest.approx(-9.0)
    assert ps.gain(90, 30) == pytest.approx(-3.0)
    assert ps.gain(90, 15) == pytest.approx((-40 - 3) / 2)  # bilinear down to the floor


def test_nec_orientation_x_east():
    ps = ap.read_nec_text(NEC)
    ps.orientation_deg = 90                                 # model drawn with +X pointing east
    assert ps.gain(90, 60) == pytest.approx(4.0)            # +X
    assert ps.gain(0, 30) == pytest.approx(-9.0)            # +Y is north when +X is east
    assert ps.gain(180, 30) == pytest.approx(-3.0)


def test_nec_vertical_and_horizontal_columns():
    assert ap.read_nec_text(NEC, column="vert").gain(0, 60) == pytest.approx(-20.0)
    assert ap.read_nec_text(NEC, column="hor").gain(0, 60) == pytest.approx(4.0)


def test_nec_negative_theta_and_several_frequencies():
    t2 = NEC.replace("1.4100E+01", "7.1000E+00")
    ps = ap.read_nec_text(NEC + t2)
    assert sorted(p.mhz for p in ps.patterns) == [7.1, 14.1]
    neg = ("FREQUENCY= 1.0000E+01 MHZ\n - - - RADIATION PATTERNS - - -\n THETA PHI\n"
           + "".join(f" {th:.2f} 0.00 0 0 {th / 10:.2f}\n" for th in (-60, -30, 0, 30, 60))
           + "\n")
    p = ap.read_nec_text(neg).patterns[0]
    assert p.azimuths == [0, 180] and p.elevations == [30, 60, 90]
    assert p.gain(180, 30) == pytest.approx(-6.0)           # theta -60 at phi 0 -> theta 60 at phi 180
    assert p.gain(0, 30) == pytest.approx(6.0)


def test_nec_without_a_table():
    with pytest.raises(ap.PatternError, match="RADIATION PATTERNS"):
        ap.read_nec_text("NEC output with no pattern\n")


def test_read_pattern_detects_nec(tmp_path):
    p = tmp_path / "model.out"
    p.write_text(NEC, encoding="cp1252")
    assert ap.read_pattern(str(p)).kind == "NEC"


# -- absolute gain from A/B pairs -------------------------------------------------------
def dec(sec, msg, snr, mhz=14.074):
    return antenna.Decode(T0 + dt.timedelta(seconds=sec), msg, snr, None, mhz)


def flat(g):
    return ap.read_csv_text(csv_grid(lambda a, e: g, els=(0, 90)))


def test_absolute_gain_reference_b():
    a = [dec(0, "CQ N0CALL EM12", -5), dec(15, "CQ N0CALL EM12", -7), dec(30, "CQ N1CALL JO01", -15)]
    b = [dec(0, "CQ N0CALL EM12", -9), dec(15, "CQ N0CALL EM12", -8)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME, reference=flat(2.0))
    assert c.reference_side == "B"
    pairs = [r for r in c.decodes if r["diff_db"] is not None]
    assert sorted(r["abs_gain_dbi"] for r in pairs) == [3.0, 6.0]       # 2 dBi + (A - B)
    assert all(r["ref_gain_dbi"] == 2.0 for r in pairs)
    single = [r for r in c.decodes if r["diff_db"] is None][0]
    assert single["abs_gain_dbi"] is None and single["ref_gain_dbi"] is None   # censored: counts only
    st = c.by_station["N0CALL"]
    assert st.abs_n == 2 and st.median_abs_dbi == 4.5 and st.median_ref_dbi == 2.0
    assert c.total.abs_n == 2 and c.total.only_a == 1
    assert c.by_station["N1CALL"].abs_n == 0 and c.by_station["N1CALL"].median_abs_dbi is None


def test_absolute_gain_reference_a_and_takeoff_dependence():
    # reference A: gain rises 1 dB per degree of elevation; B = ref - (A - B)
    ref = ap.read_csv_text(csv_grid(lambda a, e: e / 10.0))
    a = [dec(0, "CQ N0CALL EM12", -5)]
    b = [dec(0, "CQ N0CALL EM12", -9)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME, reference=ref, reference_side="a")
    r = c.decodes[0]
    assert r["ref_gain_dbi"] == pytest.approx(r["takeoff_deg"] / 10.0, abs=0.01)
    assert r["abs_gain_dbi"] == pytest.approx(r["ref_gain_dbi"] - 4, abs=0.01)
    assert c.reference_side == "A"


def test_reference_uses_the_bearing_and_orientation():
    # beam: +10 dBi in pattern azimuth 0..30, -10 elsewhere; aimed at the station's bearing
    bearing = mh.distance_bearing(HOME, "JO01")[1]
    text = csv_grid(lambda a, e: 10.0 if a == 0 else -10.0, azs=range(0, 360, 5))
    ref = ap.read_csv_text(text)
    a, b = [dec(0, "CQ N1CALL JO01", -10)], [dec(0, "CQ N1CALL JO01", -10)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME, reference=ref)
    assert c.decodes[0]["ref_gain_dbi"] < 0
    ref.orientation_deg = mh.distance_bearing(HOME, "JO01")[1]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME, reference=ref)
    assert c.decodes[0]["ref_gain_dbi"] == pytest.approx(10.0, abs=0.5)   # bearing kept to 0.1°
    assert bearing == pytest.approx(ref.orientation_deg)


def test_pairs_without_a_reference_value_are_counted():
    ref = ap.read_csv_text(csv_grid(lambda a, e: 0, els=(40, 90)))      # no low angles
    a, b = [dec(0, "CQ N0CALL EM12", -5)], [dec(0, "CQ N0CALL EM12", -9)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME, reference=ref)
    assert c.ref_notes == {ap.NO_ELEVATION: 1} and c.total.abs_n == 0
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME,
                        reference=ap.read_csv_text("mhz,az,el,gain\n7.1,0,10,0\n"))
    assert c.ref_notes == {ap.NO_FREQUENCY: 1}


def test_without_a_reference_nothing_changes():
    a, b = [dec(0, "CQ N0CALL EM12", -5)], [dec(0, "CQ N0CALL EM12", -9)]
    c = antenna.compare(antenna.match(a, b), antenna.learn_grids(a, b), HOME)
    assert c.reference_side is None and c.total.abs_n == 0 and c.decodes[0]["abs_gain_dbi"] is None
    with pytest.raises(ValueError):
        antenna.compare([], {}, HOME, reference=flat(0), reference_side="C")


def test_median_ci():
    assert antenna.median_ci([1, 2, 3]) is None
    lo, hi = antenna.median_ci(list(range(100)))
    assert lo < 49.5 < hi and hi - lo < 25
    assert antenna.median_ci([5.0] * 10) == (5.0, 5.0)
