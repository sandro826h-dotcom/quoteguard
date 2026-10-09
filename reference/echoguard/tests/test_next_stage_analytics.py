from pathlib import Path
import sys
import itertools
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from calibrate_detector import MAP,generate,statistic,persistent_stats,upper_p,wilson,exact_checks
from analyze_cell_attribution import restrict

def test_permutation_vectorization_and_e0_match_scalar_metric():
    assert exact_checks()['scalar_metric_cases_checked']==101

def test_exact_constant_trajectory_upper_tail_is_one_over_144():
    obs=np.zeros((3,3),dtype=int)
    permutations=np.array(list(itertools.product(range(24),repeat=3)))
    values=persistent_stats(obs,permutations)
    assert np.mean(values>=statistic(obs))==pytest.approx(1/144)

def test_mc_upper_tail_includes_ties_and_plus_one():
    assert upper_p(.5,np.array([.25,.5,.5,.75]))==pytest.approx(4/5)

def test_zero_bias_persistent_generation_preserves_three_repeats():
    rng=np.random.default_rng(123)
    samples=np.array([generate(rng,0.,'provider_persistent') for _ in range(200)])
    assert np.array_equal(samples[:,0,:],samples[:,1,:])
    assert np.array_equal(samples[:,1,:],samples[:,2,:])
    assert any(len(set(s[0]))>1 for s in samples)

def test_wilson_interval_retains_uncertainty_at_boundaries():
    assert wilson(0,500)[1]>0
    assert wilson(500,500)[0]<1
    lo,hi=wilson(25,500)
    assert lo<.05<hi

def test_cell_removal_does_not_renormalize_or_impute_empty_portfolio():
    x=restrict([[{'cash':.2,'bond':.8},{'bond':1.}]],{'cash'})
    assert x==[[{'cash':.2},{}]]

def test_full_pair_mapping_is_uniform_over_ordered_two_product_states():
    for state in range(12):
        assert np.array_equal(np.bincount(MAP[:,state],minlength=12),np.full(12,2))
