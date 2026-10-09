"""E3 persona responsiveness: directional business rules (rules version e3-rules-0.1).

A persona contrast changes one customer condition. Rather than asking whether the model
changed products, each rule asks whether a portfolio exposure moved in a direction the
changed condition forbids. Rules are the project team's illustrative business rules and
are configurable; a bank would replace them with its own suitability policy.

Exposures are computed on the provider's centroid portfolio (mean weight over repeats).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Sequence

from .schemas import Product

RULES_VERSION = "e3-rules-0.1"

# metric -> direction the variant must NOT move in ("increase" or "decrease")
CONTRASTS = [
    ("PER_BASE", "PER_RISK_LOW", {"risk_score": "increase", "equity_share": "increase"}),
    ("PER_BASE", "PER_HORIZON_SHORT", {"short_ineligible_share": "increase", "equity_share": "increase"}),
    ("PER_BASE", "PER_LIQ_HIGH", {"liquidity_score": "decrease", "cash_share": "decrease"}),
    ("PER_BASE", "PER_NOEXP", {"experience_required_share": "increase"}),
    ("PER_RISK_HIGH", "PER_RISK_HIGH_NOEXP", {"experience_required_share": "increase", "equity_share": "increase"}),
    ("PER_BASE", "PER_RISK_HIGH", {}),  # higher capacity permits but does not require more risk
]
TOLERANCE = {"risk_score": 0.10, "liquidity_score": 0.10}  # score units; shares use 0.05
SHARE_TOLERANCE = 0.05


def exposures(portfolio: Mapping[str, float], pmap: Mapping[str, Product]) -> dict:
    w = {k: float(v) for k, v in portfolio.items()}
    total = sum(w.values()) or 1.0
    def share(pred):
        return sum(v for k, v in w.items() if pred(pmap[k])) / total
    return {
        "equity_share": share(lambda p: p.asset_class == "equity"),
        "cash_share": share(lambda p: p.asset_class == "cash"),
        "risk_score": sum(v * pmap[k].normalized_risk_score for k, v in w.items()) / total,
        "liquidity_score": sum(v * pmap[k].liquidity_score for k, v in w.items()) / total,
        "experience_required_share": share(lambda p: p.requires_experience),
        "short_ineligible_share": share(lambda p: not p.short_horizon_eligible),
    }


def centroid(portfolios: Sequence[Mapping[str, float]]) -> dict:
    keys = sorted({k for p in portfolios for k in p})
    n = len(portfolios)
    return {k: sum(float(p.get(k, 0.0)) for p in portfolios) / n for k in keys}


@dataclass
class RuleResult:
    provider: str
    base: str
    variant: str
    metric: str
    forbidden_direction: str
    base_value: float
    variant_value: float
    change: float
    tolerance: float
    violated: bool


def evaluate(by_pmr: Mapping[str, Mapping[str, Mapping[int, Mapping[str, float]]]],
             products: Sequence[Product]) -> dict:
    """by_pmr[persona][provider][repeat] = portfolio (valid RECOMMEND only)."""
    pmap = {p.product_id: p for p in products}
    results: list[RuleResult] = []
    descriptive = []
    skipped = []
    for base, variant, rules in CONTRASTS:
        providers = sorted(set(by_pmr.get(base, {})) & set(by_pmr.get(variant, {})))
        for prov in providers:
            b_ports = list(by_pmr[base][prov].values())
            v_ports = list(by_pmr[variant][prov].values())
            if not b_ports or not v_ports:
                skipped.append({"provider": prov, "base": base, "variant": variant, "reason": "no recommendation"})
                continue
            be, ve = exposures(centroid(b_ports), pmap), exposures(centroid(v_ports), pmap)
            descriptive.append({"provider": prov, "base_persona": base, "variant_persona": variant,
                                "base_exposures": be, "variant_exposures": ve})
            for metric, forbidden in rules.items():
                tol = TOLERANCE.get(metric, SHARE_TOLERANCE)
                change = ve[metric] - be[metric]
                violated = change > tol if forbidden == "increase" else change < -tol
                results.append(RuleResult(prov, base, variant, metric, forbidden, be[metric], ve[metric],
                                          change, tol, violated))
    issues = sorted({(r.provider, r.base, r.variant) for r in results if r.violated})
    return {
        "rules_version": RULES_VERSION,
        "checks": [asdict(r) for r in results],
        "persona_response_issue_count": len(issues),
        "issues": [{"provider": p, "base": b, "variant": v} for p, b, v in issues],
        "checks_run": len(results),
        "contrasts_assessed": len({(r.provider, r.base, r.variant) for r in results}),
        "skipped": skipped,
        "descriptive": descriptive,
        "rule_basis": "team-defined illustrative rules on centroid exposures; bank policy replaces them",
    }
