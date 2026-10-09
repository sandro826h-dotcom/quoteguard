import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import label_control_executor as ex
from prepare_label_control import prepare

@pytest.fixture
def setup(tmp_path):
    design,planned=prepare(tmp_path/'plan',2026100302)
    configs=ex.configurations(design)
    ledger=ex.Ledger(tmp_path/'ledger',planned,configs,True,'label_control_mock',design['request_plan_sha256'])
    return ledger,design,planned,configs

def test_provider_payloads_follow_distinct_required_modes(setup):
    ledger,_,planned,configs=setup
    for family in ex.PROVIDERS:
        request=next(r for r in planned if r['provider']==family)
        p=ex.payload_for(request,configs[family])
        if family=='kimi':assert 'temperature' not in p and p['thinking']=={'type':'disabled'}
        elif family=='deepseek':assert p['temperature']==.3 and p['thinking']=={'type':'disabled'}
        else:assert p['enable_thinking'] is False and 'thinking' not in p

def test_resume_skips_completed_prefix_and_preserves_first_answers(setup):
    ledger,design,planned,configs=setup
    calls=[];transport=ex.mock_transport(planned)
    def count(r,c):calls.append(r['run_key']);return transport(r,c)
    ex.execute(ledger,count,stop_after=3)
    before=[ex.sha(ledger.result_path(r['run_key'])) for r in ledger.results()]
    resumed=ex.Ledger(ledger.directory,planned,configs,True,'label_control_mock',design['request_plan_sha256'])
    ex.execute(resumed,count,stop_after=5)
    assert len(calls)==len(set(calls))==5 and len(resumed.results())==5
    assert before==[ex.sha(resumed.result_path(r['run_key'])) for r in resumed.results()[:3]]

def test_retry_has_same_payload_and_global_attempt_limit(setup,monkeypatch):
    ledger,_,planned,_=setup;good=ex.mock_transport(planned);calls=[]
    monkeypatch.setattr(ex.time,'sleep',lambda _:None)
    def failed_once(r,c):
        calls.append(ex.digest(ex.payload_for(r,c)))
        if len(calls)==1:return dict(status='http_failure',retryable=True,retry_after=0)
        return good(r,c)
    ex.execute(ledger,failed_once,stop_after=1)
    assert len(calls)==2 and calls[0]==calls[1]
    assert len(ledger.results())==1 and len(ledger.intents())==2
    assert len(ledger.accounting()['unknown_cost_attempts'])==1

def test_abstain_is_retained_and_never_semantically_retried(setup):
    ledger,_,planned,_=setup;calls=[];good=ex.mock_transport(planned)
    def abstain(r,c):
        calls.append(r['run_key']);outcome=good(r,c)
        outcome['content']=json.dumps({'decision':'ABSTAIN','portfolio':[],'justification_summary':'无法选择'})
        return outcome
    ex.execute(ledger,abstain,stop_after=1)
    assert ledger.results()[0]['valid'] and ledger.results()[0]['parsed']['decision']=='ABSTAIN'
    with pytest.raises(ValueError,match='cannot be retried'):ledger.begin(planned[0])
    assert len(calls)==1

def test_http200_bad_json_is_one_completed_observation(setup):
    ledger,_,planned,_=setup;good=ex.mock_transport(planned)
    def invalid(r,c):
        outcome=good(r,c);outcome['content']='not JSON';return outcome
    ex.execute(ledger,invalid,stop_after=1)
    assert not ledger.results()[0]['valid'] and len(ledger.intents())==1

def test_commit_interruption_recovers_without_repeating_request(setup):
    ledger,_,planned,_=setup
    ex.execute(ledger,ex.mock_transport(planned),stop_after=1)
    row=ledger.results()[0];(ledger.directory/'outcomes'/(row['attempt_id']+'.json')).unlink()
    ledger.recover_commits();ledger.validate()
    assert ex.read(ledger.directory/'outcomes'/(row['attempt_id']+'.json'))['recovered_committed_observation']
    with pytest.raises(ValueError):ledger.begin(planned[0])

def test_uncertain_intent_is_reserved_and_only_one_remaining_attempt(setup):
    ledger,_,planned,_=setup
    ledger.begin(planned[0]) # simulate process death before recording an outcome
    ex.execute(ledger,ex.mock_transport(planned),stop_after=1)
    assert len(ledger.intents())==2 and len(ledger.results())==1
    assert len(ledger.accounting()['unknown_cost_attempts'])==1

def test_changed_mode_or_model_cannot_resume_existing_journal(setup):
    ledger,design,planned,configs=setup
    with pytest.raises(ValueError,match='identity mismatch'):
        ex.Ledger(ledger.directory,planned,configs,False,'label_control_real',design['request_plan_sha256'])
    altered=copy.deepcopy(configs);altered['deepseek']['model']='different'
    with pytest.raises(ValueError,match='identity mismatch'):
        ex.Ledger(ledger.directory,planned,altered,True,'label_control_mock',design['request_plan_sha256'])

def test_concurrent_executor_lock_is_refused(setup):
    ledger,*_=setup
    with ex.exclusive(ledger.directory):
        with pytest.raises(ValueError,match='Another executor'):
            with ex.exclusive(ledger.directory):pass

def test_frozen_schedule_cannot_skip_ahead(setup):
    ledger,_,planned,_=setup
    with pytest.raises(ValueError,match='schedule'):ledger.begin(planned[1])

def test_budget_threshold_stops_before_an_attempt(setup,monkeypatch):
    ledger,_,planned,_=setup;monkeypatch.setattr(ex,'CAPS',{'USD':0.,'CNY':0.})
    with pytest.raises(ValueError,match='budget'):ledger.begin(planned[0])
    assert not ledger.intents()

def test_nonretryable_failure_stops_without_new_observation(setup):
    ledger,_,planned,_=setup
    def denied(r,c):return dict(status='http_failure',retryable=False,metadata={'http_status':401})
    with pytest.raises(RuntimeError,match='Transport stopped'):ex.execute(ledger,denied)
    assert len(ledger.intents())==1 and not ledger.results()
    with pytest.raises(ValueError,match='Nonretryable'):ledger.begin(planned[0])

def test_committed_response_tampering_is_refused(setup):
    ledger,_,planned,_=setup;ex.execute(ledger,ex.mock_transport(planned),stop_after=1)
    path=ledger.result_path(planned[0]['run_key']);row=ex.read(path);row['parsed']['decision']='ABSTAIN';ex.atomic(path,row)
    with pytest.raises(ValueError,match='integrity'):ledger.validate()

def test_model_drift_remains_stopped_on_resume(setup):
    ledger,_,planned,_=setup
    ledger.identity['expected_models']={f:'EXPECTED' for f in ex.PROVIDERS}
    with pytest.raises(RuntimeError,match='Returned model drift'):
        ex.execute(ledger,ex.mock_transport(planned),stop_after=1)
    called=[]
    with pytest.raises(RuntimeError,match='drift'):
        ex.execute(ledger,lambda r,c:called.append(r))
    assert not called and len(ledger.results())==1

def test_nonfinite_and_duplicate_product_outputs_are_invalid(setup):
    _,_,planned,_=setup;r=planned[0];key=next(iter(r['alias_to_brand']))
    bad={'decision':'RECOMMEND','portfolio':[{'product_id':key,'weight':float('nan')},{'product_id':key,'weight':.4}],'justification_summary':'x'}
    parsed,valid,errors,_=ex.parse_and_validate(json.dumps(bad),r)
    assert not valid and parsed=={} and 'nonfinite_json' in errors

def test_http_response_and_error_redact_private_values(setup):
    _,_,planned,configs=setup;request=planned[0];secret='PRIVATE_TEST_TOKEN_VALUE'
    transport=ex.HttpTransport({f:secret for f in ex.PROVIDERS})
    class Response:
        status_code=200
        headers={'x-request-id':secret}
        text=json.dumps({'id':'response','model':'MOCK_ONLY','usage':{'prompt_tokens':10,'completion_tokens':20},
                        'choices':[{'message':{'content':secret},'finish_reason':'stop'}]})
        def json(self):return {'error':{'code':secret}}
    class Session:
        def post(self,url,**kwargs):
            assert kwargs['headers']['Authorization']=='Bearer '+secret
            assert kwargs['allow_redirects'] is False
            return Response()
    transport.session=Session();outcome=transport(request,configs[request['provider']])
    assert secret not in json.dumps(outcome)
    assert outcome['status']=='response_received' and outcome['metadata']['reasoning_content_present'] is False
    Response.status_code=401
    transport.last.clear();outcome=transport(request,configs[request['provider']])
    assert secret not in json.dumps(outcome) and outcome['retryable'] is False

def test_missing_received_envelope_is_retained_as_bad_observation(setup):
    ledger,_,planned,configs=setup
    class Response:
        status_code=200;headers={};text='not valid API JSON'
    class Session:
        def post(self,*args,**kwargs):return Response()
    transport=ex.HttpTransport({f:'PRIVATE_TEST_TOKEN_VALUE' for f in ex.PROVIDERS});transport.session=Session()
    ex.execute(ledger,transport,stop_after=1)
    assert len(ledger.intents())==1 and not ledger.results()[0]['valid']
    assert ledger.results()[0]['provider_metadata']['response_envelope_error']
