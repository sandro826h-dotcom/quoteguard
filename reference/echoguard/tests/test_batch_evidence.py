from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from echoguard.batch import canonical_source_hashes, evidence_issues, expected_keys, sha256
from echoguard.release_gate import GateMetrics, evaluate_release_gate


FAMILIES = ["deepseek", "qwen", "kimi"]
PERSONAS = [f"PER_{i}" for i in range(9)]
SCENARIO = "S0_BASELINE"
POLICY = {
    "evidence_floor": {"evaluated_persona_count_lt": 3},
    "hard_holds": {"suitability_violation_count_gt": 0, "valid_run_rate_lt": 0.95},
}


@pytest.fixture
def frozen_matrix(tmp_path):
    f = tmp_path / "input.csv"
    f.write_text("frozen inputs", encoding="utf-8")
    keys = expected_keys(FAMILIES, PERSONAS, SCENARIO, 3)
    manifest = {
        "batch_id": "batch-1", "expected_run_keys": keys,
        "actual_run_keys": keys.copy(), "file_hashes": {"products": sha256(f)},
        "plan": {"kind": "pilot", "mock": True, "providers": FAMILIES,
                 "personas": PERSONAS, "scenario": SCENARIO, "repeats": 3,
                 "prompt_version": "v0.2", "seed": 42,
                 "models": {p: f"mock-{p}" for p in FAMILIES},
                 "requests": {p: {"base_url": None, "temperature": 0.3,
                                  "max_tokens": 800, "extra_payload": {}} for p in FAMILIES}},
    }
    runs = []
    for family in FAMILIES:
        for persona in PERSONAS:
            for repeat in range(1, 4):
                key = f"{family}__{persona}__{SCENARIO}__named__r{repeat}"
                seed = int(hashlib.sha256(f"{persona}|{SCENARIO}|{repeat}".encode()).hexdigest()[:8], 16)
                runs.append({"batch_id": "batch-1", "is_mock": True,
                             "run_id": key, "valid": True, "errors": [],
                             "parsed": {"decision": "ABSTAIN", "portfolio": []}, "meta": {
                                 "run_id": key, "model_family": family, "model_name": f"mock-{family}",
                                 "persona_id": persona, "scenario_id": SCENARIO,
                                 "shelf_condition": "named", "repeat_id": repeat,
                                 "shelf_order_seed": seed, "prompt_version": "v0.2"}})
    return manifest, runs, {"products": f}


def test_complete_matrix_is_analyzable(frozen_matrix):
    assert evidence_issues(*frozen_matrix) == []


@pytest.mark.parametrize("remove", [
    lambda runs: [r for r in runs if r["meta"]["model_family"] != "kimi"],
    lambda runs: runs[:-1],
])
def test_missing_provider_or_single_repeat_blocks_batch(frozen_matrix, remove):
    manifest, runs, files = frozen_matrix
    assert any("missing run keys" in issue for issue in evidence_issues(manifest, remove(runs), files))


def test_duplicate_key_and_changed_inputs_block_batch(frozen_matrix):
    manifest, runs, files = frozen_matrix
    assert any("duplicate run keys" in issue for issue in evidence_issues(manifest, runs + [runs[0]], files))
    files["products"].write_text("changed", encoding="utf-8")
    assert any("file hash changed" in issue for issue in evidence_issues(manifest, runs, files))


def test_cross_batch_record_blocks_batch(frozen_matrix):
    manifest, runs, files = frozen_matrix
    tampered = deepcopy(runs)
    tampered[0]["batch_id"] = "another-batch"
    assert any("incompatible run metadata" in issue for issue in evidence_issues(manifest, tampered, files))


@pytest.mark.parametrize("field", ["models", "requests", "seed"])
def test_missing_frozen_request_configuration_blocks_batch(frozen_matrix, field):
    manifest, runs, files = frozen_matrix
    manifest["plan"].pop(field)
    assert any("invalid manifest or input" in issue for issue in evidence_issues(manifest, runs, files))


@pytest.mark.parametrize("field", ["base_url", "temperature", "max_tokens", "extra_payload"])
def test_incomplete_provider_request_blocks_batch(frozen_matrix, field):
    manifest, runs, files = frozen_matrix
    manifest["plan"]["requests"]["kimi"].pop(field)
    assert any("plan request fields" in issue for issue in evidence_issues(manifest, runs, files))


def test_missing_model_or_shelf_seed_blocks_batch(frozen_matrix):
    manifest, runs, files = frozen_matrix
    manifest["plan"]["models"].pop("kimi")
    assert any("plan models" in issue for issue in evidence_issues(manifest, runs, files))
    manifest["plan"]["models"]["kimi"] = "mock-kimi"
    runs[0]["meta"].pop("shelf_order_seed")
    assert any("shelf order seed mismatch" in issue for issue in evidence_issues(manifest, runs, files))


@pytest.fixture
def real_matrix(frozen_matrix):
    manifest, runs, files = frozen_matrix
    manifest["plan"]["mock"] = False
    for request in manifest["plan"]["requests"].values():
        request["base_url"] = "https://example.invalid/v1"
    for run in runs:
        family = run["meta"]["model_family"]
        run["is_mock"] = False
        run["meta"]["model_version"] = f"returned-{family}"
        run["provider_metadata"] = {
            **manifest["plan"]["requests"][family],
            "provider_family": family, "requested_model": manifest["plan"]["models"][family],
            "returned_model": f"returned-{family}", "request_id": "test-request",
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            "latency_ms": 12.0, "finish_reason": "stop",
        }
    return manifest, runs, files


def test_complete_real_request_metadata_is_analyzable(real_matrix):
    assert evidence_issues(*real_matrix) == []


@pytest.mark.parametrize("field,value", [
    ("requested_model", "another-model"), ("provider_family", "another-provider"),
    ("temperature", 0.8), ("max_tokens", 801),
    ("base_url", "https://different.invalid/v1"), ("extra_payload", {"thinking": {"type": "enabled"}}),
])
def test_real_request_configuration_drift_blocks_batch(real_matrix, field, value):
    manifest, runs, files = real_matrix
    runs[0]["provider_metadata"][field] = value
    assert any("mismatch" in issue for issue in evidence_issues(manifest, runs, files))


def test_truncated_real_response_blocks_even_parseable_json(real_matrix):
    manifest, runs, files = real_matrix
    runs[0]["provider_metadata"]["finish_reason"] = "length"
    assert any("reached token limit" in issue for issue in evidence_issues(manifest, runs, files))


def test_invalid_response_still_checks_outbound_model(real_matrix):
    manifest, runs, files = real_matrix
    runs[0].update(valid=False, failure_stage="validation")
    runs[0]["meta"]["model_name"] = "another-model"
    assert any("requested model mismatch" in issue for issue in evidence_issues(manifest, runs, files))


def test_transport_parameters_are_frozen_separately_from_payload(real_matrix):
    manifest, runs, files = real_matrix
    transports = {family: {"min_interval_s": 25.0 if family == "kimi" else 0.0,
                           "timeout_s": 90.0, "max_retries": 2} for family in FAMILIES}
    manifest["plan"]["transport_parameters"] = transports
    for run in runs:
        run["provider_metadata"]["transport_parameters"] = deepcopy(transports[run["meta"]["model_family"]])
    assert evidence_issues(manifest, runs, files) == []
    # A failed HTTP attempt must still identify the transport used for that attempt.
    runs[0].update(valid=False, failure_stage="provider")
    runs[0]["provider_metadata"]["transport_parameters"]["min_interval_s"] = 99.0
    assert any("transport parameters mismatch" in issue for issue in evidence_issues(manifest, runs, files))


@pytest.mark.parametrize("bad_transport", [
    {}, {"min_interval_s": 25.0, "timeout_s": 90.0},
    {"min_interval_s": -1.0, "timeout_s": 90.0, "max_retries": 2},
    {"min_interval_s": 25.0, "timeout_s": 0.0, "max_retries": 2},
    {"min_interval_s": 25.0, "timeout_s": 90.0, "max_retries": True},
])
def test_new_transport_schema_must_be_complete(real_matrix, bad_transport):
    manifest, runs, files = real_matrix
    manifest["plan"]["transport_parameters"] = {
        family: deepcopy(bad_transport) for family in FAMILIES}
    assert any("invalid manifest or input" in issue for issue in evidence_issues(manifest, runs, files))


@pytest.mark.parametrize("record", [None, [], "value", 1])
def test_nonobject_records_are_evidence_issues(frozen_matrix, record):
    manifest, runs, files = frozen_matrix
    assert any("not a JSON object" in issue for issue in evidence_issues(manifest, runs + [record], files))


def test_source_paths_are_portable_and_collisions_are_rejected():
    assert canonical_source_hashes({"src\\echoguard\\batch.py": "hash"}) == {"src/echoguard/batch.py": "hash"}
    with pytest.raises(ValueError, match="duplicate canonical source path"):
        canonical_source_hashes({"src\\echoguard\\batch.py": "hash", "src/echoguard/batch.py": "hash"})


@pytest.mark.parametrize("bad_line", ["null", "[]", '"value"', "{malformed JSON"])
def test_analyzer_preserves_hold_with_malformed_records(frozen_matrix, monkeypatch, capsys, tmp_path, bad_line):
    manifest, runs, _ = frozen_matrix
    runs[0].update(valid=False, failure_stage="validation", errors=["Ineligible product P99 for PER_0"])
    run_path = tmp_path / "runs.jsonl"
    run_path.write_text("\n".join([json.dumps(r) for r in runs] + [bad_line]) + "\n", encoding="utf-8")
    manifest.update(status="complete", runs_sha256=sha256(run_path))
    (tmp_path / "batch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_gate1_results.py"
    spec = importlib.util.spec_from_file_location("analyzer_under_test", script)
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    monkeypatch.setattr("sys.argv", [str(script), "--runs", str(run_path), "--policy", str(policy_path)])
    analyzer.main()
    output = capsys.readouterr().out
    assert "RELEASE GATE DECISION: HOLD" in output
    assert "No full-batch E0/E1/E2 analysis was performed." in output


def test_all_abstain_and_hard_hold_with_deficiency():
    all_abstain = GateMetrics(valid_run_rate=1.0, suitability_violation_count=0,
                              evaluated_persona_count=0, matrix_complete=True)
    assert evaluate_release_gate(all_abstain, POLICY).suggested_action == "INSUFFICIENT_EVIDENCE"
    deficient = GateMetrics(valid_run_rate=1.0, suitability_violation_count=0,
                            evaluated_persona_count=9, matrix_complete=False)
    assert evaluate_release_gate(deficient, POLICY).suggested_action == "INSUFFICIENT_EVIDENCE"
    violation = GateMetrics(valid_run_rate=1.0, suitability_violation_count=1,
                            evaluated_persona_count=0, matrix_complete=False)
    decision = evaluate_release_gate(violation, POLICY)
    assert decision.suggested_action == "HOLD"
    assert "evidence_floor:incomplete_or_incompatible_matrix" in decision.triggered_rules
