"""OpenAI-compatible chat adapter for the three providers used in experiments.

Authentication: with QG_PROXY_AUTH=1 requests carry no Authorization header (the
runtime injects credentials); otherwise the key is read from the provider's
environment variable. Keys never enter return values, logs or exception text.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import yaml

PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "key_env": "DASHSCOPE_API_KEY"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "key_env": "SILICONFLOW_API_KEY"},
}
TIERS = ("strong", "low", "dev", "vision")
DEFAULT_IMAGE_TOKEN_BOUND = 4000
BODY_LIMIT = 20000
GATEWAY_STATUSES = (502, 504, 520, 521, 522, 523, 524)   # possibly billed; retried at most once
DEFAULT_STREAM = True                  # streamed requests avoid proxy timeouts on long generations
DEFAULT_TIMEOUT = (15.0, 120.0)        # (connect, max seconds between two chunks)
DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[2] / "configs" / "models.yaml"

_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class ProviderConfigError(RuntimeError):
    """Missing key or invalid provider/model configuration."""


class ProviderCallError(RuntimeError):
    """A call failed after retries or with a non-retryable error. metadata holds no secrets;
    metadata["attempts"] lists every attempt (status, redacted body, usage)."""

    def __init__(self, message: str, metadata: dict):
        super().__init__(message)
        self.metadata = metadata


class BatchStop(Exception):
    """Provider-level condition that must stop the whole batch (no retry).

    reason: quota_exhausted | insufficient_balance | auth_failed."""

    def __init__(self, reason: str, provider: str, attempts: Optional[list] = None):
        super().__init__(f"{provider}: {reason}")
        self.reason = reason
        self.provider = provider
        self.attempts = attempts or []


@dataclass(frozen=True)
class ModelConfig:
    config_id: str
    provider: str
    model: str
    tier: str
    vision_config_id: Optional[str]
    temperature: Optional[float]
    max_tokens: int
    extra_payload: dict = field(default_factory=dict)
    verified: bool = False
    image_token_bound: int = DEFAULT_IMAGE_TOKEN_BOUND
    stream: Optional[bool] = None   # None: the client default (streaming)


@dataclass
class LLMResponse:
    text: str
    raw_body: str
    usage: dict
    returned_model: str
    finish_reason: Optional[str]
    request_id: str
    http_status: int
    latency_ms: float
    attempt: int
    attempts: list = field(default_factory=list)
    valid: bool = True
    body_redacted: bool = False
    endpoint: str = ""
    streamed: bool = False
    reasoning_content: str = ""
    stream_complete: bool = True


def load_models(path: str | os.PathLike | None = None) -> dict[str, ModelConfig]:
    path = Path(path) if path else DEFAULT_MODELS_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, ModelConfig] = {}
    for item in data["models"]:
        cfg = ModelConfig(
            config_id=item["config_id"], provider=item["provider"], model=item["model"],
            tier=item["tier"], vision_config_id=item.get("vision_config_id"),
            temperature=item.get("temperature"), max_tokens=int(item["max_tokens"]),
            extra_payload=dict(item.get("extra_payload") or {}), verified=bool(item.get("verified", False)),
            image_token_bound=int(item.get("image_token_bound", DEFAULT_IMAGE_TOKEN_BOUND)),
            stream=item.get("stream"))
        if cfg.provider not in PROVIDERS:
            raise ProviderConfigError(f"unknown provider {cfg.provider!r} in {cfg.config_id}")
        if cfg.tier not in TIERS:
            raise ProviderConfigError(f"unknown tier {cfg.tier!r} in {cfg.config_id}")
        if cfg.config_id in out:
            raise ProviderConfigError(f"duplicate config_id {cfg.config_id}")
        out[cfg.config_id] = cfg
    for cfg in out.values():
        if cfg.vision_config_id is not None and cfg.vision_config_id not in out:
            raise ProviderConfigError(f"{cfg.config_id}: unknown vision_config_id {cfg.vision_config_id}")
    return out


def proxy_auth() -> bool:
    return os.environ.get("QG_PROXY_AUTH", "").strip() == "1"


def base_url(provider: str) -> str:
    """Endpoint root. Local-key mode accepts https URLs only."""
    if provider not in PROVIDERS:
        raise ProviderConfigError(f"unknown provider {provider!r}")
    override = os.environ.get(f"QG_{provider.upper()}_BASE_URL", "").strip()
    url = (override or PROVIDERS[provider]["base_url"]).rstrip("/")
    if not proxy_auth() and not url.lower().startswith("https://"):
        raise ProviderConfigError(f"{provider}: base_url must use https when a local API key is used")
    return url


def _known_keys() -> list[str]:
    keys = []
    for spec in PROVIDERS.values():
        key = os.environ.get(spec["key_env"], "").strip()
        if len(key) >= 6:
            keys.append(key)
    return keys


def redact_body(text: Any) -> tuple[str, bool]:
    """Replace exact key values only. Returns (text, whether anything was replaced)."""
    s = "" if text is None else str(text)
    replaced = False
    for key in _known_keys():
        if key in s:
            s = s.replace(key, "[REDACTED]")
            replaced = True
    return s, replaced


def redact(text: Any) -> str:
    """For exception and log text: exact key values plus any sk- style token."""
    s, _ = redact_body(text)
    return _SK_RE.sub("[REDACTED]", s)


def build_messages(system: Optional[str], text: str,
                   images: list[tuple[bytes, str]] | None = None) -> list[dict]:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if images:
        content: list[dict] = [{"type": "text", "text": text}]
        for data, mime in images:
            b64 = base64.b64encode(data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": text})
    return messages


def _error_code_and_message(body_text: str) -> tuple[str, str]:
    try:
        data = json.loads(body_text)
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    err = data.get("error")
    code = msg = None
    if isinstance(err, dict):
        code, msg = err.get("code"), err.get("message")
    code = code if code is not None else data.get("code")
    msg = msg if msg is not None else data.get("message")
    if isinstance(err, str) and msg is None:
        msg = err
    return ("" if code is None else str(code)), ("" if msg is None else str(msg))


def classify_error(provider: str, status: Optional[int], body_text: str, in_stream: bool = False) -> str:
    """Return quota_exhausted | insufficient_balance | auth_failed | retryable | fatal.

    in_stream: the error arrived as an SSE chunk of a 200 response, so the code and message decide.
    """
    code, msg = _error_code_and_message(body_text or "")
    if in_stream and ("invalid_api_key" in code.lower() or "unauthorized" in code.lower()
                      or "authentication" in code.lower()):
        return "auth_failed"
    if status == 401:
        return "auth_failed"
    if status == 402:
        return "insufficient_balance"
    if provider == "dashscope":
        if code == "AllocationQuota.FreeTierOnly" and (status == 403 or in_stream):
            return "quota_exhausted"
        if code == "Arrearage":
            return "insufficient_balance"
    if provider == "siliconflow":
        low = msg.lower()
        if code == "30001" or ("insufficient" in low and "balance" in low):
            return "insufficient_balance"
    if status is None or status == 429 or status >= 500:
        return "retryable"
    return "fatal"


class _StreamError(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class Client:
    """Synchronous client. One instance per worker thread (owns a requests.Session).

    Requests stream by default (SSE); timeout=(connect, read) where read is the longest allowed gap
    between two chunks. stream=False (or ModelConfig.stream false) uses a single JSON response.
    """

    def __init__(self, max_retries: int = 2, backoff_s: float = 1.5, rate_limit_delay_s: float = 30.0,
                 timeout=None, sleep: Callable[[float], None] = time.sleep, stream: Optional[bool] = None):
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.rate_limit_delay_s = rate_limit_delay_s
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.sleep = sleep
        self.stream = DEFAULT_STREAM if stream is None else stream
        self.session = requests.Session()

    def headers(self, provider: str) -> dict:
        h = {"Content-Type": "application/json"}
        if proxy_auth():
            return h
        env = PROVIDERS[provider]["key_env"]
        key = os.environ.get(env, "").strip()
        if not key:
            raise ProviderConfigError(f"{provider}: environment variable {env} is not set")
        h["Authorization"] = f"Bearer {key}"
        return h

    check_auth = headers  # raises ProviderConfigError when the key is missing

    def complete(self, config: ModelConfig, messages: list[dict], seed: Optional[int] = None) -> LLMResponse:
        provider = config.provider
        url = base_url(provider) + "/chat/completions"
        headers = self.headers(provider)
        stream = self.stream if config.stream is None else bool(config.stream)
        payload: dict[str, Any] = {"model": config.model, "messages": messages,
                                   "max_tokens": config.max_tokens}
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if seed is not None:
            payload["seed"] = seed
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        payload.update(config.extra_payload)

        attempts: list[dict] = []
        status: Optional[int] = None
        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            rec: dict[str, Any] = {"attempt": attempt, "http_status": None, "body": "", "usage": None,
                                   "error": None, "possibly_billed": False}
            body = ""
            resp = None
            status = None
            lines: list = []
            try:
                resp = self.session.post(url, headers=headers, json=payload, timeout=self.timeout, stream=stream)
                status = resp.status_code
                rec["http_status"] = status
                if status == 200:
                    if stream:
                        out = self._read_stream(resp, provider, attempt, started, url, lines)
                    else:
                        out = self._parse(resp.text or "", resp, attempt,
                                          (time.perf_counter() - started) * 1000.0, url)
                    rec.update(usage=out.usage, body="(see raw_body)", valid=out.valid,
                               stream_complete=out.stream_complete)
                    attempts.append(rec)
                    out.attempts = attempts
                    return out
                body = resp.text or ""
                red, _ = redact_body(body)
                rec["body"] = red[:BODY_LIMIT]
                kind = classify_error(provider, status, body)
                if status in GATEWAY_STATUSES:
                    rec["possibly_billed"] = True   # the upstream may have run the request
            except _StreamError as exc:
                rec.update(error="StreamError", possibly_billed=True,
                           body=redact_body("\n".join(lines))[0][:BODY_LIMIT])
                kind = exc.kind if exc.kind in ("quota_exhausted", "insufficient_balance", "auth_failed") \
                    else "retryable"
            except requests.RequestException as exc:
                # a read timeout, or any failure after the response started, may have been billed
                rec.update(error=type(exc).__name__,
                           possibly_billed=resp is not None or isinstance(exc, requests.ReadTimeout))
                if lines:
                    rec["body"] = redact_body("\n".join(lines))[0][:BODY_LIMIT]
                kind = "retryable"
            finally:
                close = getattr(resp, "close", None)
                if callable(close):
                    close()
            rec["classification"] = kind
            attempts.append(rec)
            if kind in ("quota_exhausted", "insufficient_balance", "auth_failed"):
                raise BatchStop(kind, provider, attempts)
            gateway = sum(1 for a in attempts if a.get("http_status") in GATEWAY_STATUSES)
            if kind == "retryable" and attempt <= self.max_retries and gateway < 2:
                delay = self.backoff_s * attempt
                if status == 429:
                    delay = max(delay, self.rate_limit_delay_s, _retry_after(resp))
                self.sleep(delay)
                continue
            code, _ = _error_code_and_message(body)
            raise ProviderCallError(
                f"{provider} call failed (HTTP {status if status is not None else 'network'})",
                {"provider": provider, "model": config.model, "http_status": status, "attempt": attempt,
                 "classification": kind, "error_code": redact(code)[:128], "attempts": attempts})
        raise AssertionError("unreachable")

    @staticmethod
    def _read_stream(resp, provider: str, attempt: int, started: float, url: str,
                     lines: Optional[list] = None) -> LLMResponse:
        """Parse an SSE chat stream. Raises requests.RequestException when the connection breaks."""
        lines = [] if lines is None else lines
        content, reasoning = [], []
        usage: dict = {}
        finish = model = rid = None
        done = bad = False
        n_data = 0
        for raw in resp.iter_lines():
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            lines.append(line)
            s = line.strip()
            if not s.startswith("data:"):
                continue
            n_data += 1
            data = s[5:].strip()
            if data == "[DONE]":
                done = True
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                bad = True
                continue
            if not isinstance(chunk, dict):
                bad = True
                continue
            if "error" in chunk and "choices" not in chunk:
                raise _StreamError(classify_error(provider, 200, data, in_stream=True))
            model = chunk.get("model") or model
            rid = chunk.get("id") or rid
            if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                if not isinstance(ch, dict):
                    continue
                delta = ch.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content.append(delta["content"])
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning.append(delta["reasoning_content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        latency_ms = (time.perf_counter() - started) * 1000.0
        raw_text = "\n".join(lines)
        if n_data == 0:
            # the provider ignored stream=true and sent one JSON document
            out = Client._parse(raw_text, resp, attempt, latency_ms, url)
            return out
        headers = getattr(resp, "headers", None) or {}
        rid = headers.get("x-request-id") or headers.get("request-id") or rid or ""
        raw, replaced = redact_body(raw_text)
        complete = done or bool(usage)
        return LLMResponse(
            text=redact_body("".join(content))[0], raw_body=raw, usage=usage, returned_model=str(model or ""),
            finish_reason=finish, request_id=redact_body(rid)[0], http_status=resp.status_code,
            latency_ms=round(latency_ms, 2), attempt=attempt, valid=bool(finish) and not bad and complete,
            body_redacted=replaced, endpoint=url, streamed=True,
            reasoning_content=redact_body("".join(reasoning))[0], stream_complete=complete)

    @staticmethod
    def _parse(body: str, resp, attempt: int, latency_ms: float, url: str) -> LLMResponse:
        try:
            data = json.loads(body)
        except Exception:
            data = None
        usage = data.get("usage") if isinstance(data, dict) and isinstance(data.get("usage"), dict) else {}
        valid, text, finish, reasoning = True, "", None, ""
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            reasoning = choice["message"].get("reasoning_content") or ""
            finish = choice.get("finish_reason")
            if not isinstance(text, str):
                raise TypeError("content is not a string")
            if not isinstance(reasoning, str):
                reasoning = ""
        except Exception:
            valid, text, finish, reasoning = False, "", None, ""
        headers = getattr(resp, "headers", None) or {}
        rid = headers.get("x-request-id") or headers.get("request-id") or \
            (data.get("id", "") if isinstance(data, dict) else "")
        raw, replaced = redact_body(body)
        return LLMResponse(
            text=redact_body(text)[0], raw_body=raw, usage=usage,
            returned_model=str(data.get("model", "")) if isinstance(data, dict) else "",
            finish_reason=finish, request_id=redact_body(rid)[0], http_status=resp.status_code,
            latency_ms=round(latency_ms, 2), attempt=attempt, valid=valid, body_redacted=replaced,
            endpoint=url, reasoning_content=redact_body(reasoning)[0])


def _retry_after(resp) -> float:
    try:
        headers = getattr(resp, "headers", None) or {}
        v = next((x for k, x in headers.items() if str(k).lower() == "retry-after"), None)
        s = float(v)
        return s if 0 <= s < 3600 else 0.0
    except (TypeError, ValueError):
        return 0.0
