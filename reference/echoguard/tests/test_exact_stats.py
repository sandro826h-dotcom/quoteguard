"""Analysis v2: deterministic statistics, exact ties and hash-seed independence."""
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import subprocess
import sys

import numpy as np
import pytest

from echoguard.exact_stats import (
    as_fraction, bh_exact, by_exact, exact_upper_tail, mean_pairwise_exact,
    repeated_stat_exact, repeated_stat_stable, weighted_jaccard_exact, weighted_jaccard_stable,
)
from echoguard.metrics import benjamini_hochberg
from echoguard.null_model_v2 import independent_repeat_null, persistent_cell_null
from echoguard.schemas import Product

ROOT = Path(__file__).resolve().parents[1]


def product(pid, cell):
    return Product(pid, pid, pid, "issuer-" + pid, "cash", "mm", cell, "R1", 1, "", "daily", 5, 0,
                   True, False, "", "", "test")


def test_as_fraction_recovers_json_decimal():
    assert as_fraction(json.loads("0.6")) == Fraction(3, 5)
    assert as_fraction(0.1) + as_fraction(0.2) == Fraction(3, 10)
    with pytest.raises(ValueError):
        as_fraction(float("nan"))


def test_stable_weighted_jaccard_is_order_independent_and_matches_exact():
    rng = random.Random(7)
    for _ in range(200):
        keys = [f"P{i:02d}" for i in range(rng.randint(2, 8))]
        a = {k: round(rng.random(), 3) for k in rng.sample(keys, rng.randint(1, len(keys)))}
        b = {k: round(rng.random(), 3) for k in rng.sample(keys, rng.randint(1, len(keys)))}
        a_rev = dict(reversed(list(a.items())))
        b_rev = dict(reversed(list(b.items())))
        assert weighted_jaccard_stable(a, b) == weighted_jaccard_stable(a_rev, b_rev)
        assert abs(weighted_jaccard_stable(a, b) - float(weighted_jaccard_exact(a, b))) < 1e-15


def test_theoretical_tie_that_float_sums_can_split():
    # 0.1 + 0.2 != 0.3 in floats; exact arithmetic treats these overlaps as equal.
    a = {"P1": 0.1, "P2": 0.2}
    b = {"P1": 0.1, "P2": 0.2}
    c = {"P3": 0.3}
    assert weighted_jaccard_exact(a, b) == 1
    assert mean_pairwise_exact([a, b]) == mean_pairwise_exact([c, c])


def test_exact_upper_tail_counts_ties_exactly():
    observed = Fraction(1, 3)
    values = np.array([1 / 3, 1 / 3 - 1e-16, 1 / 3 + 1e-16, 0.2, 0.9])
    exact = [Fraction(1, 3), Fraction(1, 3), Fraction(1, 3), Fraction(1, 5), Fraction(9, 10)]
    out = exact_upper_tail(observed, values, lambda i: exact[i])
    assert out["tail_count"] == 4
    assert out["exact_tie_count"] == 3
    assert out["p_exact"] == Fraction(5, 6)
    # A raw float comparison would drop the tie that landed one ulp below.
    assert out["p_raw_float"] < float(out["p_exact"])


def test_bh_and_by_exact_match_float_reference():
    rng = random.Random(3)
    for m in (1, 3, 9):
        ps = [Fraction(rng.randint(1, 1000), 1001) for _ in range(m)]
        ref = benjamini_hochberg([float(p) for p in ps])
        assert np.allclose([float(q) for q in bh_exact(ps)], ref, atol=1e-12)
        harmonic = sum(1 / i for i in range(1, m + 1))
        assert np.allclose([float(q) for q in by_exact(ps)], np.minimum(np.array(ref) * harmonic, 1), atol=1e-12)


def test_degenerate_null_is_exactly_one():
    # Every eligible structure is forced: the null equals the observed value in every draw.
    products = [product("P1", "C"), product("P2", "C")]
    groups = [[{"P1": 0.6, "P2": 0.4}, {"P1": 0.6, "P2": 0.4}, {"P1": 0.4, "P2": 0.6}] for _ in range(3)]
    res = independent_repeat_null(groups, products, {"P1", "P2"}, 200, 11)
    assert res["exact_tie_count"] >= 1
    assert res["p_exact"] <= 1
    res2 = independent_repeat_null([[{"P1": 1.0}, {"P1": 1.0}]], [product("P1", "C")], {"P1"}, 100, 5)
    assert res2["p_exact"] == 1 and res2["exact_tie_count"] == 100


def test_independent_null_uses_same_draws_as_v1():
    from echoguard.null_model import simulate_repeated_k_model_null
    products = [product(f"P{i}", "C" if i < 4 else "D") for i in range(1, 7)]
    groups = [[{"P1": 0.6, "P2": 0.4}, {"P2": 0.5, "P5": 0.5}, {"P3": 0.7, "P6": 0.3}],
              [{"P1": 0.6, "P3": 0.4}, {"P2": 0.5, "P5": 0.5}, {"P4": 0.7, "P5": 0.3}]]
    v1 = simulate_repeated_k_model_null(groups, products, {p.product_id for p in products}, 300, 99)
    v2 = independent_repeat_null(groups, products, {p.product_id for p in products}, 300, 99)
    # Same random portfolios: values agree to rounding, so the tail differs only through ties.
    obs = float(repeated_stat_exact(groups))
    assert abs(v2["null_mean"] - float(np.mean(v1))) < 1e-12
    assert v2["tail_count"] >= int(np.count_nonzero(v1 > obs + 1e-9))


def test_persistent_null_matches_frozen_candidate_draws():
    sys.path.insert(0, str(ROOT / "scripts"))
    import experimental_persistent_v01 as cand
    rng = np.random.default_rng(5)
    x = np.zeros((3, 3, 6))
    for r in range(3):
        for p in range(3):
            chosen = rng.choice(6, size=2, replace=False)
            x[r, p, chosen] = [0.6, 0.4]
    cells = [[0, 1, 2, 3], [4, 5]]
    ref = cand.evaluate(x, cells, 2000, np.random.default_rng(42))
    new = persistent_cell_null(x.tolist(), cells, 2000, 42)
    assert abs(new["null_mean"] - ref["null_mean"]) < 1e-12
    assert abs(float(new["p_exact"]) - ref["p"]) < 1e-12


@pytest.mark.parametrize("seeds", [("0", "1", "2", "4242")])
def test_v2_statistics_do_not_depend_on_python_hash_seed(seeds):
    code = (
        "import json,sys; sys.path.insert(0, %r);"
        "from echoguard.null_model_v2 import independent_repeat_null;"
        "from echoguard.schemas import Product;"
        "P=lambda i,c: Product(i,i,i,'x','cash','mm',c,'R1',1,'','d',5,0,True,False,'','','t');"
        "prods=[P('P%%d'%%k,'C' if k<5 else 'D') for k in range(1,9)];"
        "g=[[{'P1':0.6,'P2':0.4},{'P1':0.6,'P2':0.4},{'P3':0.6,'P5':0.4}],[{'P2':0.6,'P1':0.4},{'P4':0.6,'P6':0.4},{'P1':0.6,'P2':0.4}]];"
        "r=independent_repeat_null(g,prods,{p.product_id for p in prods},500,7);"
        "print(json.dumps([str(r['p_exact']),r['exact_tie_count'],repr(r['null_mean'])]))"
    ) % str(ROOT / "src")
    outs = set()
    for s in seeds:
        env = dict(os.environ, PYTHONHASHSEED=s, PYTHONDONTWRITEBYTECODE="1")
        outs.add(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout)
    assert len(outs) == 1
