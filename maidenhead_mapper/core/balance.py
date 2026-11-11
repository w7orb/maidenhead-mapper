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
ears"). Each exchange with both reports gives one difference; least
squares over all of them gives every station's x, up to a constant per
connected group of stations, so it is centred to mean 0 per group.

Receive contrast: when I decode A and B reports how *it* hears A, then
    S(A->B) - S(A->me) = (N_me - N_B) + (gain and path differences)
Over many exchanges in many directions the path part averages out,
leaving how well a station hears compared with me.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional


PRIOR_SIGMA_DB = 5.0   # typical scatter of (report A->B minus report B->A), fading included
MIN_DOF = 10           # cross-checks needed to trust the measured scatter instead


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


def solve(pairs: List[TwoWay], iterations=2000, tol=1e-6) -> Dict[str, Balance]:
    """Least-squares station balance (Gauss-Seidel on the station graph)."""
    nbrs = defaultdict(list)            # call -> [(other, x_call - x_other)]
    for p in pairs:
        if p.a == p.b:
            continue
        nbrs[p.a].append((p.b, p.diff))
        nbrs[p.b].append((p.a, -p.diff))
    x = {c: 0.0 for c in nbrs}
    for _ in range(iterations):
        delta = 0.0
        for c, es in nbrs.items():
            new = sum(x[o] + d for o, d in es) / len(es)
            delta = max(delta, abs(new - x[c]))
            x[c] = new
        if delta < tol:
            break
    # connected groups; centre each at mean 0
    group, gid = {}, 0
    for c in nbrs:
        if c in group:
            continue
        gid += 1
        stack = [c]
        group[c] = gid
        while stack:
            u = stack.pop()
            for o, _ in nbrs[u]:
                if o not in group:
                    group[o] = gid
                    stack.append(o)
    members = defaultdict(list)
    for c, g in group.items():
        members[g].append(c)
    for g, cs in members.items():
        m = sum(x[c] for c in cs) / len(cs)
        for c in cs:
            x[c] -= m
    # residual scatter -> standard errors
    edges = [(p.a, p.b, p.diff) for p in pairs if p.a != p.b]
    sq = sum((x[a] - x[b] - d) ** 2 for a, b, d in edges)
    dof = len(edges) - len(x) + len(members)
    # With few cross-checks (tree-like graphs) the fit can't measure its own
    # scatter; assume the typical scatter of a report difference instead.
    sigma = math.sqrt(sq / dof) if dof >= MIN_DOF else PRIOR_SIGMA_DB
    out = {}
    for c in x:
        n = len(nbrs[c])
        se = sigma / math.sqrt(n) if sigma is not None else None
        out[c] = Balance(c, round(x[c], 2), n, None if se is None else round(se, 2),
                         group[c], len(members[group[c]]))
    return out


def verdict(b: Balance, min_n=3, threshold_db=3.0):
    if b.n < min_n or b.std_err is None or b.std_err > threshold_db:
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
    n: int


def contrasts(rows) -> Dict[str, Contrast]:
    """rows: (receiver, their SNR of a signal, my SNR of the same signal).

    Positive hears_vs_me_db: the station heard those signals better than I did.
    """
    diffs = defaultdict(list)
    for receiver, theirs, mine in rows:
        if theirs is None or mine is None:
            continue
        diffs[receiver].append(theirs - mine)
    return {c: Contrast(c, float(statistics.median(d)), len(d)) for c, d in diffs.items()}


def exchange_contrasts(a, b, a_hears_b, b_hears_a, me_hears_a, me_hears_b):
    """Per-exchange rows for contrasts(): B's report of A vs my SNR of A, and
    A's report of B vs my SNR of B."""
    return [(b, b_hears_a, me_hears_a), (a, a_hears_b, me_hears_b)]
