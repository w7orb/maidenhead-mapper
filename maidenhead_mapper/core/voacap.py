"""Point-to-point predictions from the user's installed VOACAP. Pure Python.

VOACAP (Voice of America Coverage Analysis Program, NTIA/ITS) predicts the
monthly-median performance of an HF circuit: MUF, mode, takeoff angle,
signal power, SNR and reliability per hour and frequency. The plugin does
not ship VOACAP: it writes an input deck, runs the user's copy and reads the
output.

* Windows: the NTIA/ITS "itshfbc" package, normally C:\\itshfbc, run as
  ``bin_win\\voacapw.exe SILENT <itshfbc dir> <deck> <output>`` with deck and
  output named relative to ``<itshfbc dir>\\run`` (itshfbc
  news_win\\command.txt, "P-P circuit"). It is a Salford/ClearWin program:
  errors open message boxes that wait for a click. It is therefore started
  on a private, invisible Windows desktop and killed after a timeout, so
  nothing ever appears on the user's screen and a stuck run can't hang QGIS.
* Linux/macOS: ``voacapl`` (J. A. Watson's port, https://github.com/jawatson/voacapl)
  on PATH with its data in ~/itshfbc, run as
  ``voacapl -s --run-dir=<tmp> <itshfbc dir> <deck> <output>`` (voacapl
  src/voacapw/voacapw.for).

Input deck ("card" format: a 10-character card name, then fixed-width
Fortran fields). Formats are taken from the READ statements in voacapl's
src/voacapw/decred.for and antcalc.for, and checked against the sample
decks in voacapl (itshfbc/run/voacapx.dat, tests/p2p/test01.dat):

  LINEMAX   I5            lines per output page
  COEFFS    A4            CCIR (default) or URSI foF2 maps
  TIME      4I5           first hour, last hour, step, 1 = UT (hours 1-24)
  MONTH     A5,F5.2       year, month (month.day would force URSI daily maps)
  SUNSPOT   F5.0          12-month smoothed sunspot number
  LABEL     2A20          transmitter and receiver names (printed in the output)
  CIRCUIT   F5.2,A1,3(F9.2,A1),4X,I5   TX lat N/S, TX lon E/W, RX lat, RX lon,
                          0 = short path
  SYSTEM    F5.2,F5.0,F5.2,F5.0,3F5.2  (power, unused: it is on the ANTENNA
                          card), man-made noise at 3 MHz in -dBW in 1 Hz,
                          minimum takeoff angle (deg), required reliability (%),
                          required SNR (dB in 1 Hz), multipath power
                          tolerance (dB), multipath delay tolerance (ms)
  FPROB     4F5.2         multipliers for foE, foF1, foF2, foEs (1,1,1,0 is
                          the ITS default: no sporadic-E)
  ANTENNA   4I5,F10.3,1X,A21,1X,F5.1,F10.4   number, 1 = TX / 2 = RX, min and
                          max MHz, design MHz (an isotrope's gain in dBi),
                          [antenna file under itshfbc/antennas], beam azimuth,
                          TX power in kW
  FREQUENCY 11F5.2        up to 11 frequencies, MHz
  METHOD    2I5           30 = short/long path smoothing, recommended in
                          itshfbc database/Method.dat
  EXECUTE                 run with the cards so far
  QUIT

Cards keep their values after EXECUTE, so one deck holds many circuits: each
block changes TIME, MONTH, SUNSPOT, LABEL, CIRCUIT and FREQUENCY and ends in
EXECUTE. ANTENNA cards must all come before the first EXECUTE (antcalc.for:
"You cannot have an ANTENNA card after EXECUTE"); the antennas here are
isotropes, which have no azimuth, so one set serves every circuit.

Output (METHOD 30, the "complete system performance" table; sample in
voacapl tests/p2p/test01.out, CC0/public domain): per hour a block whose
first line is ``hour MUF f1 .. f11 FREQ`` and whose next lines hold one value
per column, labelled at the right: MODE, TANGLE, DELAY, V HITE, MUFday,
LOSS, DBU, S DBW, N DBW, SNR, RPWRG, REL, MPROB, S PRB, SIG LW, SIG UP,
SNR LW, SNR UP, TGAIN, RGAIN, SNRxx. Columns are 5 characters wide after a
1-character carriage-control column; the first column is the hour, the
second is the MUF itself (its MODE, TANGLE etc. are for the MUF), then one
column per frequency.

SNR is in a 1 Hz bandwidth. FT8 (and WSJT-X in general) reports SNR in
2500 Hz, so the FT8-equivalent SNR is 10*log10(2500) = 34.0 dB lower
(F6IRF, "VOACAP vs WSPR reports", 2008; WSJT-X user guide).

Man-made noise levels at 3 MHz (dBW in 1 Hz) follow ITU-R P.372:
Fam = c - d*log10(f MHz), noise = Fam - 204 dBW: city (business) 76.8/27.7
-> -140.4; residential 72.5/27.7 -> -144.7; rural 67.2/27.7 -> -150.0;
quiet rural 53.6/28.6 -> -164.1. VOACAP reads the level into an integer
(NOISE in decred.for), so the deck gives whole dB: -140, -145, -150, -164.

These are monthly medians: VOACAP describes a typical day of the month at
that sunspot number, not a given evening.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ITS_URL = "https://its.ntia.gov/software/high-frequency/voacap-propagation-model/"
VOACAP_COM = "https://www.voacap.com/"
VOACAPL_URL = "https://github.com/jawatson/voacapl"
DEFAULT_WIN_DIR = r"C:\itshfbc"
SETTINGS_KEY = "maidenhead_mapper/voacap_dir"

BW_FT8_DB = 10 * math.log10(2500.0)       # 33.98 dB: 1 Hz -> 2500 Hz
F_MIN, F_MAX = 2.0, 30.0                  # VOACAP's frequency range (MHz)
MAX_FREQS = 11                            # per FREQUENCY card

# Man-made noise at 3 MHz, dBW in 1 Hz (ITU-R P.372, see module docstring)
# VOACAP keeps the level as a whole number of dB, so these are rounded.
NOISE_LEVELS = (("City (industrial), -140 dBW", -140.0), ("Residential, -145 dBW", -145.0),
                ("Rural, -150 dBW", -150.0), ("Quiet rural, -164 dBW", -164.0))

ROWS = ("MODE", "TANGLE", "DELAY", "V HITE", "MUFday", "LOSS", "DBU", "S DBW", "N DBW", "SNR", "RPWRG",
        "REL", "MPROB", "S PRB", "SIG LW", "SIG UP", "SNR LW", "SNR UP", "TGAIN", "RGAIN", "SNRxx")


class VoacapError(Exception):
    pass


class NotInstalled(VoacapError):
    pass


def ft8_snr(snr_hz):
    """FT8/WSJT-X SNR (2500 Hz) from VOACAP's SNR in 1 Hz."""
    return None if snr_hz is None else snr_hz - BW_FT8_DB


def snr_hz_from_ft8(snr_ft8):
    return snr_ft8 + BW_FT8_DB


# --- install ------------------------------------------------------------------

@dataclass
class Install:
    kind: str           # "windows" (voacapw.exe) or "voacapl"
    exe: str
    root: str           # the itshfbc folder

    @property
    def run_dir(self):
        return os.path.join(self.root, "run")


def not_installed_message(detail=""):
    where = ("Install the ITS HF propagation package (itshfbc, which includes VOACAP) in C:\\itshfbc, "
             "or set its folder in the tool. " if sys.platform.startswith("win") else
             "Install voacapl (" + VOACAPL_URL + ", packaged in Debian/Ubuntu as 'voacapl') and run "
             "makeitshfbc once to create ~/itshfbc. ")
    return (f"VOACAP was not found{': ' + detail if detail else ''}. {where}"
            f"VOACAP is free from NTIA/ITS: {ITS_URL} (see also {VOACAP_COM}). "
            "The plugin runs your copy; it does not include VOACAP.")


def find_install(folder: Optional[str] = None, platform: Optional[str] = None, which=shutil.which) -> Install:
    """The VOACAP installation to use, or NotInstalled with what to do.

    folder: the itshfbc folder (the maidenhead_mapper/voacap_dir setting or
    the tool's parameter); empty = C:\\itshfbc on Windows, ~/itshfbc elsewhere.
    """
    platform = platform or sys.platform
    folder = (folder or "").strip()
    if platform.startswith("win"):
        root = folder or DEFAULT_WIN_DIR
        exe = os.path.join(root, "bin_win", "voacapw.exe")
        if not os.path.isfile(exe):
            raise NotInstalled(not_installed_message(f"no bin_win\\voacapw.exe in {root}"))
        if not os.path.isdir(os.path.join(root, "run")):
            raise NotInstalled(not_installed_message(f"no run folder in {root}"))
        return Install("windows", exe, root)
    exe = which("voacapl")
    if not exe:
        raise NotInstalled(not_installed_message("voacapl is not on the PATH"))
    root = folder or os.path.join(os.path.expanduser("~"), "itshfbc")
    if not os.path.isdir(os.path.join(root, "antennas")):
        raise NotInstalled(not_installed_message(f"no itshfbc data in {root} (run makeitshfbc)"))
    return Install("voacapl", exe, root)


# --- settings and requests ------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    power_w: float = 100.0
    tx_gain_dbi: float = 0.0          # isotrope gain at the transmitter
    rx_gain_dbi: float = 0.0          # isotrope gain at the receiver
    noise_dbw: float = -145.0         # man-made noise at 3 MHz (residential)
    req_snr_ft8: float = -21.0        # FT8 decode threshold, dB in 2500 Hz
    req_rel_pct: float = 90.0
    min_angle_deg: float = 3.0        # VOACAP's own default when none is given
    coeffs: str = "CCIR"

    @property
    def req_snr_hz(self):
        return snr_hz_from_ft8(self.req_snr_ft8)

    def key(self):
        return (f"p{self.power_w:g}|t{self.tx_gain_dbi:g}|r{self.rx_gain_dbi:g}|n{self.noise_dbw:g}|"
                f"s{self.req_snr_ft8:g}|q{self.req_rel_pct:g}|a{self.min_angle_deg:g}|{self.coeffs}")


@dataclass(frozen=True)
class Circuit:
    """One run: a path at an hour of a month, with up to 11 frequencies."""
    tx_lat: float
    tx_lon: float
    rx_lat: float
    rx_lon: float
    year: int
    month: int
    ssn: float
    hour: int                         # UT, 1-24 (24 = midnight)
    freqs: Tuple[float, ...]


def vo_hour(h: int) -> int:
    """VOACAP hours run 1-24: 0 UT is hour 24."""
    h = int(h) % 24
    return 24 if h == 0 else h


def _q(v, n=2):
    return round(float(v), n)


def request_key(tx_lat, tx_lon, rx_lat, rx_lon, year, month, ssn, hour, freq, settings: Settings) -> str:
    """Cache key: rounded as the deck rounds them (0.01 deg, 0.01 MHz, whole SSN)."""
    return (f"{_q(tx_lat):.2f},{_q(tx_lon):.2f}>{_q(rx_lat):.2f},{_q(rx_lon):.2f}|{int(year)}-{int(month):02d}|"
            f"ssn{round(ssn)}|h{vo_hour(hour)}|f{_q(freq):.2f}|{settings.key()}")


# --- deck ---------------------------------------------------------------------

def _latlon(v, pos, neg, width):
    return f"{abs(v):{width}.2f}{pos if v >= 0 else neg}"


def antenna_name(sep):
    """The isotrope antenna file shipped with itshfbc (antennas/default/isotrope)."""
    return f"default{sep}isotrope"


def deck(circuits: Sequence[Circuit], settings: Settings, sep: str = "\\", ids: Optional[Sequence[str]] = None) -> str:
    """The input deck for these circuits (lines joined with \\n; write it with CRLF on Windows)."""
    if not circuits:
        raise ValueError("no circuits")
    ids = list(ids) if ids is not None else [f"MM{i:06d}" for i in range(len(circuits))]
    s = settings
    ant = antenna_name(sep)
    lines = ["COMMENT    Maidenhead Mapper: VOACAP point-to-point predictions",
             "LINEMAX    9999       number of lines-per-page",
             f"COEFFS    {s.coeffs:4s}"]
    for i, (c, cid) in enumerate(zip(circuits, ids)):
        freqs = list(c.freqs)[:MAX_FREQS]
        if not freqs:
            raise ValueError("a circuit needs a frequency")
        h = vo_hour(c.hour)
        lines += [f"TIME      {h:5d}{h:5d}{1:5d}{1:5d}",
                  f"MONTH     {c.year:5d}{float(c.month):5.2f}",
                  # F5.2 fields need the decimal point: "  120" would read as 1.20
                  f"SUNSPOT   {round(c.ssn):4d}.",
                  f"LABEL     {cid:20s}{cid + 'RX':20s}",
                  "CIRCUIT   " + _latlon(c.tx_lat, "N", "S", 5) + _latlon(c.tx_lon, "E", "W", 9)
                  + _latlon(c.rx_lat, "N", "S", 9) + _latlon(c.rx_lon, "E", "W", 9) + f"  S {0:5d}",
                  # noise is stored as a whole number of dB (integer NOISE in decred.for)
                  f"SYSTEM    {1.0:5.2f}{round(-s.noise_dbw):4d}.{s.min_angle_deg:5.2f}{round(s.req_rel_pct):4d}."
                  f"{s.req_snr_hz:5.1f}{3.0:5.2f}{0.1:5.2f}",
                  "FPROB      1.00 1.00 1.00 0.00"]
        if i == 0:
            lines += [f"ANTENNA   {1:5d}{1:5d}{2:5d}{30:5d}{s.tx_gain_dbi:10.3f}[{ant:21s}]{0.0:5.1f}"
                      f"{s.power_w / 1000.0:10.4f}",
                      f"ANTENNA   {2:5d}{2:5d}{2:5d}{30:5d}{s.rx_gain_dbi:10.3f}[{ant:21s}]{0.0:5.1f}"
                      f"{0.0:10.4f}"]
        lines += ["FREQUENCY " + "".join(f"{f:5.2f}" for f in freqs + [0.0] * (MAX_FREQS - len(freqs))),
                  "METHOD       30    0",
                  "EXECUTE"]
    lines.append("QUIT")
    for ln in lines:
        if len(ln) > 80:
            raise ValueError(f"deck line too long: {ln!r}")
    return "\n".join(lines) + "\n"


# --- output -------------------------------------------------------------------

@dataclass
class Block:
    """One hour of one circuit, as printed by METHOD 30."""
    label: str
    hour: float
    muf: Optional[float]
    freqs: List[Optional[float]]
    rows: Dict[str, List] = field(default_factory=dict)     # row name -> [MUF column, f1, f2, ...]
    header: Dict[str, str] = field(default_factory=dict)


def _cell(text):
    t = text.strip()
    if not t or t == "-" or set(t) == {"*"}:
        return None
    try:
        return float(t)
    except ValueError:
        return t


def _columns(line, n=13):
    """Values in the 5-wide columns after the carriage-control character, and the row label."""
    body = line[1:1 + 5 * n]
    return [_cell(body[i * 5:(i + 1) * 5]) for i in range(n)], line[1 + 5 * n:].strip()


def parse_output(text: str) -> List[Block]:
    """Every hour block in a METHOD 30 (or 20) output file, in order."""
    blocks, cur, label, header = [], None, "", {}
    lines = text.replace("\r", "").replace("\f", "\n").split("\n")
    for i, line in enumerate(lines):
        if "SSN =" in line and i + 1 < len(lines):
            # page header: month/year/SSN line, then "TX label  RX label  AZIMUTHS ..."
            nxt = lines[i + 1]
            label = nxt[2:22].strip()
            header = {"month_line": line.strip(), "label_line": nxt.rstrip(),
                      "coords": lines[i + 2].strip() if i + 2 < len(lines) else ""}
            m = re.search(r"SSN =\s*(-?[\d.]+)", line)
            header["ssn"] = float(m.group(1)) if m else None
            continue
        if len(line) < 67:
            if cur is not None and not line.strip():
                cur = None
            continue
        values, name = _columns(line)
        if name == "FREQ" and isinstance(values[0], float):
            cur = Block(label, values[0], values[1] if isinstance(values[1], float) else None,
                        [v if isinstance(v, float) else None for v in values[2:]], {}, dict(header))
            blocks.append(cur)
        elif cur is not None and name in ROWS:
            cur.rows[name] = values[1:]
    return blocks


@dataclass
class Prediction:
    ok: bool
    error: str = ""
    muf: Optional[float] = None
    mode: Optional[str] = None
    tangle: Optional[float] = None
    rel: Optional[float] = None
    snr_hz: Optional[float] = None
    sdbw: Optional[float] = None
    ndbw: Optional[float] = None
    loss: Optional[float] = None
    mufday: Optional[float] = None
    snr_lw: Optional[float] = None

    @property
    def snr_ft8(self):
        return None if self.snr_hz is None else round(ft8_snr(self.snr_hz), 1)

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, d):
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


def prediction(block: Block, index: int) -> Prediction:
    """The prediction for frequency number `index` (0-based) of a block."""
    col = index + 1           # rows hold [MUF column, f1, f2, ...]

    def row(name):
        r = block.rows.get(name)
        v = r[col] if r and col < len(r) else None
        return None if isinstance(v, float) and v <= -999 else v

    mode = row("MODE")
    if mode in ("NANA", "NA"):
        mode = None
    if block.freqs[index] is None:
        return Prediction(False, "no such frequency in the output")
    if mode is None:
        return Prediction(False, "VOACAP found no propagating mode", muf=block.muf)
    return Prediction(True, "", block.muf, str(mode), row("TANGLE"), row("REL"), row("SNR"), row("S DBW"),
                      row("N DBW"), row("LOSS"), row("MUFday"), row("SNR LW"))


# --- running ----------------------------------------------------------------------

_LOCK = threading.Lock()      # the Windows build shares gainNN.dat files in its run folder


def _run_hidden_windows(cmd, cwd, timeout):
    """Run a command on a private, invisible desktop; kill it after timeout s.

    Returns (exit code, timed out). Message boxes from the Salford runtime
    land on that desktop, so they never reach the user's screen."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    u32.CreateDesktopW.restype = wintypes.HANDLE
    u32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p]
    u32.CloseDesktop.argtypes = [wintypes.HANDLE]
    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.c_void_p, ctypes.c_void_p]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    GENERIC_ALL, CREATE_NO_WINDOW, STARTF_USESHOWWINDOW, SW_HIDE = 0x10000000, 0x08000000, 1, 0

    name = f"maidenhead_voacap_{os.getpid()}_{threading.get_ident()}"
    hdesk = u32.CreateDesktopW(name, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise VoacapError(f"Couldn't create a hidden desktop to run VOACAP on (error {ctypes.get_last_error()})")
    try:
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = name
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        pi = PROCESS_INFORMATION()
        buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        if not k32.CreateProcessW(None, buf, None, None, False, CREATE_NO_WINDOW, None, cwd,
                                  ctypes.byref(si), ctypes.byref(pi)):
            raise VoacapError(f"Couldn't start {cmd[0]} (error {ctypes.get_last_error()})")
        try:
            timed_out = k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) != 0
            if timed_out:
                k32.TerminateProcess(pi.hProcess, 1)
                k32.WaitForSingleObject(pi.hProcess, 5000)
            code = wintypes.DWORD()
            k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            return code.value, timed_out
        finally:
            k32.CloseHandle(pi.hThread)
            k32.CloseHandle(pi.hProcess)
    finally:
        u32.CloseDesktop(hdesk)


def _run_plain(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout)
        return p.returncode, False
    except subprocess.TimeoutExpired:
        return None, True


def run_deck(install: Install, deck_text: str, timeout: float = 60.0, keep_dir: Optional[str] = None) -> str:
    """Run one deck and return the output text. Deck and output files are
    uniquely named and deleted afterwards (copied to keep_dir on failure)."""
    tag = "mm" + hashlib.sha1(f"{os.getpid()} {time.time()} {threading.get_ident()}".encode()).hexdigest()[:6]
    dat, out = tag + ".dat", tag + ".out"
    tmp = None
    if install.kind == "windows":
        run_dir = install.run_dir
        cmd = [install.exe, "SILENT", install.root, dat, out]
        cwd = os.path.dirname(install.exe)
        newline = "\r\n"
    else:
        tmp = run_dir = tempfile.mkdtemp(prefix="mm_voacap_")
        cmd = [install.exe, "-s", f"--run-dir={run_dir}", install.root, dat, out]
        cwd = run_dir
        newline = "\n"
    dat_path, out_path = os.path.join(run_dir, dat), os.path.join(run_dir, out)
    # VOACAP writes the antenna gains to gain01.dat/gain02.dat in the run
    # folder; remove them afterwards unless they were there before
    gains = [os.path.join(run_dir, f"gain{i:02d}.dat") for i in (1, 2)]
    gains = [p for p in gains if not os.path.exists(p)] if tmp is None else []
    with _LOCK:
        try:
            with open(dat_path, "w", encoding="ascii", newline=newline) as f:
                f.write(deck_text)
            if install.kind == "windows" and sys.platform.startswith("win"):
                code, timed_out = _run_hidden_windows(cmd, cwd, timeout)
            else:
                code, timed_out = _run_plain(cmd, cwd, timeout)
            text = ""
            if os.path.isfile(out_path):
                with open(out_path, encoding="latin-1") as f:
                    text = f.read()
            if timed_out or "END OF RUN" not in text:
                if keep_dir:
                    os.makedirs(keep_dir, exist_ok=True)
                    for p in (dat_path, out_path):
                        if os.path.isfile(p):
                            shutil.copy(p, os.path.join(keep_dir, "last_failed" + os.path.splitext(p)[1]))
                why = (f"VOACAP did not finish within {timeout:.0f} s and was stopped" if timed_out else
                       f"VOACAP stopped without finishing its output (exit code {code})")
                hint = "NAME IDENTIFIER ERRORS" in text and " (it rejected the input deck)" or ""
                raise VoacapError(why + hint + (f"; the deck is in {keep_dir}" if keep_dir else ""))
            return text
        finally:
            for p in [dat_path, out_path] + gains:
                try:
                    os.remove(p)
                except OSError:
                    pass
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)


def version(text: str) -> str:
    for line in text.splitlines()[:5]:
        if "VERSION" in line:
            return " ".join(line.split()).replace("VERSION ", "")
    return ""


# --- cache and batch -------------------------------------------------------------

class Cache:
    """Predictions by request_key, in a JSON file (None path = memory only)."""
    VERSION = 1

    def __init__(self, path: Optional[str] = None):
        self.path, self.data, self.dirty = path, {}, False
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("version") == self.VERSION:
                    self.data = d.get("predictions", {})
            except (OSError, ValueError):
                self.data = {}

    def get(self, key) -> Optional[Prediction]:
        d = self.data.get(key)
        return Prediction.from_json(d) if d else None

    def put(self, key, p: Prediction):
        self.data[key] = p.to_json()
        self.dirty = True

    def save(self):
        if not (self.path and self.dirty):
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": self.VERSION, "predictions": self.data}, f)
        os.replace(tmp, self.path)
        self.dirty = False


@dataclass(frozen=True)
class Request:
    tx_lat: float
    tx_lon: float
    rx_lat: float
    rx_lon: float
    year: int
    month: int
    ssn: float
    hour: int
    freq: float

    def key(self, settings):
        return request_key(self.tx_lat, self.tx_lon, self.rx_lat, self.rx_lon, self.year, self.month,
                           self.ssn, self.hour, self.freq, settings)


def group_circuits(requests: Iterable[Request]) -> List[Tuple[Circuit, List[Request]]]:
    """Requests sharing path, month, SSN and hour share one circuit (up to 11 frequencies)."""
    groups: Dict[tuple, List[Request]] = {}
    for r in requests:
        k = (_q(r.tx_lat), _q(r.tx_lon), _q(r.rx_lat), _q(r.rx_lon), r.year, r.month, round(r.ssn), vo_hour(r.hour))
        lst = groups.setdefault(k, [])
        if all(_q(x.freq) != _q(r.freq) for x in lst):
            lst.append(r)
    out = []
    for k, rs in groups.items():
        for i in range(0, len(rs), MAX_FREQS):
            chunk = rs[i:i + MAX_FREQS]
            out.append((Circuit(k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7],
                                tuple(_q(r.freq) for r in chunk)), chunk))
    return out


def predict(requests: Sequence[Request], settings: Settings, install: Optional[Install] = None,
            cache: Optional[Cache] = None, per_run: int = 200, timeout_s: float = 30.0,
            per_circuit_s: float = 0.5, progress=None, cancelled=None, keep_dir=None) -> Dict[str, Prediction]:
    """Predictions by request key: from the cache, else by running VOACAP in
    batches of per_run circuits. Frequencies outside 2-30 MHz get an error
    prediction without running anything. Raises NotInstalled only when a
    run is actually needed and install is None."""
    cache = cache or Cache()
    out: Dict[str, Prediction] = {}
    todo = []
    for r in requests:
        k = r.key(settings)
        if k in out:
            continue
        if not F_MIN <= r.freq <= F_MAX:
            out[k] = Prediction(False, f"{r.freq:g} MHz is outside VOACAP's 2-30 MHz")
            continue
        hit = cache.get(k)
        if hit is not None:
            out[k] = hit
        else:
            todo.append(r)
    if not todo:
        return out
    if install is None:
        raise NotInstalled(not_installed_message())
    circuits = group_circuits(todo)
    sep = "\\" if install.kind == "windows" else "/"
    done = 0
    for i in range(0, len(circuits), per_run):
        if cancelled and cancelled():
            break
        batch = circuits[i:i + per_run]
        ids = [f"MM{j:06d}" for j in range(len(batch))]
        text = run_deck(install, deck([c for c, _ in batch], settings, sep, ids),
                        timeout_s + per_circuit_s * len(batch), keep_dir)
        by_id: Dict[Tuple[str, int], Block] = {}
        for b in parse_output(text):
            by_id[(b.label, int(round(b.hour)))] = b
        for cid, (c, rs) in zip(ids, batch):
            b = by_id.get((cid, c.hour))
            got_ssn = b.header.get("ssn") if b is not None else None
            for j, r in enumerate(rs):
                k = r.key(settings)
                if b is None:
                    out[k] = Prediction(False, "no output for this circuit")
                    continue
                if got_ssn is not None and abs(got_ssn - round(c.ssn)) > 0.5:
                    out[k] = Prediction(False, f"VOACAP read SSN {got_ssn:g}, not {round(c.ssn)}")
                    continue
                p = prediction(b, j)
                out[k] = p
                cache.put(k, p)
        done += len(batch)
        if progress:
            progress(done, len(circuits))
    cache.save()
    return out


# --- paths from layer attributes -------------------------------------------------

def ssn_for(when, smoothed=None, monthly=None, essn=None):
    """(SSN, source) for VOACAP at a time: as the CCIR model picks R12 (SILSO
    smoothed, KC2G effective, SILSO monthly), else the latest SILSO smoothed
    value from the 12 months before (smoothed values lag about 6 months),
    else the latest monthly value."""
    from .ccir import r12_for
    r, src = r12_for(when, smoothed, monthly, essn)
    if r is not None:
        return r, src
    for data, name in ((smoothed, "SILSO smoothed"), (monthly, "SILSO monthly")):
        y, m = when.year, when.month
        for _ in range(12):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            if data and (y, m) in data:
                return data[(y, m)], f"{name} {y}-{m:02d} (latest)"
    return None, ""


def path_time(attrs):
    """(UTC hour to predict for, note) from a feature's attributes (aware
    datetimes); (None, note) without a time.

    Corridors over time: the start of the bin. Corridors (cell_a, first_utc
    and last_utc): the middle of first..last. Exchanges: first_utc (else
    last_utc). Rounded to the nearest whole hour, except bin starts."""
    from datetime import timedelta
    start, end = attrs.get("bin_start"), attrs.get("bin_end")
    if start is not None:
        note = ""
        if end is not None and (end - start).total_seconds() > 3600:
            note = f"bin of {(end - start).total_seconds() / 3600:g} h: used its start hour"
        return start.replace(minute=0, second=0, microsecond=0), note
    first, last = attrs.get("first_utc"), attrs.get("last_utc")
    note = ""
    if first is not None and last is not None and "cell_a" in attrs:
        t = first + (last - first) / 2
        span = (last - first).total_seconds() / 3600
        if span > 2:
            note = f"spans {span:.0f} h: used the middle"
    else:
        t = first or last
    if t is None:
        return None, "no time"
    return (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0), note


def path_band(attrs):
    """(band, MHz, note): the band's FT8 frequency; several bands -> the highest (as Check paths)."""
    from .bands import BAND_NAMES, band_mhz, band_for_mhz
    names = []
    if attrs.get("band"):
        names = [str(attrs["band"])]
    elif attrs.get("bands"):
        names = [b for b in str(attrs["bands"]).split(",") if b]
    names = [b for b in names if band_mhz(b)]
    if names:
        band = max(names, key=lambda b: BAND_NAMES.index(b) if b in BAND_NAMES else -1)
        note = f"several bands ({','.join(names)}): used {band}" if len(names) > 1 else ""
        return band, band_mhz(band), note
    f = attrs.get("freq_mhz")
    if isinstance(f, (int, float)) and f > 0:
        return band_for_mhz(f) or "", float(f), ""
    return None, None, "no band"


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def observed(attrs):
    """(A->B, B->A, both) observed SNR in dB (FT8, 2500 Hz), None where missing.

    Corridors: a_to_b_db, b_to_a_db and snr_db (median of all reports).
    Exchanges: b_hears_a is A's signal at B (A->B), a_hears_b the reverse;
    'both' is their mean."""
    ab, ba = _num(attrs.get("a_to_b_db")), _num(attrs.get("b_to_a_db"))
    if ab is None and ba is None:
        ab, ba = _num(attrs.get("b_hears_a")), _num(attrs.get("a_hears_b"))
    both = _num(attrs.get("snr_db"))
    if both is None:
        vals = [v for v in (ab, ba) if v is not None]
        both = sum(vals) / len(vals) if vals else None
    return ab, ba, both


def combine(p_ab: Optional[Prediction], p_ba: Optional[Prediction], obs):
    """Output values for one path from the A->B and B->A predictions and the
    observed SNRs: dict of voacap_* fields and the gaps (observed - predicted)."""
    ok = [p for p in (p_ab, p_ba) if p is not None and p.ok]
    main = p_ab if (p_ab is not None and p_ab.ok) else (ok[0] if ok else None)
    out = {"voacap_ok": 1 if main else 0}
    if main is None:
        errs = [p.error for p in (p_ab, p_ba) if p is not None and p.error]
        out["voacap_note"] = errs[0] if errs else "no prediction"
        muf = next((p.muf for p in (p_ab, p_ba) if p is not None and p.muf is not None), None)
        out["voacap_muf"] = muf
        return out

    def r(v, n=1):
        return None if v is None else round(v, n)

    snr_ab = p_ab.snr_ft8 if p_ab is not None and p_ab.ok else None
    snr_ba = p_ba.snr_ft8 if p_ba is not None and p_ba.ok else None
    both = [p for p in (p_ab, p_ba) if p is not None and p.ok and p.snr_hz is not None]
    snr_hz = sum(p.snr_hz for p in both) / len(both) if both else None
    rels = [p.rel for p in (p_ab, p_ba) if p is not None and p.ok and p.rel is not None]
    out.update({
        "voacap_muf": main.muf, "voacap_mufday": main.mufday, "voacap_mode": main.mode,
        "voacap_tangle_deg": main.tangle, "voacap_rel": r(sum(rels) / len(rels), 2) if rels else None,
        "voacap_snr_hz": r(snr_hz), "voacap_snr_db": r(ft8_snr(snr_hz)) if snr_hz is not None else None,
        "voacap_snr_ab_db": snr_ab, "voacap_snr_ba_db": snr_ba, "voacap_sdbw": main.sdbw,
    })
    o_ab, o_ba, o_all = obs
    out["obs_snr_db"] = r(o_all)
    out["gap_a_to_b_db"] = r(o_ab - snr_ab) if o_ab is not None and snr_ab is not None else None
    out["gap_b_to_a_db"] = r(o_ba - snr_ba) if o_ba is not None and snr_ba is not None else None
    pred = out["voacap_snr_db"]
    out["snr_gap_db"] = r(o_all - pred) if o_all is not None and pred is not None else None
    if main.mufday is not None and main.mufday < 0.1:
        # far above the MUF VOACAP's SNR falls off a cliff: gaps of 50-150 dB
        out["voacap_note"] = f"above VOACAP's MUF on most days (MUFday {main.mufday:.2f})"
    return out


def summary(rows: Iterable[dict]) -> dict:
    """Totals over the output rows (dicts from combine)."""
    import statistics
    rows = list(rows)
    gaps = [x["snr_gap_db"] for x in rows if x.get("snr_gap_db") is not None]
    observed_rows = [x for x in rows if x.get("obs_snr_db") is not None and x.get("voacap_ok")]
    low = [x for x in observed_rows if (x.get("voacap_rel") or 0) < 0.10]
    out = {"paths": len(rows), "predicted": sum(1 for x in rows if x.get("voacap_ok")),
           "with_gap": len(gaps), "observed": len(observed_rows), "rel_below_10": len(low)}
    if gaps:
        q = statistics.quantiles(gaps, n=4) if len(gaps) >= 2 else [gaps[0]] * 3
        out.update({"median_gap": round(statistics.median(gaps), 1), "q1_gap": round(q[0], 1),
                    "q3_gap": round(q[2], 1)})
    if observed_rows:
        out["share_rel_below_10"] = round(len(low) / len(observed_rows), 3)
    return out
