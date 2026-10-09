import sys,itertools
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from experimental_persistent_v01 import validate,statistic,maps_for,move,inclusive_p,adjust,evaluate
from calibrate_persistent_extended import self_checks,cases,generate_dataset,cell_ids

def test_generalized_metric_and_cellwise_repeat_invariants():
    assert self_checks()['e0_and_cell_weights_preserved']

def test_by_matches_hand_calculated_harmonic_adjustment():
    # BH(.01,.04,.03) = (.03,.04,.04); H3=11/6.
    assert adjust([.01,.04,.03],'BY')==pytest.approx([.055,11/150,11/150])
    assert adjust([],'BY').tolist()==[]

def test_exact_four_product_constant_orbit():
    x=np.zeros((3,3,4));x[:,:,0]=.6;x[:,:,1]=.4
    permutations=list(itertools.permutations(range(4)))
    maps=np.array(list(itertools.product(permutations,repeat=3)))
    v=statistic(move(x,maps))
    assert np.mean(v>=1.-1e-12)==pytest.approx(1/144)

def test_candidate_never_returns_zero_and_handles_ties_conservatively():
    assert inclusive_p(1.,[0.,.9])==pytest.approx(1/3)
    assert inclusive_p(.5,[.5-1e-13,.5,.7])==1.

@pytest.mark.parametrize('x,cells',[
    (np.zeros((3,3,4)),[[0,1,2,3]]),
    (np.ones((3,3,4)),[[0,1],[1,2,3]]),
    (np.ones((3,3,4)),[[0,1,2]]),
    (np.full((3,3,4),np.nan),[[0,1,2,3]]),
    (-np.ones((3,3,4)),[[0,1,2,3]]),
])
def test_bad_evidence_refused(x,cells):
    with pytest.raises(ValueError):validate(x,cells)

def test_correlated_persona_dgp_copies_provider_trajectories():
    spec=next(s for s in cases() if s['case_id']=='correlated_personas')
    observed=generate_dataset(np.random.default_rng(14),spec)
    assert all(np.array_equal(observed[0],x) for x in observed)

def test_singleton_cells_are_conditioned_not_dropped():
    x=np.ones((3,3,2))*.5
    result=evaluate(x,[[0],[1]],99,np.random.default_rng(15))
    assert result['p']==1. and result['delta']==0.
