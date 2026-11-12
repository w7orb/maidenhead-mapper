import random

import pytest

from maidenhead_mapper.core import balance as bal


def synth(x, edges, noise=0.0, seed=1):
    """Exchanges between stations with true balances x (dict)."""
    rng = random.Random(seed)
    out = []
    for a, b in edges:
        path = rng.uniform(-20, 0)                      # same both ways: cancels
        s_ab = round(path + x[a] + rng.gauss(0, noise))  # A heard at B: depends on x_A ...
        s_ba = round(path + x[b] + rng.gauss(0, noise))  # ... (power + noise floor convention)
        out.append(bal.TwoWay(a, b, a_hears_b=s_ba, b_hears_a=s_ab))
    return out


def test_recovers_balance_exactly_without_noise():
    x = {"A": 6, "B": 0, "C": -3, "D": -3}
    pairs = synth(x, [("A", "B"), ("B", "C"), ("C", "D"), ("A", "C"), ("B", "D")])
    r = bal.solve(pairs, floor_db=None, compress_db=None)   # some synthetic reports sit below -20
    mean = sum(x.values()) / 4
    for c, v in x.items():
        assert r[c].balance_db == pytest.approx(v - mean, abs=0.01)
    assert r["A"].group_size == 4
    assert r["A"].n == 2 and r["A"].std_err == pytest.approx(bal.PRIOR_SIGMA_DB / 2 ** 0.5, abs=0.01)  # prior scatter


def test_noise_and_verdicts():
    x = {"BIG": 10, "Q1": 0, "Q2": 0, "Q3": 0, "EARS": -8}
    edges = [(a, b) for a in x for b in x if a < b] * 6
    # synth() doesn't censor its reports, so none should be treated as bounds
    r = bal.solve(synth(x, edges, noise=3.0), floor_db=None, compress_db=None)
    assert r["BIG"].balance_db - r["EARS"].balance_db == pytest.approx(18, abs=2)
    assert bal.verdict(r["BIG"]) == "loud but deaf" and bal.verdict(r["EARS"]) == "good ears"
    assert bal.verdict(r["Q1"]) == "balanced"
    assert bal.verdict(bal.Balance("X", 9, 1, 0.5, 1, 2)) == "uncertain"      # too few exchanges


def test_separate_groups_are_centred_separately():
    pairs = [bal.TwoWay("A", "B", -10, -4), bal.TwoWay("C", "D", -20, -20)]
    r = bal.solve(pairs)
    assert r["A"].group != r["C"].group
    assert r["A"].balance_db == pytest.approx(3) and r["B"].balance_db == pytest.approx(-3)
    assert r["C"].balance_db == pytest.approx(0)
    # both C-D reports at the floor: bounds point opposite ways, so no information
    assert r["C"].n == 1 and r["C"].n_dropped == 1 and r["C"].std_err is None
    assert bal.verdict(r["C"], min_n=1) == "uncertain"


def test_contrasts():
    rows = []
    rows += bal.exchange_contrasts("A", "B", a_hears_b=-5, b_hears_a=-2, me_hears_a=-12, me_hears_b=-9)
    rows += bal.exchange_contrasts("C", "B", a_hears_b=0, b_hears_a=-6, me_hears_a=-10, me_hears_b=None)
    c = bal.contrasts(rows)
    assert c["B"].n == 2 and c["B"].hears_vs_me_db == pytest.approx((10 + 4) / 2)   # B hears better than me
    assert c["A"].hears_vs_me_db == 4 and "C" not in c                            # my SNR of B missing


PLAIN = {"floor_db": None, "compress_db": None, "robust": False}


def test_pair_bounds():
    assert bal.pair_bound(bal.TwoWay("A", "B", -10, -5)) == 0
    assert bal.pair_bound(bal.TwoWay("A", "B", -10, -20)) == -1     # S(A->B) at floor: diff <= observed
    assert bal.pair_bound(bal.TwoWay("A", "B", -20, -10)) == 1      # S(B->A) at floor: diff >= observed
    assert bal.pair_bound(bal.TwoWay("A", "B", 0, 15)) == 1         # S(A->B) compressed: diff >= observed
    assert bal.pair_bound(bal.TwoWay("A", "B", 16, -22)) == -1      # both say diff <= observed
    assert bal.pair_bound(bal.TwoWay("A", "B", -21, -24)) is None   # opposite bounds
    assert bal.pair_bound(bal.TwoWay("A", "B", -21, -24), floor_db=None) == 0


def censored_synth(x, n, seed, floor=-20):
    """Random exchanges, Gaussian fading, reports below `floor` read as `floor`."""
    rng = random.Random(seed)
    calls, out = list(x), []
    for _ in range(n):
        a, b = rng.sample(calls, 2)
        path = rng.uniform(-28, -5)
        s_ab = round(path + x[a] + rng.gauss(0, 3))
        s_ba = round(path + x[b] + rng.gauss(0, 3))
        out.append(bal.TwoWay(a, b, a_hears_b=max(s_ba, floor), b_hears_a=max(s_ab, floor)))
    return out


def test_censoring_bias_is_corrected():
    x = {"BIG": 9, "A": 3, "B": 0, "C": 0, "D": -3, "EARS": -9}
    mean = sum(x.values()) / len(x)
    pairs = censored_synth(x, 600, seed=7)
    old, new = bal.solve(pairs, **PLAIN), bal.solve(pairs)

    def errors(r):
        return [abs(r[c].balance_db - (x[c] - mean)) for c in x]
    # plain least squares shrinks the spread: floor reports understate the difference
    assert old["BIG"].balance_db - old["EARS"].balance_db < 18 - 6
    assert max(errors(old)) > 4
    # the censoring-aware fit recovers it
    assert new["BIG"].balance_db - new["EARS"].balance_db == pytest.approx(18, abs=2)
    assert max(errors(new)) < 1.2
    assert sum(errors(new)) < 0.35 * sum(errors(old))
    assert new["BIG"].n_censored > 0 and new["BIG"].n_dropped <= new["BIG"].n_censored <= new["BIG"].n


def test_censoring_bias_corrected_on_average():
    import math
    better, ratio = 0, []
    for seed in range(8):
        rng = random.Random(100 + seed)
        x = {f"S{i}": rng.choice([-8, -4, 0, 0, 4, 8]) for i in range(10)}
        mean = sum(x.values()) / len(x)
        pairs = censored_synth(x, 350, seed=seed)

        def rmse(r):
            return math.sqrt(sum((r[c].balance_db - (x[c] - mean)) ** 2 for c in x) / len(x))
        o, n = rmse(bal.solve(pairs, **PLAIN)), rmse(bal.solve(pairs))
        better += n < o
        ratio.append(n / o)
    assert better >= 7 and sum(ratio) / len(ratio) < 0.6


def test_fading_outlier_barely_moves_estimates():
    x = {"A": 4, "B": 0, "C": -2, "D": -2}
    rng = random.Random(3)
    pairs = []
    for a in x:
        for b in x:
            if a < b:
                for _ in range(4):
                    path = rng.uniform(-15, -5)
                    pairs.append(bal.TwoWay(a, b, round(path + x[b] + rng.gauss(0, 1)),
                                            round(path + x[a] + rng.gauss(0, 1))))
    outlier = bal.TwoWay("A", "B", -18, 12)                       # a 30 dB fade (DK3BA saw up to 30 dB)

    def shift(**kw):
        r0, r1 = bal.solve(pairs, **kw), bal.solve(pairs + [outlier], **kw)
        return max(abs(r0[c].balance_db - r1[c].balance_db) for c in x)
    assert shift(**PLAIN) > 1.0
    assert shift() < 0.4


def test_robust_std_err_uses_measured_scatter():
    x = {"BIG": 10, "Q1": 0, "Q2": 0, "Q3": 0, "EARS": -8}
    edges = [(a, b) for a in x for b in x if a < b] * 6
    r = bal.solve(synth(x, edges, noise=3.0), floor_db=None, compress_db=None)
    # dof = 60 - 5 + 1 >= MIN_DOF: sigma from the residuals (difference of two N(0,3): ~4.2), not the prior
    se = r["Q1"].std_err * (r["Q1"].n ** 0.5)
    assert 2.5 < se < 6.5
    few = bal.solve(synth(x, edges[:10], noise=3.0), floor_db=None, compress_db=None)   # dof 6: prior
    # (slightly above PRIOR/sqrt(n) when Huber down-weights an exchange)
    assert few["Q1"].std_err == pytest.approx(bal.PRIOR_SIGMA_DB / few["Q1"].n ** 0.5, abs=0.2)


def _network(seed, sigma=7.0, n_core=30, n_pairs=300, n_single=200):
    """Well-connected core stations, plus stations seen once; difference noise sigma."""
    rng = random.Random(seed)
    x = {f"C{i}": rng.gauss(0, 4) for i in range(n_core)}
    core = list(x)

    def pair(a, b):
        d = x[a] - x[b] + rng.gauss(0, sigma)
        s_ba = round(rng.uniform(-15, -5))
        return bal.TwoWay(a, b, a_hears_b=s_ba, b_hears_a=round(s_ba + d))
    pairs = [pair(*rng.sample(core, 2)) for _ in range(n_pairs)]
    singles = []
    for i in range(n_single):
        x[f"S{i}"] = rng.gauss(0, 4)
        singles.append(pair(rng.choice(core), f"S{i}"))
    return pairs, singles


def _implied_sigma(r):
    import statistics
    return statistics.median(r[c].std_err * r[c].n ** 0.5 for c in r if c.startswith("C"))


def test_std_err_matches_known_scatter():
    # std_err ~ sigma / sqrt(n): the implied sigma is close to the true 7 dB
    for seed in range(3):
        pairs, _ = _network(seed)
        assert 5.8 < _implied_sigma(bal.solve(pairs, floor_db=None, compress_db=None)) < 8.2


def test_single_exchange_stations_do_not_shrink_scatter():
    # stations seen once fit their exchange exactly; their zero residuals must not
    # pull sigma down (they did: ~1.4-2.4 dB instead of 7 before the leverage correction)
    for seed in range(3):
        pairs, singles = _network(seed)
        core = _implied_sigma(bal.solve(pairs, floor_db=None, compress_db=None))
        both = _implied_sigma(bal.solve(pairs + singles, floor_db=None, compress_db=None))
        assert both == pytest.approx(core, rel=0.15) and both > 5.5


def test_star_network_falls_back_to_prior():
    # one hub, everyone else seen once: no residual says anything about the scatter
    rng = random.Random(4)
    pairs = [bal.TwoWay("HUB", f"S{i}", round(rng.uniform(-15, -5)), round(rng.uniform(-15, -5)))
             for i in range(40)]
    r = bal.solve(pairs, floor_db=None, compress_db=None)
    assert r["S3"].std_err == pytest.approx(bal.PRIOR_SIGMA_DB, abs=0.5)
    assert r["HUB"].std_err == pytest.approx(bal.PRIOR_SIGMA_DB / 40 ** 0.5, abs=0.2)


def test_contrasts_skip_censored():
    rows = [("B", -5, -12), ("B", -3, -10), ("B", -2, -21), ("B", 18, 0), ("B", None, -5)]
    c = bal.contrasts(rows)
    assert c["B"].n == 2 and c["B"].n_skipped == 2 and c["B"].hears_vs_me_db == pytest.approx(7)
    c = bal.contrasts(rows, floor_db=None, compress_db=None)
    assert c["B"].n == 4 and c["B"].n_skipped == 0
    assert "Z" not in bal.contrasts([("Z", -22, -5)])               # only censored comparisons
