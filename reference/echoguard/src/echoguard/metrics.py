from __future__ import annotations
from itertools import combinations
from typing import Dict, Iterable, List, Mapping, Sequence
import math
import numpy as np

Portfolio = Dict[str, float]


def _keys_positive(p: Mapping[str, float]) -> set[str]:
    return {k for k, v in p.items() if v > 0}


def set_jaccard(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    sa, sb = _keys_positive(a), _keys_positive(b)
    if not sa and not sb:
        return 1.0
    u = sa | sb
    return len(sa & sb) / len(u) if u else 1.0


def weighted_jaccard(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    num = sum(min(float(a.get(k, 0.0)), float(b.get(k, 0.0))) for k in keys)
    den = sum(max(float(a.get(k, 0.0)), float(b.get(k, 0.0))) for k in keys)
    return num / den if den > 0 else 1.0


def cosine_similarity(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = sorted(set(a) | set(b))
    if not keys:
        return 1.0
    va = np.array([float(a.get(k, 0.0)) for k in keys], dtype=float)
    vb = np.array([float(b.get(k, 0.0)) for k in keys], dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def mean_pairwise(portfolios: Sequence[Mapping[str, float]], metric=weighted_jaccard) -> float:
    if len(portfolios) < 2:
        raise ValueError("Need at least two portfolios")
    vals = [metric(a, b) for a, b in combinations(portfolios, 2)]
    return float(np.mean(vals))


def centroid(portfolios: Sequence[Mapping[str, float]]) -> Portfolio:
    if not portfolios:
        raise ValueError("No portfolios")
    keys = sorted(set().union(*(p.keys() for p in portfolios)))
    out = {k: float(np.mean([float(p.get(k, 0.0)) for p in portfolios])) for k in keys}
    return {k: v for k, v in out.items() if v > 1e-12}


def hhi_from_shares(shares: Iterable[float]) -> float:
    vals = [float(x) for x in shares if float(x) > 0]
    s = sum(vals)
    if s <= 0:
        return 0.0
    return float(sum((x / s) ** 2 for x in vals))


def product_hhi(portfolios: Sequence[Mapping[str, float]]) -> float:
    agg: Dict[str, float] = {}
    for p in portfolios:
        for pid, w in p.items():
            agg[pid] = agg.get(pid, 0.0) + float(w)
    return hhi_from_shares(agg.values())


def issuer_hhi(portfolios: Sequence[Mapping[str, float]], product_to_issuer: Mapping[str, str]) -> float:
    agg: Dict[str, float] = {}
    for p in portfolios:
        for pid, w in p.items():
            issuer = product_to_issuer[pid]
            agg[issuer] = agg.get(issuer, 0.0) + float(w)
    return hhi_from_shares(agg.values())


def within_model_stability(repeats: Sequence[Mapping[str, float]]) -> Dict[str, float]:
    return {
        "weighted_jaccard": mean_pairwise(repeats, weighted_jaccard),
        "cosine": mean_pairwise(repeats, cosine_similarity),
        "set_jaccard": mean_pairwise(repeats, set_jaccard),
    }


def benjamini_hochberg(p_values: Sequence[float]) -> List[float]:
    """Return BH adjusted q-values in original order."""
    m = len(p_values)
    if m == 0:
        return []
    order = np.argsort(np.asarray(p_values, dtype=float))
    ranked = np.asarray(p_values, dtype=float)[order]
    adjusted = np.empty(m, dtype=float)
    running = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        running = min(running, ranked[i] * m / rank)
        adjusted[i] = running
    out = np.empty(m, dtype=float)
    out[order] = np.clip(adjusted, 0.0, 1.0)
    return out.tolist()
