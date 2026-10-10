"""Reproduce the provisional blind subset. Requires metadata review before freeze."""
import argparse
import hashlib
import json
import random
from pathlib import Path
from collections import Counter, defaultdict

def choose(units):
    groups=defaultdict(list)
    for u in units:groups[(u['source_class'],u['source_type'])].append(u)
    rng=random.Random(20261010)
    for k in sorted(groups):
        groups[k].sort(key=lambda u:u['seed_id']);rng.shuffle(groups[k])
    quotas=[(('carrier_published','pdf'),4),(('forwarder','pdf'),2),(('forwarder','web_page'),9)]
    selected=[];sources=Counter();publishers=Counter()
    for k,n in quotas:
        for u in groups[k]:
            if sources[u['source_id']]>=2 or publishers[u['publisher_alias']]>=5:continue
            selected.append(u['seed_id']);sources[u['source_id']]+=1;publishers[u['publisher_alias']]+=1;n-=1
            if n==0:break
        if n:raise ValueError('Current predefined stratum quota cannot be satisfied. Review the collection, not the seed.')
    if len(selected)!=15 or len(publishers)<3:raise ValueError('Subset constraints failed')
    return selected

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--units',required=True);ap.add_argument('--verify-subset',required=True)
    args=ap.parse_args();p=Path(args.units);units=json.loads(p.read_text(encoding='utf-8'))
    subset=json.loads(Path(args.verify_subset).read_text(encoding='utf-8'))
    selected=choose(units)
    assert selected==subset['selected_ids'],'Subset selection does not reproduce'
    assert hashlib.sha256(p.read_bytes()).hexdigest()==subset['units_sha256'],'Unit file hash mismatch'
    print(json.dumps({'selection_reproduces':True,'selected_ids':selected,'random_seed':20261010},ensure_ascii=False))
