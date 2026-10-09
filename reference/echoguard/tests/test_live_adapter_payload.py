import importlib.util
import json
from pathlib import Path

import pytest

from echoguard import providers_live


def test_kimi_request_omits_fixed_temperature_and_records_returned_metadata(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-only-key")
    captured = {}

    class FakeResponse:
        headers = {"x-request-id": "req-test"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "response-test", "model": "kimi-k2.6", "usage": {
                "prompt_tokens": 12, "completion_tokens": 8},
                "choices": [{"message": {"content": '{"decision":"ABSTAIN","portfolio":[]}'},
                             "finish_reason": "stop"}]}

    def fake_post(url, headers, json, timeout):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setattr(providers_live.requests, "post", fake_post)
    response = providers_live.kimi().complete("test prompt")
    assert "temperature" not in captured
    assert captured["thinking"] == {"type": "disabled"}
    assert response.metadata["request_id"] == "req-test"
    assert response.metadata["returned_model"] == "kimi-k2.6"
    assert response.metadata["usage"]["completion_tokens"] == 8
    assert response.metadata["transport_parameters"] == {
        "min_interval_s": 25.0, "timeout_s": 90.0, "max_retries": 2}
    assert "transport_parameters" not in captured


def test_http_400_does_not_retry_or_log_response_body(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-only-key")
    calls = []

    class FakeResponse:
        headers = {"x-request-id": "req-failed"}
        status_code = 400

        def raise_for_status(self):
            error = providers_live.requests.HTTPError("bad request body contains a secret")
            error.response = self
            raise error

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse()

    monkeypatch.setattr(providers_live.requests, "post", fake_post)
    with pytest.raises(providers_live.ProviderCallError) as exc:
        providers_live.kimi().complete("test prompt")
    assert len(calls) == 1
    assert "secret" not in str(exc.value)
    assert exc.value.metadata["http_status"] == 400
    assert exc.value.metadata["transport_parameters"]["min_interval_s"] == 25.0


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(providers_live.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(providers_live.time, "perf_counter", lambda: clock.now)
    monkeypatch.setattr(providers_live.time, "sleep", clock.sleep)
    monkeypatch.setattr(providers_live.OpenAICompatibleProvider, "_api_key", lambda self: "test-only-key")
    monkeypatch.setattr(providers_live, "_env", lambda name, default: default)
    return clock


class ClockResponse:
    def __init__(self, model="test-model", status=200, headers=None, error=None):
        self.model = model
        self.status_code = status
        self.headers = headers or {"x-request-id": "test-request"}
        self.error = error

    def raise_for_status(self):
        if self.status_code >= 400:
            error = providers_live.requests.HTTPError("server message must not be logged")
            error.response = self
            raise error

    def json(self):
        if self.status_code >= 400:
            return {"error": self.error or {"type": "rate_limit_error", "code": "rate_limit_exceeded"}}
        return {"id": "test-response", "model": self.model,
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                "choices": [{"message": {"content": '{"decision":"ABSTAIN","portfolio":[]}'},
                             "finish_reason": "stop"}]}


def clock_post(monkeypatch, clock, outcomes):
    starts = []
    outcomes = iter(outcomes)

    def fake_post(*args, **kwargs):
        starts.append(clock.now)
        clock.now += 2.0
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(providers_live.requests, "post", fake_post)
    return starts


def test_kimi_spaces_every_request_start_by_25_seconds(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse() for _ in range(3)])
    provider = providers_live.kimi(model_name="test-model")
    for _ in range(3):
        provider.complete("prompt")
    assert starts == [0.0, 25.0, 50.0]
    assert fake_clock.sleeps == [23.0, 23.0]


@pytest.mark.parametrize("failure", [ClockResponse(status=500), providers_live.requests.ConnectionError("test")])
def test_kimi_retry_also_obeys_request_start_interval(monkeypatch, fake_clock, failure):
    starts = clock_post(monkeypatch, fake_clock, [failure, ClockResponse()])
    response = providers_live.kimi(model_name="test-model").complete("prompt")
    assert starts == [0.0, 25.0]
    assert fake_clock.sleeps == [1.5, 21.5]
    assert response.metadata["attempt"] == 2


@pytest.mark.parametrize("family", ["deepseek", "qwen"])
def test_other_providers_have_no_new_request_interval(monkeypatch, fake_clock, family):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse(), ClockResponse()])
    provider = providers_live.REGISTRY[family](model_name="test-model")
    provider.complete("first")
    response = provider.complete("second")
    assert starts == [0.0, 2.0]
    assert fake_clock.sleeps == []
    assert response.metadata["transport_parameters"]["min_interval_s"] == 0.0


def test_ordinary_429_waits_at_least_65_seconds(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse(status=429), ClockResponse()])
    response = providers_live.kimi(model_name="test-model").complete("prompt")
    assert fake_clock.sleeps == [65.0]
    assert starts == [0.0, 67.0]
    assert response.metadata["attempt"] == 2


def test_429_respects_larger_numeric_retry_after(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [
        ClockResponse(status=429, headers={"Retry-After": "80.5"}), ClockResponse()])
    providers_live.kimi(model_name="test-model").complete("prompt")
    assert fake_clock.sleeps == [80.5]
    assert starts == [0.0, 82.5]


def test_quota_429_stops_without_retry_and_saves_only_identifiers(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse(status=429, error={
        "type": "exceeded_current_quota_error", "code": "quota_exceeded", "message": "secret body text"})])
    provider = providers_live.kimi(model_name="test-model")
    with pytest.raises(providers_live.ProviderCallError) as exc:
        provider.complete("prompt")
    assert starts == [0.0]
    assert fake_clock.sleeps == []
    assert exc.value.metadata["attempt"] == 1
    assert exc.value.metadata["error_type"] == "exceeded_current_quota_error"
    assert exc.value.metadata["error_code"] == "quota_exceeded"
    assert exc.value.metadata["transport_parameters"] == provider.transport_parameters()
    assert "secret" not in str(exc.value)
    assert "secret" not in str(exc.value.metadata)


def test_unsafe_error_identifiers_are_discarded_and_400_does_not_retry(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse(status=400, error={
        "type": "identifier contains secret text", "code": "private/value", "message": "secret message"})])
    with pytest.raises(providers_live.ProviderCallError) as exc:
        providers_live.kimi(model_name="test-model").complete("prompt")
    assert starts == [0.0]
    assert fake_clock.sleeps == []
    assert "error_type" not in exc.value.metadata and "error_code" not in exc.value.metadata


def test_non_kimi_server_retry_preserves_original_backoff(monkeypatch, fake_clock):
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse(status=500), ClockResponse()])
    providers_live.qwen(model_name="test-model").complete("prompt")
    assert starts == [0.0, 3.5]
    assert fake_clock.sleeps == [1.5]


@pytest.mark.parametrize("retry_limit", [None, 1])
def test_runner_freezes_transport_outside_payload_requests(monkeypatch, fake_clock, tmp_path, capsys, retry_limit):
    from echoguard.batch import evidence_issues

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_gate1_pilot.py"
    spec = importlib.util.spec_from_file_location("runner_under_test", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    provider = providers_live.kimi(model_name="test-model")
    starts = clock_post(monkeypatch, fake_clock, [ClockResponse()])
    monkeypatch.setattr(runner, "build_provider", lambda *args, **kwargs: provider)
    batch_dir = tmp_path / "smoke"
    argv = [str(script), "--providers", "kimi", "--smoke", "--batch-dir", str(batch_dir)]
    if retry_limit is not None:
        argv.extend(["--max-retries", str(retry_limit)])
    monkeypatch.setattr("sys.argv", argv)
    runner.main()
    manifest = json.loads((batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    runs = [json.loads(line) for line in (batch_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert starts == [0.0]
    assert provider.max_retries == (2 if retry_limit is None else retry_limit)
    assert manifest["plan"]["transport_parameters"] == {"kimi": provider.transport_parameters()}
    assert set(manifest["plan"]["requests"]["kimi"]) == {"base_url", "temperature", "max_tokens", "extra_payload"}
    assert runs[0]["provider_metadata"]["transport_parameters"] == provider.transport_parameters()
    assert evidence_issues(manifest, runs, {}) == []
    capsys.readouterr()
