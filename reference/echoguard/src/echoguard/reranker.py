from __future__ import annotations
from typing import Dict, List, Mapping, Sequence
import numpy as np
from scipy.optimize import minimize
from .schemas import Product, Persona
from .suitability import is_eligible


def rerank_with_issuer_hhi_penalty(
    original_weights: np.ndarray,
    products: Sequence[Product],
    persona: Persona,
    issuer_exposure_before: Mapping[str, float] | None = None,
    lambda_issuer: float = 0.5,
    lambda_fidelity: float = 1.0,
    max_class_drift: float = 0.03,
    max_product_weight: float = 0.60,
) -> np.ndarray:
    """Governance what-if; not used in Stage 1 empirical claims."""
    n = len(products)
    if len(original_weights) != n:
        raise ValueError("Length mismatch")
    original_weights = np.asarray(original_weights, dtype=float)
    issuers = sorted({p.issuer_name for p in products})
    issuer_index = {x: i for i, x in enumerate(issuers)}
    A = np.zeros((len(issuers), n), dtype=float)
    for j, p in enumerate(products):
        A[issuer_index[p.issuer_name], j] = 1.0
    prior = np.zeros(len(issuers), dtype=float)
    if issuer_exposure_before:
        for name, val in issuer_exposure_before.items():
            if name in issuer_index:
                prior[issuer_index[name]] = float(val)
    eligible = np.array([is_eligible(p, persona) for p in products], dtype=bool)
    bounds = [(0.0, max_product_weight if eligible[i] else 0.0) for i in range(n)]

    classes = sorted({p.asset_class for p in products})
    class_idx = {c: [i for i,p in enumerate(products) if p.asset_class == c] for c in classes}

    def objective(w):
        fidelity = 0.5 * lambda_fidelity * np.sum((w - original_weights) ** 2)
        issuer_after = prior + A @ w
        concentration = lambda_issuer * np.sum(issuer_after ** 2)
        return fidelity + concentration

    constraints = [{'type':'eq','fun':lambda w: np.sum(w) - 1.0}]
    for _, idx in class_idx.items():
        orig = float(np.sum(original_weights[idx]))
        constraints.append({'type':'ineq','fun':lambda w, idx=idx, orig=orig: max_class_drift - (np.sum(w[idx]) - orig)})
        constraints.append({'type':'ineq','fun':lambda w, idx=idx, orig=orig: max_class_drift + (np.sum(w[idx]) - orig)})

    w0 = original_weights.copy()
    w0[~eligible] = 0.0
    if w0.sum() <= 0:
        raise ValueError("No eligible initial mass; reranking infeasible")
    w0 /= w0.sum()
    res = minimize(objective, w0, method='SLSQP', bounds=bounds, constraints=constraints,
                   options={'maxiter':1000, 'ftol':1e-10})
    if not res.success:
        raise ValueError(f"Reranker did not converge: {res.message}")
    out = np.clip(res.x, 0, None)
    out /= out.sum()
    return out
