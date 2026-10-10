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
    kw.setdefault("stream", False)
    return Client(sleep=lambda s: None, backoff_s=0, rate_limit_delay_s=0, **kw)


def capture(monkeypatch, responses):
    calls = []
    it = iter(responses)

    def post(self, url, headers=None, json=None, timeout=None, stream=None):
        calls.append({"url": url, "headers": headers, "json": json, "stream": stream, "timeout": timeout})
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
    assert {c.config_id for c in m.values() if c.verified} == {c.config_id for c in m.values()}
    assert all(c.max_tokens == 2000 for c in m.values() if c.config_id != "ds_flash_think")
    for cid in ("qwen_max", "qwen_plus", "qwen_vl", "glm_v", "kimi"):
        assert m[cid].extra_payload == {"enable_thinking": False}, cid
    # GLM-5.3 cannot disable thinking (step 0); the minimum budget is used instead.
    assert m["glm"].extra_payload == {"enable_thinking": False, "thinking_budget": 1}
    for cid in ("ds_pro", "ds_flash"):
        assert m[cid].extra_payload == {"thinking": {"type": "disabled"}}
    t = m["ds_flash_think"]
    assert (t.model, t.tier, t.temperature, t.max_tokens) == ("deepseek-flash", "low", None, 8000)
    assert t.extra_payload == {"thinking": {"type": "enabled"}}
    bounds = {"ds_flash": 1500, "qwen_vl": 2000, "glm_v": 3000, "kimi": 3000}
    assert all(c.image_token_bound == bounds.get(c.config_id, 4000) for c in m.values())


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
    c = Client(sleep=sleeps.append, backoff_s=0, rate_limit_delay_s=0, stream=False)
    capture(env, [FakeResp(429, "slow down", headers={"Retry-After": "7"}), FakeResp()])
    assert c.complete(cfg(), []).attempt == 2
    assert sleeps == [7.0]


def test_auth_failed_stops(env):
    capture(env, [FakeResp(401, "bad key")])
    with pytest.raises(BatchStop) as e:
        client().complete(cfg(), [])
    assert e.value.reason == "auth_failed"


# ---------------------------------------------------------------- streaming
class FakeStream:
    def __init__(self, lines, status=200, headers=None, fail_after=None, exc=None, text=None):
        self.status_code = status
        self.headers = headers if headers is not None else {"x-request-id": "rid-s"}
        self._lines, self._fail_after, self._exc = lines, fail_after, exc
        self.text = text if text is not None else "\n".join(lines)

    def iter_lines(self):
        for i, ln in enumerate(self._lines):
            if self._fail_after is not None and i >= self._fail_after:
                raise self._exc
            yield ln.encode("utf-8") if i % 2 else ln


def chunk(content=None, reasoning=None, finish=None, usage=None, model="m-ret", cid="c-1", choices=True):
    d = {"id": cid, "model": model}
    if choices:
        delta = {}
        if content is not None:
            delta["content"] = content
        if reasoning is not None:
            delta["reasoning_content"] = reasoning
        d["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
    else:
        d["choices"] = []
    if usage:
        d["usage"] = usage
    return "data: " + json.dumps(d, ensure_ascii=False)


USAGE = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}


def good_stream():
    return [chunk(reasoning="think "), "", chunk(reasoning="more"), chunk(content="你"), chunk(content="好"),
            ": keep-alive", chunk(finish="stop"), chunk(usage=USAGE, choices=False), "data: [DONE]"]


def test_stream_default_payload_and_timeout(env):
    calls = capture(env, [FakeStream(good_stream())])
    r = Client(sleep=lambda s: None).complete(cfg(), [])
    c = calls[0]
    assert c["json"]["stream"] is True and c["json"]["stream_options"] == {"include_usage": True}
    assert c["stream"] is True and c["timeout"] == (15.0, 120.0)
    assert r.streamed and r.text == "你好" and r.reasoning_content == "think more"
    assert r.finish_reason == "stop" and r.usage == USAGE and r.returned_model == "m-ret"
    assert r.request_id == "rid-s" and r.valid and r.stream_complete and r.http_status == 200
    assert "[DONE]" in r.raw_body and ": keep-alive" in r.raw_body and r.raw_body.startswith("data:")
    assert r.attempts[0]["usage"] == USAGE


def test_stream_request_id_falls_back_to_chunk_id(env):
    capture(env, [FakeStream(good_stream(), headers={})])
    assert Client(sleep=lambda s: None).complete(cfg(), []).request_id == "c-1"


def test_stream_can_be_disabled_by_client_or_config(env):
    calls = capture(env, [FakeResp()])
    Client(sleep=lambda s: None, stream=False).complete(cfg(), [])
    assert "stream" not in calls[0]["json"] and calls[0]["stream"] is False
    calls = capture(env, [FakeResp()])
    c = ModelConfig("c", "siliconflow", "m", "dev", None, 0, 100, {}, False, stream=False)
    Client(sleep=lambda s: None).complete(c, [])
    assert "stream" not in calls[0]["json"]
    assert load_models()["ds_flash"].stream is None


def test_stream_non_200_uses_full_body_and_same_classification(env):
    capture(env, [FakeStream([], status=402, text='{"message":"x"}')])
    with pytest.raises(BatchStop) as e:
        Client(sleep=lambda s: None).complete(cfg(), [])
    assert e.value.reason == "insufficient_balance"
    calls = capture(env, [FakeStream([], status=502, text="upstream request failed"), FakeStream(good_stream())])
    r = Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    assert len(calls) == 2 and r.attempts[0]["http_status"] == 502 and "upstream" in r.attempts[0]["body"]


def test_stream_break_is_retryable_and_possibly_billed(env):
    broken = FakeStream(good_stream(), fail_after=3, exc=requests.exceptions.ChunkedEncodingError("cut"))
    calls = capture(env, [broken, FakeStream(good_stream())])
    r = Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    assert len(calls) == 2 and r.attempt == 2
    assert r.attempts[0]["possibly_billed"] is True and r.attempts[0]["error"] == "ChunkedEncodingError"
    capture(env, [FakeStream(good_stream(), fail_after=2, exc=requests.ConnectionError("x"))] * 3)
    with pytest.raises(ProviderCallError) as e:
        Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    assert sum(a["possibly_billed"] for a in e.value.metadata["attempts"]) == 3
    capture(env, [FakeStream(good_stream(), fail_after=2, exc=requests.ReadTimeout("gap"))] * 3)
    with pytest.raises(ProviderCallError):
        Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])


def test_stream_ends_without_done_or_usage(env):
    capture(env, [FakeStream([chunk(content="par"), chunk(content="tial")])])
    r = Client(sleep=lambda s: None).complete(cfg(), [])
    assert r.text == "partial" and r.usage == {} and r.valid is False and r.stream_complete is False


def test_stream_json_fallback_when_stream_ignored(env):
    capture(env, [FakeStream([json.dumps(OK_BODY)])])
    r = Client(sleep=lambda s: None).complete(cfg(), [])
    assert r.text == "hello" and r.usage["prompt_tokens"] == 10 and r.streamed is False


def test_stream_redacts_key_in_raw_body(env):
    capture(env, [FakeStream([f"data: {json.dumps({'echo': KEY})}"] + good_stream())])
    r = Client(sleep=lambda s: None).complete(cfg(), [])
    assert KEY not in r.raw_body and r.body_redacted is True


# ---------------------------------------------------------------- review fixes
def test_gateway_errors_possibly_billed_and_retried_once(env):
    for st in (502, 504):
        calls = capture(env, [FakeResp(st, "upstream request failed")] * 3)
        with pytest.raises(ProviderCallError) as e:
            client(max_retries=2).complete(cfg(), [])
        assert len(calls) == 2                      # one retry at most
        att = e.value.metadata["attempts"]
        assert [a["possibly_billed"] for a in att] == [True, True]
    calls = capture(env, [FakeResp(502, "bad gateway"), FakeResp()])
    r = client().complete(cfg(), [])
    assert r.attempt == 2 and r.attempts[0]["possibly_billed"] is True and r.attempts[1]["possibly_billed"] is False
    # other 5xx keep the full retry budget and are not marked billed
    calls = capture(env, [FakeResp(503, "busy")] * 3)
    with pytest.raises(ProviderCallError) as e:
        client(max_retries=2).complete(cfg(), [])
    assert len(calls) == 3 and not any(a["possibly_billed"] for a in e.value.metadata["attempts"])


def test_in_stream_error_chunks(env):
    err = lambda code, msg="x": "data: " + json.dumps({"error": {"code": code, "message": msg}})
    env.setenv("DASHSCOPE_API_KEY", "k" * 12)
    capture(env, [FakeStream([chunk(content="a"), err("AllocationQuota.FreeTierOnly")])])
    with pytest.raises(BatchStop) as e:
        Client(sleep=lambda s: None).complete(cfg("dashscope"), [])
    assert e.value.reason == "quota_exhausted"
    assert "AllocationQuota" in e.value.attempts[0]["body"] and e.value.attempts[0]["possibly_billed"] is True
    capture(env, [FakeStream([err("30001", "x")])])
    with pytest.raises(BatchStop) as e:
        Client(sleep=lambda s: None).complete(cfg(), [])
    assert e.value.reason == "insufficient_balance"
    capture(env, [FakeStream([err("Arrearage")])])
    with pytest.raises(BatchStop) as e:
        Client(sleep=lambda s: None).complete(cfg("dashscope"), [])
    assert e.value.reason == "insufficient_balance"
    capture(env, [FakeStream([err("invalid_api_key")])])
    with pytest.raises(BatchStop) as e:
        Client(sleep=lambda s: None).complete(cfg(), [])
    assert e.value.reason == "auth_failed"
    # any other in-stream error is retried
    calls = capture(env, [FakeStream([err("server_busy")]), FakeStream(good_stream())])
    r = Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    assert len(calls) == 2 and r.attempts[0]["error"] == "StreamError" and r.text == "你好"


def test_broken_stream_keeps_received_lines_redacted(env):
    lines = [chunk(content="partial"), f"data: {json.dumps({'echo': KEY})}"] + good_stream()
    capture(env, [FakeStream(lines, fail_after=2, exc=requests.exceptions.ChunkedEncodingError("cut"))] * 3)
    with pytest.raises(ProviderCallError) as e:
        Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    body = e.value.metadata["attempts"][0]["body"]
    assert "partial" in body and KEY not in body and "[REDACTED]" in body and len(body) <= llm.BODY_LIMIT


def test_response_is_closed(env):
    closed = []

    class R(FakeStream):
        def close(self):
            closed.append(1)
    capture(env, [R(good_stream())])
    Client(sleep=lambda s: None).complete(cfg(), [])
    capture(env, [R([], status=400, text="bad")])
    with pytest.raises(ProviderCallError):
        Client(sleep=lambda s: None).complete(cfg(), [])
    capture(env, [R(good_stream(), fail_after=1, exc=requests.ConnectionError("x"))] * 3)
    with pytest.raises(ProviderCallError):
        Client(sleep=lambda s: None, backoff_s=0).complete(cfg(), [])
    assert len(closed) == 2 + 3
