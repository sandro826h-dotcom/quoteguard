import importlib.util,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import stage4_mechanism_v12 as s

def test_v12_frozen_matrix_and_mock_response_mapping():
 m,req=s.load_plan()
 assert len(req)==1896 and m['blocks']==144 and m['factorial_blocks']==24
 assert Counter(r['provider'] for r in req)=={p:632 for p in s.PROVIDERS}
 call=s.mock_transport()
 for r in req:
  response=call(r,{})
  parsed,valid,errors,_=s.validate(response['content'],r)
  assert valid and not errors
  if r['guardrail']:assert parsed['canonical_product_id']==r['expected_choice']

def test_v12_extension_has_complete_neutral_triplets():
 _,req=s.load_plan()
 extra=[r for r in req if r.get('endpoint_extension')]
 assert len(extra)==720
 counts=Counter((r['block'],r['independent']) for r in req if not r['guardrail'] and r['named']==0 and r['opaque']==1 and r['example']==0)
 assert len(counts)==288 and set(counts.values())=={3}
 for r in extra:
  assert (r['named'],r['opaque'],r['example'])==(0,1,0)
  assert set(r['order'])==set(r['alias_to_canonical'])==set(r['expected_eligible'])
  assert 'P01' not in r['prompt'] and '华夏' not in r['prompt']


def test_v12_choice_instruction_and_unchanged_design():
 m,req=s.load_plan()
 assert m['schema_version']=='stage4-mechanism-1.2'
 for r in req:
  if r['guardrail']:
   continue
  assert '也允许在缺乏区分依据时弃答' not in r['prompt']
  assert '从合格候选中选择一只产品' in r['prompt'] and '任选其一即可' in r['prompt']
 import importlib
 v11=importlib.import_module('stage4_mechanism_v11')
 _,old=v11.load_plan()
 strip=lambda r:{k:v for k,v in r.items() if k not in ('prompt','prompt_sha256')}
 assert [strip(r) for r in old]==[strip(r) for r in req]
