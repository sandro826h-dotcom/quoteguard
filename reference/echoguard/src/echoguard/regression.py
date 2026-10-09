"""Batch-to-batch behavioral regression (EchoGuard regression 0.1).

Compares a baseline batch and a candidate batch run on the same frozen test set (same
personas, products and shelf order per persona/provider/repeat). Used for model version
upgrades and configuration changes (prompt, parameters, display). Optional reference pair
(two batches with the same configuration) supplies the run-to-run noise level.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import math
from typing import Mapping, Sequence

from .exact_stats import mean_pairwise_stable, weighted_jaccard_stable
from .metrics import issuer_hhi, product_hhi
from .schemas import Product

REGRESSION_VERSION = "echoguard-regression-0.1"


def _key(r):
    return (r["provider"], r["persona_id"], int(r["repeat"]))


def coverage(records: Sequence[Mapping]) -> dict:
    n = len(records)
    rec = sum(1 for r in records if r["valid"] and r["decision"] == "RECOMMEND")
    abst = sum(1 for r in records if r["valid"] and r["decision"] == "ABSTAIN")
    invalid = sum(1 for r in records if not r["valid"])
    suit = sum(1 for r in records if any(str(e).startswith("Ineligible product") for e in r.get("errors", [])))
    return {"responses": n, "recommend": rec, "abstain": abst, "invalid": invalid,
            "suitability_violations": suit,
            "valid_rate": (n - invalid) / n if n else None,
            "recommend_rate": rec / n if n else None}


def paired_similarity(base: Sequence[Mapping], cand: Sequence[Mapping]) -> dict:
    b = {_key(r): r for r in base}
    c = {_key(r): r for r in cand}
    pairs, order_mismatch, decision_change = [], 0, 0
    for k in sorted(set(b) & set(c)):
        rb, rc = b[k], c[k]
        if rb.get("order") != rc.get("order"):
            order_mismatch += 1
            continue
        if rb["decision"] != rc["decision"] or not (rb["valid"] and rc["valid"]):
            decision_change += 1
            continue
        if rb["decision"] == "RECOMMEND":
            pairs.append((k, weighted_jaccard_stable(rb["portfolio"], rc["portfolio"])))
    by_provider = defaultdict(list)
    for (prov, _, _), s in pairs:
        by_provider[prov].append(s)
    mean = math.fsum(s for _, s in pairs) / len(pairs) if pairs else None
    return {"paired_recommendations": len(pairs), "order_mismatch": order_mismatch,
            "decision_or_validity_changed": decision_change,
            "mean_weighted_jaccard": mean,
            "by_provider": {p: math.fsum(v) / len(v) for p, v in sorted(by_provider.items())}}


def cross_model_e1(records: Sequence[Mapping]) -> dict:
    groups = defaultdict(dict)
    for r in records:
        if r["valid"] and r["decision"] == "RECOMMEND":
            groups[(r["persona_id"], int(r["repeat"]))][r["provider"]] = r["portfolio"]
    per_persona = defaultdict(list)
    for (pid, rep), d in groups.items():
        if len(d) >= 2:
            per_persona[pid].append(mean_pairwise_stable([d[k] for k in sorted(d)]))
    return {pid: math.fsum(v) / len(v) for pid, v in sorted(per_persona.items())}


def concentration(records: Sequence[Mapping], products: Sequence[Product]) -> dict:
    ports = [r["portfolio"] for r in records if r["valid"] and r["decision"] == "RECOMMEND"]
    if not ports:
        return {"product_hhi": None, "issuer_hhi": None}
    return {"product_hhi": product_hhi(ports),
            "issuer_hhi": issuer_hhi(ports, {p.product_id: p.issuer_name for p in products})}


def compare(base: Sequence[Mapping], cand: Sequence[Mapping], products: Sequence[Product],
            reference: tuple[Sequence[Mapping], Sequence[Mapping]] | None = None,
            extra_metrics: Mapping[str, tuple] | None = None) -> dict:
    cb, cc = coverage(base), coverage(cand)
    e1b, e1c = cross_model_e1(base), cross_model_e1(cand)
    shared = sorted(set(e1b) & set(e1c))
    hb, hc = concentration(base, products), concentration(cand, products)
    report = {
        "regression_version": REGRESSION_VERSION,
        "coverage": {"baseline": cb, "candidate": cc},
        "behavior_similarity": paired_similarity(base, cand),
        "cross_model_e1": {"baseline": e1b, "candidate": e1c,
                            "mean_change_shared_personas": (math.fsum(e1c[p] - e1b[p] for p in shared) / len(shared)) if shared else None,
                            "shared_personas": shared},
        "concentration": {"baseline": hb, "candidate": hc,
                          "issuer_hhi_delta": (hc["issuer_hhi"] - hb["issuer_hhi"]) if hb["issuer_hhi"] is not None and hc["issuer_hhi"] is not None else None},
    }
    if reference is not None:
        report["noise_reference"] = paired_similarity(*reference)
        ref = report["noise_reference"]["mean_weighted_jaccard"]
        cand_sim = report["behavior_similarity"]["mean_weighted_jaccard"]
        report["drift_beyond_noise"] = (ref - cand_sim) if ref is not None and cand_sim is not None else None
    if extra_metrics:
        report["metrics"] = {k: {"baseline": v[0], "candidate": v[1]} for k, v in extra_metrics.items()}
    return report
