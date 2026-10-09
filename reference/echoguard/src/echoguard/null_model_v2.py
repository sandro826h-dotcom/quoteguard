"""Analysis v2 null models with deterministic statistics and exact tie handling.

Two nulls are provided:

* ``independent_repeat_null``: the historical v1 null (each run randomized independently
  inside matched economic cells). It consumes the random stream exactly as
  ``null_model.simulate_repeated_k_model_null`` does, so for the same seed the simulated
  portfolios are identical to v1 and only the statistic/tie rule differs. It is kept as the
  historical comparator; synthetic calibration showed it over-flags when a model keeps the
  same product preference across repeats.
* ``persistent_cell_null``: the frozen candidate ``persistent-cell-BY-candidate-0.1``
  (scripts/experimental_persistent_v01.py). One uniform label permutation per provider and
  economic cell is reused across all repeats, which preserves each model's own
  cross-repeat structure. Random draws are identical to the candidate for the same seed.
  Validity still assumes products are exchangeable inside each cell.
"""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Iterable, Mapping, Sequence

import numpy as np

from .exact_stats import (
    as_fraction, exact_upper_tail, repeated_stat_exact, repeated_stat_stable,
)
from .null_model import (
    _cell_structure, _eligible_by_cell, _randomize_from_structure, _validate_structures,
)
from .schemas import Product

PAIRS = ((0, 1), (0, 2), (1, 2))


def independent_repeat_null(
    observed_repeat_groups: Sequence[Sequence[Mapping[str, float]]],
    products: Sequence[Product],
    eligible_ids: Iterable[str],
    num_simulations: int,
    seed: int,
) -> dict:
    if not observed_repeat_groups or any(len(g) < 2 for g in observed_repeat_groups):
        raise ValueError("Every repeat group needs at least two model portfolios")
    pmap = {p.product_id: p for p in products}
    pools = _eligible_by_cell(products, set(eligible_ids))
    structures = [[_cell_structure(port, pmap) for port in group] for group in observed_repeat_groups]
    _validate_structures([s for group in structures for s in group], pools)

    rng = np.random.default_rng(seed)
    simulated: list[list[list[dict]]] = []
    values = np.empty(num_simulations, dtype=float)
    for b in range(num_simulations):
        groups = [[_randomize_from_structure(s, pools, rng) for s in group] for group in structures]
        simulated.append(groups)
        values[b] = repeated_stat_stable(groups)

    observed_exact = repeated_stat_exact(observed_repeat_groups)
    tail = exact_upper_tail(observed_exact, values, lambda i: repeated_stat_exact(simulated[i]))
    null_mean = math.fsum(values.tolist()) / len(values)
    return {
        "observed": float(observed_exact),
        "observed_exact": observed_exact,
        "null_mean": null_mean,
        "delta": float(observed_exact) - null_mean,
        "null_p95": float(np.quantile(values, 0.95)),
        **tail,
    }


def _maps_for(cells: Sequence[Sequence[int]], providers: int, sims: int, rng: np.random.Generator) -> np.ndarray:
    n = sum(len(cell) for cell in cells)
    maps = np.broadcast_to(np.arange(n), (sims, providers, n)).copy()
    for cell in cells:
        ids = np.asarray(cell)
        order = np.argsort(rng.random((sims, providers, len(ids))), axis=-1)
        maps[..., ids] = ids[order]
    return maps


def _float_statistic(x: np.ndarray) -> np.ndarray:
    parts = []
    for a, b in PAIRS:
        num = np.minimum(x[..., a, :], x[..., b, :]).sum(axis=-1)
        den = np.maximum(x[..., a, :], x[..., b, :]).sum(axis=-1)
        parts.append(num / den)
    return np.mean(parts, axis=0).mean(axis=-1)


def _exact_statistic(xf: Sequence[Sequence[Sequence[Fraction]]], mapping=None) -> Fraction:
    total = Fraction(0)
    repeats = len(xf)
    for r in range(repeats):
        rows = []
        for p in range(3):
            row = xf[r][p]
            rows.append([row[j] for j in mapping[p]] if mapping is not None else list(row))
        for a, b in PAIRS:
            num = sum((min(u, v) for u, v in zip(rows[a], rows[b])), Fraction(0))
            den = sum((max(u, v) for u, v in zip(rows[a], rows[b])), Fraction(0))
            total += num / den
    return total / (3 * repeats)


def persistent_cell_null(
    weights: Sequence[Sequence[Sequence[float]]],
    cells: Sequence[Sequence[int]],
    num_simulations: int,
    seed: int,
) -> dict:
    """weights[r][provider][product] for repeats x 3 providers x full eligible inventory."""
    x = np.asarray(weights, dtype=float)
    if x.ndim != 3 or x.shape[0] < 2 or x.shape[1] != 3:
        raise ValueError("Expected repeats x 3 providers x products with at least two repeats")
    flat = [i for cell in cells for i in cell]
    if sorted(flat) != list(range(x.shape[2])) or not cells or any(not c for c in cells):
        raise ValueError("Cells must partition the full eligible inventory")
    if not np.all(np.isfinite(x)) or np.any(x < 0) or np.any(x.sum(axis=-1) <= 0):
        raise ValueError("Empty, negative or non-finite portfolios refused")
    xf = [[[as_fraction(float(v)) for v in row] for row in rep] for rep in x.tolist()]

    rng = np.random.default_rng(seed)
    maps = _maps_for(cells, 3, num_simulations, rng)
    moved = np.take_along_axis(np.broadcast_to(x, (num_simulations,) + x.shape), maps[:, None, :, :], axis=-1)
    values = _float_statistic(moved)
    del moved

    observed_exact = _exact_statistic(xf)
    tail = exact_upper_tail(observed_exact, values, lambda i: _exact_statistic(xf, maps[i].tolist()))
    null_mean = math.fsum(values.tolist()) / len(values)
    return {
        "observed": float(observed_exact),
        "observed_exact": observed_exact,
        "null_mean": null_mean,
        "delta": float(observed_exact) - null_mean,
        "null_p95": float(np.quantile(values, 0.95)),
        **tail,
    }
