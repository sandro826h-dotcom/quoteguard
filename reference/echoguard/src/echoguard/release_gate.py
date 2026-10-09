from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Mapping, List

# Policy fields without which a PASS cannot be justified. Missing fields make the gate
# fail closed (INSUFFICIENT_EVIDENCE) instead of silently skipping the rule. Added in 1.4.0.
REQUIRED_POLICY_FIELDS = (
    ("evidence_floor", "evaluated_persona_count_lt"),
    ("hard_holds", "suitability_violation_count_gt"),
    ("hard_holds", "valid_run_rate_lt"),
    ("human_review", "excess_convergence_q_lt"),
    ("human_review", "excess_convergence_delta_gt"),
)


@dataclass
class GateMetrics:
    valid_run_rate: float
    suitability_violation_count: int
    # Personas with enough valid RECOMMEND data to actually compute E0/E1/E2. Distinct
    # from valid_run_rate: an all-ABSTAIN batch can have valid_run_rate=1.0 (every run
    # was a well-formed response) while evaluated_persona_count=0 (there was nothing to
    # analyze). Without this field a no-evidence batch and a clean-evidence batch both
    # read as "nothing triggered" and fall through to the same PASS -- see
    # CHANGELOG_v1.3.2.md for the reproduced case.
    evaluated_persona_count: int = 0
    matrix_complete: bool = True
    evidence_issues: List[str] = field(default_factory=list)
    excess_convergence_q: float | None = None
    excess_convergence_delta: float | None = None
    # None means "not assessed". 0 means "assessed and nothing found". Analysis v1 passed 0
    # although E3 had no validated rule; v2 passes None and lists it under unassessed checks.
    persona_response_issue_count: int | None = 0
    version_weighted_jaccard: float | None = None
    issuer_hhi_delta: float | None = None
    # Coverage breakdown (1.4.0). When planned_run_count is None the legacy single
    # valid_run_rate rule is used unchanged.
    planned_run_count: int | None = None
    transport_failure_count: int = 0      # no model response received (network, auth, quota)
    output_failure_count: int = 0         # response received but parse/schema/rule validation failed
    recommend_count: int | None = None
    abstain_count: int | None = None
    planned_persona_count: int | None = None
    # Single-model display-position bias (1.5.0): for each provider, the share of single picks from
    # a same-cell group that went to the first-shown product, and the number of such picks.
    # None means not assessed. Cross-model checks can pass while each model still follows order.
    first_shown_share_by_provider: Dict[str, float] | None = None
    first_shown_picks_by_provider: Dict[str, int] | None = None
    # 1.6.0: Holm-adjusted one-sided exact p value of each provider's first-shown count against the
    # order-irrelevant expectation (echoguard.position_test). Used when the policy sets
    # watch.single_model_first_shown_p_lt; the share threshold then acts as a materiality floor.
    first_shown_p_by_provider: Dict[str, float] | None = None


@dataclass
class GateDecision:
    suggested_action: str
    triggered_rules: List[str]
    metrics: Dict[str, Any]
    disclaimer: str = (
        "Illustrative decision-support output only. Final model approval remains a human bank governance decision."
    )
    # Review/watch signals seen in the available data while a higher-priority outcome
    # (HOLD or INSUFFICIENT_EVIDENCE) was returned. They are reported, not acted on.
    secondary_signals: List[str] = field(default_factory=list)
    unassessed_checks: List[str] = field(default_factory=list)


def _gt(value: float | int | None, threshold: float | int | None) -> bool:
    return value is not None and threshold is not None and value > threshold


def _lt(value: float | int | None, threshold: float | int | None) -> bool:
    return value is not None and threshold is not None and value < threshold


def missing_policy_fields(policy: Mapping[str, Any] | None) -> List[str]:
    policy = policy or {}
    missing = []
    for section, key in REQUIRED_POLICY_FIELDS:
        block = policy.get(section) or {}
        if not isinstance(block, Mapping) or block.get(key) is None:
            missing.append(f"{section}.{key}")
    return missing


def evaluate_release_gate(metrics: GateMetrics, policy: Mapping[str, Any]) -> GateDecision:
    """Config-driven model release recommendation.

    The function intentionally contains no embedded regulatory thresholds. Every threshold comes
    from an external policy configuration so the same engine can be adapted to a bank's own risk
    appetite. It is a decision-support layer, not an autonomous model approval system.

    Priority: HOLD (real suitability violation or model output failures) > INSUFFICIENT_EVIDENCE
    (incomplete matrix, missing responses, too little analyzable data, incomplete policy) >
    HUMAN_REVIEW > WATCH > PASS. Lower-priority signals are kept in ``secondary_signals``.
    """
    policy = policy or {}
    triggered: List[str] = []
    hard = policy.get("hard_holds", {}) or {}
    review = policy.get("human_review", {}) or {}
    watch = policy.get("watch", {}) or {}
    evidence = policy.get("evidence_floor", {}) or {}

    # 1. Hard holds. A missing suitability threshold defaults to 0 so a violation always holds.
    suit_thr = hard.get("suitability_violation_count_gt", 0)
    if _gt(metrics.suitability_violation_count, suit_thr):
        triggered.append("hard_hold:suitability_violation")
    deficiencies: List[str] = []
    if metrics.planned_run_count is None:
        # Legacy rule (analysis v1): one validity rate covering every failure type.
        if metrics.matrix_complete and _lt(metrics.valid_run_rate, hard.get("valid_run_rate_lt")):
            triggered.append("hard_hold:low_valid_run_rate")
    else:
        received = metrics.planned_run_count - metrics.transport_failure_count
        if received > 0:
            output_valid_rate = 1 - metrics.output_failure_count / received
            if _lt(output_valid_rate, hard.get("valid_run_rate_lt")):
                triggered.append("hard_hold:low_model_output_valid_rate")
        transport_thr = evidence.get("transport_failure_count_gt", 0)
        if _gt(metrics.transport_failure_count, transport_thr):
            deficiencies.append("evidence_floor:missing_model_responses")

    # 2. Evidence deficiencies.
    missing = missing_policy_fields(policy)
    if missing:
        deficiencies.append("policy:missing_required_fields:" + ",".join(missing))
    if not metrics.matrix_complete or metrics.evidence_issues:
        deficiencies.append("evidence_floor:incomplete_or_incompatible_matrix")
    floor = evidence.get("evaluated_persona_count_lt")
    if floor is not None and metrics.evaluated_persona_count < floor:
        deficiencies.append("evidence_floor:too_few_analyzable_personas")
    frac_floor = evidence.get("analyzable_persona_fraction_lt")
    if frac_floor is not None and metrics.planned_persona_count:
        if metrics.evaluated_persona_count / metrics.planned_persona_count < frac_floor:
            deficiencies.append("evidence_floor:low_analyzable_persona_fraction")

    # 3. Review and watch signals are always computed so they are never silently dropped.
    review_hits: List[str] = []
    q_thr = review.get("excess_convergence_q_lt")
    d_thr = review.get("excess_convergence_delta_gt")
    if _lt(metrics.excess_convergence_q, q_thr) and _gt(metrics.excess_convergence_delta, d_thr):
        review_hits.append("human_review:excess_convergence")
    if _gt(metrics.persona_response_issue_count, review.get("persona_response_issue_count_gt")):
        review_hits.append("human_review:persona_responsiveness")
    watch_hits: List[str] = []
    if _lt(metrics.version_weighted_jaccard, watch.get("version_weighted_jaccard_lt")):
        watch_hits.append("watch:version_behavior_drift")
    if _gt(metrics.issuer_hhi_delta, watch.get("issuer_hhi_delta_gt")):
        watch_hits.append("watch:issuer_concentration_change")
    pos_thr = watch.get("single_model_first_shown_share_gt")
    pos_p = watch.get("single_model_first_shown_p_lt")
    pos_min = watch.get("single_model_min_picks", 1)
    pos_rule = pos_thr is not None or pos_p is not None
    if pos_rule and metrics.first_shown_share_by_provider:
        picks = metrics.first_shown_picks_by_provider or {}
        pvals = metrics.first_shown_p_by_provider or {}
        for provider in sorted(metrics.first_shown_share_by_provider):
            share = metrics.first_shown_share_by_provider[provider]
            if picks.get(provider, 0) < pos_min:
                continue
            if pos_thr is not None and not _gt(share, pos_thr):
                continue
            if pos_p is not None and not _lt(pvals.get(provider), pos_p):
                continue
            watch_hits.append(f"watch:single_model_position_bias:{provider}")

    unassessed: List[str] = []
    if metrics.persona_response_issue_count is None:
        unassessed.append("E3_persona_responsiveness")
    if metrics.excess_convergence_q is None:
        unassessed.append("E2_excess_convergence")
    if pos_rule and not metrics.first_shown_share_by_provider:
        unassessed.append("single_model_position_bias")
    elif pos_p is not None and not metrics.first_shown_p_by_provider:
        unassessed.append("single_model_position_test")

    m = asdict(metrics)
    if triggered:
        return GateDecision("HOLD", triggered + deficiencies, m,
                            secondary_signals=review_hits + watch_hits, unassessed_checks=unassessed)
    if deficiencies:
        return GateDecision(
            "INSUFFICIENT_EVIDENCE", deficiencies, m,
            disclaimer="Incomplete or non-analyzable evidence cannot support a full-batch PASS.",
            secondary_signals=review_hits + watch_hits, unassessed_checks=unassessed,
        )
    if review_hits:
        return GateDecision("HUMAN_REVIEW", review_hits, m,
                            secondary_signals=watch_hits, unassessed_checks=unassessed)
    if watch_hits:
        return GateDecision("WATCH", watch_hits, m, unassessed_checks=unassessed)
    return GateDecision("PASS", [], m, unassessed_checks=unassessed)
