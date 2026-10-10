"""Frozen-plan executor: append-only intent/observation journal, per-provider threads,
resumable runs, currency budget caps and provider-level batch stops.

CLI:
  python -m quoteguard.executor run --plan P --mode mock|live --out runs/
  python -m quoteguard.executor check --provider deepseek|dashscope|siliconflow
  python -m quoteguard.executor spend --out runs/
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from . import llm, util
from .llm import (BatchStop, LLMResponse, ModelConfig, ProviderCallError, ProviderConfigError,
                  build_messages, load_models, redact)
from .util import (append_jsonl, atomic_json, canonical_json, exclusive_json, sha256_file, sha256_text,
                   utc_now)

REQUIRED_FIELDS = ("run_key", "experiment", "method", "config_id", "task_id", "stage", "cand_id",
                   "repeat", "system", "text", "images", "seed", "prompt_sha256")
STOP_STATUS = {"quota_exhausted": "stopped_quota", "insufficient_balance": "stopped_balance",
               "auth_failed": "stopped_config"}
DEFAULT_PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "docs" / "protocol"
LEDGER_NAME = "spend_ledger.jsonl"


class PlanError(ValueError):
    """The frozen plan failed validation."""


class IdentityMismatch(RuntimeError):
    """A resumed run does not match the identity recorded in its manifest."""


class BatchLocked(RuntimeError):
    """Another executor holds the batch directory lock."""


class ProtocolError(RuntimeError):
    """A formal plan does not match its frozen protocol hash."""


def prompt_hash(system: Optional[str], text: str, image_sha256s: list[str],
                max_tokens: Optional[int] = None) -> str:
    """Hash of the prompt content; a request-level max_tokens override is part of it when present."""
    body = {"system": system, "text": text, "images": list(image_sha256s)}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return sha256_text(canonical_json(body))


def make_run_key(experiment, method, config_id, task_id, stage, cand_id, repeat) -> str:
    return f"{experiment}|{method}|{config_id}|{task_id}|{stage}|{cand_id}|r{repeat}"


def load_plan(path: str | Path, models: dict[str, ModelConfig]) -> dict:
    path = Path(path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    for k in ("plan_id", "kind", "budget", "rates", "requests"):
        if k not in plan:
            raise PlanError(f"plan missing field {k}")
    if plan["kind"] not in ("dev", "pilot", "formal"):
        raise PlanError(f"bad plan kind {plan['kind']!r}")
    if plan["kind"] == "formal" and not plan.get("protocol_sha256"):
        raise PlanError("formal plan requires protocol_sha256")
    if plan["kind"] == "pilot" and not 20 <= len(plan["requests"]) <= 40:
        raise PlanError("pilot plan must have 20-40 requests")
    budget, rates = plan["budget"], plan["rates"]
    for cur, amt in budget.items():
        if isinstance(amt, bool) or not isinstance(amt, (int, float)) or amt <= 0:
            raise PlanError(f"budget for {cur} must be positive")
    for cid, rate in rates.items():
        if not isinstance(rate, dict) or "currency" not in rate:
            raise PlanError(f"rates[{cid}] needs currency, input, output")
        for f in ("input", "output"):
            v = rate.get(f)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise PlanError(f"rates[{cid}].{f} must be a non-negative number")
    seen = set()
    for i, r in enumerate(plan["requests"]):
        missing = [f for f in REQUIRED_FIELDS if f not in r]
        if missing:
            raise PlanError(f"request {i} missing fields {missing}")
        key = r["run_key"]
        if key in seen:
            raise PlanError(f"duplicate run_key {key}")
        seen.add(key)
        if r["seed"] is not None and (isinstance(r["seed"], bool) or not isinstance(r["seed"], int)):
            raise PlanError(f"{key}: seed must be an integer or null")
        expect = make_run_key(r["experiment"], r["method"], r["config_id"], r["task_id"],
                              r["stage"], r["cand_id"], r["repeat"])
        if key != expect:
            raise PlanError(f"run_key does not match its fields: {key}")
        if r["config_id"] not in models:
            raise PlanError(f"{key}: config_id not in models.yaml")
        if r["config_id"] not in rates:
            raise PlanError(f"{key}: no rates for config_id {r['config_id']}")
        cur = rates[r["config_id"]]["currency"]
        if cur not in budget:
            raise PlanError(f"{key}: budget has no entry for currency {cur}")
        shas = [_check_image(path.parent, img, key)[1] for img in r["images"]]
        mt = r.get("max_tokens")
        if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int) or mt <= 0):
            raise PlanError(f"{key}: max_tokens override must be a positive integer")
        if prompt_hash(r["system"], r["text"], shas, mt) != r["prompt_sha256"]:
            raise PlanError(f"prompt_sha256 mismatch: {key}")
    return plan


def _check_image(base: Path, img: dict, key: str) -> tuple[bytes, str]:
    p = Path(img["path"])
    p = p if p.is_absolute() else base / p
    if not p.is_file():
        raise PlanError(f"{key}: image file missing {img['path']}")
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != img["sha256"]:
        raise PlanError(f"{key}: image sha256 mismatch {img['path']}")
    return data, img["sha256"]


def check_protocol(plan: dict, protocol_sha256: Optional[str], protocol_dir: Optional[Path]) -> None:
    """Formal plans must carry protocol_sha256 equal to the frozen hash (argument or <plan_id>.sha256)."""
    if plan["kind"] != "formal":
        return
    expected = protocol_sha256
    if not expected:
        f = Path(protocol_dir or DEFAULT_PROTOCOL_DIR) / f"{plan['plan_id']}.sha256"
        if f.is_file():
            parts = f.read_text(encoding="utf-8").split()
            expected = parts[0] if parts else None
    if not expected:
        raise ProtocolError("no frozen protocol hash found (argument or docs/protocol/<plan_id>.sha256)")
    if plan["protocol_sha256"] != expected:
        raise ProtocolError("plan protocol_sha256 does not match the frozen protocol hash")


class MockClient:
    """Deterministic offline stand-in: output and token counts derive from the request hash."""

    def check_auth(self, provider=None):
        return {}

    def complete(self, config: ModelConfig, messages: list[dict], seed=None) -> LLMResponse:
        digest = sha256_text(canonical_json({"model": config.model, "messages": messages, "seed": seed}))
        text = canonical_json({"mock": True, "digest": digest[:16], "answer": int(digest[:4], 16) % 2})
        usage = {"prompt_tokens": 1 + int(digest[6:10], 16) % 400, "completion_tokens": 20 + int(digest[4:6], 16) % 20}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        body = canonical_json({"id": "mock-" + digest[:12], "model": config.model + "-mock",
                               "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                               "usage": usage})
        return LLMResponse(text=text, raw_body=body, usage=usage, returned_model=config.model + "-mock",
                           finish_reason="stop", request_id="mock-" + digest[:12], http_status=200,
                           latency_ms=0.0, attempt=1,
                           attempts=[{"attempt": 1, "http_status": 200, "usage": usage, "possibly_billed": False}],
                           endpoint="mock://" + config.provider)


def _file_key(run_key: str) -> str:
    return sha256_text(run_key)[:24]


class Executor:
    def __init__(self, plan_path, out_root, mode: str = "mock", models_path=None,
                 client_factory: Optional[Callable[[str], object]] = None,
                 min_interval_s: float | dict = 0.0, batch_id: Optional[str] = None,
                 max_consecutive_errors: int = 5, sleep: Callable[[float], None] = time.sleep,
                 protocol_sha256: Optional[str] = None, protocol_dir=None):
        if mode not in ("mock", "live"):
            raise ValueError("mode must be mock or live")
        self.mode = mode
        if mode == "live":
            for p in llm.PROVIDERS:
                if os.environ.get(f"QG_{p.upper()}_BASE_URL", "").strip():
                    raise ProviderConfigError(f"live mode refuses QG_{p.upper()}_BASE_URL overrides")
        self.plan_path = Path(plan_path)
        self.models_path = Path(models_path) if models_path else llm.DEFAULT_MODELS_PATH
        self.models = load_models(self.models_path)
        self.plan = load_plan(self.plan_path, self.models)
        check_protocol(self.plan, protocol_sha256, Path(protocol_dir) if protocol_dir else None)
        self.rates = self.plan["rates"]
        self.budget = {k: float(v) for k, v in self.plan["budget"].items()}
        self.batch_id = batch_id or f"{self.plan['plan_id']}-{mode}"
        self.out_root = Path(out_root)
        self.dir = self.out_root / self.batch_id
        if client_factory is None:
            client_factory = (lambda p: MockClient()) if mode == "mock" else (lambda p: llm.Client())
        self.client_factory = client_factory
        self.min_interval_s = min_interval_s
        self.max_consecutive_errors = max_consecutive_errors
        self.sleep = sleep
        used = sorted({self.models[r["config_id"]].provider for r in self.plan["requests"]})
        self.identity = {
            "plan_sha256": sha256_file(self.plan_path),
            "models_sha256": sha256_file(self.models_path),
            "executor_sha256": sha256_file(__file__),
            "llm_sha256": sha256_file(llm.__file__),
            "util_sha256": sha256_file(util.__file__),
            "base_urls": {p: llm.base_url(p) for p in used},
            "mode": mode,
        }
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stop_info: Optional[dict] = None
        self._spent: dict[str, float] = {}
        self._reserved: dict[str, float] = {}
        self._failed: dict[str, int] = {}
        self._manifest: dict = {}

    # ---- paths and journal
    def _obs_path(self, run_key): return self.dir / "observations" / (_file_key(run_key) + ".json")

    def _append_record(self, kind: str, run_key: str, obj: dict) -> Path:
        """Create the next numbered file for this run_key; existing files are never rewritten."""
        d = self.dir / kind
        d.mkdir(exist_ok=True)
        k = _file_key(run_key)
        n = len(list(d.glob(k + ".*.json"))) + 1
        while True:
            path = d / f"{k}.{n:03d}.json"
            try:
                exclusive_json(path, {**obj, "n": n})
                return path
            except FileExistsError:
                n += 1

    def _write_manifest(self):
        atomic_json(self.dir / "manifest.json", self._manifest)

    def _ledger(self, run_key: str, currency: str, amount: float, kind: str):
        if self.mode == "live" and amount:
            append_jsonl(self.out_root / LEDGER_NAME,
                         {"at": utc_now(), "batch_id": self.batch_id, "run_key": run_key,
                          "currency": currency, "amount": amount, "kind": kind})

    # ---- cost helpers
    def _messages(self, req):
        base = self.plan_path.parent
        loaded = [_check_image(base, im, req["run_key"]) for im in req["images"]]
        return build_messages(req["system"], req["text"],
                              [(data, im["mime"]) for (data, _), im in zip(loaded, req["images"])])

    def _upper_cost(self, req, cfg: ModelConfig):
        if req.get("max_tokens") is not None:
            cfg = dataclasses.replace(cfg, max_tokens=req["max_tokens"])
        rate = self.rates[cfg.config_id]
        in_tok = (len((req["system"] or "").encode("utf-8")) + len(req["text"].encode("utf-8"))) / 2 \
            + cfg.image_token_bound * len(req["images"])
        return rate["currency"], (in_tok * rate["input"] + cfg.max_tokens * rate["output"]) / 1e6

    def _cost(self, usage, req, cfg):
        rate = self.rates[cfg.config_id]
        try:
            i, o = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        except (KeyError, TypeError, ValueError):
            cur, amt = self._upper_cost(req, cfg)
            return {"currency": cur, "amount": amt, "input_tokens": None, "output_tokens": None,
                    "usage_missing": True}
        return {"currency": rate["currency"], "amount": (i * rate["input"] + o * rate["output"]) / 1e6,
                "input_tokens": i, "output_tokens": o, "usage_missing": False}

    @staticmethod
    def _unknown_cost(attempts, cur, upper):
        n = sum(1 for a in (attempts or []) if a.get("possibly_billed"))
        return {"currency": cur, "amount": n * upper, "attempts": n} if n else None

    def _interval(self, provider):
        v = self.min_interval_s
        return float(v.get(provider, 0.0)) if isinstance(v, dict) else float(v)

    def _request_stop(self, status, **info):
        with self._lock:
            if self._stop_info is None:
                self._stop_info = {"status": status, **info, "at": utc_now()}
        self._stop.set()

    def _poll(self):
        """Hook called by the waiting loop; tests override it to simulate an interrupt."""

    # ---- run
    def run(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        lockf = open(self.dir / ".lock", "a+")
        try:
            try:
                fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BatchLocked(f"batch directory is locked: {self.dir}") from exc
            return self._run_locked()
        finally:
            lockf.close()  # releases the flock

    def _run_locked(self) -> dict:
        mpath = self.dir / "manifest.json"
        if mpath.exists():
            old = json.loads(mpath.read_text(encoding="utf-8"))
            if old.get("identity") != self.identity:
                diff = sorted(k for k in self.identity if old.get("identity", {}).get(k) != self.identity[k])
                raise IdentityMismatch(f"resume refused, identity differs: {diff}")
            self._manifest = old
        else:
            self._manifest = {"batch_id": self.batch_id, "plan_id": self.plan["plan_id"],
                              "kind": self.plan["kind"], "identity": self.identity, "budget": self.budget,
                              "created_at": utc_now(), "runs": []}
        for src, name in ((self.plan_path, "plan.json"), (self.models_path, "models.yaml")):
            if not (self.dir / name).exists():
                shutil.copyfile(src, self.dir / name)
        (self.dir / "intents").mkdir(exist_ok=True)
        (self.dir / "observations").mkdir(exist_ok=True)

        done, self._spent, self._reserved = set(), {}, {}

        def add_spent(c):
            if c:
                self._spent[c["currency"]] = self._spent.get(c["currency"], 0.0) + c["amount"]
        for req in self.plan["requests"]:
            p = self._obs_path(req["run_key"])
            if p.exists():
                done.add(req["run_key"])
                o = json.loads(p.read_text(encoding="utf-8"))
                add_spent(o["cost_estimate"])
                add_spent(o.get("possibly_billed_cost"))
        for f in (self.dir / "errors").glob("*.json") if (self.dir / "errors").is_dir() else []:
            add_spent(json.loads(f.read_text(encoding="utf-8")).get("possibly_billed_cost"))
        pending: dict[str, list] = {}
        for req in self.plan["requests"]:
            if req["run_key"] not in done:
                pending.setdefault(self.models[req["config_id"]].provider, []).append(req)

        clients = {p: self.client_factory(p) for p in pending}
        if self.mode == "live":
            for prov, c in clients.items():
                if hasattr(c, "check_auth"):
                    c.check_auth(prov)

        self._stop.clear()
        self._stop_info = None
        self._failed = {}
        run_rec = {"started_at": utc_now(), "skipped_done": len(done)}
        self._manifest.update(status="running", stop=None)
        self._write_manifest()
        threads = [threading.Thread(target=self._worker, args=(p, clients[p], items), name=f"qg-{p}")
                   for p, items in pending.items()]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                self._poll()
                for t in threads:
                    t.join(0.05)
        except KeyboardInterrupt:
            self._request_stop("stopped_user", reason="keyboard_interrupt")
        for t in threads:
            while t.is_alive():
                try:
                    t.join()
                except KeyboardInterrupt:
                    pass

        n_done = sum(1 for r in self.plan["requests"] if self._obs_path(r["run_key"]).exists())
        total = len(self.plan["requests"])
        if self._stop_info:
            status = self._stop_info["status"]
        elif n_done == total:
            status = "completed"
        else:
            status = "incomplete_errors"
        run_rec.update(finished_at=utc_now(), status=status)
        self._manifest["runs"].append(run_rec)
        self._manifest.update(status=status, stop=self._stop_info, completed=n_done, total=total,
                              spent={k: round(v, 8) for k, v in self._spent.items()},
                              failed_providers=sorted(p for p, n in self._failed.items()
                                                      if n >= self.max_consecutive_errors),
                              finished_at=utc_now())
        self._write_manifest()
        return self._manifest

    def _release(self, cur, upper):
        with self._lock:
            self._reserved[cur] = self._reserved.get(cur, 0.0) - upper

    def _record_failure(self, key, provider, cur, upper, error_text, attempts, meta, stop_reason=None):
        unknown = self._unknown_cost(attempts, cur, upper)
        rec = {"run_key": key, "provider": provider, "at": utc_now(), "error": redact(error_text),
               "metadata": meta, "attempts": attempts, "stop_reason": stop_reason,
               "possibly_billed_cost": unknown}
        with self._lock:
            self._reserved[cur] = self._reserved.get(cur, 0.0) - upper
            if unknown:
                self._spent[cur] = self._spent.get(cur, 0.0) + unknown["amount"]
            self._append_record("errors", key, rec)
            if unknown:
                self._ledger(key, cur, unknown["amount"], "possibly_billed")
            over = self._spent.get(cur, 0.0) > self.budget[cur]
        if over:
            self._request_stop("stopped_budget", provider=provider, reason="budget", currency=cur)

    def _worker(self, provider: str, client, items: list):
        last_start = None
        consecutive = 0
        for req in items:
            if self._stop.is_set():
                return
            key = req["run_key"]
            cfg = self.models[req["config_id"]]
            if req.get("max_tokens") is not None:
                cfg = dataclasses.replace(cfg, max_tokens=req["max_tokens"])
            cur, upper = self._upper_cost(req, cfg)
            with self._lock:
                over = self._spent.get(cur, 0.0) + self._reserved.get(cur, 0.0) + upper > self.budget[cur]
                if not over:
                    self._reserved[cur] = self._reserved.get(cur, 0.0) + upper
            if over:
                self._request_stop("stopped_budget", provider=provider, reason="budget", currency=cur,
                                   next_run_key=key)
                return
            try:
                if last_start is not None:
                    wait = self._interval(provider) - (time.monotonic() - last_start)
                    if wait > 0:
                        self.sleep(wait)
                if self._stop.is_set():
                    self._release(cur, upper)
                    return
                last_start = time.monotonic()
                messages = self._messages(req)
                self._append_record("intents", key, {"run_key": key, "config_id": cfg.config_id,
                                                     "provider": provider,
                                                     "prompt_sha256": req["prompt_sha256"],
                                                     "intent_at": utc_now()})
                started_at = utc_now()
                resp = client.complete(cfg, messages, req["seed"])
            except BatchStop as stop:
                self._record_failure(key, provider, cur, upper, str(stop), stop.attempts,
                                     {"batch_stop": stop.reason}, stop_reason=stop.reason)
                self._request_stop(STOP_STATUS[stop.reason], provider=stop.provider, reason=stop.reason,
                                   next_run_key=key)
                return
            except ProviderConfigError as exc:
                self._record_failure(key, provider, cur, upper, f"ProviderConfigError: {exc}", [], {},
                                     stop_reason="config")
                self._request_stop("stopped_config", provider=provider, reason=redact(exc))
                return
            except Exception as exc:  # ProviderCallError and unexpected failures
                meta = exc.metadata if isinstance(exc, ProviderCallError) else {}
                self._record_failure(key, provider, cur, upper, f"{type(exc).__name__}: {exc}",
                                     meta.get("attempts", []), meta)
                consecutive += 1
                with self._lock:
                    self._failed[provider] = consecutive
                if consecutive >= self.max_consecutive_errors:
                    return
                continue
            consecutive = 0
            cost = self._cost(resp.usage, req, cfg)
            unknown = self._unknown_cost(resp.attempts, cur, upper)
            obs = {"run_key": key, "experiment": req["experiment"], "method": req["method"],
                   "task_id": req["task_id"], "stage": req["stage"], "cand_id": req["cand_id"],
                   "repeat": req["repeat"], "config_id": cfg.config_id, "provider": provider,
                   "model": {"requested": cfg.model, "returned": resp.returned_model},
                   "endpoint": resp.endpoint, "temperature": cfg.temperature, "max_tokens": cfg.max_tokens,
                   "max_tokens_override": req.get("max_tokens"),
                   "extra_payload": cfg.extra_payload, "seed": req["seed"],
                   "prompt_sha256": req["prompt_sha256"], "raw_body": resp.raw_body, "text": resp.text,
                   "valid": resp.valid, "body_redacted": resp.body_redacted,
                   "has_reasoning_content": "reasoning_content" in resp.raw_body,
                   "usage": resp.usage, "finish_reason": resp.finish_reason, "request_id": resp.request_id,
                   "latency_ms": resp.latency_ms, "http_status": resp.http_status, "attempt": resp.attempt,
                   "attempts": resp.attempts, "started_at": started_at, "finished_at": utc_now(),
                   "cost_estimate": cost, "possibly_billed_cost": unknown,
                   "upper_bound_exceeded": cost["currency"] == cur and cost["amount"] > upper,
                   "is_mock": self.mode == "mock"}
            stop_over = None
            try:
                with self._lock:
                    self._reserved[cur] = self._reserved.get(cur, 0.0) - upper
                    self._spent[cost["currency"]] = self._spent.get(cost["currency"], 0.0) + cost["amount"]
                    if unknown:
                        self._spent[cur] = self._spent.get(cur, 0.0) + unknown["amount"]
                    exclusive_json(self._obs_path(key), obs)
                    self._ledger(key, cost["currency"], cost["amount"], "observation")
                    if unknown:
                        self._ledger(key, cur, unknown["amount"], "possibly_billed")
                    for c, total in self._spent.items():
                        if total > self.budget.get(c, float("inf")):
                            stop_over = c
            except FileExistsError:
                self._request_stop("stopped_integrity", provider=provider, reason="observation exists",
                                   next_run_key=key)
                return
            if stop_over:
                self._request_stop("stopped_budget", provider=provider, reason="budget_exceeded",
                                   currency=stop_over)
                return


def platform_check(provider: str, models: dict[str, ModelConfig], session: Optional[requests.Session] = None,
                   timeout_s: float = 30.0) -> dict:
    """GET {base_url}/models and compare against models.yaml entries of this provider."""
    session = session or requests.Session()
    headers = llm.Client().headers(provider)
    url = llm.base_url(provider) + "/models"
    try:
        resp = session.get(url, headers=headers, timeout=timeout_s)
        status, body = resp.status_code, resp.text or ""
    except requests.RequestException as exc:
        return {"provider": provider, "http_status": None, "error": redact(type(exc).__name__),
                "model_ids": [], "configs": {}}
    ids: list[str] = []
    try:
        ids = sorted(str(m["id"]) for m in json.loads(body).get("data", []))
    except Exception:
        pass
    configs = {c.config_id: {"model": c.model, "listed": c.model in ids}
               for c in models.values() if c.provider == provider}
    return {"provider": provider, "http_status": status, "model_ids": ids, "configs": configs,
            "checked_at": utc_now()}


def spend_summary(out_root) -> dict:
    """Total recorded spend per currency (and per batch) from runs/spend_ledger.jsonl."""
    path = Path(out_root) / LEDGER_NAME
    totals: dict[str, float] = {}
    by_batch: dict[str, dict[str, float]] = {}
    n = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            n += 1
            totals[e["currency"]] = totals.get(e["currency"], 0.0) + e["amount"]
            b = by_batch.setdefault(e["batch_id"], {})
            b[e["currency"]] = b.get(e["currency"], 0.0) + e["amount"]
    return {"entries": n, "totals": totals, "by_batch": by_batch}


def parse_budget(text: str) -> dict[str, float]:
    out = {}
    for part in text.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quoteguard.executor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--mode", choices=["mock", "live"], required=True)
    r.add_argument("--out", default="runs/")
    r.add_argument("--models", default=None)
    r.add_argument("--batch-id", default=None)
    r.add_argument("--min-interval", type=float, default=0.0)
    r.add_argument("--confirm-budget", default=None, help="USD=x,CNY=y; required for live mode")
    r.add_argument("--protocol-sha256", default=None)
    c = sub.add_parser("check")
    c.add_argument("--provider", required=True, choices=sorted(llm.PROVIDERS))
    c.add_argument("--models", default=None)
    s = sub.add_parser("spend")
    s.add_argument("--out", default="runs/")
    a = ap.parse_args(argv)

    if a.cmd == "check":
        print(json.dumps(platform_check(a.provider, load_models(a.models)), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "spend":
        print(json.dumps(spend_summary(a.out), ensure_ascii=False, indent=2))
        return 0

    if a.mode == "live":
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
        if a.confirm_budget is None:
            print("live mode requires --confirm-budget matching the plan budget", file=sys.stderr)
            return 2
        try:
            given = parse_budget(a.confirm_budget)
        except ValueError:
            print("cannot parse --confirm-budget", file=sys.stderr)
            return 2
        if given != {k: float(v) for k, v in plan["budget"].items()}:
            print("--confirm-budget does not match the plan budget; refusing to run", file=sys.stderr)
            return 2
    ex = Executor(a.plan, a.out, a.mode, models_path=a.models, batch_id=a.batch_id,
                  min_interval_s=a.min_interval, protocol_sha256=a.protocol_sha256)
    m = ex.run()
    print(json.dumps({k: m.get(k) for k in ("batch_id", "status", "completed", "total", "spent", "stop")},
                     ensure_ascii=False))
    return 0 if m["status"] == "completed" else 3


if __name__ == "__main__":
    sys.exit(main())
