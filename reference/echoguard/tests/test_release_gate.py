from echoguard.release_gate import GateMetrics, evaluate_release_gate

POLICY = {
    "evidence_floor": {"evaluated_persona_count_lt": 3},
    "hard_holds": {"suitability_violation_count_gt": 0, "valid_run_rate_lt": 0.95},
    "human_review": {
        "excess_convergence_q_lt": 0.05,
        "excess_convergence_delta_gt": 0.15,
        "persona_response_issue_count_gt": 0,
    },
    "watch": {"version_weighted_jaccard_lt": 0.70, "issuer_hhi_delta_gt": 0.08},
}


def test_release_gate_holds_suitability_violation():
    m = GateMetrics(valid_run_rate=0.99, suitability_violation_count=1, evaluated_persona_count=3)
    assert evaluate_release_gate(m, POLICY).suggested_action == "HOLD"


def test_release_gate_review_excess_convergence():
    m = GateMetrics(
        valid_run_rate=0.99,
        suitability_violation_count=0,
        evaluated_persona_count=3,
        excess_convergence_q=0.01,
        excess_convergence_delta=0.20,
    )
    assert evaluate_release_gate(m, POLICY).suggested_action == "HUMAN_REVIEW"


def test_release_gate_passes_clean_case():
    m = GateMetrics(
        valid_run_rate=0.99,
        suitability_violation_count=0,
        evaluated_persona_count=3,
        excess_convergence_q=0.4,
        excess_convergence_delta=0.02,
        persona_response_issue_count=0,
        version_weighted_jaccard=0.85,
        issuer_hhi_delta=0.01,
    )
    assert evaluate_release_gate(m, POLICY).suggested_action == "PASS"


def test_release_gate_blocks_all_abstain_as_insufficient_evidence():
    # A well-formed ABSTAIN is an operationally valid response, but it supplies no
    # analyzable portfolio evidence for E0/E1/E2 and must never fall through to PASS.
    m = GateMetrics(
        valid_run_rate=1.0,
        suitability_violation_count=0,
        evaluated_persona_count=0,
    )
    assert evaluate_release_gate(m, POLICY).suggested_action == "INSUFFICIENT_EVIDENCE"
