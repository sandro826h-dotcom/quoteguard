import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_label_control import prepare,digest
from revise_label_output_contract import revise,CONTRACT
from label_control_executor import load_plan,configurations,payload_for

def test_contract_revision_preserves_intervention_and_schedule(tmp_path):
    old,requests=prepare(tmp_path/'old',2026100302)
    new,revised=revise(tmp_path/'old',tmp_path/'new')
    assert new['parent_request_plan_sha256']==old['request_plan_sha256']
    for a,b in zip(requests,revised):
        assert b['prompt']==a['prompt']+CONTRACT
        assert {k:v for k,v in a.items() if k not in ('prompt','prompt_sha256')}=={k:v for k,v in b.items() if k not in ('prompt','prompt_sha256')}
    plan,loaded=load_plan(tmp_path/'new')
    assert digest(loaded)==new['request_plan_sha256'] and plan['formal_outcomes_known_at_revision']==0
    for provider,config in configurations(plan).items():
        request=next(r for r in loaded if r['provider']==provider)
        payload=payload_for(request,config)
        assert payload['response_format']=={'type':'json_object'}
        if provider=='kimi':assert 'temperature' not in payload and payload['thinking']=={'type':'disabled'}
