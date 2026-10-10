"""Offline statistics of the full-review M4 against the trigger-only variant M4_trig on the pilot observations.

Reads <root>/{a,b}/scores/rows.json and <root>/{a,b}/outputs (written by `orchestrate assemble` and `score`),
no model call. Writes <root>/fullreview_summary.json.

Usage:  PYTHONPATH=src python scripts/fullreview_stats.py [--root runs/derived/pilot_rescore_20261010_fullreview]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data/gold/dev"
GROUPS = {"A": "a", "B": "b"}


def gold_risk(pc) -> bool:
    return bool(pc["conflict_ids"] or pc["missing_ids"] or pc["hidden_fee_ids"]
                or (pc["ddp_level"] not in (None, "none")) or pc["claim_statuses"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "runs/derived/pilot_rescore_20261010_fullreview"))
    root = Path(ap.parse_args(argv).root)
    summary = {}
    for g, d in GROUPS.items():
        rows = json.loads((root / d / "scores/rows.json").read_text("utf-8"))
        res = {}
        for m in ("M4", "M4_TRIG"):
            rs = [r for r in rows if r["method"] == m]
            uncovered, risk_cands, no_partner_obs = [], 0, []
            for f in sorted((root / d / "outputs" / m.replace("_TRIG", "_trig")).glob("*/*.json")):
                o = json.loads(f.read_text("utf-8"))
                gold = json.loads((GOLD / f"{o['task_id']}.json").read_text("utf-8"))
                reviewed = set(o["diagnostics"]["review"]["reviewed"])
                for cid, pc in gold["per_cand"].items():
                    if gold_risk(pc):
                        risk_cands += 1
                        if cid not in reviewed:
                            uncovered.append(f"{o['config_id']}.{o['task_id']}.{cid}")
                if len(reviewed) < len(gold["per_cand"]) and m == "M4":
                    no_partner_obs.append(f"{o['config_id']}.{o['task_id']}")
            res[m.replace("_TRIG", "_trig")] = {
                "outputs": len(rs), "decisions_correct": sum(r["decision_correct"] or 0 for r in rs),
                "conflict_detected": sum(r["tp_conflict"] for r in rs), "conflict_missed": sum(r["fn_conflict"] for r in rs),
                "gap_detected": sum(r["tp_gap"] for r in rs), "gap_missed": sum(r["fn_gap"] for r in rs),
                "false_positives_conflict_gap_ddp": sum(r["fp_conflict"] + r["fp_gap"] + r["fp_ddp_risk"] for r in rs),
                "gold_risk_candidates": risk_cands, "gold_risk_not_reviewed": len(uncovered),
                "gold_risk_not_reviewed_ids": uncovered, "outputs_with_unreviewed_candidates": no_partner_obs}
        summary[g] = res
    (root / "fullreview_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
