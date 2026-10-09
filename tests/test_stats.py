import numpy as np
import pytest
from scipy.stats import binomtest

from quoteguard.stats import (bootstrap_ci, holm, mcnemar_exact, paired_permutation,
                              stratified_permutation)


@pytest.mark.parametrize("b,c", [(0, 0), (3, 3), (0, 10), (7, 2), (15, 30), (100, 80)])
def test_mcnemar_matches_binomtest(b, c):
    n = b + c
    expect = 1.0 if n == 0 else binomtest(min(b, c), n, 0.5).pvalue
    assert mcnemar_exact(b, c) == pytest.approx(expect, abs=1e-12)


def test_mcnemar_negative():
    with pytest.raises(ValueError):
        mcnemar_exact(-1, 2)


def test_permutation_exact_small():
    # diffs 1,1,1: only all-positive (sum 3) and all-negative reach |3|; 2 of 8
    r = paired_permutation([1, 1, 1], alternative="two-sided")
    assert r["method"] == "exact" and r["p"] == pytest.approx(2 / 8)
    assert paired_permutation([1, 1, 1], alternative="greater")["p"] == pytest.approx(1 / 8)
    assert paired_permutation([1, 1, 1], alternative="less")["p"] == pytest.approx(1.0)
    assert r["n"] == 3 and r["mean_difference"] == 1.0


def test_permutation_exact_mixed_and_none():
    # diffs 2,1,-1: sums over 8 sign patterns: 4,2,0,-2 ... obs=2; |s|>=2 counts
    r = paired_permutation([2, 1, -1, None], alternative="two-sided")
    assert r["n"] == 3
    sums = [a * 2 + b * 1 + c * -1 for a in (1, -1) for b in (1, -1) for c in (1, -1)]
    assert r["p"] == pytest.approx(sum(abs(s) >= 2 for s in sums) / 8)


def test_permutation_empty():
    assert paired_permutation([])["p"] is None


def test_permutation_monte_carlo_reproducible_and_sensible():
    d = [1.0] * 30
    a = paired_permutation(d, sims=2000, seed=3, alternative="greater")
    b = paired_permutation(d, sims=2000, seed=3, alternative="greater")
    assert a == b and a["method"] == "monte_carlo"
    assert a["p"] < 0.01
    assert paired_permutation(np.zeros(30), sims=500, seed=1)["p"] == 1.0


def test_stratified_equals_pooled_exact():
    pairs = {"A": [(1, 0), (1, 0)], "B": [(1, 0)]}
    r = stratified_permutation(pairs, alternative="greater")
    assert r["method"] == "exact" and r["p"] == pytest.approx(1 / 8)
    assert r["strata"]["A"]["mean_difference"] == 1.0 and r["n"] == 3


def test_stratified_mc_reproducible():
    rng = np.random.default_rng(0)
    pairs = {f"s{i}": [(float(x), 0.0) for x in rng.random(10) + 0.2] for i in range(4)}
    a = stratified_permutation(pairs, sims=3000, seed=5)
    assert a == stratified_permutation(pairs, sims=3000, seed=5)
    assert a["method"] == "monte_carlo" and a["p"] < 0.01


def test_bootstrap_reproducible_and_covers():
    vals = list(np.random.default_rng(1).normal(5, 1, 200))
    a = bootstrap_ci(vals, B=999, seed=9)
    assert a == bootstrap_ci(vals, B=999, seed=9)
    assert a["lo"] < a["estimate"] < a["hi"]
    assert a["lo"] < 5 < a["hi"]
    m = bootstrap_ci([1, 2, 3, 4, 100], stat=np.median, B=200, seed=1)
    assert m["estimate"] == 3
    assert bootstrap_ci([])["lo"] is None


def test_holm_known():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    assert holm({"x": 0.5}) == {"x": 0.5}
    assert holm({"a": 0.6, "b": 0.7})["a"] == 1.0


# ---------------------------------------------------------------- additions
import math

from quoteguard.stats import (_tango_restricted, cluster_bootstrap, generic_stratified_permutation,
                              noninferiority_bootstrap_lower, tango_noninferiority)


def test_mcnemar_large_counts_no_overflow():
    p = mcnemar_exact(600, 500)
    assert p == pytest.approx(binomtest(500, 1100, 0.5).pvalue) and 0 < p < 0.05


def test_exact_enumeration_n20_memory():
    import tracemalloc
    tracemalloc.start()
    r = paired_permutation([1.0] * 20, alternative="greater")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert r["method"] == "exact" and r["p"] == pytest.approx(1 / 2 ** 20)
    assert peak < 100e6


def test_generic_matches_stratified_for_mean_difference():
    pairs = [(1.0, 0.0), (2.0, 0.5), (0.0, 1.0), (3.0, 1.0), (1.5, 1.5)]
    units = [{"A": a, "B": b} for a, b in pairs]

    def stat(us):
        return float(np.mean([u["A"] - u["B"] for u in us]))
    g = generic_stratified_permutation(units, stat)
    s = stratified_permutation({"x": pairs[:2], "y": pairs[2:]})
    assert g["method"] == "exact" and g["p"] == pytest.approx(s["p"])
    for alt in ("greater", "less"):
        assert generic_stratified_permutation(units, stat, alternative=alt)["p"] == pytest.approx(
            stratified_permutation({"x": pairs}, alternative=alt)["p"])


def test_generic_micro_f1_known_small_example():
    # unit records are (tp, fp, fn); two units, so 4 label patterns
    units = [{"A": (8, 2, 2), "B": (4, 4, 6)}, {"A": (3, 1, 1), "B": (3, 1, 1)}]

    def f1(recs):
        tp, fp, fn = (sum(r[i] for r in recs) for i in range(3))
        return 2 * tp / (2 * tp + fp + fn)

    def stat(us):
        return f1([u["A"] for u in us]) - f1([u["B"] for u in us])
    patterns = [stat([{"A": u["B"] if f else u["A"], "B": u["A"] if f else u["B"]}
                      for u, f in zip(units, fl)]) for fl in [(0, 0), (0, 1), (1, 0), (1, 1)]]
    obs = patterns[0]
    expect = sum(abs(v) >= abs(obs) - 1e-12 for v in patterns) / 4
    r = generic_stratified_permutation(units, stat)
    assert r["p"] == pytest.approx(expect) and r["observed"] == pytest.approx(obs)
    assert generic_stratified_permutation(units, stat, alternative="greater")["p"] == pytest.approx(
        sum(v >= obs - 1e-12 for v in patterns) / 4)


def test_generic_monte_carlo_branch_reproducible():
    units = [{"A": 1.0, "B": 0.0} for _ in range(18)]
    stat = lambda us: float(np.mean([u["A"] - u["B"] for u in us]))
    a = generic_stratified_permutation(units, stat, sims=500, seed=2, alternative="greater")
    assert a == generic_stratified_permutation(units, stat, sims=500, seed=2, alternative="greater")
    assert a["method"] == "monte_carlo" and a["p"] < 0.02


def test_tango_delta0_equals_mcnemar_score():
    for b, c, n in [(20, 10, 100), (5, 9, 60), (0, 7, 30)]:
        r = tango_noninferiority(b, c, n, 0.0)
        assert r["statistic"] == pytest.approx((b - c) / math.sqrt(b + c))
        assert r["p"] == pytest.approx(0.5 * math.erfc(r["statistic"] / math.sqrt(2)))


def test_tango_restricted_mle_matches_numeric():
    from scipy.optimize import minimize_scalar
    for b, c, n, d in [(20, 10, 100, 0.05), (8, 12, 80, 0.10), (3, 14, 50, 0.05), (30, 30, 200, 0.02)]:
        th = -d
        m = n - b - c

        def nll(u):
            q = u + th
            return -(b * math.log(q) + c * math.log(u) + m * math.log(1 - u - q))
        res = minimize_scalar(nll, bounds=(max(0, -th) + 1e-9, (1 - th) / 2 - 1e-9), method="bounded",
                              options={"xatol": 1e-12})
        assert _tango_restricted(b, c, n, th) == pytest.approx(res.x, abs=1e-6)


def test_tango_type_one_error_at_boundary():
    rng = np.random.default_rng(123)
    n, delta = 300, 0.05
    p01, p10 = 0.15, 0.10  # p_new - p_ref = -delta exactly
    probs = [p10, p01, 1 - p10 - p01]
    rejects = 0
    for _ in range(2000):
        b, c, _ = rng.multinomial(n, probs)
        rejects += tango_noninferiority(int(b), int(c), n, delta)["p"] < 0.05
    assert 0.03 < rejects / 2000 < 0.07


def test_tango_validation():
    with pytest.raises(ValueError):
        tango_noninferiority(5, 5, 8, 0.05)


def test_cluster_bootstrap_resamples_whole_clusters():
    recs = [{"task": t, "x": float(t)} for t in range(10) for _ in range(3)]
    stat = lambda rs: float(np.mean([r["x"] for r in rs]))
    a = cluster_bootstrap(recs, "task", stat, B=300, seed=4)
    assert a == cluster_bootstrap(recs, lambda r: r["task"], stat, B=300, seed=4)
    assert a["n_clusters"] == 10 and a["lo"] < 4.5 < a["hi"]
    # constant within a cluster: every draw has size 3 * 10 records, checked via a count statistic
    cnt = cluster_bootstrap(recs, "task", lambda rs: float(len(rs)), B=50, seed=1)
    assert cnt["lo"] == cnt["hi"] == 30.0
    uneven = [{"task": 0, "x": 0}] + [{"task": 1, "x": 0}] * 4
    assert cluster_bootstrap(uneven, "task", lambda rs: float(len(rs)), B=200, seed=1)["hi"] > 5
    assert cluster_bootstrap([], "task", stat)["lo"] is None


def test_noninferiority_bootstrap_lower():
    rng = np.random.default_rng(0)
    clusters = {f"t{i}": [(int(rng.random() < 0.8), int(rng.random() < 0.8)) for _ in range(5)] for i in range(40)}
    r = noninferiority_bootstrap_lower(clusters, B=500, seed=3)
    assert r == noninferiority_bootstrap_lower(clusters, B=500, seed=3)
    assert r["lower"] < r["estimate"] and r["n_clusters"] == 40
    assert noninferiority_bootstrap_lower({})["lower"] is None
