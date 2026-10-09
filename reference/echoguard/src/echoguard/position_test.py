"""Exact test for a single model's display-position preference (added in 1.6.0).

For each single pick from a same-group set of m eligible products, the chance that the first-listed
product is picked is 1/m if display order does not matter. The number of first-listed picks then
follows a Poisson-binomial distribution; the one-sided p value is its upper tail at the observed count.
Holm's step-down adjustment is applied over the models tested together.
"""
from __future__ import annotations

from typing import Mapping, Sequence


def poisson_binomial_upper(probs: Sequence[float], k: int) -> float:
    dist = [1.0] + [0.0] * len(probs)
    for p in probs:
        for j in range(len(dist) - 1, 0, -1):
            dist[j] = dist[j] * (1 - p) + dist[j - 1] * p
        dist[0] *= (1 - p)
    return min(1.0, max(0.0, sum(dist[k:])))


def holm(pvalues: Mapping[str, float]) -> dict:
    keys = sorted(pvalues, key=lambda k: (pvalues[k], k))
    out, running, n = {}, 0.0, len(keys)
    for rank, k in enumerate(keys):
        running = max(running, min(1.0, (n - rank) * pvalues[k]))
        out[k] = running
    return out


def first_shown_tests(picks: Mapping[str, Sequence[int]]) -> dict:
    """picks[provider] = group sizes m of each single pick, with a separate count of first-listed picks.

    Accepts {provider: {"first": k, "group_sizes": [m, ...]}} and returns raw and Holm-adjusted p values.
    """
    raw = {p: poisson_binomial_upper([1 / m for m in v["group_sizes"]], v["first"]) for p, v in picks.items()}
    adj = holm(raw)
    return {p: {"first": picks[p]["first"], "picks": len(picks[p]["group_sizes"]),
                "expected_if_order_irrelevant": sum(1 / m for m in picks[p]["group_sizes"]),
                "p": raw[p], "holm_p": adj[p]} for p in sorted(picks)}
