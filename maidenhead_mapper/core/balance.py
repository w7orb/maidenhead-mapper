"""Station balance and receive contrasts from FT8 exchanges. Pure Python.

An FT8 report is the SNR at which the sender hears the addressee. Model the
SNR of station i heard at station j as

    S(i->j) = P_i + G_i - L_ij + G_j - N_j

(power, antenna gains, path loss, receiver noise floor). Antenna gain and
path loss are the same in both directions over the minute an exchange
takes, so the difference between the two reports of one exchange cancels
them, and both antennas:

    S(A->B) - S(B->A) = (P_A + N_A) - (P_B + N_B) = x_A - x_B

x = P + N is a station's *balance*: high = loud transmitter and/or noisy
receiver ("loud but deaf"); low = weak signal and/or quiet receiver ("good
ears"). Each exchange with both reports gives one difference; a fit over
all of them gives every station's x, up to a constant per connected group
of stations, so it is centred to mean 0 per group.

Why not plain least squares
---------------------------
K. Solbach DK3BA, "FT8 Reports - What we can learn from" (March 2024,
https://www.uni-due.de/imperia/md/content/hft/papers/ft8_reports_engl.pdf):

* p.1: single exchanges differ by "up to a 30 dB difference" between the
  two reports; p.2: ionospheric propagation is not strictly reciprocal
  (polarisation rotation) and QSB varies the path within seconds; p.4:
  with fast QSB "errors arise due to the time-shifted 'reading' of the
  signal levels in successive runs" (the two reports come from different
  15 s slots). So single-pair differences are heavy-tailed.
* p.4: "The non-linear detector characteristic in WSJT-X ... evaluates very
  strong signals lower than their real amplitude", so strong reports are
  compressed (too low). DK3BA gives no threshold; COMPRESS_DB = +15 is our
  choice (WSJT-X reports above it are rare and their tail is short).
* p.3: the weakest signals "fall below the decoding threshold and remain
  invisible". DK3BA gives no number. WSJT-X clamps FT8 reports at -24 dB
  (a pile-up at -24 is visible in our own ALL.TXT data) and decodes become
  hit-and-miss from about -20 dB, so a report near the floor exists only
  because fading lifted the signal for that slot: it overstates the
  typical level. FLOOR_DB = -20 marks reports at or below it as censored.
* p.6: average the report difference over "at least one hundred" QSOs.

So a report <= FLOOR_DB says only "the typical level was at most this", a
report >= COMPRESS_DB says "at least this". For the difference d = x_A - x_B
(= S(A->B) - S(B->A)) this gives:

    S(A->B) low  or S(B->A) compressed  ->  d <= observed   (upper bound)
    S(A->B) compressed or S(B->A) low   ->  d >= observed   (lower bound)
    both ends bounded the same way      ->  that bound
    bounds in opposite directions       ->  no information (left out)

Method (chosen for being simple, standard and pure Python): iteratively
reweighted least squares with

* a Huber loss (k = 1.345 sigma) on exact differences, so fading outliers
  get weight k/|r| instead of 1;
* a Tobit-style EM step for bounded differences: each is replaced by its
  expected value given the bound and the current fit, assuming Gaussian
  scatter, e.g. for d <= c: E[d | d <= c] = mu - sigma phi(z)/Phi(z),
  z = (c - mu)/sigma. A bound the fit already satisfies by a wide margin
  barely moves anything; a violated one pulls the fit back across it;
* sigma = 1.4826 * MAD of the exact residuals (robust), at least
  MIN_SIGMA_DB, and PRIOR_SIGMA_DB while the graph has too few cross-checks.

Each pass solves the weighted least squares by Gauss-Seidel on the station
graph. Standard errors are sigma / sqrt(n_eff), n_eff = sum of the
station's Huber weights plus CENSORED_INFO per bounded difference (a bound
carries less information than a value); sigma falls back to PRIOR_SIGMA_DB
when dof < MIN_DOF. Pass floor_db=None and compress_db=None for the old
plain least-squares fit.

Receive contrast: when I decode A and B reports how *it* hears A, then
    S(A->B) - S(A->me) = (N_me - N_B) + (gain and path differences)
Over many exchanges in many directions the path part averages out,
leaving how well a station hears compared with me. Comparisons where
either SNR is censored are skipped (and counted): such a value is only a
bound, so the difference is too.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional


PRIOR_SIGMA_DB = 5.0   # typical scatter of (report A->B minus report B->A), fading included
MIN_DOF = 10           # cross-checks needed to trust the measured scatter instead
FLOOR_DB = -20         # reports at or below: near the decode floor (censored)
COMPRESS_DB = 15       # reports at or above: compressed by the WSJT-X detector (censored)
HUBER_K = 1.345        # Huber tuning constant, in units of sigma (95% efficiency for Gaussian)
MIN_SIGMA_DB = 1.0     # reports are whole dB; don't let sigma collapse
CENSORED_INFO = 0.5    # information of a bounded difference relative to an exact one (for std_err)


@dataclass
class TwoWay:
    a: str
    b: str
    a_hears_b: int          # S(B->A), reported by A
    b_hears_a: int          # S(A->B), reported by B

    @property
    def diff(self):         # x_A - x_B
        return self.b_hears_a - self.a_hears_b


@dataclass
class Balance:
    call: str
    balance_db: float
    n: int                  # two-way exchanges involving the station
    std_err: Optional[float]
    group: int              # connected group of stations
    group_size: int
    n_censored: int = 0     # of n: a report at the floor or compressed (used as a bound)
    n_dropped: int = 0      # of n: bounds in opposite directions, no information


def censor_side(snr, floor_db=FLOOR_DB, compress_db=COMPRESS_DB):
    """-1 if the SNR is at/below the floor (true level <= it), +1 if at/above
    the compression threshold (true level >= it), 0 if usable as it is."""
    if snr is None:
        return 0
    if floor_db is not None and snr <= floor_db:
        return -1
    if compress_db is not None and snr >= compress_db:
        return 1
    return 0


def pair_bound(p: TwoWay, floor_db=FLOOR_DB, compress_db=COMPRESS_DB):
    """0: exact difference; -1: true diff <= observed; +1: true diff >= observed;
    None: no information (bounds point opposite ways)."""
    s_ab = censor_side(p.b_hears_a, floor_db, compress_db)   # enters diff with +
    s_ba = -censor_side(p.a_hears_b, floor_db, compress_db)  # enters diff with -
    if s_ab and s_ba and s_ab != s_ba:
        return None
    return s_ab or s_ba


def _phi(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z):  # noqa: N802
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _mills(z):
    """phi(z)/Phi(z), stable for very negative z."""
    if z < -30:
        return -z
    p = _Phi(z)
    return _phi(z) / p if p > 0 else -z


def _tobit_mean(mu, c, sigma, side):
    """E[d] for d ~ N(mu, sigma) given d <= c (side -1) or d >= c (side +1)."""
    if side < 0:
        return mu - sigma * _mills((c - mu) / sigma)
    return mu + sigma * _mills((mu - c) / sigma)


def _mad_sigma(res):
    if not res:
        return None
    med = statistics.median(res)
    return 1.4826 * statistics.median(abs(r - med) for r in res)


def _gauss_seidel(nbrs, x, iterations, tol):
    """Weighted LS: nbrs[c] = [(other, edge index, sign)], y/w looked up per edge."""
    for _ in range(iterations):
        delta = 0.0
        for c, es in nbrs.items():
            sw = sum(w for _, w, _ in es)
            if sw <= 0:
                continue
            new = sum(w * (x[o] + y) for o, w, y in es) / sw
            delta = max(delta, abs(new - x[c]))
            x[c] = new
        if delta < tol:
            break


def solve(pairs: List[TwoWay], iterations=2000, tol=1e-6, floor_db=FLOOR_DB,
          compress_db=COMPRESS_DB, robust=True, max_passes=50) -> Dict[str, Balance]:
    """Censoring-aware robust station balance (see module docstring).

    robust=False, floor_db=None, compress_db=None gives plain least squares.
    """
    calls = []
    seen = set()
    n_all = defaultdict(int)
    n_cens = defaultdict(int)
    n_drop = defaultdict(int)
    edges = []              # (a, b, observed diff, bound side)
    for p in pairs:
        if p.a == p.b:
            continue
        for c in (p.a, p.b):
            n_all[c] += 1
            if c not in seen:
                seen.add(c)
                calls.append(c)
        side = pair_bound(p, floor_db, compress_db)
        if side is None:
            n_drop[p.a] += 1
            n_drop[p.b] += 1
            n_cens[p.a] += 1
            n_cens[p.b] += 1
            continue
        if side:
            n_cens[p.a] += 1
            n_cens[p.b] += 1
        edges.append((p.a, p.b, p.diff, side))

    adj = defaultdict(list)
    for i, (a, b, _, _) in enumerate(edges):
        adj[a].append((b, i))
        adj[b].append((a, i))

    # connected groups (by informative exchanges)
    group, gid = {}, 0
    for c in calls:
        if c in group:
            continue
        gid += 1
        stack = [c]
        group[c] = gid
        while stack:
            u = stack.pop()
            for o, _ in adj[u]:
                if o not in group:
                    group[o] = gid
                    stack.append(o)
    members = defaultdict(list)
    for c, g in group.items():
        members[g].append(c)
    dof = len(edges) - sum(1 for c in calls if adj[c]) + \
        sum(1 for cs in members.values() if any(adj[c] for c in cs))

    x = {c: 0.0 for c in calls}
    y = [d for _, _, d, _ in edges]     # working values (imputed for bounds)
    w = [1.0] * len(edges)

    def build():
        nb = {}
        for c in calls:
            if adj[c]:
                nb[c] = [(o, w[i], y[i] if edges[i][0] == c else -y[i]) for o, i in adj[c]]
        return nb

    def exact_residuals():
        return [d - (x[a] - x[b]) for a, b, d, s in edges if s == 0]

    def scale():
        if dof >= MIN_DOF:
            s = _mad_sigma(exact_residuals())
            if s is not None:
                return max(s, MIN_SIGMA_DB)
        return PRIOR_SIGMA_DB

    _gauss_seidel(build(), x, iterations, tol)
    iterate = robust or any(s for *_, s in edges)
    for _ in range(max_passes if iterate else 0):
        sigma = scale()
        old = dict(x)
        for i, (a, b, d, s) in enumerate(edges):
            mu = x[a] - x[b]
            if s == 0:
                r = abs(d - mu)
                k = HUBER_K * sigma
                w[i] = 1.0 if (not robust or r <= k) else k / r
            else:
                y[i] = _tobit_mean(mu, d, sigma, s)
        _gauss_seidel(build(), x, iterations, tol)
        if max((abs(x[c] - old[c]) for c in x), default=0.0) < 1e-4:
            break

    for g, cs in members.items():
        m = sum(x[c] for c in cs) / len(cs)
        for c in cs:
            x[c] -= m

    # standard errors: robust scale over the exact differences, prior when few cross-checks
    sigma = PRIOR_SIGMA_DB
    if dof >= MIN_DOF:
        res = exact_residuals()
        if robust:
            s = _mad_sigma(res)
        else:
            s = math.sqrt(sum(r * r for r in res) / dof) if res else None
        if s is not None:
            sigma = max(s, MIN_SIGMA_DB) if robust else s
    n_eff = defaultdict(float)
    for i, (a, b, _, s) in enumerate(edges):
        info = w[i] if s == 0 else CENSORED_INFO
        n_eff[a] += info
        n_eff[b] += info
    out = {}
    for c in calls:
        se = sigma / math.sqrt(n_eff[c]) if n_eff[c] > 0 else None
        out[c] = Balance(c, round(x[c], 2), n_all[c], None if se is None else round(se, 2),
                         group[c], len(members[group[c]]), n_cens[c], n_drop[c])
    return out


def verdict(b: Balance, min_n=3, threshold_db=3.0):
    if b.n - b.n_dropped < min_n or b.std_err is None or b.std_err > threshold_db:
        return "uncertain"
    if b.balance_db > threshold_db:
        return "loud but deaf"
    if b.balance_db < -threshold_db:
        return "good ears"
    return "balanced"


@dataclass
class Contrast:
    call: str
    hears_vs_me_db: float   # median: how this station heard a signal minus how I heard it
    n: int                  # comparisons used
    n_skipped: int = 0      # comparisons left out because an SNR was censored


def contrasts(rows, floor_db=FLOOR_DB, compress_db=COMPRESS_DB) -> Dict[str, Contrast]:
    """rows: (receiver, their SNR of a signal, my SNR of the same signal).

    Positive hears_vs_me_db: the station heard those signals better than I did.
    Comparisons where either SNR is at/below floor_db or at/above compress_db
    are skipped and counted in n_skipped. A station with only skipped
    comparisons gets no entry.
    """
    diffs = defaultdict(list)
    skipped = defaultdict(int)
    for receiver, theirs, mine in rows:
        if theirs is None or mine is None:
            continue
        if censor_side(theirs, floor_db, compress_db) or censor_side(mine, floor_db, compress_db):
            skipped[receiver] += 1
            continue
        diffs[receiver].append(theirs - mine)
    return {c: Contrast(c, float(statistics.median(d)), len(d), skipped[c]) for c, d in diffs.items()}


def exchange_contrasts(a, b, a_hears_b, b_hears_a, me_hears_a, me_hears_b):
    """Per-exchange rows for contrasts(): B's report of A vs my SNR of A, and
    A's report of B vs my SNR of B."""
    return [(b, b_hears_a, me_hears_a), (a, a_hears_b, me_hears_b)]
