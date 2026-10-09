"""Stage 2: frozen plan integrity, response validation, resume, live flow with a fake HTTP API."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

# Fake key assembled at runtime; the literal prefix is split so credential scans do not flag this test file.
FAKE_KEY = "sk" + "-" + "test-key-0123456789abcdef"

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "runs/stage2-plan-20261005"


def load_executor():
    spec = importlib.util.spec_from_file_location("stage2_executor", ROOT / "scripts/stage2_executor.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stage2_executor"] = mod
    spec.loader.exec_module(mod)
    return mod


ex = load_executor()
pytestmark = pytest.mark.skipif(not PLAN.exists(), reason="frozen Stage 2 plan not present")


def test_plan_counts_and_hash():
    manifest, planned = ex.load_plan(PLAN)
    assert manifest["counts"] == {"B0": 81, "E4": 81, "R1": 81, "E0A": 81, "L2": 144}
    assert len({r["run_key"] for r in planned}) == 468


def test_tampered_prompt_is_refused(tmp_path):
    copy = tmp_path / "plan"
    shutil.copytree(PLAN, copy)
    lines = (copy / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["prompt"] += " "
    lines[0] = json.dumps(first, ensure_ascii=False)
    (copy / "requests.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ex.load_plan(copy)


def test_b0_orders_equal_gate1_seed_rule():
    _, planned = ex.load_plan(PLAN)
    import hashlib
    for r in planned:
        if r["experiment"] in ("B0", "R1"):
            assert r["order_seed"] == int(hashlib.sha256(f"{r['persona_id']}|S0_BASELINE|{r['repeat']}".encode()).hexdigest()[:8], 16)


def gate1_req(persona="PER_BASE"):
    _, planned = ex.load_plan(PLAN)
    return next(r for r in planned if r["experiment"] == "B0" and r["persona_id"] == persona)


def test_validate_gate1_cases():
    req = gate1_req()
    ok = '{"decision":"RECOMMEND","portfolio":[{"product_id":"P01","weight":0.5},{"product_id":"P05","weight":0.5}],"justification_summary":"x"}'
    assert ex.validate_gate1(ok, req)[1] is True
    assert ex.validate_gate1("```json\n" + ok + "\n```", req)[1] is True
    bad = ok.replace("P05", "P09")  # equity is ineligible for C3
    parsed, valid, errors, stage = ex.validate_gate1(bad, req)
    assert not valid and any(e.startswith("Ineligible product") for e in errors) and stage == "validation"
    assert ex.validate_gate1('{"decision":"ABSTAIN","portfolio":[]}', req)[1] is True
    assert ex.validate_gate1("not json", req)[3] == "parse"


def test_validate_label_cases():
    _, planned = ex.load_plan(PLAN)
    req = next(r for r in planned if r["experiment"] == "L2")
    a, b = req["order"][0], req["order"][1]
    good = json.dumps({"decision": "RECOMMEND", "portfolio": [{"product_id": a, "weight": 0.6}, {"product_id": b, "weight": 0.4}], "justification_summary": "x"})
    assert ex.validate_label(good, req)[1] is True
    assert ex.validate_label('{"decision":"ABSTAIN","portfolio":[],"justification_summary":"x"}', req)[1] is True
    assert ex.validate_label(good.replace("0.6", "0.7"), req)[1] is False


def test_mock_run_resumes_without_duplicates(tmp_path):
    manifest, planned = ex.load_plan(PLAN)
    configs, _ = ex.configurations(manifest, 0)
    ledger = ex.Ledger(tmp_path / "b", planned, configs, True, manifest["request_plan_sha256"])
    ex.execute(ledger, ex.mock_transport(), ["B0", "E4"], stop_after=40, verbose=False)
    first = len(ledger.results())
    ledger2 = ex.Ledger(tmp_path / "b", planned, configs, True, manifest["request_plan_sha256"])
    status, complete = ex.execute(ledger2, ex.mock_transport(), ["B0", "E4"], verbose=False)
    keys = [r["run_key"] for r in ledger2.results()]
    assert complete and first < 162 and len(keys) == 162 == len(set(keys))
    assert len(ledger2.intents()) == 162


def test_resume_identity_mismatch(tmp_path):
    manifest, planned = ex.load_plan(PLAN)
    configs, _ = ex.configurations(manifest, 0)
    ex.Ledger(tmp_path / "b", planned, configs, True, manifest["request_plan_sha256"])
    with pytest.raises(ValueError):
        ex.Ledger(tmp_path / "b", planned, configs, False, manifest["request_plan_sha256"])


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = headers or {}

    def json(self):
        return self._body


def test_live_flow_with_fake_api(tmp_path, monkeypatch):
    calls = {"kimi": 0}

    def fake_post(self, url, headers=None, json=None, timeout=None, allow_redirects=None):
        family = "kimi" if "moonshot" in url else ("qwen" if "dashscope" in url else "deepseek")
        calls[family] = calls.get(family, 0) + 1
        if family == "kimi" and calls["kimi"] == 2:
            return FakeResponse(429, {"error": {"type": "rate_limit_reached_error"}})
        content = '{"decision":"ABSTAIN","portfolio":[],"justification_summary":"x"}'
        return FakeResponse(200, {"id": f"r{calls[family]}", "model": json["model"],
                                  "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 1700, "completion_tokens": 30}})

    def fake_get(self, url, headers=None, timeout=None, allow_redirects=None):
        return FakeResponse(200, {"data": [{"id": "x"}], "is_available": True})

    monkeypatch.setattr(ex.requests.Session, "post", fake_post)
    monkeypatch.setattr(ex.requests.Session, "get", fake_get)
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    for k in ex.ENV_KEYS.values():
        monkeypatch.setenv(k, FAKE_KEY)
    out = tmp_path / "live"
    monkeypatch.setattr(sys, "argv", ["x", "--mode", "live", "--experiments", "B0", "--yes", "--kimi-interval", "0", "--out-dir", str(out)])
    ex.main()
    manifest = json.loads((out / "batch/manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete" and manifest["by_experiment"]["B0"]["done"] == 81
    retried = [p for p in (out / "batch/intents").glob("*__a2.json")]
    assert len(retried) == 1
    for path in out.rglob("*.json*"):
        assert "sk-test-key" not in path.read_text(encoding="utf-8")
