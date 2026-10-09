"""Gate 1.6.0: test-based single-model position rule and the exact Poisson-binomial helper."""
import math
from pathlib import Path

import yaml

from echoguard.position_test import first_shown_tests, holm, poisson_binomial_upper
from echoguard.release_gate import GateMetrics, evaluate_release_gate

ROOT = Path(__file__).resolve().parents[1]
V4 = yaml.safe_load((ROOT / "configs/release_policy.v4.example.yaml").read_text(encoding="utf-8"))
V3 = yaml.safe_load((ROOT / "configs/release_policy.v3.example.yaml").read_text(encoding="utf-8"))


def _m(**kw):
    base = dict(valid_run_rate=1.0, suitability_violation_count=0, evaluated_persona_count=8, matrix_complete=True,
                excess_convergence_q=1.0, excess_convergence_delta=None, persona_response_issue_count=0,
                planned_run_count=81, transport_failure_count=0, output_failure_count=0, recommend_count=81,
                abstain_count=0, planned_persona_count=9)
    base.update(kw)
    return GateMetrics(**base)


def test_poisson_binomial_matches_binomial():
    n, k = 20, 12
    exact = sum(math.comb(n, j) * 0.25 ** j * 0.75 ** (n - j) for j in range(k, n + 1))
    assert abs(poisson_binomial_upper([0.25] * n, k) - exact) < 1e-12
    assert poisson_binomial_upper([0.5, 0.5], 0) == 1.0


def test_holm_monotone():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}


def test_first_shown_tests_shape():
    t = first_shown_tests({"qwen": {"first": 16, "group_sizes": [4] * 17}, "kimi": {"first": 5, "group_sizes": [4] * 20}})
    assert t["qwen"]["holm_p"] < 1e-6
    assert t["kimi"]["holm_p"] > 0.05
    assert t["qwen"]["picks"] == 17


def test_v4_requires_significance_and_floor():
    m = _m(first_shown_share_by_provider={"kimi": 0.55, "qwen": 0.94},
           first_shown_picks_by_provider={"kimi": 11, "qwen": 17},
           first_shown_p_by_provider={"kimi": 0.08, "qwen": 1e-8})
    d = evaluate_release_gate(m, V4)
    assert d.suggested_action == "WATCH"
    assert d.triggered_rules == ["watch:single_model_position_bias:qwen"]


def test_v4_without_p_values_is_unassessed_not_triggered():
    m = _m(first_shown_share_by_provider={"qwen": 0.94}, first_shown_picks_by_provider={"qwen": 17})
    d = evaluate_release_gate(m, V4)
    assert d.suggested_action == "PASS"
    assert "single_model_position_test" in d.unassessed_checks


def test_v3_behaviour_unchanged():
    m = _m(first_shown_share_by_provider={"kimi": 0.70, "qwen": 0.94}, first_shown_picks_by_provider={"kimi": 23, "qwen": 17})
    d = evaluate_release_gate(m, V3)
    assert d.triggered_rules == ["watch:single_model_position_bias:kimi", "watch:single_model_position_bias:qwen"]
