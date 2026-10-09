import json

import pytest
import requests

from quoteguard import llm
from quoteguard.llm import (BatchStop, Client, ModelConfig, ProviderCallError, ProviderConfigError,
                            build_messages, classify_error, load_models, redact)

KEY = "sk-abcdefghijklmnop123456"


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body if body is not None else OK_BODY)
        self.headers = headers if headers is not None else {"x-request-id": "rid-1"}


OK_BODY = {"id": "cmpl-1", "model": "returned-model",
           "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


@pytest.fixture
def env(monkeypatch):
    for v in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY", "QG_PROXY_AUTH"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", KEY)
    return monkeypatch


def cfg(provider="siliconflow", temperature=0, extra=None):
    return ModelConfig("c", provider, "m-name", "dev", None, temperature, 100, extra or {}, False)


def client(**kw):
    return Client(sleep=lambda s: None, backoff_s=0, rate_limit_delay_s=0, **kw)


def capture(monkeypatch, responses):
    calls = []
    it = iter(responses)

    def post(self, url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(requests.Session, "post", post)
    return calls


def test_models_yaml_loads():
    m = load_models()
    assert set(m) == {"ds_pro", "ds_flash", "ds_flash_think", "qwen_max", "qwen_plus", "qwen_vl", "glm", "glm_v", "kimi",
                      "dev_ds", "dev_vl"}
    assert m["kimi"].temperature is None and m["ds_pro"].extra_payload == {"thinking": {"type": "disabled"}}
    assert m["glm"].provider == "siliconflow" and m["ds_pro"].vision_config_id == "ds_flash"
    assert not any(c.verified for c in m.values())
    assert all(c.max_tokens == 2000 for c in m.values() if c.config_id != "ds_flash_think")
    for cid in ("qwen_max", "qwen_plus", "qwen_vl", "glm", "glm_v", "kimi"):
        assert m[cid].extra_payload == {"enable_thinking": False}, cid
    for cid in ("ds_pro", "ds_flash"):
        assert m[cid].extra_payload == {"thinking": {"type": "disabled"}}
    t = m["ds_flash_think"]
    assert (t.model, t.tier, t.temperature, t.max_tokens) == ("deepseek-v4-flash", "low", None, 8000)
    assert t.extra_payload == {"thinking": {"type": "enabled"}}
    assert all(c.image_token_bound == 4000 for c in m.values())


def test_build_messages_text_and_images():
    assert build_messages(None, "hi") == [{"role": "user", "content": "hi"}]
    msgs = build_messages("sys", "hi", [(b"\x01\x02", "image/png")])
    assert msgs[0] == {"role": "system", "content": "sys"}
    content = msgs[1]["content"]
    assert content[0] == {"type": "text", "text": "hi"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AQI="}}


def test_auth_header_bearer_and_no_key_leak(env):
    calls = capture(env, [FakeResp()])
    r = client().complete(cfg(), build_messages(None, "x"), seed=7)
    assert calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert calls[0]["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert calls[0]["json"]["seed"] == 7 and calls[0]["json"]["temperature"] == 0
    assert KEY not in repr(r)
    assert r.text == "hello" and r.returned_model == "returned-model" and r.request_id == "rid-1"
    assert r.usage["prompt_tokens"] == 10 and r.finish_reason == "stop" and r.attempt == 1


def test_proxy_auth_no_authorization(env):
    env.setenv("QG_PROXY_AUTH", "1")
    env.delenv("SILICONFLOW_API_KEY")
    calls = capture(env, [FakeResp()])
    client().complete(cfg(), build_messages(None, "x"))
    assert "Authorization" not in calls[0]["headers"]
    assert "seed" not in calls[0]["json"]


def test_missing_key_raises(env):
    env.delenv("SILICONFLOW_API_KEY")
    with pytest.raises(ProviderConfigError) as e:
        client().complete(cfg(), build_messages(None, "x"))
    assert "SILICONFLOW_API_KEY" in str(e.value)


def test_temperature_none_not_sent_and_extra_payload(env):
    calls = capture(env, [FakeResp()])
    client().complete(cfg(temperature=None, extra={"thinking": {"type": "disabled"}}), [])
    assert "temperature" not in calls[0]["json"]
    assert calls[0]["json"]["thinking"] == {"type": "disabled"}


def test_base_url_override_and_https_rule(env):
    env.setenv("QG_SILICONFLOW_BASE_URL", "http://localhost:9/v1/")
    with pytest.raises(ProviderConfigError, match="https"):
        client().complete(cfg(), [])
    env.setenv("QG_SILICONFLOW_BASE_URL", "https://example.invalid/v1/")
    calls = capture(env, [FakeResp()])
    client().complete(cfg(), [])
    assert calls[0]["url"] == "https://example.invalid/v1/chat/completions"
    env.setenv("QG_PROXY_AUTH", "1")
    env.setenv("QG_SILICONFLOW_BASE_URL", "http://localhost:9/v1/")
    calls = capture(env, [FakeResp()])
    client().complete(cfg(), [])
    assert calls[0]["url"] == "http://localhost:9/v1/chat/completions"


def test_redact_removes_keys(env):
    s = redact(f"bad {KEY} and sk-ZZZZZZZZZZZZZZ and plain")
    assert KEY not in s and "sk-ZZZ" not in s and "plain" in s


def test_response_body_redacted_exact_only(env):
    body = dict(OK_BODY, echo=KEY, other="sk-ZZZZZZZZZZZZZZ")
    capture(env, [FakeResp(200, body)])
    r = client().complete(cfg(), [])
    assert KEY not in r.raw_body and r.body_redacted is True
    assert "sk-ZZZZZZZZZZZZZZ" in r.raw_body  # body is only exact-replaced
    capture(env, [FakeResp()])
    assert client().complete(cfg(), []).body_redacted is False


def test_classify_error_branches():
    ds_quota = json.dumps({"error": {"code": "AllocationQuota.FreeTierOnly", "message": "x"}})
    assert classify_error("dashscope", 403, ds_quota) == "quota_exhausted"
    assert classify_error("dashscope", 403, json.dumps({"code": "AllocationQuota.FreeTierOnly"})) == "quota_exhausted"
    assert classify_error("dashscope", 403, '{"error":{"code":"other"}}') == "fatal"
    assert classify_error("deepseek", 403, ds_quota) == "fatal"
    assert classify_error("dashscope", 400, '{"error":{"code":"Arrearage"}}') == "insufficient_balance"
    for prov in ("deepseek", "dashscope", "siliconflow"):
        assert classify_error(prov, 402, "") == "insufficient_balance"
        assert classify_error(prov, 401, "") == "auth_failed"
    sf = lambda body, st=400: classify_error("siliconflow", st, json.dumps(body))
    assert sf({"message": "Insufficient account Balance"}) == "insufficient_balance"
    assert sf({"error": {"code": 30001, "message": "x"}}) == "insufficient_balance"
    assert sf({"message": "balance"}) == "fatal"
    assert sf({"message": "insufficient quota"}) == "fatal"
    assert classify_error("siliconflow", 503, "upstream load balancer unavailable") == "retryable"
    assert classify_error("siliconflow", 503, '{"message":"load balancer"}') == "retryable"
    assert classify_error("siliconflow", 429, "rate limit") == "retryable"
    assert classify_error("deepseek", 500, "") == "retryable"
    assert classify_error("deepseek", None, "") == "retryable"
    assert classify_error("deepseek", 400, "bad") == "fatal"


def test_batchstop_dashscope_and_siliconflow_no_retry(env):
    env.setenv("DASHSCOPE_API_KEY", "k" * 12)
    calls = capture(env, [FakeResp(403, {"error": {"code": "AllocationQuota.FreeTierOnly"}})])
    with pytest.raises(BatchStop) as e:
        client().complete(cfg("dashscope"), [])
    assert e.value.reason == "quota_exhausted" and e.value.provider == "dashscope" and len(calls) == 1
    calls = capture(env, [FakeResp(402, "balance")])
    with pytest.raises(BatchStop) as e:
        client().complete(cfg(), [])
    assert e.value.reason == "insufficient_balance" and len(calls) == 1


def test_retry_then_success_and_exhaustion(env):
    calls = capture(env, [FakeResp(500, "err"), requests.ConnectionError("x"), FakeResp()])
    r = client(max_retries=2).complete(cfg(), [])
    assert r.attempt == 3 and len(calls) == 3
    calls = capture(env, [FakeResp(503, f"oops {KEY}")] * 3)
    with pytest.raises(ProviderCallError) as e:
        client(max_retries=2).complete(cfg(), [])
    assert len(calls) == 3 and KEY not in str(e.value) and KEY not in json.dumps(e.value.metadata)


def test_fatal_no_retry(env):
    calls = capture(env, [FakeResp(400, "bad request")])
    with pytest.raises(ProviderCallError) as e:
        client().complete(cfg(), [])
    assert len(calls) == 1 and e.value.metadata["attempts"][0]["http_status"] == 400


def test_http200_unparseable_is_not_retried_and_kept(env):
    calls = capture(env, [FakeResp(200, "not json"), FakeResp()])
    r = client().complete(cfg(), [])
    assert len(calls) == 1 and r.valid is False and r.text == "" and r.raw_body == "not json"
    capture(env, [FakeResp(200, {"usage": {"prompt_tokens": 3, "completion_tokens": 4}, "choices": []})])
    r = client().complete(cfg(), [])
    assert r.valid is False and r.usage["prompt_tokens"] == 3


def test_attempts_recorded_redacted(env):
    capture(env, [FakeResp(500, f"boom {KEY}"), FakeResp()])
    r = client().complete(cfg(), [])
    assert [a["http_status"] for a in r.attempts] == [500, 200]
    assert KEY not in json.dumps(r.attempts) and "[REDACTED]" in r.attempts[0]["body"]
    assert r.attempts[1]["usage"]["prompt_tokens"] == 10 and r.endpoint.endswith("/chat/completions")


def test_read_timeout_marked_possibly_billed(env):
    capture(env, [requests.ReadTimeout("slow"), requests.ConnectionError("x"), FakeResp()])
    r = client().complete(cfg(), [])
    assert [a["possibly_billed"] for a in r.attempts] == [True, False, False]
    capture(env, [requests.ReadTimeout("slow")] * 3)
    with pytest.raises(ProviderCallError) as e:
        client().complete(cfg(), [])
    assert sum(a["possibly_billed"] for a in e.value.metadata["attempts"]) == 3


def test_429_retry_after(env):
    sleeps = []
    c = Client(sleep=sleeps.append, backoff_s=0, rate_limit_delay_s=0)
    capture(env, [FakeResp(429, "slow down", headers={"Retry-After": "7"}), FakeResp()])
    assert c.complete(cfg(), []).attempt == 2
    assert sleeps == [7.0]


def test_auth_failed_stops(env):
    capture(env, [FakeResp(401, "bad key")])
    with pytest.raises(BatchStop) as e:
        client().complete(cfg(), [])
    assert e.value.reason == "auth_failed"
