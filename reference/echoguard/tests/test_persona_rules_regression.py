from echoguard import persona_rules, regression
from echoguard.schemas import Product


def P(pid, cls, risk, liq, exp=False, short=True):
    return Product(pid, pid, pid, "iss-" + pid, cls, "x", cls.upper(), "R", risk, "experimental_normalization",
                   "daily", liq, 0, short, exp, "http://x", "", "t")


PRODUCTS = [P("C1", "cash", 1, 3), P("B1", "fixed_income", 2, 2), P("E1", "equity", 4, 2, exp=True, short=False)]


def test_rule_flags_risk_increase_when_capacity_falls():
    d = {"PER_BASE": {"m": {1: {"C1": 0.5, "B1": 0.5}}}, "PER_RISK_LOW": {"m": {1: {"C1": 0.2, "B1": 0.8}}}}
    r = persona_rules.evaluate(d, PRODUCTS)
    assert r["persona_response_issue_count"] == 1
    d["PER_RISK_LOW"]["m"][1] = {"C1": 0.6, "B1": 0.4}
    assert persona_rules.evaluate(d, PRODUCTS)["persona_response_issue_count"] == 0


def rec(provider, persona, repeat, pf, order=("C1", "B1")):
    return {"provider": provider, "persona_id": persona, "repeat": repeat, "order": list(order), "valid": True,
            "decision": "RECOMMEND", "portfolio": pf, "errors": []}


def test_regression_pairs_only_same_order():
    base = [rec("a", "p", 1, {"C1": 0.5, "B1": 0.5}), rec("b", "p", 1, {"C1": 0.5, "B1": 0.5})]
    cand = [rec("a", "p", 1, {"C1": 0.5, "B1": 0.5}), rec("b", "p", 1, {"C1": 1.0}, order=("B1", "C1"))]
    r = regression.compare(base, cand, PRODUCTS)
    assert r["behavior_similarity"]["paired_recommendations"] == 1
    assert r["behavior_similarity"]["order_mismatch"] == 1
    assert r["behavior_similarity"]["mean_weighted_jaccard"] == 1.0
