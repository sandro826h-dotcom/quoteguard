"""Paired tests, bootstrap intervals and multiplicity adjustment."""
from __future__ import annotations

import math
from typing import Callable, Mapping, Sequence

import numpy as np
from scipy.stats import binomtest, norm

_EXACT_MAX_N = 20
_ENUM_CHUNK = 1 << 15


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p from discordant counts b and c (binomial, p=0.5)."""
    if b < 0 or c < 0:
        raise ValueError("counts must be non-negative")
    n = b + c
    if n == 0:
        return 1.0
    return float(binomtest(min(b, c), n, 0.5).pvalue)


def _clean(values) -> np.ndarray:
    arr = np.array([v for v in values if v is not None], dtype=float)
    return arr[~np.isnan(arr)]


def _tail_count(null: np.ndarray, obs: float, alternative: str, eps: float) -> int:
    if alternative == "greater":
        return int(np.count_nonzero(null >= obs - eps))
    if alternative == "less":
        return int(np.count_nonzero(null <= obs + eps))
    if alternative == "two-sided":
        return int(np.count_nonzero(np.abs(null) >= abs(obs) - eps))
    raise ValueError(f"unknown alternative {alternative!r}")


def _sign_flip_null(d: np.ndarray, sims: int, rng, chunk: int = 4000) -> np.ndarray:
    out = np.empty(sims)
    for s in range(0, sims, chunk):
        m = min(chunk, sims - s)
        signs = rng.integers(0, 2, size=(m, d.size), dtype=np.int8) * 2 - 1
        out[s:s + m] = signs @ d
    return out


def _sign_flip_p(d: np.ndarray, sims: int, seed: int, alternative: str):
    eps = 1e-9 * max(1.0, float(np.abs(d).sum()))
    obs = float(d.sum())
    if d.size <= _EXACT_MAX_N:
        total = 2 ** d.size
        count = 0
        shifts = np.arange(d.size)
        for start in range(0, total, _ENUM_CHUNK):
            masks = np.arange(start, min(total, start + _ENUM_CHUNK))
            signs = ((masks[:, None] >> shifts) & 1) * 2.0 - 1.0
            count += _tail_count(signs @ d, obs, alternative, eps)
        return count / total, "exact"
    null = _sign_flip_null(d, sims, np.random.default_rng(seed))
    count = _tail_count(null, obs, alternative, eps)
    return (1 + count) / (sims + 1), "monte_carlo"


def paired_permutation(diffs: Sequence[float], sims: int = 99999, seed: int = 0,
                       alternative: str = "two-sided") -> dict:
    """Sign-flip test on paired differences. Exact enumeration when n <= 20.

    Returns n, mean_difference, p, alternative, method, sims. None for empty input.
    """
    d = _clean(diffs)
    if d.size == 0:
        return {"n": 0, "mean_difference": None, "p": None, "alternative": alternative,
                "method": None, "sims": 0}
    p, method = _sign_flip_p(d, sims, seed, alternative)
    return {"n": int(d.size), "mean_difference": float(d.mean()), "p": float(p),
            "alternative": alternative, "method": method, "sims": 0 if method == "exact" else sims}


def stratified_permutation(pairs_by_stratum: Mapping[str, Sequence[tuple[float, float]]],
                           sims: int = 99999, seed: int = 0, alternative: str = "two-sided") -> dict:
    """Swap the (a, b) labels within each pair; statistic is the pooled mean of a - b.

    Strata are kept for per-stratum reporting; pairs are only exchanged within themselves.
    """
    per = {}
    all_d = []
    for name in sorted(pairs_by_stratum):
        d = np.array([float(a) - float(b) for a, b in pairs_by_stratum[name]])
        per[name] = {"n": int(d.size), "mean_difference": float(d.mean()) if d.size else None}
        all_d.append(d)
    d = np.concatenate(all_d) if all_d else np.array([])
    if d.size == 0:
        return {"n": 0, "mean_difference": None, "p": None, "alternative": alternative,
                "method": None, "sims": 0, "strata": per}
    p, method = _sign_flip_p(d, sims, seed, alternative)
    return {"n": int(d.size), "mean_difference": float(d.mean()), "p": float(p),
            "alternative": alternative, "method": method, "sims": 0 if method == "exact" else sims,
            "strata": per}


def bootstrap_ci(values: Sequence[float], stat: Callable = np.mean, B: int = 9999,
                 alpha: float = 0.05, seed: int = 0) -> dict:
    """Percentile bootstrap interval, resampling units with replacement."""
    v = _clean(values)
    if v.size == 0:
        return {"n": 0, "estimate": None, "lo": None, "hi": None, "B": B, "alpha": alpha}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, v.size, size=(B, v.size))
    boots = np.array([stat(v[row]) for row in idx])
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return {"n": int(v.size), "estimate": float(stat(v)), "lo": float(lo), "hi": float(hi),
            "B": B, "alpha": alpha}


def holm(pvalues: Mapping[str, float]) -> dict:
    """Holm step-down adjusted p values."""
    keys = sorted(pvalues, key=lambda k: (pvalues[k], k))
    out, running, n = {}, 0.0, len(keys)
    for rank, k in enumerate(keys):
        running = max(running, min(1.0, (n - rank) * pvalues[k]))
        out[k] = running
    return out


def generic_stratified_permutation(units: Sequence[Mapping], statistic: Callable[[list], float],
                                   sims: int = 99999, seed: int = 0, alternative: str = "two-sided") -> dict:
    """Permutation test with arbitrary per-unit records.

    Each unit is a mapping with keys "A" and "B" (any records, e.g. tp/fp/fn counts). statistic maps the
    list of all units to a scalar (e.g. micro-F1 of A minus micro-F1 of B). A and B labels are exchanged
    independently inside each unit. Up to 16 units are enumerated exactly, otherwise Monte Carlo.
    """
    units = [dict(u) for u in units]
    n = len(units)
    if n == 0:
        return {"n": 0, "observed": None, "p": None, "alternative": alternative, "method": None, "sims": 0}
    obs = float(statistic(units))
    eps = 1e-12 * max(1.0, abs(obs))

    def swapped(mask):
        return [{**u, "A": u["B"], "B": u["A"]} if f else u for u, f in zip(units, mask)]

    if n <= 16:
        null = np.array([statistic(swapped([(m >> i) & 1 for i in range(n)])) for m in range(2 ** n)], float)
        method, used = "exact", 0
        p = _tail_count(null, obs, alternative, eps) / null.size
    else:
        rng = np.random.default_rng(seed)
        flips = rng.integers(0, 2, size=(sims, n))
        null = np.array([statistic(swapped(row)) for row in flips], float)
        method, used = "monte_carlo", sims
        p = (1 + _tail_count(null, obs, alternative, eps)) / (sims + 1)
    return {"n": n, "observed": obs, "p": float(p), "alternative": alternative, "method": method, "sims": used}


def _tango_restricted(b: int, c: int, n: int, theta0: float) -> float:
    """Restricted MLE of p01 (new fails, reference succeeds) given p10 - p01 = theta0."""
    m = n - b - c
    B = b * (1 - theta0) + c * (1 - 3 * theta0) - 2 * m * theta0
    disc = max(0.0, B * B + 8 * n * c * theta0 * (1 - theta0))
    lo, hi = max(0.0, -theta0), (1 - theta0) / 2
    best, best_ll = None, -math.inf
    for u in ((B + math.sqrt(disc)) / (4 * n), (B - math.sqrt(disc)) / (4 * n)):
        u = min(max(u, lo), hi)
        q = u + theta0
        z = 1 - u - q
        if min(u, q, z) < 0:
            continue
        ll = sum(k * math.log(pr) for k, pr in ((b, q), (c, u), (m, z)) if k > 0) \
            if all(pr > 0 or k == 0 for k, pr in ((b, q), (c, u), (m, z))) else -math.inf
        if ll > best_ll:
            best, best_ll = u, ll
    return lo if best is None else best


def tango_noninferiority(b: int, c: int, n: int, delta: float) -> dict:
    """Tango (1998) score test for paired binary non-inferiority.

    b: pairs with new correct and reference wrong; c: new wrong and reference correct; n: all pairs.
    H0: p_new - p_ref <= -delta against H1: p_new - p_ref > -delta. Returns the score statistic and the
    one-sided p value. With delta = 0 the statistic equals the McNemar score statistic.
    """
    if n <= 0 or b < 0 or c < 0 or b + c > n or delta < 0:
        raise ValueError("invalid counts or delta")
    theta0 = -float(delta)
    u = _tango_restricted(b, c, n, theta0)
    var = 2 * u + theta0 - theta0 ** 2
    diff = (b - c) / n
    num = diff - theta0
    if var <= 0:
        z = math.inf if num > 0 else (-math.inf if num < 0 else 0.0)
    else:
        z = num / math.sqrt(var / n)
    return {"statistic": z, "p": float(norm.sf(z)), "theta_hat": diff, "delta": float(delta), "n": n,
            "b": b, "c": c}


def _cluster_draws(records, cluster_key, stat, B, seed):
    groups: dict = {}
    for r in records:
        k = r[cluster_key] if isinstance(cluster_key, str) else cluster_key(r)
        groups.setdefault(k, []).append(r)
    clusters = [groups[k] for k in sorted(groups, key=str)]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(clusters), size=(B, len(clusters)))
    return np.array([stat([r for i in row for r in clusters[i]]) for row in idx], float), len(clusters)


def cluster_bootstrap(records: Sequence, cluster_key, stat: Callable[[list], float], B: int = 9999,
                      alpha: float = 0.05, seed: int = 0) -> dict:
    """Percentile interval resampling whole clusters (e.g. tasks); stat sees all drawn records."""
    records = list(records)
    if not records:
        return {"n_clusters": 0, "estimate": None, "lo": None, "hi": None, "B": B, "alpha": alpha}
    boots, k = _cluster_draws(records, cluster_key, stat, B, seed)
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return {"n_clusters": k, "estimate": float(stat(records)), "lo": float(lo), "hi": float(hi),
            "B": B, "alpha": alpha}


def noninferiority_bootstrap_lower(cluster_records: Mapping[str, Sequence[tuple[int, int]]], B: int = 9999,
                                   alpha: float = 0.05, seed: int = 0) -> dict:
    """One-sided lower bound of p_new - p_ref from a cluster bootstrap (primary non-inferiority analysis;
    the Tango test is the sensitivity analysis).

    cluster_records maps a cluster id (task) to its (new, reference) 0/1 outcome pairs.
    """
    records = [(cid, a, b) for cid, prs in cluster_records.items() for a, b in prs]
    if not records:
        return {"n_clusters": 0, "estimate": None, "lower": None, "B": B, "alpha": alpha}

    def stat(rs):
        return float(np.mean([a - b for _, a, b in rs]))
    boots, k = _cluster_draws(records, lambda r: r[0], stat, B, seed)
    return {"n_clusters": k, "estimate": stat(records), "lower": float(np.quantile(boots, alpha)),
            "B": B, "alpha": alpha}
