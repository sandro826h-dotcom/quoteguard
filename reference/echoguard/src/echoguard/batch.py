"""Frozen batch identity and evidence checks for Gate 1."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_key(family: str, persona: str, scenario: str, repeat: int) -> str:
    return f"{family}__{persona}__{scenario}__named__r{repeat}"


def expected_keys(families: list[str], personas: list[str], scenario: str, repeats: int) -> list[str]:
    if len(set(families)) != len(families) or len(set(personas)) != len(personas):
        raise ValueError("Duplicate provider or persona in frozen plan")
    if not families or not personas or repeats < 1:
        raise ValueError("Batch plan must include providers, personas and positive repeats")
    return [run_key(f, p, scenario, r) for f in families for p in personas for r in range(1, repeats + 1)]


def canonical_source_hashes(hashes: dict[str, str]) -> dict[str, str]:
    """Keep source identities portable across Windows and POSIX batch manifests."""
    if not isinstance(hashes, dict):
        raise ValueError("source hashes must be an object")
    canonical: dict[str, str] = {}
    for path, digest in hashes.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            raise ValueError("source paths and hashes must be strings")
        key = path.replace("\\", "/")
        if key in canonical:
            raise ValueError(f"duplicate canonical source path: {key}")
        canonical[key] = digest
    return canonical


def _validate_plan(plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    for field in ("providers", "personas"):
        values = plan.get(field)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise ValueError(f"plan {field} must contain nonempty strings")
    if plan.get("kind") not in ("pilot", "smoke") or type(plan.get("mock")) is not bool:
        raise ValueError("plan kind/mock is missing or invalid")
    if type(plan.get("repeats")) is not int or plan["repeats"] < 1:
        raise ValueError("plan repeats must be a positive integer")
    for field in ("scenario", "prompt_version"):
        if not isinstance(plan.get(field), str) or not plan[field]:
            raise ValueError(f"plan {field} is missing or invalid")
    if type(plan.get("seed")) is not int:
        raise ValueError("plan seed must be an integer")
    providers = set(plan["providers"])
    models, requests = plan.get("models"), plan.get("requests")
    if (not isinstance(models, dict) or set(models) != providers
            or any(not isinstance(model, str) or not model for model in models.values())):
        raise ValueError("plan models must specify every provider exactly once")
    if not isinstance(requests, dict) or set(requests) != providers:
        raise ValueError("plan requests must specify every provider exactly once")
    for family, request in requests.items():
        fields = {"base_url", "temperature", "max_tokens", "extra_payload"}
        if not isinstance(request, dict) or not fields <= set(request):
            raise ValueError(f"plan request fields are missing for {family}")
        base_url, temperature = request["base_url"], request["temperature"]
        if not ((plan["mock"] and base_url is None) or (isinstance(base_url, str) and base_url)):
            raise ValueError(f"plan base_url is invalid for {family}")
        if temperature is not None and (type(temperature) not in (int, float) or not math.isfinite(temperature)):
            raise ValueError(f"plan temperature is invalid for {family}")
        if type(request["max_tokens"]) is not int or request["max_tokens"] < 1:
            raise ValueError(f"plan max_tokens is invalid for {family}")
        if not isinstance(request["extra_payload"], dict):
            raise ValueError(f"plan extra_payload is invalid for {family}")
    if "transport_parameters" in plan:
        transports = plan["transport_parameters"]
        if not isinstance(transports, dict) or set(transports) != providers:
            raise ValueError("plan transport_parameters must specify every provider exactly once")
        for family, transport in transports.items():
            fields = {"min_interval_s", "timeout_s", "max_retries"}
            if not isinstance(transport, dict) or set(transport) != fields:
                raise ValueError(f"plan transport fields are missing or invalid for {family}")
            for name in ("min_interval_s", "timeout_s"):
                value = transport[name]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"plan transport {name} is invalid for {family}")
            if transport["timeout_s"] == 0 or type(transport["max_retries"]) is not int or transport["max_retries"] < 0:
                raise ValueError(f"plan timeout_s/max_retries is invalid for {family}")


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        try:
            json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            name = tmp.name
        except BaseException:
            Path(tmp.name).unlink(missing_ok=True)
            raise
    os.replace(name, path)


def evidence_issues(manifest: dict, runs: list[object], files: dict[str, str | Path]) -> list[str]:
    """Check the complete frozen matrix, even when responses are invalid/abstain."""
    issues: list[str] = []
    records = []
    for index, record in enumerate(runs, 1):
        if not isinstance(record, dict):
            issues.append(f"run record {index} is not a JSON object")
        else:
            records.append(record)
    try:
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
        plan = manifest["plan"]
        _validate_plan(plan)
        expected = expected_keys(plan["providers"], plan["personas"], plan["scenario"], plan["repeats"])
        if expected != manifest["expected_run_keys"]:
            issues.append("manifest expected keys differ from plan")
        if plan["kind"] == "pilot" and ((len(plan["providers"]), len(plan["personas"]), plan["repeats"]) != (3, 9, 3)
                                         or set(plan["providers"]) != {"deepseek", "qwen", "kimi"}):
            issues.append("pilot plan is not the 3 x 9 x 3 matrix")
        keys = [r.get("run_id") for r in records]
        if any(not isinstance(key, str) for key in keys):
            issues.append("run keys must be strings")
        keys = [key for key in keys if isinstance(key, str)]
        counts = Counter(keys)
        missing = sorted(set(expected) - set(keys))
        extra = sorted(set(keys) - set(expected), key=str)
        duplicate = sorted((k for k, n in counts.items() if n > 1), key=str)
        if missing:
            issues.append(f"missing run keys ({len(missing)}): {missing[:5]}")
        if extra:
            issues.append(f"unexpected run keys ({len(extra)}): {extra[:5]}")
        if duplicate:
            issues.append(f"duplicate run keys ({len(duplicate)}): {duplicate[:5]}")
        if sorted(keys, key=str) != sorted(manifest.get("actual_run_keys", []), key=str):
            issues.append("manifest actual keys differ from log")
        for label, path in files.items():
            if sha256(path) != manifest["file_hashes"][label]:
                issues.append(f"{label} file hash changed")
        for r in records:
            if r.get("_load_error"):
                issues.append(r["_load_error"])
            m = r.get("meta", {})
            if not isinstance(m, dict):
                issues.append(f"run metadata is not an object: {r.get('run_id')}")
                continue
            if type(r.get("valid")) is not bool or not isinstance(r.get("errors", []), list):
                issues.append(f"invalid validity/errors fields: {r.get('run_id')}")
            parsed = r.get("parsed")
            if (r.get("valid") and not isinstance(parsed, dict)) or (parsed is not None and not isinstance(parsed, dict)):
                issues.append(f"parsed response is not an object: {r.get('run_id')}")
            key = run_key(m.get("model_family"), m.get("persona_id"), m.get("scenario_id"), m.get("repeat_id"))
            if (key != r.get("run_id") or m.get("run_id") != key
                    or m.get("shelf_condition") != "named"
                    or m.get("prompt_version") != plan["prompt_version"]
                    or r.get("batch_id") != manifest["batch_id"]
                    or r.get("is_mock") != plan["mock"]):
                issues.append(f"incompatible run metadata: {r.get('run_id')}")
            seed_text = f"{m.get('persona_id')}|{m.get('scenario_id')}|{m.get('repeat_id')}"
            seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
            if type(m.get("shelf_order_seed")) is not int or m["shelf_order_seed"] != seed:
                issues.append(f"shelf order seed mismatch: {r.get('run_id')}")
            if m.get("model_family") in plan["models"] and (r.get("valid") or r.get("failure_stage") != "provider"):
                if m.get("model_name") != plan["models"][m["model_family"]]:
                    issues.append(f"requested model mismatch: {r.get('run_id')}")
            if not plan["mock"]:
                metadata = r.get("provider_metadata") or {}
                if not isinstance(metadata, dict):
                    issues.append(f"provider metadata is not an object: {r.get('run_id')}")
                    continue
                family = m.get("model_family")
                if family in plan["models"]:
                    if ("transport_parameters" in plan
                            and metadata.get("transport_parameters") != plan["transport_parameters"][family]):
                        issues.append(f"transport parameters mismatch: {r.get('run_id')}")
                    if (metadata.get("provider_family") != family
                            or metadata.get("requested_model") != plan["models"][family]):
                        issues.append(f"requested provider/model metadata mismatch: {r.get('run_id')}")
                    has_response = bool(r.get("valid") or metadata.get("returned_model")
                                        or r.get("failure_stage") in ("parse", "validation"))
                    for field, value in plan["requests"][family].items():
                        if ((has_response and field not in metadata)
                                or (field in metadata and metadata[field] != value)):
                            issues.append(f"request {field} mismatch: {r.get('run_id')}")
                else:
                    has_response = bool(r.get("valid"))
                if has_response and (not metadata.get("returned_model")
                                       or not metadata.get("usage")
                                       or not metadata.get("request_id")
                                       or metadata.get("latency_ms") is None
                                       or not metadata.get("finish_reason")
                                       or m.get("model_version") != metadata.get("returned_model")):
                    issues.append(f"incomplete real API metadata: {r.get('run_id')}")
                if metadata.get("finish_reason") == "length":
                    issues.append(f"real response reached token limit: {r.get('run_id')}")
                if not r.get("valid") and not r.get("failure_stage"):
                    issues.append(f"missing failure stage: {r.get('run_id')}")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        issues.append(f"invalid manifest or input: {exc}")
    return issues
