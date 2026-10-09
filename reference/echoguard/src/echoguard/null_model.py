from __future__ import annotations
from collections import defaultdict
from typing import Dict, Iterable, Mapping, Sequence
import numpy as np
from .schemas import Product
from .metrics import weighted_jaccard, mean_pairwise

Portfolio = Dict[str, float]


def _cell_structure(portfolio: Mapping[str, float], product_map: Mapping[str, Product]):
    """Preserve selected count and exact weight multiset inside each economic cell."""
    cells = defaultdict(list)
    for pid, weight in portfolio.items():
        if weight <= 0:
            continue
        cells[product_map[pid].economic_cell].append(float(weight))
    return {cell: sorted(weights, reverse=True) for cell, weights in cells.items()}


def _eligible_by_cell(products: Iterable[Product], eligible_ids: set[str]):
    out = defaultdict(list)
    for p in products:
        if p.product_id in eligible_ids:
            out[p.economic_cell].append(p.product_id)
    return dict(out)


def _validate_structures(structures, pools) -> None:
    for struct in structures:
        for cell, weights in struct.items():
            pool = pools.get(cell, [])
            if len(pool) < len(weights):
                raise ValueError(
                    f"Null design invalid: cell={cell} has {len(pool)} eligible products "
                    f"but observed portfolio selects {len(weights)}. Expand the matched cell."
                )


def _randomize_from_structure(struct, pools, rng: np.random.Generator) -> Portfolio:
    port: Portfolio = {}
    for cell, weights in struct.items():
        pool = pools[cell]
        chosen = rng.choice(pool, size=len(weights), replace=False)
        shuffled_weights = rng.permutation(np.asarray(weights, dtype=float))
        for pid, w in zip(chosen, shuffled_weights):
            port[str(pid)] = float(w)
    return port


def simulate_k_model_null(
    observed_portfolios: Sequence[Mapping[str, float]],
    products: Sequence[Product],
    eligible_ids: Iterable[str],
    num_simulations: int = 10000,
    seed: int = 20260925,
    metric=weighted_jaccard,
) -> np.ndarray:
    """Single-repeat K-model conditional randomization null."""
    if len(observed_portfolios) < 2:
        raise ValueError("Need at least two model portfolios")
    pmap = {p.product_id: p for p in products}
    eligible_ids = set(eligible_ids)
    pools = _eligible_by_cell(products, eligible_ids)
    structures = [_cell_structure(p, pmap) for p in observed_portfolios]
    _validate_structures(structures, pools)

    rng = np.random.default_rng(seed)
    stats = np.empty(num_simulations, dtype=float)
    for b in range(num_simulations):
        simulated = [_randomize_from_structure(s, pools, rng) for s in structures]
        stats[b] = mean_pairwise(simulated, metric=metric)
    return stats


def simulate_repeated_k_model_null(
    observed_repeat_groups: Sequence[Sequence[Mapping[str, float]]],
    products: Sequence[Product],
    eligible_ids: Iterable[str],
    num_simulations: int = 10000,
    seed: int = 20260925,
    metric=weighted_jaccard,
) -> np.ndarray:
    """Repeated K-model conditional randomization null.

    `observed_repeat_groups[r]` contains one portfolio per model for the same repeat r.
    The observed statistic should be the mean of the repeat-level K-model pairwise
    similarities. Each null simulation randomizes every individual run inside matched
    economic cells, then recomputes that exact same repeated statistic. This avoids
    centroid-support inflation when a model selects different products across repeats.
    """
    if not observed_repeat_groups:
        raise ValueError("Need at least one repeat group")
    if any(len(group) < 2 for group in observed_repeat_groups):
        raise ValueError("Every repeat group needs at least two model portfolios")

    pmap = {p.product_id: p for p in products}
    eligible_ids = set(eligible_ids)
    pools = _eligible_by_cell(products, eligible_ids)
    repeat_structures = [
        [_cell_structure(port, pmap) for port in group]
        for group in observed_repeat_groups
    ]
    _validate_structures([s for group in repeat_structures for s in group], pools)

    rng = np.random.default_rng(seed)
    stats = np.empty(num_simulations, dtype=float)
    for b in range(num_simulations):
        repeat_stats = []
        for group in repeat_structures:
            simulated = [_randomize_from_structure(s, pools, rng) for s in group]
            repeat_stats.append(mean_pairwise(simulated, metric=metric))
        stats[b] = float(np.mean(repeat_stats))
    return stats


def evaluate_excess_convergence(observed_stat: float, null_dist: np.ndarray) -> Dict[str, float | bool]:
    null_dist = np.asarray(null_dist, dtype=float)
    if null_dist.size == 0:
        raise ValueError("Empty null distribution")
    mean_null = float(np.mean(null_dist))
    p = float((1 + np.sum(null_dist >= observed_stat)) / (null_dist.size + 1))
    return {
        "observed_stat": float(observed_stat),
        "null_mean": mean_null,
        "excess_delta": float(observed_stat - mean_null),
        "empirical_p_value": p,
        "null_p95": float(np.quantile(null_dist, 0.95)),
        "null_p99": float(np.quantile(null_dist, 0.99)),
        "significant_0_05": bool(p < 0.05),
    }
