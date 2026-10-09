import sys,json
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_label_control import prepare,mock_responses,analyze,exact_paired_test,PERSONAS,PROVIDERS

@pytest.fixture
def plan(tmp_path):
    return prepare(tmp_path/'plan',1917)

def test_paired_plan_is_latin_balanced_and_changes_only_labels(plan):
    manifest,requests=plan
    assert len(requests)==72 and len({r['run_key'] for r in requests})==72
    assert manifest['new_api_calls']==0
    for pid in PERSONAS:
        rows=[r for r in requests if r['persona_id']==pid and r['provider']==PROVIDERS[0] and r['view']=='named']
        for brand in range(4):assert sorted(r['brand_order'].index(brand) for r in rows)==[0,1,2,3]
        for provider in PROVIDERS:
            named=[r for r in requests if r['persona_id']==pid and r['provider']==provider and r['view']=='named']
            assert sum(r['slot']==0 for r in named)==2
    for i in range(0,72,2):
        a,b=requests[i:i+2];assert a['pair_id']==b['pair_id']
        assert a['alias_to_brand']==b['alias_to_brand'] and a['brand_order']==b['brand_order']
        shelves=[]
        for r in (a,b):
            shelf=json.loads(r['prompt'].split('\n货架：')[1])
            for p in shelf:p.pop('product_name');p.pop('issuer_label')
            shelves.append(shelf)
        assert shelves[0]==shelves[1]

def test_exact_randomization_identity_outcomes_give_p_one():
    slots=np.zeros((4,3,2,4));slots[:,:,:,0]=.6;slots[:,:,:,1]=.4
    assignment=np.array([[True]*3,[True]*3,[False]*3,[False]*3])
    r=exact_paired_test(slots,assignment)
    assert r['exact_one_sided_p']==1. and r['assignments']==216 and r['delta_named_minus_blind']==0.

def test_extreme_label_effect_is_detectable_under_exact_design():
    # Named outputs all align; blind outputs are disjoint provider preferences.
    assignment=np.array([[True]*3,[True]*3,[False]*3,[False]*3])
    slots=np.zeros((4,3,2,4))
    for r in range(4):
        for m in range(3):
            slots[r,m,int(not assignment[r,m]),0]=.6
            slots[r,m,int(not assignment[r,m]),1]=.4
            slots[r,m,int(assignment[r,m]),(m+1)%4]=.6
            slots[r,m,int(assignment[r,m]),(m+2)%4]=.4
    result=exact_paired_test(slots,assignment)
    assert 0<result['exact_one_sided_p']<.05

def test_mock_and_real_cannot_mix_and_duplicates_refused(plan):
    manifest,requests=plan;responses=mock_responses(requests,1)
    with pytest.raises(ValueError,match='Duplicate'):analyze(manifest,requests,responses+responses[:1])
    responses[0]['is_mock']=False
    with pytest.raises(ValueError,match='mix'):analyze(manifest,requests,responses)

def test_abstain_and_missing_are_insufficient_without_family_shrinkage(plan):
    manifest,requests=plan;responses=mock_responses(requests,2)
    responses[0]['parsed']={'decision':'ABSTAIN','portfolio':[]}
    result=analyze(manifest,requests,responses[1:])
    assert result['planned_family_size']==3
    affected=next(r for r in result['comparisons'] if r['persona_id']==requests[0]['persona_id'])
    assert affected['status']=='INSUFFICIENT_EVIDENCE' and affected['BY_q']==1.

def test_altered_prompt_or_response_provenance_is_refused(plan):
    manifest,requests=plan;responses=mock_responses(requests,3)
    responses[0]['prompt_sha256']='bad'
    with pytest.raises(ValueError,match='provenance'):analyze(manifest,requests,responses)
    requests[0]['prompt']+='changed'
    with pytest.raises(ValueError,match='plan hash'):analyze(manifest,requests,responses)
