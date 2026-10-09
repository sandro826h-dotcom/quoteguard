"""Frozen factorial shortlist benchmark (v1.2: choice among eligible candidates; see prepare_stage4_v12.py). No paid request in prepare/mock/analyze.

python scripts/stage4_mechanism_v12.py prepare
python scripts/stage4_mechanism_v12.py mock
python scripts/stage4_mechanism_v12.py live
python scripts/stage4_mechanism_v12.py analyze --batch runs/stage4-live-20261006-v12/batch
"""
from __future__ import annotations
import argparse, csv, hashlib, itertools, json, math, random, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import stage3_executor as old
from echoguard.plan_validation import validate_observations
from echoguard.position_test import holm

PLAN = ROOT/'runs/stage4-plan-20261006-v12'
VERSION = 'stage4-mechanism-1.2'
CAPS = {'USD':3.0, 'CNY':20.0}
PROVIDERS = old.PROVIDERS

def seed(x): return int(hashlib.sha256(str(x).encode()).hexdigest()[:16],16)
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def lines(p): return [json.loads(x) for x in Path(p).read_text(encoding='utf-8').splitlines() if x.strip()]
def write(p,x):
    Path(p).parent.mkdir(parents=True,exist_ok=True)
    old.atomic(p,x)

def source_paths():
    return ['scripts/stage4_mechanism_v12.py','scripts/stage3_executor.py','src/echoguard/plan_validation.py',
            'data/products_pilot_v0.2.csv','data/stage3/products_shelfB_v1.0.csv']

def prepare():
    if PLAN.exists(): raise ValueError('Frozen plan exists; use it unchanged or create a new version')
    allreq=[]
    for shelf, rel in [('A','data/products_pilot_v0.2.csv'),('B','data/stage3/products_shelfB_v1.0.csv')]:
        with (ROOT/rel).open(encoding='utf-8-sig') as f:
            products=[x for x in csv.DictReader(f) if x['economic_cell']=='CASH_MM']
        assert len(products)==4
        ids=[p['product_id'] for p in products]
        for j in range(12):
            block=f'{shelf}{j+1:02d}'
            focal=ids[0]
            exemplar=ids[j%4]
            shared=random.Random(seed(block+'order')).sample(ids,4)
            for provider in PROVIDERS:
                independent=random.Random(seed(block+provider+'order')).sample(ids,4)
                aliases=random.Random(seed(block+provider+'alias')).sample(['K7F9','M2R8','V6C3','H8T4'],4)
                arms=list(itertools.product([0,1],repeat=4))
                random.Random(seed(block+provider+'schedule')).shuffle(arms)
                for named,opaque,example,indep in arms:
                    arm=f'N{named}I{opaque}X{example}O{indep}'
                    mapping=dict(zip(ids,aliases if opaque else ids))
                    order=independent if indep else shared
                    shown=[]
                    for pid in order:
                        p=next(x for x in products if x['product_id']==pid)
                        shown.append(dict(product_id=mapping[pid],product_name=p['product_name'] if named else None,
                            issuer_name=p['issuer_name'] if named else None,asset_class='cash',
                            normalized_risk_score=1,liquidity_score=3,annual_fee_pct=0.30,eligible=True))
                    schema='字段类型：decision 为 RECOMMEND 或 ABSTAIN；product_id 为货架编号字符串或 null；justification_summary 为简短字符串。'
                    if example:
                        schema += '\n格式示例（字段结构示意）：'+json.dumps(dict(decision='RECOMMEND',product_id=mapping[exemplar],justification_summary='满足输入约束'),ensure_ascii=False)
                    prompt=('封闭式候选产品筛选实验。名称与管理人标签取自公开基金，其他字段是受控实验设定，不能视为真实产品比较或投资建议。'
                        '仅按本次字段从合格候选中选择一只产品，所有候选经济条件相同，任选其一即可。只有没有合格候选时才返回 ABSTAIN。'
                        '不使用记忆补全产品信息；不输出资金配置。只输出合法 JSON。没有合格候选必须 ABSTAIN。'
                        '有明确费用差别时从合格候选中选年费率最低者。\n'
                        '客户：'+json.dumps(dict(risk_capacity=['C1','C2','C3'][j%3],horizon='short',liquidity_need='high',budget_cny=[30000,100000,500000][j%3]),ensure_ascii=False)+
                        '\n货架：'+json.dumps(shown,ensure_ascii=False)+'\n'+schema)
                    allreq.append(dict(run_key=block+'__'+provider+'__'+arm,experiment=arm,kind='gate1',provider=provider,
                        persona_id=block,repeat=1,view='named' if named else 'masked',order=[mapping[x] for x in order],
                        prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),shelf=shelf,
                        named=named,opaque=opaque,example=example,independent=indep,block=block,
                        alias_to_canonical={v:k for k,v in mapping.items()},focal=focal,exemplar=exemplar,
                        expected_eligible=list(mapping.values()),guardrail=None))
    # Pre-live power amendment: 120 additional blocks for the order endpoint only.
    # No new observed outcomes informed this amendment. All other factorial contrasts retain 24 blocks.
    factorial=list(allreq)
    for shelf in ['A','B']:
        for j in range(12,72):
            block=f'{shelf}{j+1:02d}'
            for provider in PROVIDERS:
                base=next(r for r in factorial if r['shelf']==shelf and r['provider']==provider
                          and r['block']==f'{shelf}{j%12+1:02d}' and r['named']==0 and r['opaque']==1 and r['example']==0 and r['independent']==0)
                canonical=list(base['alias_to_canonical'].values())
                mapping=dict(zip(canonical,random.Random(seed(block+provider+'alias')).sample(['K7F9','M2R8','V6C3','H8T4'],4)))
                prefix,tail=base['prompt'].split('\n货架：',1)
                oldshown,schema=tail.split('\n字段类型：',1)
                shown_by={base['alias_to_canonical'][p['product_id']]:p for p in json.loads(oldshown)}
                for indep in [0,1]:
                    order=random.Random(seed(block+(provider if indep else '')+'order')).sample(canonical,4)
                    shown=[dict(shown_by[pid],product_id=mapping[pid]) for pid in order]
                    prompt=prefix+'\n货架：'+json.dumps(shown,ensure_ascii=False)+'\n字段类型：'+schema
                    arm=f'N0I1X0O{indep}'
                    r=dict(base,run_key=block+'__'+provider+'__'+arm,experiment=arm,block=block,persona_id=block,
                        independent=indep,order=[mapping[x] for x in order],prompt=prompt,
                        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                        alias_to_canonical={v:k for k,v in mapping.items()},expected_eligible=list(mapping.values()),endpoint_extension=True)
                    allreq.append(r)
    # Eight independent engineering guards, excluded from inferential endpoints.
    for j in range(8):
        for provider in PROVIDERS:
            base=dict(next(r for r in allreq if r['provider']==provider and r['named']==0 and r['opaque']==1 and r['example']==0))
            ids=list(base['alias_to_canonical'])
            shown=[dict(product_id=x,eligible=j>=4,annual_fee_pct=.1 if x==ids[j%4] else .5) for x in ids]
            prompt=('受控候选筛选，只选合格候选中年费率最低的一只；全部不合格则弃答。只输出 JSON，字段 decision（RECOMMEND 或 ABSTAIN）、product_id（字符串或 null）、justification_summary。\n货架：'+json.dumps(shown))
            base.update(run_key=f'G{j}__{provider}',experiment='GUARD',persona_id=f'G{j}',block=f'G{j}',
                prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),order=ids,
                expected_eligible=ids if j>=4 else [],guardrail='abstain' if j<4 else 'lowest_fee',
                expected_choice=None if j<4 else base['alias_to_canonical'][ids[j%4]])
            allreq.append(base)
    reference=read(ROOT/'runs/stage3-plan-20261005/design_manifest.json')
    manifest={k:reference[k] for k in ['models','gate1_requests','gate1_transport','label_amendments']}
    manifest.update(schema_version=VERSION,created_at_utc=old.now(),status='frozen_before_live',requests=len(allreq),
        blocks=144,factorial_blocks=24,benchmark='two controlled four-option cash shelves; 12 factorial blocks plus 60 order-endpoint blocks per shelf; 144 randomized benchmark blocks, not sampled customers',
        source_sha256={p:old.sha(ROOT/p) for p in source_paths()},request_plan_sha256=old.digest(allreq),
        caps=CAPS,rates=old.RATES,primary=['example_copying','identity_focal_A','id_focal_A','shared_vs_independent_all3'],
        analysis='Two-sided paired sign-flip Monte Carlo, 99999 draws plus one; four tests Holm; block bootstrap CI 9999, descriptive; no outcome-driven additions',
        missing='Keep all scheduled requests; primary inference unavailable if any received result invalid or any request missing. ABSTAIN is a separate state and is never counted as agreement.',
        completion='All 1896 scheduled observations including 24 engineering guardrails; stop only engineering budget/provider errors, never significance.')
    PLAN.mkdir(parents=True)
    (PLAN/'requests.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in allreq),encoding='utf-8')
    write(PLAN/'design_manifest.json',manifest)
    print(json.dumps({'requests':len(allreq),'per_provider':len(allreq)//3,'plan_hash':manifest['request_plan_sha256'],'caps':CAPS}))

def load_plan():
    m=read(PLAN/'design_manifest.json'); reqs=lines(PLAN/'requests.jsonl')
    if m['schema_version']!=VERSION or old.digest(reqs)!=m['request_plan_sha256']: raise ValueError('Plan hash mismatch')
    for p,h in m['source_sha256'].items():
        if old.sha(ROOT/p)!=h: raise ValueError('Frozen implementation/input changed: '+p)
    validate_observations(reqs,[],require_complete=False)
    return m,reqs

def validate(text, request):
    try:
        p=old.extract_json(text)
        if not isinstance(p,dict) or p.get('decision') not in ['RECOMMEND','ABSTAIN'] or not isinstance(p.get('justification_summary'),str):
            raise ValueError('Wrong response schema')
        choice=p.get('product_id')
        if p['decision']=='ABSTAIN':
            if choice is not None: raise ValueError('ABSTAIN requires null product_id')
            p['canonical_product_id']=None
        else:
            if choice not in request['expected_eligible']: raise ValueError('Unknown or ineligible candidate')
            p['canonical_product_id']=request['alias_to_canonical'][choice]
        return p,True,[],None
    except (ValueError,TypeError,KeyError): return {},False,['invalid_candidate_response'],'validation'

def mock_transport():
    def call(req,cfg):
        eligible=req['expected_eligible']
        choice=eligible[0] if eligible else None
        if req.get('expected_choice'):
            choice=next(a for a,c in req['alias_to_canonical'].items() if c==req['expected_choice'])
        p=dict(decision='RECOMMEND' if choice else 'ABSTAIN',product_id=choice,justification_summary='MOCK ONLY')
        return dict(status='response_received',retryable=False,content=json.dumps(p),raw_api_body=None,
            metadata=dict(returned_model='MOCK_ONLY',response_id='MOCK__'+req['run_key'],usage={'prompt_tokens':900,'completion_tokens':70},finish_reason='stop',latency_ms=0))
    return call

def run(mode, interval, yes=False, out=None):
    m,reqs=load_plan()
    old.VERSION=VERSION; old.CAPS=dict(CAPS); old.validate_response=validate
    cfg,intervals=old.configurations(m,interval)
    for c in cfg.values(): c['max_tokens']=400
    out=out or ROOT/f'runs/stage4-{mode}-20261006-v12'
    out.mkdir(parents=True,exist_ok=True)
    exps=sorted({r['experiment'] for r in reqs})
    if mode=='mock':
        ledger=old.Ledger(out/'batch',reqs,cfg,True,m['request_plan_sha256'],label=VERSION)
        status,complete=old.execute(ledger,mock_transport(),exps,verbose=False)
        print(json.dumps({'mode':'mock','complete':complete,'observations':len(ledger.results()),'status':status})); return
    # All fees require an explicit RUN before even the smoke calls.
    print('1896 formal calls + 3 smoke; indicative historical-rate estimate USD 2 + CNY 13; total engineering cap USD 3 / CNY 20.')
    print(f'Kimi scheduling floor {633*interval/60:.0f} minutes, plus latency/retries. Prices are estimates; check your provider billing console.')
    if not yes and input('Type RUN to authorize smoke and formal calls within these caps: ').strip()!='RUN': return
    keys=old.read_keys(); transport=old.HttpTransport(keys,intervals)
    try:
        smokeplan=[]
        for p in PROVIDERS:
            r=dict(next(x for x in reqs if x['provider']==p)); r.update(run_key='SMOKE__'+p,experiment='SMOKE'); smokeplan.append(r)
        smoke=old.Ledger(out/'smoke',smokeplan,cfg,False,m['request_plan_sha256'],label=VERSION+'_smoke')
        old.execute(smoke,transport,['SMOKE'])
        sm=smoke.results()
        if len(sm)!=3 or any(not r['valid'] or not r['actual_model_id'] for r in sm): raise ValueError('Smoke incomplete; formal calls not started')
        models={r['provider']:r['actual_model_id'] for r in sm}
        if models!=m['models']: raise ValueError('Returned model changed from frozen protocol; retain smoke and create a new protocol version')
        spent=smoke.accounting()['charged_usage_or_reserved']
        old.CAPS={c:CAPS[c]-spent[c] for c in CAPS}
        ledger=old.Ledger(out/'batch',reqs,cfg,False,m['request_plan_sha256'],expected_models=models,label=VERSION)
        status,complete=old.execute(ledger,transport,exps,expected_models=models)
        write(out/'cost_report.json',{'smoke':smoke.accounting(),'formal':ledger.accounting(),'global_caps':CAPS,'finished_at_utc':old.now()})
        print(json.dumps({'complete':complete,'observations':len(ledger.results()),'status':status}))
    finally: keys.clear(); transport.close()

def paired_stats(diffs,label):
    a=np.asarray(diffs,float); rng=np.random.default_rng(seed(label))
    actual=float(a.mean())
    # Monte Carlo sign flips at randomized benchmark block; no response-level independence assumption.
    null=(rng.integers(0,2,(99999,len(a)))*2-1)@a/len(a)
    p=(1+int(np.count_nonzero(np.abs(null)>=abs(actual)-1e-12)))/100000
    boots=a[rng.integers(0,len(a),(9999,len(a)))].mean(axis=1)
    return dict(blocks=len(a),difference=actual,p=p,bootstrap95=list(map(float,np.quantile(boots,[.025,.975]))),block_differences=list(map(float,a)))

def analyze(batch):
    m,reqs=load_plan(); obs=lines(batch/'runs.jsonl'); manifest=read(batch/'manifest.json')
    matrix=validate_observations(reqs,obs,require_complete=False)
    if manifest.get('plan_hash')!=m['request_plan_sha256']: raise ValueError('Wrong batch/plan')
    by={r['run_key']:r for r in obs}; out={'version':VERSION,'is_mock':manifest['is_mock'],'matrix':matrix,
        'source_runs_sha256':old.sha(batch/'runs.jsonl'),'plan_sha256':m['request_plan_sha256'],'coverage':{},'primary':{},'guardrails':{}}
    for arm in sorted({r['experiment'] for r in reqs}):
        rr=[by[r['run_key']] for r in reqs if r['experiment']==arm and r['run_key'] in by]
        out['coverage'][arm]={'planned':sum(r['experiment']==arm for r in reqs),'received':len(rr),'invalid':sum(not r['valid'] for r in rr),
            'abstain':sum(r['valid'] and r['parsed']['decision']=='ABSTAIN' for r in rr)}
    guards=[r for r in reqs if r['guardrail']]
    out['guardrails']={'planned':len(guards),'correct':sum(r['run_key'] in by and by[r['run_key']]['valid'] and by[r['run_key']]['parsed'].get('canonical_product_id')==r['expected_choice'] for r in guards)}
    if matrix['complete'] and all(r['valid'] for r in obs):
        formal=[r for r in reqs if not r['guardrail']]
        choose=lambda r:by[r['run_key']]['parsed'].get('canonical_product_id')
        def contrasts(factor,target,shelf=None):
            grouped=defaultdict(lambda:defaultdict(list))
            for r in formal:
                if r.get('endpoint_extension'): continue
                if shelf and r['shelf']!=shelf: continue
                grouped[r['block']][r[factor]].append(float(choose(r)==r[target]))
            return [float(np.mean(g[1])-np.mean(g[0])) for _,g in sorted(grouped.items())]
        out['primary']['example_copying']=paired_stats(contrasts('example','exemplar'),'example')
        out['primary']['identity_focal_A']=paired_stats(contrasts('named','focal','A'),'identity')
        # Positive means original IDs select the focal fund more often than opaque IDs.
        out['primary']['id_focal_A']=paired_stats([-x for x in contrasts('opaque','focal','A')],'id')
        blockorder=defaultdict(dict)
        for r in formal:
            if r['named']==0 and r['opaque']==1 and r['example']==0:
                blockorder[r['block']].setdefault(r['independent'],[]).append(choose(r))
        def same(v): return int(len(v)==3 and None not in v and len(set(v))==1)
        diffs=[same(g[0])-same(g[1]) for _,g in sorted(blockorder.items())]
        out['primary']['shared_vs_independent_all3']=paired_stats(diffs,'order')
        out['all3_scheduled']={str(o):{'same':sum(same(g[o]) for g in blockorder.values()),'scheduled_units':len(blockorder),
            'all_recommended':sum(None not in g[o] for g in blockorder.values())} for o in [0,1]}
        adj=holm({k:v['p'] for k,v in out['primary'].items()})
        for k,p in adj.items(): out['primary'][k]['holm_p']=p
        out['interpretation']='Controlled benchmark only. Identity includes name and issuer, not isolated brand. ABSTAIN contributes zero selection/agreement; inspect coverage separately. Bootstrap intervals are descriptive, unadjusted, and weak with few blocks. No automatic mitigation recommendation.'
    else: out['interpretation']='INCOMPLETE: no primary inference. Keep missing/invalid outcomes; do not fill with zeros or rerun received responses.'
    dest=batch.parent/'analysis'
    if dest.exists(): raise ValueError('Refusing to replace saved analysis')
    write(dest/'results.json',out)
    print(json.dumps({'is_mock':out['is_mock'],'complete':matrix['complete'],'primary':{k:{a:b for a,b in v.items() if a!='block_differences'} for k,v in out['primary'].items()}},ensure_ascii=False))

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('command',choices=['prepare','mock','live','analyze'])
    ap.add_argument('--kimi-interval',type=float,default=21); ap.add_argument('--yes',action='store_true')
    ap.add_argument('--out',type=Path); ap.add_argument('--batch',type=Path,default=ROOT/'runs/stage4-live-20261006-v12/batch')
    a=ap.parse_args()
    if not math.isfinite(a.kimi_interval) or a.kimi_interval<0: raise ValueError('Invalid interval')
    if a.command=='prepare': prepare()
    elif a.command=='analyze': analyze(a.batch)
    else: run(a.command,a.kimi_interval,a.yes,a.out)

if __name__=='__main__': main()
