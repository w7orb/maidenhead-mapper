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
    r = bal.solve(pairs)
    mean = sum(x.values()) / 4
    for c, v in x.items():
        assert r[c].balance_db == pytest.approx(v - mean, abs=0.01)
    assert r["A"].group_size == 4
    assert r["A"].n == 2 and r["A"].std_err == pytest.approx(bal.PRIOR_SIGMA_DB / 2 ** 0.5, abs=0.01)  # prior scatter


def test_noise_and_verdicts():
    x = {"BIG": 10, "Q1": 0, "Q2": 0, "Q3": 0, "EARS": -8}
    edges = [(a, b) for a in x for b in x if a < b] * 6
    r = bal.solve(synth(x, edges, noise=3.0))
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


def test_contrasts():
    rows = []
    rows += bal.exchange_contrasts("A", "B", a_hears_b=-5, b_hears_a=-2, me_hears_a=-12, me_hears_b=-9)
    rows += bal.exchange_contrasts("C", "B", a_hears_b=0, b_hears_a=-6, me_hears_a=-10, me_hears_b=None)
    c = bal.contrasts(rows)
    assert c["B"].n == 2 and c["B"].hears_vs_me_db == pytest.approx((10 + 4) / 2)   # B hears better than me
    assert c["A"].hears_vs_me_db == 4 and "C" not in c                            # my SNR of B missing
