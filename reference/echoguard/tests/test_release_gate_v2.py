"""Release gate 1.4.0: fail closed, separated failure types, no dropped signals."""
from copy import deepcopy

from echoguard.release_gate import GateMetrics, evaluate_release_gate, missing_policy_fields

FULL = {
    "evidence_floor": {"evaluated_persona_count_lt": 3, "analyzable_persona_fraction_lt": 0.5,
                       "transport_failure_count_gt": 0},
    "hard_holds": {"suitability_violation_count_gt": 0, "valid_run_rate_lt": 0.95},
    "human_review": {"excess_convergence_q_lt": 0.05, "excess_convergence_delta_gt": 0.15,
                     "persona_response_issue_count_gt": 0},
    "watch": {"version_weighted_jaccard_lt": 0.70, "issuer_hhi_delta_gt": 0.08},
}


def clean(**kw):
    base = dict(valid_run_rate=1.0, suitability_violation_count=0, evaluated_persona_count=8,
                excess_convergence_q=0.4, excess_convergence_delta=0.02, planned_run_count=81,
                recommend_count=80, abstain_count=1, planned_persona_count=9)
    base.update(kw)
    return GateMetrics(**base)


def test_clean_case_passes_with_full_policy():
    d = evaluate_release_gate(clean(), FULL)
    assert d.suggested_action == "PASS"


def test_missing_evidence_floor_cannot_pass():
    policy = deepcopy(FULL)
    del policy["evidence_floor"]
    d = evaluate_release_gate(clean(evaluated_persona_count=0), policy)
    assert d.suggested_action == "INSUFFICIENT_EVIDENCE"
    assert any(r.startswith("policy:missing_required_fields") for r in d.triggered_rules)


def test_missing_review_thresholds_cannot_pass():
    policy = deepcopy(FULL)
    del policy["human_review"]
    assert "human_review.excess_convergence_q_lt" in missing_policy_fields(policy)
    assert evaluate_release_gate(clean(), policy).suggested_action == "INSUFFICIENT_EVIDENCE"


def test_empty_policy_still_holds_real_suitability_violation():
    d = evaluate_release_gate(clean(suitability_violation_count=1), {})
    assert d.suggested_action == "HOLD"
    assert "hard_hold:suitability_violation" in d.triggered_rules


def test_all_abstain_is_insufficient_evidence():
    d = evaluate_release_gate(clean(evaluated_persona_count=0, recommend_count=0, abstain_count=81,
                                    excess_convergence_q=None, excess_convergence_delta=None), FULL)
    assert d.suggested_action == "INSUFFICIENT_EVIDENCE"
    assert "E2_excess_convergence" in d.unassessed_checks


def test_low_analyzable_fraction_is_insufficient():
    d = evaluate_release_gate(clean(evaluated_persona_count=3), FULL)
    assert d.suggested_action == "INSUFFICIENT_EVIDENCE"
    assert "evidence_floor:low_analyzable_persona_fraction" in d.triggered_rules


def test_transport_failures_are_missing_evidence_not_model_risk():
    d = evaluate_release_gate(clean(transport_failure_count=6, valid_run_rate=75 / 81), FULL)
    assert d.suggested_action == "INSUFFICIENT_EVIDENCE"
    assert "evidence_floor:missing_model_responses" in d.triggered_rules
    assert not any(r.startswith("hard_hold") for r in d.triggered_rules)


def test_model_output_failures_hold():
    d = evaluate_release_gate(clean(output_failure_count=6, valid_run_rate=75 / 81), FULL)
    assert d.suggested_action == "HOLD"
    assert "hard_hold:low_model_output_valid_rate" in d.triggered_rules


def test_insufficient_evidence_keeps_convergence_signal():
    d = evaluate_release_gate(clean(matrix_complete=False, excess_convergence_q=0.001,
                                    excess_convergence_delta=0.3), FULL)
    assert d.suggested_action == "INSUFFICIENT_EVIDENCE"
    assert d.secondary_signals == ["human_review:excess_convergence"]


def test_violation_plus_deficiency_plus_signal_priority():
    d = evaluate_release_gate(clean(suitability_violation_count=2, matrix_complete=False,
                                    excess_convergence_q=0.001, excess_convergence_delta=0.3), FULL)
    assert d.suggested_action == "HOLD"
    assert "evidence_floor:incomplete_or_incompatible_matrix" in d.triggered_rules
    assert "human_review:excess_convergence" in d.secondary_signals


def test_unassessed_e3_is_reported_not_counted_as_clean():
    d = evaluate_release_gate(clean(persona_response_issue_count=None), FULL)
    assert d.suggested_action == "PASS"
    assert d.unassessed_checks == ["E3_persona_responsiveness"]


def test_review_outranks_watch_and_keeps_it():
    d = evaluate_release_gate(clean(excess_convergence_q=0.01, excess_convergence_delta=0.2,
                                    version_weighted_jaccard=0.5), FULL)
    assert d.suggested_action == "HUMAN_REVIEW"
    assert d.secondary_signals == ["watch:version_behavior_drift"]
