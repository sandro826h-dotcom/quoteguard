import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def module():
    import sys
    sys.path.insert(0,str(ROOT/'scripts'))
    spec=importlib.util.spec_from_file_location('s4test',ROOT/'scripts/stage4_mechanism.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_factorial_plan_complete_and_masks_have_no_identity_leak():
    m=module();manifest,reqs=m.load_plan()
    assert len(reqs)==1176 and len({r['run_key'] for r in reqs})==1176
    formal=[r for r in reqs if not r['guardrail']]
    assert len(formal)==24*16*3
    for r in formal:
        assert len(r['alias_to_canonical'])==4
        if not r['named']:
            assert '"product_name": null' in r['prompt'] and '"issuer_name": null' in r['prompt']
        if not r['example']: assert '格式示例' not in r['prompt']
        if r['opaque']: assert all(not x.startswith('P') for x in r['order'])

def test_response_mapping_abstain_and_ineligible():
    m=module();_,reqs=m.load_plan();r=next(x for x in reqs if x['opaque'] and not x['guardrail'])
    raw=json.dumps(dict(decision='RECOMMEND',product_id=r['order'][0],justification_summary='x'))
    parsed,valid,_,_=m.validate(raw,r)
    assert valid and parsed['canonical_product_id']==r['alias_to_canonical'][r['order'][0]]
    assert not m.validate(raw,dict(r,expected_eligible=[]))[1]
    assert m.validate('{"decision":"ABSTAIN","product_id":null,"justification_summary":"x"}',r)[1]
    assert not m.validate('{"decision":"ABSTAIN","product_id":"P01","justification_summary":"x"}',r)[1]

def test_examples_counterbalanced():
    from collections import Counter
    m=module();_,reqs=m.load_plan()
    for shelf in ['A','B']:
        r=[x for x in reqs if not x['guardrail'] and x['shelf']==shelf and x['provider']=='qwen' and x['named']==0 and x['opaque']==0 and x['example']==1 and x['independent']==0]
        assert sorted(Counter(x['exemplar'] for x in r).values())==[3,3,3,3]
