import yaml
from pathlib import Path

from echoguard.release_gate import GateMetrics, evaluate_release_gate

ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((ROOT / "configs/release_policy.v3.example.yaml").read_text(encoding="utf-8"))


def _m(**kw):
    base = dict(valid_run_rate=1.0, suitability_violation_count=0, evaluated_persona_count=8,
                excess_convergence_q=1.0, excess_convergence_delta=0.0, persona_response_issue_count=0,
                planned_run_count=81, planned_persona_count=9)
    base.update(kw)
    return GateMetrics(**base)


def test_position_bias_turns_pass_into_watch():
    d = evaluate_release_gate(_m(first_shown_share_by_provider={"kimi": 0.70, "qwen": 0.94},
                                 first_shown_picks_by_provider={"kimi": 23, "qwen": 17}), POLICY)
    assert d.suggested_action == "WATCH"
    assert d.triggered_rules == ["watch:single_model_position_bias:kimi", "watch:single_model_position_bias:qwen"]


def test_position_bias_needs_minimum_picks():
    d = evaluate_release_gate(_m(first_shown_share_by_provider={"kimi": 0.9},
                                 first_shown_picks_by_provider={"kimi": 5}), POLICY)
    assert d.suggested_action == "PASS"


def test_position_bias_below_threshold_passes():
    d = evaluate_release_gate(_m(first_shown_share_by_provider={"kimi": 0.30},
                                 first_shown_picks_by_provider={"kimi": 30}), POLICY)
    assert d.suggested_action == "PASS"


def test_position_bias_is_secondary_under_human_review():
    d = evaluate_release_gate(_m(excess_convergence_q=0.001, excess_convergence_delta=0.3,
                                 first_shown_share_by_provider={"deepseek": 0.8},
                                 first_shown_picks_by_provider={"deepseek": 20}), POLICY)
    assert d.suggested_action == "HUMAN_REVIEW"
    assert "watch:single_model_position_bias:deepseek" in d.secondary_signals


def test_position_bias_unassessed_when_metric_missing():
    d = evaluate_release_gate(_m(), POLICY)
    assert d.suggested_action == "PASS"
    assert "single_model_position_bias" in d.unassessed_checks


def test_v2_policy_unchanged():
    v2 = yaml.safe_load((ROOT / "configs/release_policy.v2.example.yaml").read_text(encoding="utf-8"))
    d = evaluate_release_gate(_m(first_shown_share_by_provider={"kimi": 0.9},
                                 first_shown_picks_by_provider={"kimi": 30}), v2)
    assert d.suggested_action == "PASS" and d.unassessed_checks == []
