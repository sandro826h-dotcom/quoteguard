"""Exact prospective sensitivity for paired binary same-product outcomes, not observed evidence."""
import json,math
from pathlib import Path
from scipy.stats import binom
ROOT=Path(__file__).resolve().parents[1]
rows=[]
for n in [24,48,96,144]:
 rejection={(a,b) for a in range(n+1) for b in range(n-a+1) if a+b>0 and min(1.,2*binom.cdf(min(a,b),a+b,.5))<=.0125}
 for up,down in [(.2,.05),(.35,.05),(.5,.05)]:
  power=0.
  for a,b in rejection:
   logp=math.lgamma(n+1)-math.lgamma(a+1)-math.lgamma(b+1)-math.lgamma(n-a-b+1)+a*math.log(up)+b*math.log(down)+(n-a-b)*math.log1p(-up-down)
   power+=math.exp(logp)
  rows.append(dict(n=n,improvement_probability=up,worsening_probability=down,difference=round(up-down,2),power=power))
out={'status':'prospective sensitivity only, not pilot effect estimates','test':'two-sided exact paired binary sign test; worst-case Holm alpha 0.0125 for four primary tests','assumptions':'independent benchmark blocks with fixed probabilities of improvement, worsening, or tie; not customer sampling or proof of achieved power','rows':rows}
(ROOT/'reports/codex_audit_20261006/stage4_power.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(rows))
