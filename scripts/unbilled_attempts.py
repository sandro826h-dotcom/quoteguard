"""Read-only scan for attempts that may have been billed but were not booked.

Looks at runs/*/errors/*.json (and, as a second table, the attempts stored in observations) for
HTTP 502 or 504 attempts with possibly_billed false, and estimates the upper-bound cost with the
executor's own formula, using the plan.json and models.yaml copied into each batch directory.
Nothing under runs/ is modified.

Usage: python scripts/unbilled_attempts.py [--runs runs]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard.executor import upper_cost  # noqa: E402
from quoteguard.llm import load_models  # noqa: E402

STATUSES = (502, 504)


def scan_batch(batch: Path) -> dict:
    plan_f, models_f = batch / "plan.json", batch / "models.yaml"
    if not plan_f.is_file() or not models_f.is_file():
        return {"skipped": "no plan.json or models.yaml copy"}
    plan = json.loads(plan_f.read_text(encoding="utf-8"))
    models = load_models(models_f)
    reqs = {r["run_key"]: r for r in plan["requests"]}
    out = {"errors": {"attempts": 0, "cost": {}}, "observations": {"attempts": 0, "cost": {}}}
    for kind, sub in (("errors", "errors"), ("observations", "observations")):
        for f in sorted((batch / sub).glob("*.json")) if (batch / sub).is_dir() else []:
            rec = json.loads(f.read_text(encoding="utf-8"))
            req = reqs.get(rec.get("run_key"))
            if req is None:
                continue
            cur, upper = upper_cost(req, models[req["config_id"]], plan["rates"][req["config_id"]])
            n = sum(1 for a in rec.get("attempts") or []
                    if a.get("http_status") in STATUSES and not a.get("possibly_billed"))
            if n:
                out[kind]["attempts"] += n
                out[kind]["cost"][cur] = out[kind]["cost"].get(cur, 0.0) + n * upper
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    a = ap.parse_args(argv)
    result = {}
    for batch in sorted(p for p in Path(a.runs).iterdir() if (p / "manifest.json").is_file()):
        result[batch.name] = scan_batch(batch)
    totals = {"errors": {}, "observations": {}}
    for r in result.values():
        for kind in totals:
            for cur, v in r.get(kind, {}).get("cost", {}).items():
                totals[kind][cur] = totals[kind].get(cur, 0.0) + v
    print(json.dumps({"batches": result, "totals": totals}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
