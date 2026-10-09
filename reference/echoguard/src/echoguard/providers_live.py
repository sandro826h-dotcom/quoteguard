from __future__ import annotations
import os
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence
import requests

from .providers import ModelProvider, ProviderResponse


class ProviderConfigError(RuntimeError):
    """Raised when a required API key or setting is missing."""


class ProviderCallError(RuntimeError):
    def __init__(self, message: str, metadata: dict):
        super().__init__(message)
        self.metadata = metadata


@dataclass
class OpenAICompatibleProvider(ModelProvider):
    """Minimal audited adapter for OpenAI-compatible Chat Completions endpoints.

    Provider APIs and model aliases change. Defaults below are only execution defaults;
    every empirical run records the requested model and the provider-returned model ID.
    Override model/base URL with environment variables or --model-map before a billed run.
    """

    model_family: str
    model_name: str
    base_url: str
    api_key_env: str
    api_key_fallback_envs: Sequence[str] = ()
    temperature: float | None = 0.3
    max_tokens: int = 800
    timeout_s: float = 90.0
    max_retries: int = 2
    min_interval_s: float = 0.0
    extra_payload: Dict[str, Any] = field(default_factory=dict)
    _last_request_started_at: float | None = field(default=None, init=False, repr=False)

    def transport_parameters(self) -> dict:
        return {"min_interval_s": self.min_interval_s,
                "timeout_s": self.timeout_s, "max_retries": self.max_retries}

    def _wait_for_request_slot(self) -> None:
        if self._last_request_started_at is not None:
            remaining = self.min_interval_s - (time.monotonic() - self._last_request_started_at)
            while remaining > 0:
                time.sleep(remaining)
                remaining = self.min_interval_s - (time.monotonic() - self._last_request_started_at)
        self._last_request_started_at = time.monotonic()

    @staticmethod
    def _error_identifiers(response) -> dict[str, str]:
        """Retain only bounded machine identifiers, never the server's error message."""
        try:
            data = response.json()
            error = data.get("error", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}
        if not isinstance(error, dict):
            return {}
        return {f"error_{name}": value for name in ("type", "code")
                if isinstance(value := error.get(name), str)
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value)}

    @staticmethod
    def _rate_limit_delay(response) -> float:
        # Conservative engineering cooldown for an exhausted RPM window.
        delay = 65.0
        headers = getattr(response, "headers", {}) or {}
        try:
            retry_after = next((value for name, value in headers.items()
                                if str(name).lower() == "retry-after"), None)
            seconds = float(retry_after)
            if math.isfinite(seconds) and seconds >= 0:
                delay = max(delay, seconds)
        except (TypeError, ValueError):
            pass
        return delay

    def _api_key(self) -> str:
        for env_name in (self.api_key_env, *self.api_key_fallback_envs):
            key = os.environ.get(env_name, "").strip()
            if key:
                return key
        names = ", ".join((self.api_key_env, *self.api_key_fallback_envs))
        raise ProviderConfigError(
            f"{self.model_family}: no API key found. Set one of: {names}"
        )

    def complete(self, prompt: str) -> ProviderResponse:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        payload.update(self.extra_payload)

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            started = time.perf_counter()
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_s)
                latency_ms = (time.perf_counter() - started) * 1000.0
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
                text = message.get("content") or ""
                usage = data.get("usage") or {}
                request_id = (
                    resp.headers.get("x-request-id")
                    or resp.headers.get("request-id")
                    or data.get("id", "")
                )
                return ProviderResponse(
                    text=text,
                    model_name=self.model_name,
                    model_version=str(data.get("model", "")),
                    metadata={
                        "provider_family": self.model_family,
                        "requested_model": self.model_name,
                        "returned_model": data.get("model", ""),
                        "base_url": self.base_url,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "extra_payload": self.extra_payload,
                        "latency_ms": round(latency_ms, 2),
                        "response_id": data.get("id", ""),
                        "request_id": request_id,
                        "finish_reason": choice.get("finish_reason"),
                        "usage": usage,
                        "system_fingerprint": data.get("system_fingerprint"),
                        "attempt": attempt + 1,
                        "transport_parameters": self.transport_parameters(),
                    },
                )
            except Exception as exc:
                last_err = exc
                error_response = getattr(exc, "response", None)
                status = getattr(error_response, "status_code", None)
                identifiers = self._error_identifiers(error_response) if error_response is not None else {}
                exhausted_quota = identifiers.get("error_type") == "exceeded_current_quota_error"
                # Invalid model/parameters, denied access and depleted balance must stop.
                if (attempt < self.max_retries and not (status == 429 and exhausted_quota)
                        and (status is None or status == 429 or status >= 500)):
                    time.sleep(self._rate_limit_delay(error_response) if status == 429 else 1.5 * (attempt + 1))
                    continue
                raise ProviderCallError(
                    f"{self.model_family} API failed (HTTP {status or 'network/parse'}); "
                    f"exception={type(exc).__name__}",
                    {"provider_family": self.model_family, "requested_model": self.model_name,
                     "http_status": status, "attempt": attempt + 1,
                     "transport_parameters": self.transport_parameters(), **identifiers,
                     "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                     "request_id": getattr(locals().get("resp"), "headers", {}).get("x-request-id")
                     if status else None},
                ) from exc


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def deepseek(model_name: str | None = None) -> OpenAICompatibleProvider:
    # 2026-09: legacy deepseek-chat/reasoner aliases have been retired. Use a
    # current V4 model and explicitly disable thinking so temperature remains meaningful.
    return OpenAICompatibleProvider(
        model_family="deepseek",
        model_name=model_name or _env("ECHOGUARD_DEEPSEEK_MODEL", "deepseek-v4-pro"),
        base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        api_key_env="DEEPSEEK_API_KEY",
        extra_payload={"thinking": {"type": "disabled"}},
    )


def qwen(model_name: str | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        model_family="qwen",
        model_name=model_name or _env("ECHOGUARD_QWEN_MODEL", "qwen-plus"),
        base_url=_env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        api_key_env="DASHSCOPE_API_KEY",
    )


def kimi(model_name: str | None = None) -> OpenAICompatibleProvider:
    # K2.6 instant mode uses the provider's fixed default temperature (0.6).
    # Omit it from the request as recommended by Kimi's parameter reference.
    return OpenAICompatibleProvider(
        model_family="kimi",
        model_name=model_name or _env("ECHOGUARD_KIMI_MODEL", "kimi-k2.6"),
        base_url=_env("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
        api_key_env="MOONSHOT_API_KEY",
        api_key_fallback_envs=("KIMI_API_KEY",),
        temperature=None,
        min_interval_s=25.0,
        extra_payload={"thinking": {"type": "disabled"}},
    )


def openai(model_name: str | None = None) -> OpenAICompatibleProvider:
    # Not used in Gate 1 by default. Override ECHOGUARD_OPENAI_MODEL before use.
    return OpenAICompatibleProvider(
        model_family="openai",
        model_name=model_name or _env("ECHOGUARD_OPENAI_MODEL", "gpt-4o-mini"),
        base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key_env="OPENAI_API_KEY",
    )


REGISTRY = {"deepseek": deepseek, "qwen": qwen, "kimi": kimi, "openai": openai}


def build_provider(family: str, model_name: str | None = None) -> OpenAICompatibleProvider:
    if family not in REGISTRY:
        raise ProviderConfigError(f"Unknown provider family '{family}'. Options: {list(REGISTRY)}")
    return REGISTRY[family](model_name)
