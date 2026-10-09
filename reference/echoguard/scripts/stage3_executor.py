"""Stage 3 executor: frozen request plan, durable per-attempt journal, provider-parallel, resumable.

Copy of stage2_executor.py (stage2-executor-0.1, unchanged) with four differences: it accepts the
stage3-plan-0.1 schema and its experiments (E5F, E5R, R2S, R2I), it validates each response against
the shelf and persona set named in the request (shelf A or the new shelf B), and the engineering
stop thresholds are USD 0.6 / CNY 4.0.

Usage (project root, Windows PowerShell):
  .venv\\Scripts\\python.exe scripts\\stage3_executor.py --mode mock            # free dry run
  .venv\\Scripts\\python.exe scripts\\stage3_executor.py --mode live            # real run (asks for keys)

Re-running the same live command resumes: completed responses are kept and never re-sent.
Keys come from environment variables (DEEPSEEK_API_KEY, DASHSCOPE_API_KEY, MOONSHOT_API_KEY)
or hidden prompts; they stay in memory and are never written to disk.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "runs/live-20261002T160402Z-ddf7f62f/frozen_project"
sys.path.insert(0, str(ROOT / "src"))
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.validation import validate_portfolio  # noqa: E402

VERSION = "stage3-executor-0.1"
PROVIDERS = ["deepseek", "kimi", "qwen"]
EXPERIMENTS = ["E5F", "E5R", "R2S", "R2I"]
# Same list prices the 2026-10-03 label-control executor used (per 1M tokens, peak, uncached).
RATES = {"deepseek": {"currency": "USD", "input": 1.32, "output": 3.96},
         "qwen": {"currency": "CNY", "input": .8, "output": 2.},
         "kimi": {"currency": "CNY", "input": 6.5, "output": 27.}}
CAPS = {"USD": 0.6, "CNY": 4.0}  # engineering stop thresholds, about twice the estimate
ALLOWED_BASES = {"deepseek": "https://api.deepseek.com/v1",
                 "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "kimi": "https://api.moonshot.cn/v1"}
ENV_KEYS = {"deepseek": "DEEPSEEK_API_KEY", "qwen": "DASHSCOPE_API_KEY", "kimi": "MOONSHOT_API_KEY"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic(path, data, new=False):
    path = Path(path)
    if new and path.exists():
        raise ValueError("Refusing duplicate journal commit")
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    with tmp.open("x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def exclusive(directory):
    path = Path(directory) / "executor.lock"
    with path.open("a+b") as f:
        if path.stat().st_size == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another executor is running on this output directory") from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- plan and configuration
def load_plan(plan_dir: Path):
    manifest = read(plan_dir / "design_manifest.json")
    planned = [json.loads(l) for l in (plan_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if manifest.get("schema_version") != "stage3-plan-0.1" or digest(planned) != manifest["request_plan_sha256"]:
        raise ValueError("Prepared request plan changed or unknown")
    for r in planned:
        if hashlib.sha256(r["prompt"].encode("utf-8")).hexdigest() != r["prompt_sha256"]:
            raise ValueError("Prompt changed: " + r["run_key"])
    if sha(ROOT / "runs/live-20261002T160402Z-ddf7f62f/pilot/batch_manifest.json") != manifest["gate1_manifest_sha256"]:
        raise ValueError("Gate 1 configuration reference changed")
    for label, rel in manifest["frozen_input_paths"].items():
        if sha(ROOT / rel) != manifest["frozen_inputs"][label]:
            raise ValueError("Frozen input changed: " + label)
    return manifest, planned


def configurations(manifest, kimi_interval: float):
    configs = {}
    for family in PROVIDERS:
        base = dict(manifest["gate1_requests"][family])
        if base["base_url"] != ALLOWED_BASES[family]:
            raise ValueError("Unapproved provider endpoint")
        transport = dict(manifest["gate1_transport"][family], max_retries=2)
        configs[(family, "gate1")] = dict(base, model=manifest["models"][family], transport=transport)
        extra = dict(base["extra_payload"])
        if family == "qwen":
            extra.update(manifest["label_amendments"]["qwen_extra"])
        if manifest["label_amendments"].get("response_format"):
            extra["response_format"] = manifest["label_amendments"]["response_format"]
        configs[(family, "label")] = dict(base, extra_payload=extra, model=manifest["models"][family], transport=transport)
    intervals = {"deepseek": 0.0, "qwen": 0.0, "kimi": float(kimi_interval)}
    return configs, intervals


def payload_for(request, config):
    payload = {"model": config["model"], "messages": [{"role": "user", "content": request["prompt"]}],
               "max_tokens": config["max_tokens"]}
    if config["temperature"] is not None:
        payload["temperature"] = config["temperature"]
    payload.update(config["extra_payload"])
    return payload


def config_of(configs, request):
    return configs[(request["provider"], request["kind"])]


# ---------------------------------------------------------------- response validation
SHELVES = {"A": load_products(FROZEN / "data/products_pilot_v0.2.csv"),
           "B": load_products(ROOT / "data/stage3/products_shelfB_v1.0.csv")}
PERSONA_SETS = {"pilot_v0.2": {p.persona_id: p for p in load_personas(FROZEN / "data/personas_pilot_v0.2.csv")},
                "r2_v1.0": {p.persona_id: p for p in load_personas(ROOT / "data/stage3/personas_r2_v1.0.csv")}}


def extract_json(text: str) -> dict:
    """Same tolerance as the Gate 1 runner: optional code fence, else the outermost object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    return json.loads(text[start:end + 1])


def validate_gate1(text, request):
    try:
        parsed = extract_json(text)
    except (ValueError, TypeError):
        return {}, False, ["parse: no valid JSON object"], "parse"
    if not isinstance(parsed, dict):
        return {}, False, ["parse: not a JSON object"], "parse"
    decision = parsed.get("decision")
    if decision == "ABSTAIN":
        return parsed, True, [], None
    if decision != "RECOMMEND":
        return parsed, False, [f"Invalid decision: {decision!r}"], "validation"
    try:
        entries = parsed.get("portfolio", [])
        ids = [str(p["product_id"]) for p in entries]
        portfolio = {str(p["product_id"]): float(p["weight"]) for p in entries}
    except (TypeError, KeyError, ValueError, AttributeError):
        return parsed, False, ["Malformed portfolio entries"], "validation"
    errors = validate_portfolio(portfolio, SHELVES[request["shelf"]], PERSONA_SETS[request["persona_set"]][request["persona_id"]])
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errors.append(f"Duplicate product ids: {dup}")
    return parsed, not errors, errors, (None if not errors else "validation")


def validate_label(text, request):
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}, False, ["invalid_json"], "parse"
    if not isinstance(parsed, dict):
        return {}, False, ["not_json_object"], "parse"
    errors = []
    decision, portfolio = parsed.get("decision"), parsed.get("portfolio")
    if not isinstance(parsed.get("justification_summary"), str):
        errors.append("missing_summary")
    if not isinstance(portfolio, list):
        errors.append("portfolio_not_list")
    elif decision == "ABSTAIN":
        if portfolio:
            errors.append("abstain_nonempty")
    elif decision == "RECOMMEND":
        if len(portfolio) != 2 or any(not isinstance(x, dict) for x in portfolio):
            errors.append("need_two_products")
        else:
            ids = [x.get("product_id") for x in portfolio]
            weights = [x.get("weight") for x in portfolio]
            if any(i not in request["alias_to_brand"] for i in ids) or len(set(map(str, ids))) != 2:
                errors.append("invalid_product_ids")
            if any(not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w) for w in weights):
                errors.append("invalid_weights")
            elif any(abs(a - b) > 1e-9 for a, b in zip(sorted(weights), [.4, .6])):
                errors.append("wrong_weights")
    else:
        errors.append("invalid_decision")
    try:
        json.dumps(parsed, allow_nan=False)
    except ValueError:
        return {}, False, errors + ["nonfinite_json"], "parse"
    return parsed, not errors, errors, (None if not errors else "validation")


def validate_response(text, request):
    return (validate_gate1 if request["kind"] == "gate1" else validate_label)(text, request)


# ---------------------------------------------------------------- ledger
def estimate_attempt(request, config):
    input_bound = len(request["prompt"].encode("utf-8")) + 256
    rate = RATES[request["provider"]]
    return (input_bound * rate["input"] + config["max_tokens"] * rate["output"]) / 1e6


def cost_for(family, usage):
    if not isinstance(usage, dict):
        return None
    a, b = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (a, b)):
        return None
    rate = RATES[family]
    return (a * rate["input"] + b * rate["output"]) / 1e6


class Ledger:
    def __init__(self, directory, requests_plan, configs, is_mock, plan_hash, expected_models=None, label="batch"):
        self.directory = Path(directory)
        self.requests = requests_plan
        self.configs = configs
        self.is_mock = is_mock
        self.lock = threading.RLock()
        self.bykey = {r["run_key"]: r for r in requests_plan}
        if len(self.bykey) != len(requests_plan):
            raise ValueError("Duplicate planned keys")
        self.sequence = {}
        for r in requests_plan:
            self.sequence.setdefault((r["provider"], r["experiment"]), []).append(r["run_key"])
        cfg = {f"{p}/{k}": {kk: vv for kk, vv in c.items() if kk != "transport"} for (p, k), c in configs.items()}
        self.identity = {"schema_version": VERSION, "label": label, "is_mock": is_mock, "plan_hash": plan_hash,
                         "configs": cfg, "expected_models": expected_models, "rates": RATES,
                         "executor_sha256": sha(__file__),
                         "validation_sha256": sha(ROOT / "src/echoguard/validation.py")}
        identity_hash = digest(self.identity)
        if self.directory.exists() and (self.directory / "manifest.json").exists():
            prev = read(self.directory / "manifest.json")
            if prev["identity_hash"] != identity_hash:
                raise ValueError("Resume identity mismatch: plan, models, mode, configuration or code changed")
            self.batch_id = prev["batch_id"]
        else:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.batch_id = ("s3-mock-" if is_mock else "s3-real-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
            for d in ("intents", "outcomes", "observations"):
                (self.directory / d).mkdir()
            atomic(self.directory / "manifest.json", dict(self.identity, identity_hash=identity_hash, batch_id=self.batch_id,
                                                         created_at_utc=now(), status="created"), new=True)
        self.identity_hash = identity_hash
        self.validate()

    def keyname(self, key):
        return hashlib.sha256(key.encode()).hexdigest()[:24]

    def result_path(self, key):
        return self.directory / "observations" / (self.keyname(key) + ".json")

    def done(self, key):
        return self.result_path(key).exists()

    def results(self):
        return [read(self.result_path(r["run_key"])) for r in self.requests if self.done(r["run_key"])]

    def intents(self, key=None):
        items = [read(p) for p in sorted((self.directory / "intents").glob("*.json"))]
        return [a for a in items if key is None or a["run_key"] == key]

    def validate(self):
        seen = []
        for path in (self.directory / "observations").glob("*.json"):
            r = read(path)
            key = r.get("run_key")
            seen.append(key)
            if key not in self.bykey or r.get("batch_id") != self.batch_id or r.get("is_mock") is not self.is_mock:
                raise ValueError("Observation from another or mixed batch")
            req = self.bykey[key]
            if r.get("prompt_sha256") != req["prompt_sha256"] or r.get("payload_sha256") != digest(payload_for(req, config_of(self.configs, req))):
                raise ValueError("Observation payload provenance mismatch")
            if path != self.result_path(key):
                raise ValueError("Observation file/key mismatch")
            if not (self.directory / "intents" / (r["attempt_id"] + ".json")).exists():
                raise ValueError("Observation without durable call intent")
        if len(set(seen)) != len(seen):
            raise ValueError("Duplicate observations")
        for (prov, exp), keys in self.sequence.items():
            flags = [self.done(k) for k in keys]
            if any(flags[i + 1] and not flags[i] for i in range(len(flags) - 1)):
                raise ValueError(f"Out-of-order observations for {prov}/{exp}")

    def accounting(self):
        totals = {c: 0. for c in CAPS}
        unknown = []
        results = {r["attempt_id"]: r for r in self.results()}
        for intent in self.intents():
            row = results.get(intent["attempt_id"])
            known = cost_for(intent["provider"], row.get("provider_metadata", {}).get("usage")) if row else None
            c = RATES[intent["provider"]]["currency"]
            totals[c] += 0. if self.is_mock else (known if known is not None else intent["cost_reserve"])
            if known is None:
                unknown.append(intent["attempt_id"])
        return {"charged_usage_or_reserved": totals, "unknown_cost_attempts": unknown,
                "cost_scope": "mock_no_billing" if self.is_mock else "returned usage at list price; unknown attempts reserved"}

    def begin(self, request):
        with self.lock:
            key = request["run_key"]
            if self.done(key):
                raise ValueError("Completed observations cannot be retried")
            config = config_of(self.configs, request)
            prior = self.intents(key)
            if len(prior) >= config["transport"]["max_retries"] + 1:
                raise ValueError("Attempt limit exhausted for " + key)
            seq = self.sequence[(request["provider"], request["experiment"])]
            idx = seq.index(key)
            if idx and not self.done(seq[idx - 1]):
                raise ValueError("Frozen per-provider schedule cannot be changed")
            reserve = estimate_attempt(request, config)
            currency = RATES[request["provider"]]["currency"]
            if self.accounting()["charged_usage_or_reserved"][currency] + reserve > CAPS[currency]:
                raise ValueError("Engineering budget stop threshold reached")
            number = len(prior) + 1
            attempt_id = self.keyname(key) + f"__a{number}"
            intent = dict(batch_id=self.batch_id, attempt_id=attempt_id, attempt_number=number, run_key=key,
                          provider=request["provider"], experiment=request["experiment"], started_at_utc=now(),
                          payload_sha256=digest(payload_for(request, config)), cost_reserve=reserve)
            atomic(self.directory / "intents" / (attempt_id + ".json"), intent, new=True)
            return intent

    def finish(self, request, intent, outcome):
        with self.lock:
            outcome = dict(outcome)
            if outcome["status"] == "response_received":
                parsed, valid, errors, stage = validate_response(outcome.get("content", ""), request)
                row = dict(batch_id=self.batch_id, run_key=request["run_key"], experiment=request["experiment"],
                           kind=request["kind"], provider=request["provider"], persona_id=request["persona_id"],
                           repeat=request["repeat"], view=request["view"], order=request["order"],
                           order_seed=request.get("order_seed"), pair_id=request.get("pair_id"), slot=request.get("slot"),
                           prompt_sha256=request["prompt_sha256"], payload_sha256=intent["payload_sha256"],
                           attempt_id=intent["attempt_id"], is_mock=self.is_mock,
                           actual_model_id=outcome["metadata"].get("returned_model"),
                           started_at_utc=intent["started_at_utc"], finished_at_utc=now(),
                           raw_response=outcome.get("content", ""), raw_api_body=outcome.get("raw_api_body"),
                           provider_metadata=outcome["metadata"], parsed=parsed, valid=valid, errors=errors,
                           failure_stage=stage)
                atomic(self.result_path(request["run_key"]), row, new=True)
                outcome["observation_sha256"] = sha(self.result_path(request["run_key"]))
            outcome.pop("content", None)
            outcome.pop("raw_api_body", None)
            atomic(self.directory / "outcomes" / (intent["attempt_id"] + ".json"),
                   dict(outcome, attempt_id=intent["attempt_id"], finished_at_utc=now()), new=True)

    def recover_commits(self):
        for row in self.results():
            path = self.directory / "outcomes" / (row["attempt_id"] + ".json")
            if not path.exists():
                atomic(path, dict(status="response_received", retryable=False, attempt_id=row["attempt_id"],
                                  finished_at_utc=now(), recovered_committed_observation=True,
                                  observation_sha256=sha(self.result_path(row["run_key"]))), new=True)

    def export(self, status):
        with self.lock:
            self.validate()
            rows = self.results()
            for name, items in (("runs.jsonl", rows), ("errors.jsonl", [r for r in rows if not r["valid"]])):
                path = self.directory / name
                with path.open("w", encoding="utf-8", newline="\n") as f:
                    for r in items:
                        f.write(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n")
            report = read(self.directory / "manifest.json")
            per_exp = {}
            for r in rows:
                e = per_exp.setdefault(r["experiment"], {"done": 0, "valid": 0, "recommend": 0, "abstain": 0})
                e["done"] += 1
                e["valid"] += int(r["valid"])
                e["recommend"] += int(r["valid"] and r["parsed"].get("decision") == "RECOMMEND")
                e["abstain"] += int(r["valid"] and r["parsed"].get("decision") == "ABSTAIN")
            for e, v in per_exp.items():
                v["planned"] = sum(1 for q in self.requests if q["experiment"] == e)
            report.update(status=status, updated_at_utc=now(), observation_count=len(rows),
                          attempt_count=len(self.intents()), by_experiment=per_exp,
                          runs_sha256=sha(self.directory / "runs.jsonl"), accounting=self.accounting())
            atomic(self.directory / "manifest.json", report)


# ---------------------------------------------------------------- transports
class HttpTransport:
    def __init__(self, keys, intervals):
        self.keys = keys
        self.intervals = intervals
        self.last = {}
        self.sessions = {p: requests.Session() for p in PROVIDERS}

    def redact(self, text):
        for key in self.keys.values():
            text = text.replace(key, "[REDACTED]")
        return re.sub(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_.-]{16,}", "[REDACTED]", text)

    def __call__(self, request, config):
        family = request["provider"]
        wait = self.intervals[family] - (time.monotonic() - self.last.get(family, -1e12))
        if wait > 0:
            time.sleep(wait)
        self.last[family] = time.monotonic()
        started = time.perf_counter()
        response = self.sessions[family].post(config["base_url"] + "/chat/completions",
                                              headers={"Authorization": "Bearer " + self.keys[family]},
                                              json=payload_for(request, config),
                                              timeout=config["transport"]["timeout_s"], allow_redirects=False)
        meta = {"provider_family": family, "requested_model": config["model"], "base_url": config["base_url"],
                "temperature": config["temperature"], "max_tokens": config["max_tokens"],
                "extra_payload": config["extra_payload"], "http_status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "min_interval_s": self.intervals[family],
                "request_id": self.redact(response.headers.get("x-request-id") or response.headers.get("request-id") or ""),
                "usage": {}, "returned_model": None, "response_id": None, "finish_reason": None}
        if response.status_code != 200:
            try:
                error = response.json().get("error", {})
            except (ValueError, AttributeError):
                error = {}
            safe = {k: v for k in ("type", "code") if isinstance(v := error.get(k), str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", v)} if isinstance(error, dict) else {}
            retryable = response.status_code >= 500 or (response.status_code == 429 and safe.get("type") != "exceeded_current_quota_error")
            try:
                delay = max(65., float(response.headers.get("retry-after", 65))) if response.status_code == 429 else 3.0
            except (TypeError, ValueError):
                delay = 65.
            return dict(status="http_failure", retryable=retryable, retry_after=delay, metadata=meta, error_identifiers=safe)
        body = self.redact(response.text)
        try:
            data = json.loads(body)
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            meta.update(returned_model=data.get("model"), response_id=data.get("id"), usage=data.get("usage") or {},
                        finish_reason=choice.get("finish_reason"), system_fingerprint=data.get("system_fingerprint"),
                        reasoning_content_present=bool(choice["message"].get("reasoning_content")))
            if not meta["request_id"]:
                meta["request_id"] = meta["response_id"]
        except (ValueError, KeyError, TypeError, IndexError):
            content = ""
            meta["response_envelope_error"] = True
        return dict(status="response_received", retryable=False, content=content, metadata=meta, raw_api_body=body)

    def platform_checks(self, configs):
        checks = {}
        for family in PROVIDERS:
            config = configs[(family, "gate1")]
            checks[family] = {}
            paths = [("models", "/models")]
            if family in ("deepseek", "kimi"):
                paths.append(("balance", "/user/balance" if family == "deepseek" else "/users/me/balance"))
            for label, suffix in paths:
                try:
                    resp = self.sessions[family].get(config["base_url"] + suffix, headers={"Authorization": "Bearer " + self.keys[family]},
                                                     timeout=20, allow_redirects=False)
                    record = {"http_status": resp.status_code}
                    if resp.status_code == 200:
                        data = resp.json()
                        if label == "models":
                            ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
                            record.update(requested_model_listed=config["model"] in ids, model_count=len(ids))
                        else:
                            ready = data.get("is_available")
                            if family == "kimi" and isinstance(data.get("data"), dict):
                                avail = data["data"].get("available_balance")
                                ready = float(avail) > 0 if avail is not None else None
                            record["positive_balance"] = ready
                    checks[family][label] = record
                except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
                    checks[family][label] = {"error_type": type(exc).__name__}
        return checks

    def close(self):
        for s in self.sessions.values():
            s.close()


def mock_transport(seed=8123):
    """Deterministic synthetic responses with a built-in first-shown habit. Never research data."""
    pmap = {p.product_id: p for shelf in SHELVES.values() for p in shelf}

    def call(request, config):
        rng = random.Random(f"{seed}|{request['run_key']}")
        if request["kind"] == "label":
            abstain = request["provider"] == "kimi" and rng.random() < (0.2 if request["view"] == "named" else 0.7)
            if abstain:
                parsed = {"decision": "ABSTAIN", "portfolio": [], "justification_summary": "mock"}
            else:
                parsed = {"decision": "RECOMMEND", "portfolio": [{"product_id": request["order"][0], "weight": 0.6},
                          {"product_id": request["order"][1], "weight": 0.4}], "justification_summary": "mock"}
        else:
            persona = PERSONA_SETS[request["persona_set"]][request["persona_id"]]
            from echoguard.suitability import is_eligible
            shown = [pid for pid in request["order"] if is_eligible(pmap[pid], persona)]
            cells = []
            for pid in shown:
                if pmap[pid].economic_cell not in cells:
                    cells.append(pmap[pid].economic_cell)
            picks = []
            for cell in cells:
                members = [pid for pid in shown if pmap[pid].economic_cell == cell]
                first_bias = 0.75 if request["experiment"] != "R1" else 0.45
                picks.append(members[0] if rng.random() < first_bias else rng.choice(members))
            weights = {1: [1.0], 2: [0.6, 0.4], 3: [0.4, 0.3, 0.3]}[len(picks)] if len(picks) <= 3 else None
            if weights is None or (len(picks) == 1):
                picks = picks + [p for p in shown if p not in picks][:1]
                weights = [0.6, 0.4]
            parsed = {"decision": "RECOMMEND", "portfolio": [{"product_id": p, "weight": w} for p, w in zip(picks, weights)],
                      "justification_summary": "mock"}
        return dict(status="response_received", retryable=False, content=json.dumps(parsed, ensure_ascii=False),
                    metadata=dict(returned_model="MOCK_ONLY", response_id="MOCK__" + request["run_key"],
                                  request_id="MOCK__" + request["run_key"], usage={"prompt_tokens": 1700, "completion_tokens": 120},
                                  latency_ms=0., finish_reason="stop"), raw_api_body=None)
    return call


# ---------------------------------------------------------------- execution
def execute(ledger, transport, experiments, expected_models=None, stop_after=None, verbose=True):
    """One worker thread per provider; each walks its frozen queue. Returns per-provider status."""
    status = {}
    total = sum(1 for r in ledger.requests if r["experiment"] in experiments)
    counter = {"n": sum(1 for r in ledger.results() if r["experiment"] in experiments)}

    def worker(provider):
        queue = [r for r in ledger.requests if r["provider"] == provider and r["experiment"] in experiments]
        for request in queue:
            if ledger.done(request["run_key"]):
                continue
            while not ledger.done(request["run_key"]):
                config = config_of(ledger.configs, request)
                try:
                    intent = ledger.begin(request)
                except ValueError as exc:
                    status[provider] = f"stopped: {exc}"
                    return
                try:
                    outcome = transport(request, config)
                except (requests.Timeout, requests.ConnectionError) as exc:
                    outcome = {"status": "transport_failure", "retryable": True, "error_type": type(exc).__name__,
                               "possible_remote_completion": True, "metadata": {}}
                ledger.finish(request, intent, outcome)
                if outcome["status"] != "response_received":
                    print(f"RETRY {provider} {request['run_key']} attempt={intent['attempt_number']} status={outcome['status']}", flush=True)
                    if not outcome.get("retryable") or intent["attempt_number"] >= config["transport"]["max_retries"] + 1:
                        status[provider] = f"stopped after transport failure at {request['run_key']}"
                        return
                    time.sleep(min(outcome.get("retry_after", 3.0), 180))
            row = read(ledger.result_path(request["run_key"]))
            with ledger.lock:
                counter["n"] += 1
                n = counter["n"]
            if verbose:
                print(f"[{n}/{total}] {request['experiment']:3s} {provider:8s} {request['persona_id']:22s} r{request['repeat']} "
                      f"{request['view']:5s} valid={row['valid']} decision={row['parsed'].get('decision')}", flush=True)
            if expected_models and row["actual_model_id"] != expected_models[provider]:
                status[provider] = f"stopped: returned model drift ({row['actual_model_id']})"
                return
            if row["provider_metadata"].get("reasoning_content_present"):
                status[provider] = "stopped: reasoning-mode drift"
                return
            if n % 10 == 0:
                ledger.export("running")
            if stop_after is not None and n >= stop_after:
                status[provider] = "paused (stop-after)"
                return
        status[provider] = "complete"

    with exclusive(ledger.directory):
        ledger.recover_commits()
        ledger.validate()
        threads = [threading.Thread(target=worker, args=(p,), daemon=True) for p in PROVIDERS]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        complete = all(ledger.done(r["run_key"]) for r in ledger.requests if r["experiment"] in experiments)
        ledger.export("complete" if complete else "incomplete")
    return status, complete


def read_keys():
    keys = {}
    for family in PROVIDERS:
        value = os.environ.get(ENV_KEYS[family], "").strip()
        if not value:
            value = getpass.getpass(f"{family} API key (hidden input): ").strip()
        if not value:
            raise SystemExit(f"Missing API key for {family}")
        keys[family] = value
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan-dir", type=Path, default=ROOT / "runs/stage3-plan-20261005")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--mode", choices=("mock", "live"), default="mock")
    ap.add_argument("--experiments", default=",".join(EXPERIMENTS), help="subset, e.g. R2S,R2I")
    ap.add_argument("--kimi-interval", type=float, default=21.0,
                    help="seconds between Kimi requests; 21 fits Tier0 (3 RPM); use 1 for Tier1+ accounts")
    ap.add_argument("--yes", action="store_true", help="skip the interactive budget confirmation")
    ap.add_argument("--stop-after", type=int)
    a = ap.parse_args()
    experiments = [e.strip() for e in a.experiments.split(",") if e.strip()]
    if any(e not in EXPERIMENTS for e in experiments):
        raise SystemExit("Unknown experiment; choose from " + ",".join(EXPERIMENTS))
    manifest, planned = load_plan(a.plan_dir)
    configs, intervals = configurations(manifest, a.kimi_interval)
    out = a.out_dir or (ROOT / ("runs/stage3-mock-20261005" if a.mode == "mock" else "runs/stage3-live-20261005"))
    out.mkdir(parents=True, exist_ok=True)

    if a.mode == "mock":
        ledger = Ledger(out / "batch", planned, configs, True, manifest["request_plan_sha256"], label="stage3_mock")
        status, complete = execute(ledger, mock_transport(), experiments, stop_after=a.stop_after, verbose=False)
        print("MOCK " + json.dumps({"status": status, "complete": complete, "observations": len(ledger.results())}), flush=True)
        return

    keys = read_keys()
    transport = HttpTransport(keys, intervals)
    try:
        atomic(out / "platform_checks.json", dict(checked_at_utc=now(), checks=transport.platform_checks(configs),
               note="Model availability and positive-balance booleans only; no balances or account data written"))
        smoke_dir = out / "smoke"
        smoke_plan = []
        for family in PROVIDERS:
            first = dict(next(r for r in planned if r["provider"] == family and r["experiment"] == "E5R"))
            first.update(run_key="SMOKE__" + family, experiment="SMOKE")
            smoke_plan.append(first)
        smoke = Ledger(smoke_dir, smoke_plan, configs, False, manifest["request_plan_sha256"], label="stage3_smoke")
        if len(smoke.results()) < 3:
            print("Smoke test: one call per provider ...", flush=True)
            execute(smoke, transport, ["SMOKE"], verbose=True)
        rows = smoke.results()
        if len(rows) < 3 or any(not r["valid"] or not r["actual_model_id"] or not r["provider_metadata"].get("usage") for r in rows):
            raise RuntimeError("Smoke test incomplete or invalid; no formal call started. Inspect " + str(smoke_dir) +
                               " ; to retry the smoke test, rename that folder and run again")
        models = {r["provider"]: r["actual_model_id"] for r in rows}
        expected = manifest["models"]
        drift = {p: (m, expected[p]) for p, m in models.items() if m != expected[p]}
        ratios = {r["provider"]: r["provider_metadata"]["usage"]["prompt_tokens"] / len(next(q["prompt"] for q in smoke_plan if q["provider"] == r["provider"]).encode("utf-8")) for r in rows}
        ledger = Ledger(out / "batch", planned, configs, False, manifest["request_plan_sha256"], expected_models=models, label="stage3_real")
        remaining = [r for r in planned if r["experiment"] in experiments and not ledger.done(r["run_key"])]
        estimate = {c: 0. for c in CAPS}
        for r in remaining:
            rate = RATES[r["provider"]]
            input_est = math.ceil(len(r["prompt"].encode("utf-8")) * ratios[r["provider"]] * 1.15)
            estimate[rate["currency"]] += (input_est * rate["input"] + 400 * rate["output"]) / 1e6
        kimi_n = sum(1 for r in remaining if r["provider"] == "kimi")
        minutes = kimi_n * max(intervals["kimi"], 3.0) / 60
        atomic(out / "provider_smoke_report.json", dict(created_at_utc=now(), smoke_models=models, model_drift_vs_gate1=drift,
               smoke_metadata={r["provider"]: r["provider_metadata"] for r in rows}, remaining_requests=len(remaining),
               estimate_upper=estimate, estimate_basis="smoke input ratio +15%, 400 output tokens, list price, no cache discount",
               prices=RATES, kimi_interval_s=intervals["kimi"]))
        print("SMOKE PASS models=" + json.dumps(models), flush=True)
        if drift:
            print("NOTE returned model differs from Gate 1: " + json.dumps(drift) + " (recorded; comparisons will report it)", flush=True)
        print(f"Remaining requests: {len(remaining)}  estimated cost <= USD {estimate['USD']:.3f} + CNY {estimate['CNY']:.3f}  "
              f"estimated time ~{minutes:.0f} min (Kimi-paced)", flush=True)
        if not a.yes:
            if input("Type RUN to start the formal calls: ").strip() != "RUN":
                print("Not started.", flush=True)
                return
        status, complete = execute(ledger, transport, experiments, expected_models=models, stop_after=a.stop_after)
        acct = ledger.accounting()
        atomic(out / "cost_report.json", dict(created_at_utc=now(), smoke=smoke.accounting(), formal=acct,
               note="Returned usage at list prices; an estimate, not an invoice"))
        print("STATUS " + json.dumps({"providers": status, "complete": complete, "observations": len(ledger.results()),
                                       "cost_estimate": acct["charged_usage_or_reserved"]}), flush=True)
        if not complete:
            print("Run the same command again to resume. Completed responses are kept and never re-sent.", flush=True)
    finally:
        keys.clear()
        transport.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"EXECUTION STOPPED: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(2) from None
