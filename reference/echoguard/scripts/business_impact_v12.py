"""Business-impact conversions for the submission materials (no new model calls).

  python scripts/business_impact_v12.py --out reports/business_impact_v12/impact.json

Reads frozen stage 3 observations and the frozen stage 4 analysis. Converts measured share
differences into per-10,000-recommendation and per-100-million-yuan units under one stated
assumption: the customer follows the recommendation. Public scale figures are recorded with
their sources and are not multiplied by any assumed AI share.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage3_analyze as s3  # noqa: E402

OVERWRITE = False
PUBLIC = {
    "icbc_personal_customers_100m": {"value": 7.8, "unit": "亿户（超）", "source": "中国工商银行2026年半年度报告"},
    "icbc_personal_financial_assets_trn": {"value": 26.5, "unit": "万亿元，2026年6月末", "source": "中国工商银行2026年半年度报告；中国新闻网 2026-08-31"},
    "icbc_non_money_fund_holding_100m": {"value": 5296, "unit": "亿元，2026年上半年末，基金销售机构第4位", "source": "中国证券投资基金业协会数据，新京报报道"},
    "icbc_cm_assistant_services_10k": {"value": 2200, "unit": "万次（超），个人客户经理智能助手服务次数", "source": "金融时报 2026-09-08"},
}


def exposure(records):
    rec = [r for r in records if r["valid"] and r["decision"] == "RECOMMEND"]
    picks = s3.single_picks(records)
    with_single = {i for i, *_ in picks}
    w_single = 0.0
    for i, cell, pid, members in picks:
        w_single += records[i]["portfolio"][pid]
    w_total = sum(sum(r["portfolio"].values()) for r in rec)
    return {"recommendations": len(rec), "with_single_pick": len(with_single),
            "share_with_single_pick": len(with_single) / len(rec), "single_picks": len(picks),
            "weight_share_in_single_picks": w_single / w_total}


def anchor_money_customers(records, gorder):
    """Weight share of the fixed-order anchors in all recommended weight, and share of recommendations holding one."""
    def anchors(r):
        out = set()
        for cell in {s3.CELL[q] for q in gorder}:
            m = [q for q in gorder if s3.CELL[q] == cell and s3.is_eligible(s3.PMAP[q], s3.person(r))]
            if m:
                out.add(m[0])
        return out
    rec = [r for r in records if r["valid"] and r["decision"] == "RECOMMEND"]
    wa = sum(sum(w for p, w in r["portfolio"].items() if p in anchors(r)) for r in rec)
    wt = sum(sum(r["portfolio"].values()) for r in rec)
    ca = sum(1 for r in rec if anchors(r) & set(r["portfolio"]))
    return {"recommendations": len(rec), "anchor_weight_share": wa / wt, "recs_with_anchor": ca,
            "share_recs_with_anchor": ca / len(rec)}


def per_units(p_hi, p_lo):
    d = p_hi - p_lo
    return {"high": p_hi, "low": p_lo, "difference": d,
            "per_10000_recommendations": round(d * 10000), "per_100m_yuan_10k_yuan": round(d * 10000)}


def main(out: Path):
    by, design, manifest = s3.load(ROOT / "runs/stage3-live-20261005/batch", ROOT / "runs/stage3-plan-20261005")
    res = {"note": "Conversions assume the customer follows the recommendation; no AI usage share is assumed.",
           "stage3_runs_sha256": s3.sha(ROOT / "runs/stage3-live-20261005/batch/runs.jsonl"), "exposure": {}}
    allrecs = []
    for exp in ["E5F", "E5R", "R2S", "R2I"]:
        res["exposure"][exp] = exposure(by[exp])
        allrecs.extend(by[exp])
    tot = defaultdict(float)
    for v in res["exposure"].values():
        tot["recommendations"] += v["recommendations"]
        tot["with_single_pick"] += v["with_single_pick"]
    res["exposure"]["stage3_all"] = {"recommendations": int(tot["recommendations"]),
                                     "with_single_pick": int(tot["with_single_pick"]),
                                     "share_with_single_pick": tot["with_single_pick"] / tot["recommendations"]}
    s3r = json.loads((ROOT / "reports/stage3_analysis_20261005/stage3_results.json").read_text(encoding="utf-8"))
    rem = s3r["comparisons"]["E5_display_remedy"]
    s4 = json.loads((ROOT / "reports/stage4_analysis_v12/descriptives.json").read_text(encoding="utf-8"))
    res["conversions"] = {
        "fixed_order_single_model": dict(per_units(rem["E5F"]["anchor_share"], rem["E5R"]["anchor_share"]),
                                         counts=[rem["E5F"]["anchor_picks"], rem["E5F"]["single_picks"],
                                                 rem["E5R"]["anchor_picks"], rem["E5R"]["single_picks"]],
                                         basis="real shelf A, anchor share, fixed order vs per-request random order"),
        "sequential_ids_controlled": dict(per_units(262 / 288, 93 / 288), counts=[262, 288, 93, 288],
                                          basis="controlled shelf A, focal product share, original IDs vs random aliases"),
        "shared_order_three_models": dict(per_units(144 / 144, 16 / 144), counts=[144, 144, 16, 144],
                                          basis="controlled shelves, neutral condition, all three pick same product"),
        "issuer_share_random_order_sequential_ids": {"华夏": rem["E5R"]["issuer_share"]["华夏基金管理有限公司"],
                                                     "工银瑞信": rem["E5R"]["issuer_share"]["工银瑞信基金管理有限公司"],
                                                     "even_share": 0.25, "basis": "real shelf A, observed, not a causal estimate"},
    }
    gorder = design["e5_global_order"]["order"]
    f, r = anchor_money_customers(by["E5F"], gorder), anchor_money_customers(by["E5R"], gorder)
    res["conversions"]["fixed_order_money"] = dict(per_units(f["anchor_weight_share"], r["anchor_weight_share"]),
                                                   fixed=f, random=r, basis="share of all recommended weight on the three anchors")
    res["conversions"]["fixed_order_customers"] = dict(per_units(f["share_recs_with_anchor"], r["share_recs_with_anchor"]),
                                                       basis="share of recommendations holding at least one anchor")
    res["stage4_descriptives_keys"] = sorted(s4)[:20]
    res["public_scale"] = PUBLIC
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not OVERWRITE:
        raise SystemExit(f"Refusing to overwrite {out}")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ["exposure", "conversions"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--overwrite", action="store_true", help="replace a derived output written by this script")
    a = ap.parse_args()
    OVERWRITE = a.overwrite
    main(a.out)
