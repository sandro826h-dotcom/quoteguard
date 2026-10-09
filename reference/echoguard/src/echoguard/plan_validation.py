"""Fail-closed observation/plan checks, before aggregation can hide duplicates."""
import hashlib


def validate_observations(planned, observed, *, require_complete=True):
    keys = [r["run_key"] for r in planned]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate planned request key")
    plan = {r["run_key"]: r for r in planned}
    seen = set()
    for row in observed:
        key = row.get("run_key")
        if key in seen:
            raise ValueError("Duplicate observation: " + str(key))
        if key not in plan:
            raise ValueError("Unplanned observation: " + str(key))
        seen.add(key)
        req = plan[key]
        if hashlib.sha256(req["prompt"].encode()).hexdigest() != req["prompt_sha256"]:
            raise ValueError("Changed planned prompt: " + key)
        for field in ("provider", "experiment", "persona_id", "repeat", "order", "prompt_sha256"):
            if row.get(field) != req.get(field):
                raise ValueError("Observation/plan mismatch: " + field + " " + key)
    missing = sorted(set(plan) - seen)
    if missing and require_complete:
        raise ValueError(f"Incomplete request matrix: {len(missing)} missing")
    return {"planned": len(plan), "observed": len(seen), "missing": missing, "complete": not missing}
