import hashlib
import json
import threading
import time

import pytest
import requests

from quoteguard import executor as ex
from quoteguard.executor import (BatchLocked, Executor, IdentityMismatch, PlanError, ProtocolError,
                                 make_run_key, prompt_hash)
from quoteguard.llm import BatchStop, LLMResponse, ProviderConfigError
from quoteguard.llm import load_models
from quoteguard.util import atomic_json, canonical_json, sha256_file, sha256_text, utc_now

OK = {"id": "c1", "model": "ret", "choices": [{"message": {"content": "{\"a\":1}"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
CONFIGS = ["ds_flash", "qwen_plus", "dev_ds"]
RATES = {"ds_flash": {"currency": "USD", "input": 1.0, "output": 2.0},
         "qwen_plus": {"currency": "CNY", "input": 1.0, "output": 2.0},
         "dev_ds": {"currency": "CNY", "input": 1.0, "output": 2.0}}


def req(config_id, i, images=(), text=None, repeat=0):
    text = text or f"question {i}"
    shas = [im["sha256"] for im in images]
    r = {"experiment": "E1", "method": "m", "config_id": config_id, "task_id": f"t{i}", "stage": "s",
         "cand_id": "c0", "repeat": repeat, "system": "sys", "text": text, "images": list(images), "seed": i}
    r["run_key"] = make_run_key(r["experiment"], r["method"], config_id, r["task_id"], "s", "c0", repeat)
    r["prompt_sha256"] = prompt_hash("sys", text, shas)
    return r


def write_plan(tmp_path, requests_, budget=None, name="plan.json"):
    plan = {"plan_id": "p1", "kind": "dev", "budget": budget or {"USD": 10, "CNY": 100}, "rates": RATES,
            "requests": requests_}
    p = tmp_path / name
    p.write_text(json.dumps(plan), encoding="utf-8")
    return p


def six(tmp_path):
    return write_plan(tmp_path, [req(c, i) for i in range(2) for c in CONFIGS])


@pytest.fixture
def env(monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY"):
        monkeypatch.setenv(v, "unit-test-key-0000")
    monkeypatch.delenv("QG_PROXY_AUTH", raising=False)
    for p in ("DEEPSEEK", "DASHSCOPE", "SILICONFLOW"):
        monkeypatch.delenv(f"QG_{p}_BASE_URL", raising=False)
    return monkeypatch


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(OK if body is None else body)
        self.headers = headers if headers is not None else {"x-request-id": "r"}


def patch_post(monkeypatch, handler):
    calls = []
    seen_headers = patch_post.headers = []

    def post(self, url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        seen_headers.append(headers)
        return handler(url, json)
    monkeypatch.setattr(requests.Session, "post", post)
    return calls


def obs_files(d):
    return sorted((d / "observations").glob("*.json"))


def manifest(d):
    return json.loads((d / "manifest.json").read_text())


def test_util():
    assert canonical_json({"b": "中", "a": 1}) == '{"a":1,"b":"中"}'
    assert sha256_text("x") == hashlib.sha256(b"x").hexdigest()
    assert utc_now().endswith("Z")


def test_util_atomic_and_file_hash(tmp_path):
    p = tmp_path / "d" / "a.json"
    atomic_json(p, {"k": "值"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": "值"}
    assert not list(p.parent.glob("*.tmp"))
    assert sha256_file(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_mock_end_to_end(tmp_path):
    e = Executor(six(tmp_path), tmp_path / "runs", "mock")
    m = e.run()
    assert m["status"] == "completed" and m["completed"] == 6 and m["total"] == 6
    files = obs_files(e.dir)
    assert len(files) == 6
    o = json.loads(files[0].read_text())
    for k in ("run_key", "config_id", "provider", "model", "temperature", "max_tokens", "extra_payload", "seed",
              "prompt_sha256", "raw_body", "text", "usage", "finish_reason", "request_id", "latency_ms",
              "http_status", "started_at", "finished_at", "cost_estimate", "is_mock"):
        assert k in o
    assert o["is_mock"] is True and o["model"]["requested"]
    assert len(list((e.dir / "intents").glob("*.json"))) == 6
    # deterministic text for same run_key; resume does nothing
    texts = {json.loads(f.read_text())["run_key"]: json.loads(f.read_text())["text"] for f in files}
    m2 = Executor(six(tmp_path), tmp_path / "runs", "mock").run()
    assert m2["status"] == "completed" and m2["runs"][-1]["skipped_done"] == 6
    assert texts == {json.loads(f.read_text())["run_key"]: json.loads(f.read_text())["text"] for f in obs_files(e.dir)}


def test_plan_validation_failures(tmp_path):
    models = load_models()
    a = req("ds_flash", 0)
    with pytest.raises(PlanError, match="duplicate"):
        ex.load_plan(write_plan(tmp_path, [a, dict(a)]), models)
    b = req("ds_flash", 1)
    b["text"] = "changed"
    with pytest.raises(PlanError, match="prompt_sha256"):
        ex.load_plan(write_plan(tmp_path, [b]), models)
    c = req("ds_flash", 2)
    c["run_key"] = "wrong|key"
    with pytest.raises(PlanError, match="run_key"):
        ex.load_plan(write_plan(tmp_path, [c]), models)
    with pytest.raises(PlanError, match="models.yaml"):
        ex.load_plan(write_plan(tmp_path, [req("nope", 0)]), models)
    d = req("ds_flash", 3)
    del d["seed"]
    with pytest.raises(PlanError, match="missing"):
        ex.load_plan(write_plan(tmp_path, [d]), models)
    with pytest.raises(PlanError, match="currency"):
        ex.load_plan(write_plan(tmp_path, [req("ds_flash", 0)], budget={"CNY": 1}), models)


def test_image_hash_checks(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNGdata")
    good = {"path": "a.png", "mime": "image/png", "sha256": sha256_file(img)}
    models = load_models()
    ex.load_plan(write_plan(tmp_path, [req("ds_flash", 0, images=[good])]), models)
    bad = dict(good, sha256="0" * 64)
    r = req("ds_flash", 1, images=[good])
    r["images"] = [bad]
    with pytest.raises(PlanError, match="image sha256"):
        ex.load_plan(write_plan(tmp_path, [r]), models)
    missing = dict(good, path="nope.png")
    r2 = req("ds_flash", 2, images=[good])
    r2["images"] = [missing]
    with pytest.raises(PlanError, match="missing"):
        ex.load_plan(write_plan(tmp_path, [r2]), models)


def test_live_payload_has_image_and_seed(tmp_path, env):
    img = tmp_path / "a.png"
    img.write_bytes(b"abc")
    good = {"path": "a.png", "mime": "image/png", "sha256": sha256_file(img)}
    plan = write_plan(tmp_path, [req("ds_flash", 0, images=[good])])
    calls = patch_post(env, lambda u, j: FakeResp())
    m = Executor(plan, tmp_path / "runs", "live").run()
    assert m["status"] == "completed"
    payload = calls[0][1]
    assert payload["seed"] == 0 and payload["messages"][1]["content"][1]["type"] == "image_url"
    o = json.loads(obs_files(tmp_path / "runs" / "p1-live")[0].read_text())
    assert o["is_mock"] is False and o["cost_estimate"]["amount"] == pytest.approx((100 + 100) / 1e6)
    assert o["model"]["returned"] == "ret" and o["extra_payload"] == {"thinking": {"type": "disabled"}}
    assert "unit-test-key" not in json.dumps(o)


@pytest.mark.parametrize("stop_host,resp,status,reason", [
    ("dashscope", (403, {"error": {"code": "AllocationQuota.FreeTierOnly"}}), "stopped_quota", "quota_exhausted"),
    ("siliconflow", (402, {"message": "insufficient balance"}), "stopped_balance", "insufficient_balance"),
])
def test_batch_stop_and_resume(tmp_path, env, stop_host, resp, status, reason):
    plan = write_plan(tmp_path, [req(c, i) for i in range(3) for c in CONFIGS])
    state = {"fail": True}

    def handler(url, j):
        if state["fail"] and stop_host in url:
            return FakeResp(*resp)
        return FakeResp()
    calls = patch_post(env, handler)
    e = Executor(plan, tmp_path / "runs", "live")
    m = e.run()
    assert m["status"] == status and m["stop"]["provider"] == stop_host and m["stop"]["reason"] == reason
    done = {json.loads(f.read_text())["run_key"] for f in obs_files(e.dir)}
    assert len(done) < 9
    assert all(("|" + c + "|") not in k for k in done for c in [{"dashscope": "qwen_plus", "siliconflow": "dev_ds"}[stop_host]])
    # resume with the provider healthy: only unfinished items are sent
    state["fail"] = False
    n_before = len(calls)
    m2 = Executor(plan, tmp_path / "runs", "live").run()
    assert m2["status"] == "completed" and m2["completed"] == 9 and m2["stop"] is None
    resent = len(calls) - n_before
    assert resent == 9 - len(done)
    assert len(obs_files(e.dir)) == 9


def test_budget_stop(tmp_path, env):
    reqs = [req("ds_flash", i) for i in range(5)]
    # each call: upper bound ~ (len/2 tokens)*1 + 2000*2 per 1M = ~0.004 USD; actual ~0.0002; so the second call is refused
    plan = write_plan(tmp_path, reqs, budget={"USD": 0.0041, "CNY": 1})
    calls = patch_post(env, lambda u, j: FakeResp())
    e = Executor(plan, tmp_path / "runs", "live")
    m = e.run()
    assert m["status"] == "stopped_budget"
    assert len(obs_files(e.dir)) == 1 and len(calls) == len(obs_files(e.dir))
    assert m["stop"]["reason"] == "budget"


def test_budget_stop_before_first_call(tmp_path, env):
    plan = write_plan(tmp_path, [req("ds_flash", 0)], budget={"USD": 0.001, "CNY": 1})
    calls = patch_post(env, lambda u, j: FakeResp())
    assert Executor(plan, tmp_path / "runs", "live").run()["status"] == "stopped_budget"
    assert calls == []


def test_identity_mismatch_refuses_resume(tmp_path):
    plan = six(tmp_path)
    Executor(plan, tmp_path / "runs", "mock").run()
    data = json.loads(plan.read_text())
    data["budget"]["USD"] = 11
    plan.write_text(json.dumps(data))
    with pytest.raises(IdentityMismatch, match="plan_sha256"):
        Executor(plan, tmp_path / "runs", "mock").run()
    # different models.yaml
    plan = six(tmp_path)
    Executor(plan, tmp_path / "runs2", "mock", models_path=None).run()
    alt = tmp_path / "models_alt.yaml"
    alt.write_text(open(ex.llm.DEFAULT_MODELS_PATH, encoding="utf-8").read() + "\n# changed\n", encoding="utf-8")
    with pytest.raises(IdentityMismatch, match="models_sha256"):
        Executor(plan, tmp_path / "runs2", "mock", models_path=alt).run()


def test_live_missing_key_raises(tmp_path, monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY", "QG_PROXY_AUTH"):
        monkeypatch.delenv(v, raising=False)
    from quoteguard.llm import ProviderConfigError
    with pytest.raises(ProviderConfigError):
        Executor(six(tmp_path), tmp_path / "runs", "live").run()


def test_call_errors_are_journaled_not_observed(tmp_path, env):
    plan = write_plan(tmp_path, [req("ds_flash", i) for i in range(3)])
    patch_post(env, lambda u, j: FakeResp(400, {"error": {"code": "bad"}}))
    e = Executor(plan, tmp_path / "runs", "live", max_consecutive_errors=2)
    m = e.run()
    assert m["status"] == "incomplete_errors" and m["failed_providers"] == ["deepseek"]
    assert len(list((e.dir / "errors").glob("*.json"))) == 2 and not obs_files(e.dir)


def test_min_interval_uses_sleep(tmp_path):
    waits = []
    plan = write_plan(tmp_path, [req("ds_flash", i) for i in range(3)])
    Executor(plan, tmp_path / "runs", "mock", min_interval_s=5.0, sleep=waits.append).run()
    assert len(waits) == 2 and all(0 < w <= 5.0 for w in waits)


def test_cli_confirm_budget_and_mock(tmp_path, env, capsys):
    plan = six(tmp_path)
    out = str(tmp_path / "runs")
    assert ex.main(["run", "--plan", str(plan), "--mode", "live", "--out", out]) == 2
    assert ex.main(["run", "--plan", str(plan), "--mode", "live", "--out", out,
                    "--confirm-budget", "USD=10,CNY=99"]) == 2
    assert not (tmp_path / "runs").exists()
    assert ex.main(["run", "--plan", str(plan), "--mode", "mock", "--out", out]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "completed"
    calls = patch_post(env, lambda u, j: FakeResp())
    assert ex.main(["run", "--plan", str(plan), "--mode", "live", "--out", out,
                    "--confirm-budget", "CNY=100,USD=10"]) == 0
    assert len(calls) == 6


def test_platform_check(monkeypatch, env):
    seen = {}

    class R:
        status_code = 200
        text = json.dumps({"data": [{"id": "deepseek-v4-pro"}, {"id": "other"}]})

    def get(self, url, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return R()
    monkeypatch.setattr(requests.Session, "get", get)
    r = ex.platform_check("deepseek", load_models())
    assert seen["url"] == "https://api.deepseek.com/v1/models"
    assert r["http_status"] == 200 and r["model_ids"] == ["deepseek-v4-pro", "other"]
    assert r["configs"] == {"ds_pro": {"model": "deepseek-v4-pro", "listed": True},
                            "ds_flash": {"model": "deepseek-v4-flash", "listed": False},
                            "ds_flash_think": {"model": "deepseek-v4-flash", "listed": False}}
    env.setenv("QG_PROXY_AUTH", "1")
    ex.platform_check("deepseek", load_models())
    assert "Authorization" not in seen["headers"]


# ---------------------------------------------------------------- hardening tests
def ok_resp(usage=True):
    body = {"id": "x", "model": "ret", "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 10}
    return LLMResponse(text="{}", raw_body=json.dumps(body), usage=body.get("usage", {}), returned_model="ret",
                       finish_reason="stop", request_id="r", http_status=200, latency_ms=1.0, attempt=1,
                       attempts=[{"attempt": 1, "http_status": 200, "possibly_billed": False}], endpoint="e")


class ScriptClient:
    """complete() delegates to fn(cfg, n_call)."""
    def __init__(self, fn, log):
        self.fn, self.log = fn, log

    def complete(self, cfg, messages, seed=None):
        self.log.append(cfg.config_id)
        return self.fn(cfg, len(self.log))


def test_exclusive_json_and_append_files(tmp_path):
    from quoteguard.util import exclusive_json
    p = tmp_path / "o.json"
    exclusive_json(p, {"a": 1})
    with pytest.raises(FileExistsError):
        exclusive_json(p, {"a": 2})
    assert json.loads(p.read_text())["a"] == 1 and not list(tmp_path.glob("*.tmp"))


def test_existing_observation_not_rewritten_and_intents_append(tmp_path):
    plan = six(tmp_path)
    e = Executor(plan, tmp_path / "runs", "mock")
    e.run()
    f = obs_files(e.dir)[0]
    before = f.read_bytes()
    Executor(plan, tmp_path / "runs", "mock").run()
    assert f.read_bytes() == before
    # dropping one observation: only that item is resent, and its intent is appended, not overwritten
    key = json.loads(before)["run_key"]
    f.unlink()
    log = []
    e2 = Executor(plan, tmp_path / "runs", "mock", client_factory=lambda p: ScriptClient(lambda c, n: ok_resp(), log))
    e2.identity = e.identity
    m = e2.run()
    assert len(log) == 1 and m["status"] == "completed"
    intents = sorted((e.dir / "intents").glob(ex._file_key(key) + ".*.json"))
    assert len(intents) == 2


def test_observation_collision_stops_batch(tmp_path):
    plan = write_plan(tmp_path, [req("ds_flash", 0)])
    e = Executor(plan, tmp_path / "runs", "mock")
    log = []

    def fn(cfg, n):
        # another writer creates the observation while the call is in flight
        (e.dir / "observations").mkdir(exist_ok=True)
        (e._obs_path(req("ds_flash", 0)["run_key"])).write_text("{}")
        return ok_resp()
    e.client_factory = lambda p: ScriptClient(fn, log)
    assert e.run()["status"] == "stopped_integrity"
    assert e._obs_path(req("ds_flash", 0)["run_key"]).read_text() == "{}"


def test_second_executor_cannot_take_lock(tmp_path):
    import fcntl
    plan = six(tmp_path)
    e = Executor(plan, tmp_path / "runs", "mock")
    e.dir.mkdir(parents=True)
    holder = open(e.dir / ".lock", "a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(BatchLocked):
            e.run()
    finally:
        holder.close()
    assert e.run()["status"] == "completed"  # released lock is reusable


def test_keyboard_interrupt_stops_new_calls(tmp_path):
    plan = write_plan(tmp_path, [req("ds_flash", i) for i in range(5)])
    started, release, log = threading.Event(), threading.Event(), []

    def fn(cfg, n):
        started.set()
        release.wait(5)
        return ok_resp()
    e = Executor(plan, tmp_path / "runs", "mock", client_factory=lambda p: ScriptClient(fn, log))
    fired = []

    def poll():
        if not fired:
            started.wait(5)
            fired.append(1)
            release.set()
            raise KeyboardInterrupt
    e._poll = poll
    m = e.run()
    assert m["status"] == "stopped_user" and len(log) == 1
    assert len(obs_files(e.dir)) == 1  # in-flight call was saved


def test_stop_during_interval_wait_sends_nothing_more(tmp_path):
    plan = write_plan(tmp_path, [req("ds_flash", i) for i in range(3)])
    log = []
    e = Executor(plan, tmp_path / "runs", "mock", min_interval_s=5.0,
                 client_factory=lambda p: ScriptClient(lambda c, n: ok_resp(), log))
    e.sleep = lambda w: e._request_stop("stopped_user", reason="test")
    m = e.run()
    assert m["status"] == "stopped_user" and len(log) == 1
    assert not list((e.dir / "errors").glob("*")) if (e.dir / "errors").exists() else True
    assert abs(sum(e._reserved.values())) < 1e-12  # reservation released


def test_attempts_and_reasoning_flag_in_observation(tmp_path, env):
    body = dict(OK, choices=[{"message": {"content": "{}", "reasoning_content": "thoughts"},
                              "finish_reason": "stop"}])
    patch_post(env, lambda u, j: FakeResp(body=body))
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "live")
    e.run()
    o = json.loads(obs_files(e.dir)[0].read_text())
    assert o["has_reasoning_content"] is True and o["valid"] is True
    assert o["attempts"][0]["http_status"] == 200 and o["endpoint"].endswith("deepseek.com/v1/chat/completions")
    assert (e.dir / "plan.json").exists() and (e.dir / "models.yaml").exists()


def test_invalid_200_saved_as_observation(tmp_path, env):
    patch_post(env, lambda u, j: FakeResp(body=dict(OK, choices=[])))
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "live")
    assert e.run()["status"] == "completed"
    o = json.loads(obs_files(e.dir)[0].read_text())
    assert o["valid"] is False and o["cost_estimate"]["input_tokens"] == 100


def test_read_timeout_charged_at_upper_bound(tmp_path, env):
    state = {"n": 0}

    def post(self, url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise requests.ReadTimeout("slow")
        return FakeResp()
    env.setattr(requests.Session, "post", post)
    import quoteguard.llm as L
    orig = L.Client.__init__
    env.setattr(L.Client, "__init__", lambda self, **kw: orig(self, sleep=lambda s: None, backoff_s=0))
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "live")
    m = e.run()
    r = req("ds_flash", 0)
    cur, upper = e._upper_cost(r, e.models["ds_flash"])
    o = json.loads(obs_files(e.dir)[0].read_text())
    assert o["possibly_billed_cost"]["amount"] == pytest.approx(upper)
    assert m["spent"]["USD"] == pytest.approx(upper + o["cost_estimate"]["amount"], rel=1e-6)


def test_usage_missing_charged_at_upper_bound(tmp_path, env):
    body = {k: v for k, v in OK.items() if k != "usage"}
    patch_post(env, lambda u, j: FakeResp(body=body))
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "live")
    e.run()
    o = json.loads(obs_files(e.dir)[0].read_text())
    _, upper = e._upper_cost(req("ds_flash", 0), e.models["ds_flash"])
    assert o["cost_estimate"]["usage_missing"] is True and o["cost_estimate"]["amount"] == pytest.approx(upper)


def test_upper_bound_exceeded_flag_and_budget_stop(tmp_path):
    # actual usage far above the reservation: flagged, and the batch stops at once
    plan = write_plan(tmp_path, [req("ds_flash", i) for i in range(3)], budget={"USD": 0.01, "CNY": 1})

    def fn(cfg, n):
        r = ok_resp()
        r.usage = {"prompt_tokens": 10_000_000, "completion_tokens": 10}
        return r
    log = []
    e = Executor(plan, tmp_path / "runs", "mock", client_factory=lambda p: ScriptClient(fn, log))
    m = e.run()
    assert m["status"] == "stopped_budget" and len(log) == 1
    o = json.loads(obs_files(e.dir)[0].read_text())
    assert o["upper_bound_exceeded"] is True


def test_image_token_bound_from_models_yaml(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"abc")
    good = {"path": "a.png", "mime": "image/png", "sha256": sha256_file(img)}
    plan = write_plan(tmp_path, [req("ds_flash", 0, images=[good])])
    base = Executor(plan, tmp_path / "r1", "mock")
    _, u1 = base._upper_cost(base.plan["requests"][0], base.models["ds_flash"])
    alt = tmp_path / "m.yaml"
    alt.write_text(open(ex.llm.DEFAULT_MODELS_PATH, encoding="utf-8").read().replace(
        "    model: deepseek-v4-flash\n    tier: low\n    vision_config_id: ds_flash\n    temperature: 0\n",
        "    model: deepseek-v4-flash\n    tier: low\n    vision_config_id: ds_flash\n    temperature: 0\n    image_token_bound: 14000\n", 1),
        encoding="utf-8")
    e2 = Executor(plan, tmp_path / "r2", "mock", models_path=alt)
    assert e2.models["ds_flash"].image_token_bound == 14000
    _, u2 = e2._upper_cost(e2.plan["requests"][0], e2.models["ds_flash"])
    assert u2 - u1 == pytest.approx(10000 * 1.0 / 1e6)


def test_auth_failed_stops_with_stopped_config(tmp_path, env):
    patch_post(env, lambda u, j: FakeResp(401, "bad key"))
    e = Executor(write_plan(tmp_path, [req("ds_flash", i) for i in range(3)]), tmp_path / "runs", "live")
    m = e.run()
    assert m["status"] == "stopped_config" and m["stop"]["reason"] == "auth_failed" and not obs_files(e.dir)


def test_runtime_provider_config_error_stops(tmp_path):
    def fn(cfg, n):
        raise ProviderConfigError("SILICONFLOW_API_KEY is not set")
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "mock",
                 client_factory=lambda p: ScriptClient(fn, []))
    assert e.run()["status"] == "stopped_config"


def test_errors_file_has_no_key(tmp_path, env):
    patch_post(env, lambda u, j: FakeResp(400, "bad request echo unit-test-key-0000 and sk-ABCDEFGHIJKLMN"))
    e = Executor(write_plan(tmp_path, [req("ds_flash", 0)]), tmp_path / "runs", "live")
    e.run()
    txt = "".join(f.read_text() for f in (e.dir / "errors").glob("*.json"))
    assert "unit-test-key-0000" not in txt and "[REDACTED]" in txt
    assert "attempts" in txt


def test_proxy_auth_live_has_no_authorization_header(tmp_path, env):
    for v in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY"):
        env.delenv(v)
    env.setenv("QG_PROXY_AUTH", "1")
    patch_post(env, lambda u, j: FakeResp())
    m = Executor(six(tmp_path), tmp_path / "runs", "live").run()
    assert m["status"] == "completed" and patch_post.headers is not None
    assert all("Authorization" not in h for h in patch_post.headers) and len(patch_post.headers) == 6


def test_live_refuses_base_url_override(tmp_path, env):
    env.setenv("QG_DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    with pytest.raises(ProviderConfigError, match="BASE_URL"):
        Executor(six(tmp_path), tmp_path / "runs", "live")


def test_identity_includes_base_url_and_util(tmp_path, env):
    plan = six(tmp_path)
    e = Executor(plan, tmp_path / "runs", "mock")
    assert set(e.identity["base_urls"]) == {"deepseek", "dashscope", "siliconflow"}
    assert "util_sha256" in e.identity
    e.run()
    env.setenv("QG_DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    with pytest.raises(IdentityMismatch, match="base_urls"):
        Executor(plan, tmp_path / "runs", "mock").run()


def test_formal_plan_needs_protocol_hash(tmp_path):
    models = load_models()
    reqs = [req("ds_flash", 0)]
    p = write_plan(tmp_path, reqs)
    data = json.loads(p.read_text())
    data["kind"] = "formal"
    p.write_text(json.dumps(data))
    with pytest.raises(PlanError, match="protocol_sha256"):
        ex.load_plan(p, models)
    data["protocol_sha256"] = "a" * 64
    p.write_text(json.dumps(data))
    with pytest.raises(ProtocolError, match="no frozen"):
        Executor(p, tmp_path / "runs", "mock", protocol_dir=tmp_path / "none")
    with pytest.raises(ProtocolError, match="does not match"):
        Executor(p, tmp_path / "runs", "mock", protocol_sha256="b" * 64)
    Executor(p, tmp_path / "runs", "mock", protocol_sha256="a" * 64)
    pd = tmp_path / "proto"
    pd.mkdir()
    (pd / "p1.sha256").write_text("a" * 64 + "  protocol.md\n")
    Executor(p, tmp_path / "runs", "mock", protocol_dir=pd)


def test_plan_field_validation(tmp_path):
    models = load_models()

    def mod(fn, kind="dev", n=1):
        p = write_plan(tmp_path, [req("ds_flash", i) for i in range(n)])
        d = json.loads(p.read_text())
        d["kind"] = kind
        fn(d)
        p.write_text(json.dumps(d))
        return p
    with pytest.raises(PlanError, match="non-negative"):
        ex.load_plan(mod(lambda d: d["rates"]["ds_flash"].update(input=-1)), models)
    with pytest.raises(PlanError, match="non-negative"):
        ex.load_plan(mod(lambda d: d["rates"]["ds_flash"].pop("output")), models)
    with pytest.raises(PlanError, match="positive"):
        ex.load_plan(mod(lambda d: d["budget"].update(USD=0)), models)
    with pytest.raises(PlanError, match="seed"):
        ex.load_plan(mod(lambda d: d["requests"][0].update(seed="7")), models)
    with pytest.raises(PlanError, match="pilot"):
        ex.load_plan(mod(lambda d: None, kind="pilot", n=5), models)
    ex.load_plan(mod(lambda d: None, kind="pilot", n=25), models)
    ex.load_plan(mod(lambda d: d["requests"][0].update(seed=None)), models)


def test_multi_provider_budget_reservation(tmp_path):
    # either request fits alone; both together exceed the budget, so exactly one is sent
    plan = write_plan(tmp_path, [req("qwen_plus", 0), req("dev_ds", 1)], budget={"USD": 1, "CNY": 0.005})
    holder, log = [], []

    def fn(cfg, n):
        holder[0]._stop.wait(2)  # the other provider is refused while this call is in flight
        return ok_resp()
    e = Executor(plan, tmp_path / "runs", "mock", client_factory=lambda p: ScriptClient(fn, log))
    holder.append(e)
    m = e.run()
    assert m["status"] == "stopped_budget" and len(log) == 1 and len(obs_files(e.dir)) == 1


def test_inflight_record_of_other_provider_saved_on_stop(tmp_path):
    plan = write_plan(tmp_path, [req("qwen_plus", 0), req("dev_ds", 1), req("dev_ds", 2)])
    signaled = threading.Event()
    log = []

    def factory(provider):
        if provider == "dashscope":
            def fn(cfg, n):
                signaled.set()
                raise BatchStop("quota_exhausted", "dashscope")
        else:
            def fn(cfg, n):
                signaled.wait(2)
                time.sleep(0.3)
                return ok_resp()
        return ScriptClient(fn, log)
    e = Executor(plan, tmp_path / "runs", "mock", client_factory=factory)
    m = e.run()
    assert m["status"] == "stopped_quota"
    assert log.count("dev_ds") == 1 and len(obs_files(e.dir)) == 1  # in-flight saved, no new call


def test_ledger_and_spend_cli(tmp_path, env, capsys):
    plan = six(tmp_path)
    patch_post(env, lambda u, j: FakeResp())
    out = tmp_path / "runs"
    Executor(plan, out, "live", batch_id="b1").run()
    lines = (out / "spend_ledger.jsonl").read_text().splitlines()
    assert len(lines) == 6 and json.loads(lines[0])["batch_id"] == "b1"
    s = ex.spend_summary(out)
    assert s["entries"] == 6 and set(s["totals"]) == {"USD", "CNY"}
    # mock runs leave no ledger entries
    Executor(plan, out, "mock", batch_id="m1").run()
    assert ex.spend_summary(out)["entries"] == 6
    assert ex.main(["spend", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["USD"] == pytest.approx(s["totals"]["USD"])
