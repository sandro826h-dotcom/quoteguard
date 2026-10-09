"""Deterministic similarity statistics with exact tie handling (EchoGuard analysis v2).

Why this module exists
----------------------
Analysis v1 (frozen with batch 20261002T160551Z-13b1b9342a) computes weighted Jaccard by
iterating a Python ``set`` and summing floats in that order, and it counts the Monte Carlo
upper tail with a raw ``null >= observed`` comparison. Changing ``PYTHONHASHSEED`` changes
the set order, which moves the statistic by about one ulp; values that are equal in exact
arithmetic (theoretical ties) can then fall on either side of the observed value.

v2 rule
-------
* Products are iterated in sorted order and float sums use ``math.fsum`` (correctly rounded,
  independent of order). Float values are used only for display, means and screening.
* Tail counts use exact rational arithmetic. Model weights arrive as decimal JSON text; the
  shortest round-trip ``repr`` of the parsed float recovers that decimal, so ``Fraction``
  reproduces the value the model emitted. The statistic is a finite average of ratios of
  sums of those decimals, so it is an exact rational number and ties are decided exactly.
* For speed, simulated values farther than ``NEAR_TIE_SCREEN`` from the observed value are
  ordered by their float value. The float statistic differs from the exact one by far less
  than 1e-13 (a few dozen correctly rounded operations on numbers in [0, 2]), so the screen
  cannot change any ordering; everything inside the screen is recomputed exactly.

Why not a fixed tolerance such as 1e-12: a tolerance treats two different exact values as
tied whenever they are closer than the tolerance, and nothing in the design bounds how close
two distinct averages of ratios can be. The exact rule has no such failure mode and needs no
tuning. Tolerance-based p-values are still computed in the v2 report as a cross-check.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
import math
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

ANALYSIS_VERSION = "echoguard-analysis-2.0"
NEAR_TIE_SCREEN = 1e-9
LEGACY_TOLERANCE = 1e-12


def as_fraction(value) -> Fraction:
    """Exact rational for a model-reported weight (float parsed from JSON, int, str or Decimal)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TypeError("boolean is not a weight")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite weight")
        return Fraction(repr(value))
    return Fraction(str(value))


def _keys(a: Mapping[str, object], b: Mapping[str, object]) -> list[str]:
    return sorted(set(a) | set(b))


def weighted_jaccard_stable(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = _keys(a, b)
    if not keys:
        return 1.0
    num = math.fsum(min(float(a.get(k, 0.0)), float(b.get(k, 0.0))) for k in keys)
    den = math.fsum(max(float(a.get(k, 0.0)), float(b.get(k, 0.0))) for k in keys)
    return num / den if den > 0 else 1.0


def weighted_jaccard_exact(a: Mapping[str, object], b: Mapping[str, object]) -> Fraction:
    keys = _keys(a, b)
    if not keys:
        return Fraction(1)
    zero = Fraction(0)
    fa = {k: as_fraction(a.get(k, 0)) for k in keys}
    fb = {k: as_fraction(b.get(k, 0)) for k in keys}
    num = sum((min(fa[k], fb[k]) for k in keys), zero)
    den = sum((max(fa[k], fb[k]) for k in keys), zero)
    return num / den if den > 0 else Fraction(1)


def mean_pairwise_stable(portfolios: Sequence[Mapping[str, float]]) -> float:
    if len(portfolios) < 2:
        raise ValueError("Need at least two portfolios")
    vals = [weighted_jaccard_stable(a, b) for a, b in combinations(portfolios, 2)]
    return math.fsum(vals) / len(vals)


def mean_pairwise_exact(portfolios: Sequence[Mapping[str, object]]) -> Fraction:
    if len(portfolios) < 2:
        raise ValueError("Need at least two portfolios")
    vals = [weighted_jaccard_exact(a, b) for a, b in combinations(portfolios, 2)]
    return sum(vals, Fraction(0)) / len(vals)


def repeated_stat_stable(groups: Sequence[Sequence[Mapping[str, float]]]) -> float:
    if not groups:
        raise ValueError("Need at least one repeat group")
    return math.fsum(mean_pairwise_stable(g) for g in groups) / len(groups)


def repeated_stat_exact(groups: Sequence[Sequence[Mapping[str, object]]]) -> Fraction:
    if not groups:
        raise ValueError("Need at least one repeat group")
    return sum((mean_pairwise_exact(g) for g in groups), Fraction(0)) / len(groups)


def exact_upper_tail(
    observed: Fraction,
    null_values: np.ndarray,
    exact_value: Callable[[int], Fraction],
) -> dict:
    """Count simulated values >= observed in exact arithmetic.

    ``null_values`` are float statistics; ``exact_value(i)`` recomputes simulation ``i`` exactly.
    Returns the add-one Monte Carlo p-value as a Fraction plus diagnostics.
    """
    null_values = np.asarray(null_values, dtype=float)
    if null_values.ndim != 1 or null_values.size == 0:
        raise ValueError("Empty or malformed null distribution")
    obs_f = float(observed)
    diff = null_values - obs_f
    clearly_ge = int(np.count_nonzero(diff > NEAR_TIE_SCREEN))
    near_idx = np.flatnonzero(np.abs(diff) <= NEAR_TIE_SCREEN)
    near_ge = exact_ties = 0
    for i in near_idx:
        v = exact_value(int(i))
        if v >= observed:
            near_ge += 1
        if v == observed:
            exact_ties += 1
    count = clearly_ge + near_ge
    b = int(null_values.size)
    return {
        "p_exact": Fraction(1 + count, b + 1),
        "tail_count": count,
        "near_screen_count": int(near_idx.size),
        "exact_tie_count": exact_ties,
        "num_sims": b,
        # Cross-checks reported side by side; never used for decisions.
        "p_raw_float": float((1 + np.count_nonzero(null_values >= obs_f)) / (b + 1)),
        "p_tolerance_1e12": float((1 + np.count_nonzero(null_values >= obs_f - LEGACY_TOLERANCE)) / (b + 1)),
    }


def bh_exact(pvalues: Sequence[Fraction]) -> list[Fraction]:
    """Benjamini-Hochberg adjusted values in exact arithmetic, original order."""
    m = len(pvalues)
    if m == 0:
        return []
    ps = [as_fraction(p) for p in pvalues]
    if any(p < 0 or p > 1 for p in ps):
        raise ValueError("p-values must lie in [0, 1]")
    order = sorted(range(m), key=lambda i: (ps[i], i))
    out: list[Fraction] = [Fraction(0)] * m
    running = Fraction(1)
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        out[i] = min(running, Fraction(1))
    return out


def by_exact(pvalues: Sequence[Fraction]) -> list[Fraction]:
    """Benjamini-Yekutieli: BH times the harmonic number, capped at 1."""
    m = len(pvalues)
    harmonic = sum((Fraction(1, i) for i in range(1, m + 1)), Fraction(0))
    return [min(q * harmonic, Fraction(1)) for q in bh_exact(pvalues)]


def to_float_list(values: Iterable[Fraction]) -> list[float]:
    return [float(v) for v in values]
