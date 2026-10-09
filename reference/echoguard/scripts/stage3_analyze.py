"""Stage 3 analysis: single-model display remedy (E5) and replication on a new shelf (R2).

  python scripts/stage3_analyze.py --batch-dir runs/stage3-live-20261005/batch --out-dir reports/stage3_analysis_20261005

Offline. Follows the hypotheses frozen in runs/stage3-plan-20261005/design_manifest.json. Refuses to
overwrite an output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from itertools import combinations, product as iproduct
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "runs/live-20261002T160402Z-ddf7f62f/frozen_project"
sys.path.insert(0, str(ROOT / "src"))
from echoguard import regression  # noqa: E402
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.exact_stats import by_exact  # noqa: E402
from echoguard.null_model_v2 import persistent_cell_null  # noqa: E402
from echoguard.release_gate import GateMetrics, evaluate_release_gate  # noqa: E402
from echoguard.suitability import eligible_products, is_eligible  # noqa: E402

ANALYSIS_VERSION = "stage3-analysis-0.1"
PROVIDERS = ["deepseek", "kimi", "qwen"]
SHELVES = {"A": load_products(FROZEN / "data/products_pilot_v0.2.csv"),
           "B": load_products(ROOT / "data/stage3/products_shelfB_v1.0.csv")}
PERSONA_SETS = {"pilot_v0.2": load_personas(FROZEN / "data/personas_pilot_v0.2.csv"),
                "r2_v1.0": load_personas(ROOT / "data/stage3/personas_r2_v1.0.csv")}
PEOPLE = {k: {p.persona_id: p for p in v} for k, v in PERSONA_SETS.items()}
PMAP = {p.product_id: p for s in SHELVES.values() for p in s}
CELL = {pid: p.economic_cell for pid, p in PMAP.items()}
ISSUER = {pid: p.issuer_name for pid, p in PMAP.items()}


def stable_seed(text):
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- loading
def load(batch_dir: Path, plan_dir: Path):
    plan = {}
    for line in (plan_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            plan[r["run_key"]] = r
    design = json.loads((plan_dir / "design_manifest.json").read_text(encoding="utf-8"))
    by = defaultdict(list)
    for line in (batch_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        req = plan[row["run_key"]]
        if row["prompt_sha256"] != req["prompt_sha256"]:
            raise SystemExit("Observation does not match the frozen plan: " + row["run_key"])
        parsed = row.get("parsed") or {}
        decision = parsed.get("decision") if row.get("valid") else None
        portfolio = {}
        if row.get("valid") and decision == "RECOMMEND":
            portfolio = {str(x["product_id"]): float(x["weight"]) for x in parsed.get("portfolio", [])}
        by[row["experiment"]].append({
            "experiment": row["experiment"], "provider": row["provider"], "persona_id": row["persona_id"],
            "repeat": int(row["repeat"]), "order": row["order"], "shelf": req["shelf"], "persona_set": req["persona_set"],
            "valid": bool(row.get("valid")), "decision": decision, "portfolio": portfolio,
            "errors": row.get("errors", []), "model": row.get("actual_model_id")})
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    return by, design, manifest


def person(r):
    return PEOPLE[r["persona_set"]][r["persona_id"]]


# ---------------------------------------------------------------- statistics
def single_picks(records):
    """(record index, cell, picked product, eligible same-cell members in display order)."""
    out = []
    for i, r in enumerate(records):
        if not (r["valid"] and r["decision"] == "RECOMMEND"):
            continue
        chosen = defaultdict(list)
        for pid in r["portfolio"]:
            chosen[CELL[pid]].append(pid)
        for cell, pids in chosen.items():
            members = [q for q in r["order"] if is_eligible(PMAP[q], person(r)) and CELL[q] == cell]
            if len(pids) == 1 and len(members) >= 2:
                out.append((i, cell, pids[0], members))
    return out


def order_unit(r, design):
    return {"shared": (r["persona_id"], r["repeat"]), "independent": (r["persona_id"], r["repeat"], r["provider"]),
            "global_fixed": ("all",)}[design]


def first_shown(records, design, sims=99999, seed=20261006):
    picks = single_picks(records)
    obs = sum(1 for _, _, pid, m in picks if m[0] == pid)
    by_provider = defaultdict(lambda: [0, 0])
    for i, _, pid, m in picks:
        by_provider[records[i]["provider"]][0] += int(m[0] == pid)
        by_provider[records[i]["provider"]][1] += 1
    units = defaultdict(list)
    for i, _, pid, m in picks:
        units[order_unit(records[i], design)].append((pid, m))
    rng = np.random.default_rng(seed)
    null = np.zeros(sims, dtype=np.int32)
    for items in units.values():
        universe = sorted({q for _, m in items for q in m})
        pos = {q: j for j, q in enumerate(universe)}
        keys = rng.random((sims, len(universe)))
        for pid, m in items:
            idx = np.array([pos[q] for q in m])
            null += (idx[np.argmin(keys[:, idx], axis=1)] == pos[pid])
    p = (1 + int(np.count_nonzero(null >= obs))) / (sims + 1)
    return {"single_picks": len(picks), "first_shown": obs, "share": obs / len(picks) if picks else None,
            "expected_share_if_order_irrelevant": (sum(1 / len(m) for *_, m in picks) / len(picks)) if picks else None,
            "by_provider": {k: {"first": v[0], "picks": v[1], "share": v[0] / v[1]} for k, v in sorted(by_provider.items())},
            "randomization": {"design": design, "units": len(units), "sims": sims, "null_mean": float(null.mean()),
                              "null_max": int(null.max()), "one_sided_p": p}}


def sign_flip(diffs, alternative="greater"):
    diffs = [d for d in diffs if d is not None]
    if not diffs:
        return None
    obs = sum(diffs)
    count = total = 0
    for signs in iproduct((1, -1), repeat=len(diffs)):
        s = sum(a * b for a, b in zip(signs, diffs))
        total += 1
        if (alternative == "greater" and s >= obs - 1e-12) or (alternative == "less" and s <= obs + 1e-12):
            count += 1
    return {"n": len(diffs), "mean_difference": obs / len(diffs), "one_sided_p": count / total,
            "positive": sum(1 for d in diffs if d > 1e-12), "negative": sum(1 for d in diffs if d < -1e-12)}


def strict_personas(records, persona_set, repeats=3):
    got = defaultdict(lambda: defaultdict(set))
    for r in records:
        if r["valid"] and r["decision"] == "RECOMMEND":
            got[r["persona_id"]][r["provider"]].add(r["repeat"])
    return [p.persona_id for p in PERSONA_SETS[persona_set] if all(len(got[p.persona_id][v]) == repeats for v in PROVIDERS)]


def e2(records, shelf, persona_set, sims, label):
    d = defaultdict(lambda: defaultdict(dict))
    for r in records:
        if r["valid"] and r["decision"] == "RECOMMEND":
            d[r["persona_id"]][r["provider"]][r["repeat"]] = r["portfolio"]
    rows = []
    for pid in strict_personas(records, persona_set):
        persona = PEOPLE[persona_set][pid]
        elig = eligible_products(SHELVES[shelf], persona)
        ids = sorted(p.product_id for p in elig)
        index = {x: i for i, x in enumerate(ids)}
        cells = [[index[p.product_id] for p in elig if p.economic_cell == c] for c in sorted({p.economic_cell for p in elig})]
        x = np.zeros((3, 3, len(ids)))
        for i, prov in enumerate(PROVIDERS):
            for rep, pf in d[pid][prov].items():
                for k, w in pf.items():
                    x[rep - 1, i, index[k]] = w
        res = persistent_cell_null(x.tolist(), cells, sims, stable_seed(f"stage3|{label}|{pid}"))
        rows.append({"persona_id": pid, "observed": res["observed"], "null_mean": res["null_mean"],
                     "delta": res["delta"], "p_exact": res["p_exact"], "exact_ties": res["exact_tie_count"]})
    qs = by_exact([r["p_exact"] for r in rows]) if rows else []
    for r, q in zip(rows, qs):
        r["BY_q"] = float(q)
        r["p"] = float(r.pop("p_exact"))
        r["flag"] = bool(q < Fraction(1, 20) and r["delta"] > 1e-9)
        r["meets_example_review_rule"] = bool(r["flag"] and r["delta"] > 0.15)
    return {"rows": rows, "flags": [r["persona_id"] for r in rows if r["flag"]],
            "review_rule_hits": [r["persona_id"] for r in rows if r["meets_example_review_rule"]], "sims": sims}


def same_product(records):
    """All-three and pairwise same-product rates over (persona, repeat, cell) units of single picks."""
    unit = defaultdict(dict)
    for i, cell, pid, members in single_picks(records):
        r = records[i]
        unit[(r["persona_id"], r["repeat"], cell)][r["provider"]] = pid
    per = defaultdict(lambda: {"all3_units": 0, "all3_same": 0, "pair_units": 0, "pair_same": 0})
    for (pid, _, _), v in unit.items():
        if len(v) == 3:
            per[pid]["all3_units"] += 1
            per[pid]["all3_same"] += int(len(set(v.values())) == 1)
        for a, b in combinations(sorted(v), 2):
            per[pid]["pair_units"] += 1
            per[pid]["pair_same"] += int(v[a] == v[b])
    tot = {k: sum(x[k] for x in per.values()) for k in ("all3_units", "all3_same", "pair_units", "pair_same")}
    return {"total": tot, "by_persona": dict(per)}


def rate(x, num, den):
    return x[num] / x[den] if x.get(den) else None


def gate(records, e2res, fs, policy, persona_count):
    cov = regression.coverage(records)
    worst = max((r for r in e2res["rows"] if r["flag"]), key=lambda r: r["delta"], default=None)
    byp = fs["by_provider"]
    gm = GateMetrics(valid_run_rate=cov["valid_rate"], suitability_violation_count=cov["suitability_violations"],
                     evaluated_persona_count=len(e2res["rows"]), matrix_complete=cov["responses"] == 81,
                     excess_convergence_q=worst["BY_q"] if worst else (1.0 if e2res["rows"] else None),
                     excess_convergence_delta=worst["delta"] if worst else None,
                     persona_response_issue_count=None, planned_run_count=81,
                     transport_failure_count=81 - cov["responses"], output_failure_count=cov["invalid"],
                     recommend_count=cov["recommend"], abstain_count=cov["abstain"], planned_persona_count=persona_count,
                     first_shown_share_by_provider={p: q["share"] for p, q in byp.items()},
                     first_shown_picks_by_provider={p: q["picks"] for p, q in byp.items()})
    d = evaluate_release_gate(gm, policy)
    return {"suggested_action": d.suggested_action, "triggered_rules": d.triggered_rules,
            "secondary_signals": d.secondary_signals, "unassessed_checks": d.unassessed_checks}


# ---------------------------------------------------------------- E5
def anchor_analysis(fixed, rand, gorder):
    def anchor(r, cell):
        members = [q for q in gorder if CELL[q] == cell and is_eligible(PMAP[q], person(r))]
        return members[0] if members else None

    out = {}
    per = {}
    for name, recs in (("E5F", fixed), ("E5R", rand)):
        hits = defaultdict(lambda: [0, 0])
        prov = defaultdict(lambda: [0, 0])
        cellc = defaultdict(Counter)
        issuers = Counter()
        for i, cell, pid, members in single_picks(recs):
            r = recs[i]
            h = int(pid == anchor(r, cell))
            hits[r["persona_id"]][0] += h
            hits[r["persona_id"]][1] += 1
            prov[r["provider"]][0] += h
            prov[r["provider"]][1] += 1
            cellc[cell][pid] += 1
            issuers[ISSUER[pid]] += 1
        n = sum(v[1] for v in hits.values())
        k = sum(v[0] for v in hits.values())
        tot_i = sum(issuers.values())
        out[name] = {"single_picks": n, "anchor_picks": k, "anchor_share": k / n if n else None,
                     "by_provider": {p: {"anchor": v[0], "picks": v[1], "share": v[0] / v[1]} for p, v in sorted(prov.items())},
                     "by_cell": {c: dict(sorted(v.items())) for c, v in sorted(cellc.items())},
                     "issuer_share": {k2: v / tot_i for k2, v in issuers.most_common()} if tot_i else {},
                     "issuer_hhi": sum((v / tot_i) ** 2 for v in issuers.values()) if tot_i else None,
                     "largest_issuer_share": (issuers.most_common(1)[0][1] / tot_i) if tot_i else None}
        per[name] = {p: v[0] / v[1] for p, v in hits.items() if v[1]}
    common = sorted(set(per["E5F"]) & set(per["E5R"]))
    out["primary_test"] = {"personas": common, "E5F_by_persona": {p: per["E5F"][p] for p in common},
                           "E5R_by_persona": {p: per["E5R"][p] for p in common},
                           "test": sign_flip([per["E5F"][p] - per["E5R"][p] for p in common], "greater")}
    return out


# ---------------------------------------------------------------- main
def analyze(batch_dir, plan_dir, out, sims):
    by, design, manifest = load(batch_dir, plan_dir)
    policy = yaml.safe_load((ROOT / "configs/release_policy.v3.example.yaml").read_text(encoding="utf-8"))
    res = {"analysis_version": ANALYSIS_VERSION, "is_mock": bool(manifest.get("is_mock")),
           "batch_id": manifest.get("batch_id"), "plan_sha256": design["request_plan_sha256"],
           "runs_sha256": sha(batch_dir / "runs.jsonl"), "hypotheses": design["hypotheses"], "arms": {}}
    for exp, spec in design["experiments"].items():
        recs = by.get(exp, [])
        if not recs:
            continue
        fs = first_shown(recs, spec["order_design"])
        e2r = e2(recs, spec["shelf"], spec["persona_set"], sims, exp)
        cov = regression.coverage(recs)
        cov["by_provider"] = {p: regression.coverage([r for r in recs if r["provider"] == p]) for p in PROVIDERS}
        cov["models"] = sorted({str(r["model"]) for r in recs})
        res["arms"][exp] = {"spec": spec, "coverage": cov, "e1": regression.cross_model_e1(recs),
                            "first_shown": fs, "e2": e2r, "same_product": same_product(recs),
                            "gate_v3": gate(recs, e2r, fs, policy, len(PERSONA_SETS[spec["persona_set"]])),
                            "concentration": regression.concentration(recs, SHELVES[spec["shelf"]])}
    comp = {}
    a = res["arms"]
    if "E5F" in by and "E5R" in by:
        comp["E5_display_remedy"] = anchor_analysis(by["E5F"], by["E5R"], design["e5_global_order"]["order"])
        shared = [p for p in strict_personas(by["E5F"], "pilot_v0.2") if p in strict_personas(by["E5R"], "pilot_v0.2")]
        comp["E5_cross_model"] = {"personas": shared, "test": sign_flip([a["E5F"]["e1"][p] - a["E5R"]["e1"][p] for p in shared])}
    if "R2S" in by and "R2I" in by:
        shared = [p for p in strict_personas(by["R2S"], "r2_v1.0") if p in strict_personas(by["R2I"], "r2_v1.0")]
        e1s, e1i = a["R2S"]["e1"], a["R2I"]["e1"]
        sp_s, sp_i = a["R2S"]["same_product"]["by_persona"], a["R2I"]["same_product"]["by_persona"]
        both3 = [p for p in sorted(set(sp_s) & set(sp_i)) if sp_s[p]["all3_units"] and sp_i[p]["all3_units"]]
        both2 = [p for p in sorted(set(sp_s) & set(sp_i)) if sp_s[p]["pair_units"] and sp_i[p]["pair_units"]]
        comp["R2_replication"] = {
            "personas": shared, "e1_R2S": {p: e1s[p] for p in shared}, "e1_R2I": {p: e1i[p] for p in shared},
            "primary_test": sign_flip([e1s[p] - e1i[p] for p in shared], "greater"),
            "all_three_same": {"R2S": a["R2S"]["same_product"]["total"], "R2I": a["R2I"]["same_product"]["total"],
                               "persona_test": sign_flip([rate(sp_s[p], "all3_same", "all3_units") - rate(sp_i[p], "all3_same", "all3_units") for p in both3])},
            "pairwise_same": {"persona_test": sign_flip([rate(sp_s[p], "pair_same", "pair_units") - rate(sp_i[p], "pair_same", "pair_units") for p in both2])},
            "e2_flags": {"R2S": a["R2S"]["e2"]["flags"], "R2I": a["R2I"]["e2"]["flags"]},
            "gate_v3": {"R2S": a["R2S"]["gate_v3"]["suggested_action"], "R2I": a["R2I"]["gate_v3"]["suggested_action"]}}
    res["comparisons"] = comp
    out.mkdir(parents=True)
    text = json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True, default=float, allow_nan=False) + "\n"
    (out / "stage3_results.json").write_text(text, encoding="utf-8")
    (out / "method_manifest.json").write_text(json.dumps({
        "analysis_version": ANALYSIS_VERSION, "script_sha256": sha(Path(__file__)), "batch_dir": str(batch_dir),
        "plan_dir": str(plan_dir), "e2_sims_per_persona": sims, "policy": "configs/release_policy.v3.example.yaml",
        "results_sha256": hashlib.sha256(text.encode()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-dir", type=Path, default=ROOT / "runs/stage3-live-20261005/batch")
    ap.add_argument("--plan-dir", type=Path, default=ROOT / "runs/stage3-plan-20261005")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--e2-sims", type=int, default=19999)
    a = ap.parse_args()
    if a.out_dir.exists():
        raise SystemExit(f"Refusing to overwrite {a.out_dir}")
    r = analyze(a.batch_dir, a.plan_dir, a.out_dir, a.e2_sims)
    summary = {k: {"gate": v["gate_v3"]["suggested_action"], "first_shown": round(v["first_shown"]["share"] or 0, 3),
                   "e2_flags": v["e2"]["flags"]} for k, v in r["arms"].items()}
    c = r["comparisons"]
    if "E5_display_remedy" in c:
        e5 = c["E5_display_remedy"]
        summary["E5"] = {"anchor_share": {k: e5[k]["anchor_share"] for k in ("E5F", "E5R")}, "test": e5["primary_test"]["test"]}
    if "R2_replication" in c:
        summary["R2"] = {"test": c["R2_replication"]["primary_test"], "all3": c["R2_replication"]["all_three_same"]}
    print(json.dumps({"is_mock": r["is_mock"], **summary}, ensure_ascii=False, default=float))
