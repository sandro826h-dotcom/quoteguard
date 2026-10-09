"""Stage 2 analysis: replication, order mechanism, configuration regression, fixed-order
stability and label/abstention, following the hypotheses frozen in the plan manifest.

  python scripts/stage2_analyze.py --batch-dir runs/stage2-live-20261005/batch --out-dir runs/stage2-analysis-20261005

Offline. Reads the frozen Gate 1 pilot as reference G1. Refuses to overwrite an output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from fractions import Fraction
from itertools import combinations, product as iproduct
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "runs/live-20261002T160402Z-ddf7f62f/frozen_project"
G1_RUNS = ROOT / "runs/live-20261002T160402Z-ddf7f62f/pilot/runs.jsonl"
sys.path.insert(0, str(ROOT / "src"))
from echoguard.blind_shelf import build_shelf  # noqa: E402
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.exact_stats import by_exact, mean_pairwise_stable  # noqa: E402
from echoguard.null_model_v2 import persistent_cell_null  # noqa: E402
from echoguard import persona_rules, regression  # noqa: E402
from echoguard.release_gate import GateMetrics, evaluate_release_gate  # noqa: E402
from echoguard.suitability import eligible_products, is_eligible  # noqa: E402

ANALYSIS_VERSION = "stage2-analysis-0.1"
PROVIDERS = ["deepseek", "kimi", "qwen"]
SCENARIO = "S0_BASELINE"
PRODUCTS = load_products(FROZEN / "data/products_pilot_v0.2.csv")
PMAP = {p.product_id: p for p in PRODUCTS}
PERSONAS = load_personas(FROZEN / "data/personas_pilot_v0.2.csv")
PEOPLE = {p.persona_id: p for p in PERSONAS}
CELLS_V02 = {p.product_id: p.economic_cell for p in PRODUCTS}
# Shelf v0.3 grouping (data/products_pilot_v0.3_cells.csv): bond funds split by documented differences.
CELLS_V03 = dict(CELLS_V02, P05="BOND_PURE", P08="BOND_PURE", P07="BOND_SHORT", P06="BOND_ENHANCED")


def stable_seed(text):
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- loading
def norm(row, experiment):
    parsed = row.get("parsed") or {}
    decision = parsed.get("decision") if row.get("valid") else None
    portfolio = {}
    if row.get("valid") and decision == "RECOMMEND":
        portfolio = {str(x["product_id"]): float(x["weight"]) for x in parsed.get("portfolio", [])}
    return {"experiment": experiment, "provider": row["provider"], "persona_id": row["persona_id"],
            "repeat": int(row["repeat"]), "view": row.get("view", "named"), "order": row["order"],
            "valid": bool(row.get("valid")), "decision": decision, "portfolio": portfolio,
            "errors": row.get("errors", []), "model": row.get("actual_model_id"), "pair_id": row.get("pair_id"),
            "alias_to_brand": row.get("alias_to_brand")}


def load_g1():
    out = []
    for line in G1_RUNS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        m = r["meta"]
        shelf = build_shelf(PRODUCTS, PEOPLE[m["persona_id"]], blind=False)
        import random
        random.Random(m["shelf_order_seed"]).shuffle(shelf)
        out.append(norm({"provider": m["model_family"], "persona_id": m["persona_id"], "repeat": m["repeat_id"],
                         "order": [s["product_id"] for s in shelf], "valid": r["valid"], "parsed": r.get("parsed"),
                         "errors": r.get("errors", []), "actual_model_id": r["provider_metadata"].get("returned_model")}, "G1"))
    return out


def load_batch(batch_dir: Path, plan_dir: Path):
    plan = {json.loads(l)["run_key"]: json.loads(l) for l in (plan_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    rows = [json.loads(l) for l in (batch_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by = defaultdict(list)
    for r in rows:
        req = plan[r["run_key"]]
        if r["prompt_sha256"] != req["prompt_sha256"]:
            raise SystemExit("Observation does not match the frozen plan: " + r["run_key"])
        rec = norm(dict(r, alias_to_brand=req.get("alias_to_brand")), r["experiment"])
        by[r["experiment"]].append(rec)
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    return by, manifest, plan


# ---------------------------------------------------------------- core statistics
def by_pmr(records):
    d = defaultdict(lambda: defaultdict(dict))
    for r in records:
        if r["valid"] and r["decision"] == "RECOMMEND":
            d[r["persona_id"]][r["provider"]][r["repeat"]] = r["portfolio"]
    return d


def coverage(records):
    c = regression.coverage(records)
    c["by_provider"] = {p: regression.coverage([r for r in records if r["provider"] == p]) for p in PROVIDERS}
    c["models"] = sorted({str(r["model"]) for r in records})
    return c


def e0(records):
    d = by_pmr(records)
    out = {}
    for pid, provs in d.items():
        for prov, reps in provs.items():
            if len(reps) >= 2:
                out[f"{prov}|{pid}"] = mean_pairwise_stable([reps[k] for k in sorted(reps)])
    return out


def strict_personas(records, repeats=3):
    d = by_pmr(records)
    return [p.persona_id for p in PERSONAS if all(len(d[p.persona_id].get(v, {})) == repeats for v in PROVIDERS)]


def e2(records, cells_map, sims, label):
    d = by_pmr(records)
    rows = []
    for pid in strict_personas(records):
        persona = PEOPLE[pid]
        elig = eligible_products(PRODUCTS, persona)
        ids = sorted(p.product_id for p in elig)
        index = {x: i for i, x in enumerate(ids)}
        cells = [[index[p.product_id] for p in elig if cells_map[p.product_id] == cell]
                 for cell in sorted({cells_map[p.product_id] for p in elig})]
        x = np.zeros((3, 3, len(ids)))
        for i, prov in enumerate(PROVIDERS):
            for rep, pf in d[pid][prov].items():
                for k, w in pf.items():
                    x[rep - 1, i, index[k]] = w
        res = persistent_cell_null(x.tolist(), cells, sims, stable_seed(f"stage2|{label}|{pid}"))
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


def single_picks(records, cells_map=CELLS_V02):
    """(record index, cell, picked product, eligible cell members in display order)."""
    out = []
    for i, r in enumerate(records):
        if not (r["valid"] and r["decision"] == "RECOMMEND"):
            continue
        persona = PEOPLE[r["persona_id"]]
        chosen = defaultdict(list)
        for pid in r["portfolio"]:
            chosen[cells_map[pid]].append(pid)
        for cell, pids in chosen.items():
            members = [q for q in r["order"] if is_eligible(PMAP[q], persona) and cells_map[q] == cell]
            if len(pids) == 1 and len(members) >= 2:
                out.append((i, cell, pids[0], members))
    return out


def order_unit(r, design):
    if design == "shared":
        return (r["persona_id"], r["repeat"])
    if design == "independent":
        return (r["persona_id"], r["repeat"], r["provider"])
    if design == "fixed":
        return (r["persona_id"],)
    raise ValueError(design)


def first_shown(records, design, sims=99999, seed=20261005, cells=None):
    picks = single_picks(records)
    if cells is not None:
        picks = [x for x in picks if x[1] in cells]
    obs = sum(1 for _, _, pid, members in picks if members[0] == pid)
    expected = sum(1 / len(m) for *_, m in picks)
    by_provider = defaultdict(lambda: [0, 0])
    for i, _, pid, members in picks:
        by_provider[records[i]["provider"]][0] += int(members[0] == pid)
        by_provider[records[i]["provider"]][1] += 1
    # Re-draw orders per design unit with picks held fixed.
    units = defaultdict(list)
    for i, cell, pid, members in picks:
        units[order_unit(records[i], design)].append((pid, members))
    rng = np.random.default_rng(seed)
    null = np.zeros(sims, dtype=np.int32)
    for items in units.values():
        universe = sorted({m for _, members in items for m in members})
        pos = {q: j for j, q in enumerate(universe)}
        keys = rng.random((sims, len(universe)))
        for pid, members in items:
            idx = np.array([pos[m] for m in members])
            winner = idx[np.argmin(keys[:, idx], axis=1)]
            null += (winner == pos[pid])
    p = (1 + int(np.count_nonzero(null >= obs))) / (sims + 1)
    return {"single_picks": len(picks), "first_shown": obs, "share": obs / len(picks) if picks else None,
            "expected_share_if_order_irrelevant": expected / len(picks) if picks else None,
            "by_provider": {k: {"first": v[0], "picks": v[1], "share": v[0] / v[1]} for k, v in sorted(by_provider.items())},
            "randomization": {"design": design, "sims": sims, "null_mean": float(null.mean()), "null_max": int(null.max()),
                              "one_sided_p": p}}


def e3(records):
    return persona_rules.evaluate(by_pmr(records), PRODUCTS)


def gate(records, e2res, e3res, policy):
    cov = regression.coverage(records)
    worst = max((r for r in e2res["rows"] if r["flag"]), key=lambda r: r["delta"], default=None)
    gm = GateMetrics(valid_run_rate=cov["valid_rate"], suitability_violation_count=cov["suitability_violations"],
                     evaluated_persona_count=len(e2res["rows"]), matrix_complete=cov["responses"] == 81,
                     excess_convergence_q=worst["BY_q"] if worst else (1.0 if e2res["rows"] else None),
                     excess_convergence_delta=worst["delta"] if worst else None,
                     persona_response_issue_count=e3res["persona_response_issue_count"],
                     planned_run_count=81, transport_failure_count=81 - cov["responses"],
                     output_failure_count=cov["invalid"], recommend_count=cov["recommend"],
                     abstain_count=cov["abstain"], planned_persona_count=9)
    d = evaluate_release_gate(gm, policy)
    return {"suggested_action": d.suggested_action, "triggered_rules": d.triggered_rules,
            "secondary_signals": d.secondary_signals, "unassessed_checks": d.unassessed_checks}


# ---------------------------------------------------------------- comparisons
def sign_flip(diffs, alternative="greater"):
    """Exact one-sided sign-flip test of mean difference > 0 (or < 0)."""
    diffs = [d for d in diffs if d is not None]
    if not diffs:
        return None
    obs = sum(diffs)
    count = 0
    total = 0
    for signs in iproduct((1, -1), repeat=len(diffs)):
        s = sum(a * b for a, b in zip(signs, diffs))
        total += 1
        if (alternative == "greater" and s >= obs - 1e-12) or (alternative == "less" and s <= obs + 1e-12):
            count += 1
    return {"n": len(diffs), "mean_difference": obs / len(diffs), "one_sided_p": count / total,
            "alternative": alternative}


def paired_label_test(b0, r1, sims=99999, seed=7):
    """First-shown share B0 minus R1; labels swapped within provider/persona/repeat/cell units."""
    def picks_by_unit(records, tag):
        out = {}
        for i, cell, pid, members in single_picks(records):
            r = records[i]
            out[(r["provider"], r["persona_id"], r["repeat"], cell)] = int(members[0] == pid)
        return out
    a, b = picks_by_unit(b0, "B0"), picks_by_unit(r1, "R1")
    shared = sorted(set(a) & set(b))
    only_a = [a[k] for k in a if k not in b]
    only_b = [b[k] for k in b if k not in a]
    obs = (sum(a.values()) / len(a)) - (sum(b.values()) / len(b)) if a and b else None
    if obs is None:
        return None
    rng = np.random.default_rng(seed)
    xa = np.array([a[k] for k in shared])
    xb = np.array([b[k] for k in shared])
    swap = rng.random((sims, len(shared))) < 0.5
    ta = np.where(swap, xb, xa).sum(axis=1) + sum(only_a)
    tb = np.where(swap, xa, xb).sum(axis=1) + sum(only_b)
    null = ta / len(a) - tb / len(b)
    p = (1 + int(np.count_nonzero(null >= obs - 1e-12))) / (sims + 1)
    return {"B0_share": sum(a.values()) / len(a), "R1_share": sum(b.values()) / len(b), "difference": obs,
            "paired_units": len(shared), "one_sided_p": p, "sims": sims}


def mcnemar_label(records):
    out = {}
    pairs = defaultdict(dict)
    for r in records:
        pairs[(r["provider"], r["pair_id"])][r["view"]] = r
    for prov in PROVIDERS:
        b = c = both = neither = invalid = 0
        for (p, _), d in pairs.items():
            if p != prov or set(d) != {"named", "blind"}:
                continue
            if not (d["named"]["valid"] and d["blind"]["valid"]):
                invalid += 1
                continue
            n_rec = d["named"]["decision"] == "RECOMMEND"
            b_rec = d["blind"]["decision"] == "RECOMMEND"
            if n_rec and not b_rec:
                b += 1
            elif b_rec and not n_rec:
                c += 1
            elif n_rec and b_rec:
                both += 1
            else:
                neither += 1
        n = b + c
        p = sum(math.comb(n, k) for k in range(b, n + 1)) / 2 ** n if n else None
        named_rate = (b + both) / max(1, b + c + both + neither)
        blind_rate = (c + both) / max(1, b + c + both + neither)
        out[prov] = {"named_recommend_blind_abstain": b, "named_abstain_blind_recommend": c,
                     "both_recommend": both, "both_abstain": neither, "invalid_pairs": invalid,
                     "recommend_rate_named": named_rate, "recommend_rate_blind": blind_rate,
                     "exact_mcnemar_one_sided_p": p}
    pos1 = defaultdict(lambda: [0, 0])
    for r in records:
        if r["valid"] and r["decision"] == "RECOMMEND":
            top = max(r["portfolio"], key=r["portfolio"].get)
            pos1[(r["provider"], r["view"])][0] += int(r["order"][0] == top)
            pos1[(r["provider"], r["view"])][1] += 1
    position = {}
    for (prov, view), (k, n) in sorted(pos1.items()):
        p = sum(math.comb(n, j) * 0.25 ** j * 0.75 ** (n - j) for j in range(k, n + 1))
        position[f"{prov}|{view}"] = {"top_weight_on_position1": k, "recommendations": n, "share": k / n, "binomial_p_vs_quarter": p}
    return {"mcnemar": out, "position1_top_weight": position}


# ---------------------------------------------------------------- main
def analyze(batch_dir: Path, plan_dir: Path, out: Path, sims: int):
    by, manifest, plan = load_batch(batch_dir, plan_dir)
    design = json.loads((plan_dir / "design_manifest.json").read_text(encoding="utf-8"))
    policy = yaml.safe_load((ROOT / "configs/release_policy.v2.example.yaml").read_text(encoding="utf-8"))
    g1 = load_g1()
    batches = {"G1": g1, **{k: by[k] for k in ("B0", "E4", "R1", "E0A") if k in by}}
    designs = {"G1": "shared", "B0": "shared", "R1": "shared", "E4": "independent", "E0A": "fixed"}
    res = {"analysis_version": ANALYSIS_VERSION, "is_mock": bool(manifest.get("is_mock")),
           "batch_id": manifest.get("batch_id"), "plan_sha256": design["request_plan_sha256"],
           "runs_sha256": sha(batch_dir / "runs.jsonl"), "hypotheses": design["hypotheses"], "batches": {}}
    for name, recs in batches.items():
        e2a = e2(recs, CELLS_V02, sims, name + "|v02")
        e2b = e2(recs, CELLS_V03, sims, name + "|v03")
        e3r = e3(recs)
        res["batches"][name] = {
            "coverage": coverage(recs), "e0": e0(recs), "e1": regression.cross_model_e1(recs),
            "e2_v02_cells": e2a, "e2_v03_cells": e2b, "first_shown": first_shown(recs, designs[name]),
            "first_shown_cash_equity_cells": first_shown(recs, designs[name], cells={"CASH_MM", "CSI300_EQ"}),
            "e3": {k: e3r[k] for k in ("rules_version", "persona_response_issue_count", "issues", "checks_run", "contrasts_assessed")},
            "gate": gate(recs, e2a, e3r, policy),
            "concentration": regression.concentration(recs, PRODUCTS)}
    comp = {}
    if "B0" in batches:
        comp["replication_B0_vs_G1"] = {
            "first_shown_share": {"G1": res["batches"]["G1"]["first_shown"]["share"], "B0": res["batches"]["B0"]["first_shown"]["share"]},
            "e2_flags": {"G1": res["batches"]["G1"]["e2_v02_cells"]["flags"], "B0": res["batches"]["B0"]["e2_v02_cells"]["flags"]},
            "replay_similarity_same_order": regression.paired_similarity(g1, batches["B0"])}
    if "E4" in batches and "B0" in batches:
        e1b, e1e = res["batches"]["B0"]["e1"], res["batches"]["E4"]["e1"]
        shared = [p for p in strict_personas(batches["B0"]) if p in strict_personas(batches["E4"])]
        comp["order_mechanism_E4_vs_B0"] = {"personas": shared,
            "e1_B0": {p: e1b[p] for p in shared}, "e1_E4": {p: e1e[p] for p in shared},
            "test": sign_flip([e1b[p] - e1e[p] for p in shared], "greater")}
    if "R1" in batches and "B0" in batches:
        comp["config_regression_R1_vs_B0"] = {
            "first_shown_test": paired_label_test(batches["B0"], batches["R1"]),
            "report": regression.compare(batches["B0"], batches["R1"], PRODUCTS, reference=(g1, batches["B0"]),
                                         extra_metrics={"first_shown_share": (res["batches"]["B0"]["first_shown"]["share"], res["batches"]["R1"]["first_shown"]["share"]),
                                                        "e3_issue_count": (res["batches"]["B0"]["e3"]["persona_response_issue_count"], res["batches"]["R1"]["e3"]["persona_response_issue_count"]),
                                                        "gate": (res["batches"]["B0"]["gate"]["suggested_action"], res["batches"]["R1"]["gate"]["suggested_action"])})}
    if "E0A" in batches and "B0" in batches:
        a, b = res["batches"]["E0A"]["e0"], res["batches"]["B0"]["e0"]
        keys = sorted(set(a) & set(b))
        comp["stability_E0A_vs_B0"] = {"cells": len(keys), "mean_E0A": float(np.mean([a[k] for k in keys])) if keys else None,
                                        "mean_B0": float(np.mean([b[k] for k in keys])) if keys else None,
                                        "test": sign_flip_mc([a[k] - b[k] for k in keys])}
    if "L2" in by:
        comp["label_L2"] = {"coverage": coverage(by["L2"]), **mcnemar_label(by["L2"])}
    res["comparisons"] = comp
    out.mkdir(parents=True)
    text = json.dumps(res, ensure_ascii=False, indent=2, sort_keys=True, default=float, allow_nan=False) + "\n"
    (out / "stage2_results.json").write_text(text, encoding="utf-8")
    (out / "method_manifest.json").write_text(json.dumps({
        "analysis_version": ANALYSIS_VERSION, "script_sha256": sha(Path(__file__)), "batch_dir": str(batch_dir),
        "plan_dir": str(plan_dir), "e2_sims_per_persona": sims, "policy": "configs/release_policy.v2.example.yaml",
        "results_sha256": hashlib.sha256(text.encode()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
    return res


def sign_flip_mc(diffs, sims=200000, seed=11):
    """Monte Carlo sign-flip (one-sided, mean > 0) for many paired cells."""
    diffs = np.array([d for d in diffs if d is not None])
    if diffs.size == 0:
        return None
    obs = diffs.sum()
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1, 1], size=(sims, diffs.size))
    null = (signs * diffs).sum(axis=1)
    return {"n": int(diffs.size), "mean_difference": float(diffs.mean()),
            "one_sided_p": (1 + int(np.count_nonzero(null >= obs - 1e-12))) / (sims + 1), "sims": sims}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-dir", type=Path, default=ROOT / "runs/stage2-live-20261005/batch")
    ap.add_argument("--plan-dir", type=Path, default=ROOT / "runs/stage2-plan-20261005")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--e2-sims", type=int, default=19999)
    a = ap.parse_args()
    if a.out_dir.exists():
        raise SystemExit(f"Refusing to overwrite {a.out_dir}")
    r = analyze(a.batch_dir, a.plan_dir, a.out_dir, a.e2_sims)
    summary = {k: {"gate": v["gate"]["suggested_action"], "first_shown": v["first_shown"]["share"],
                   "e2_flags": v["e2_v02_cells"]["flags"]} for k, v in r["batches"].items()}
    print(json.dumps({"is_mock": r["is_mock"], "batches": summary}, ensure_ascii=False))
