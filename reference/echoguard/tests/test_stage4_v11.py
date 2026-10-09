import importlib.util,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import stage4_mechanism_v11 as s

def test_v11_frozen_matrix_and_mock_response_mapping():
 m,req=s.load_plan()
 assert len(req)==1896 and m['blocks']==144 and m['factorial_blocks']==24
 assert Counter(r['provider'] for r in req)=={p:632 for p in s.PROVIDERS}
 call=s.mock_transport()
 for r in req:
  response=call(r,{})
  parsed,valid,errors,_=s.validate(response['content'],r)
  assert valid and not errors
  if r['guardrail']:assert parsed['canonical_product_id']==r['expected_choice']

def test_v11_extension_has_complete_neutral_triplets():
 _,req=s.load_plan()
 extra=[r for r in req if r.get('endpoint_extension')]
 assert len(extra)==720
 counts=Counter((r['block'],r['independent']) for r in req if not r['guardrail'] and r['named']==0 and r['opaque']==1 and r['example']==0)
 assert len(counts)==288 and set(counts.values())=={3}
 for r in extra:
  assert (r['named'],r['opaque'],r['example'])==(0,1,0)
  assert set(r['order'])==set(r['alias_to_canonical'])==set(r['expected_eligible'])
  assert 'P01' not in r['prompt'] and '华夏' not in r['prompt']
