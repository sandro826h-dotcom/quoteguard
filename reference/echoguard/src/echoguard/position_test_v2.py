"""Position tests at the unit where an order was randomized.

The sharp null holds observed picks fixed while order is irrelevant. Repeated
picks under one order share a single random first product, rather than separate
Bernoulli trials. The constrained E5F global order has no supported test here.
Historical position_test.py is preserved for exact historical reproduction.
"""
from collections import defaultdict
from fractions import Fraction
from .position_test import holm


def first_shown_tests(picks, *, order_design, constrained_global=False):
    if order_design not in {"shared", "independent", "fixed", "global_fixed"}:
        raise ValueError("Unknown order design")
    per = defaultdict(list)
    for row in picks:
        members = row["members"]
        if len(members) < 2 or len(set(members)) != len(members) or row["picked"] not in members:
            raise ValueError("Invalid position-test pick")
        per[row["provider"]].append(row)
    out = {}
    for provider, rows in sorted(per.items()):
        first = sum(r["picked"] == r["members"][0] for r in rows)
        result = {"first": first, "picks": len(rows),
                  "expected_if_order_irrelevant": sum(1 / len(r["members"]) for r in rows),
                  "p": None, "holm_p": None, "order_design": order_design}
        if constrained_global or order_design == "global_fixed":
            result.update(status="unassessed", order_units=1,
                          reason="One constrained global order; product preference and position are confounded.",
                          method="descriptive_only")
            out[provider] = result
            continue
        units = defaultdict(list)
        for row in rows:
            key = (row["persona_id"], row["cell"]) if order_design == "fixed" else (row["persona_id"], row["repeat"], row["cell"])
            units[key].append(row)
        dist = {0: Fraction(1)}
        for group in units.values():
            members = group[0]["members"]
            if any(r["members"] != members for r in group):
                raise ValueError("Incompatible orders within a randomization unit")
            unit_dist = defaultdict(Fraction)
            for candidate in members:
                count = sum(r["picked"] == candidate for r in group)
                unit_dist[count] += Fraction(1, len(members))
            new = defaultdict(Fraction)
            for count, prob in dist.items():
                for add, probability in unit_dist.items():
                    new[count + add] += prob * probability
            dist = new
        result.update(status="assessed", order_units=len(units),
                      p=float(sum(prob for count, prob in dist.items() if count >= first)),
                      method="exact_convolution_of_randomized_order_units")
        out[provider] = result
    adjusted = holm({p: r["p"] for p, r in out.items() if r["p"] is not None})
    for p, val in adjusted.items():
        out[p]["holm_p"] = val
    return out
