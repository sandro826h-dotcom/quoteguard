"""Build the retry plan of a batch: the requests that have no usable observation after the run.

A request is retried when its observation is missing (never sent, or only failed attempts under errors/) or
unusable (valid false, empty text; for transcripts empty after the box marks are removed). A response that
returns text but does not parse is not retried: re-asking until it parses would select answers.

The plan keeps plan fields, run_keys, prompts, seeds and prompt hashes of the original; plan_id gets the suffix
-retry (or --plan-id). Run it as a separate batch:
    python -m quoteguard.orchestrate run --plan <out> --mode live --batch-id <batch>-retry --confirm-budget ...
and list the original and the retry batches together in assemble --batches. When a run_key has several
observations, assemble takes the earliest usable one (finished_at, then started_at; a tie goes to the batch
listed first), and the earliest of any kind when none is usable (orchestrate.load_observations).

Usage: python scripts/make_retry_plan.py BATCH_DIR --out PLAN.json [--budget USD=x,CNY=y] [--plan-id ID]
       [--plan ORIGINAL_PLAN.json] [--also EARLIER_RETRY_BATCH ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard.executor import parse_budget  # noqa: E402
from quoteguard.orchestrate import obs_usable, read_observations  # noqa: E402
from quoteguard.util import atomic_json  # noqa: E402


def retry_plan(batch, *, plan_path=None, also=(), budget=None, plan_id=None) -> dict:
    """Retry plan document (requests may be empty). Relative image paths are made absolute against the
    original plan's directory so that the plan can live anywhere."""
    batch = Path(batch)
    plan_path = Path(plan_path) if plan_path else batch / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    done = {o["run_key"] for o in read_observations([batch, *map(Path, also)]) if obs_usable(o)}
    reqs = []
    for r in plan["requests"]:
        if r["run_key"] in done:
            continue
        r = json.loads(json.dumps(r))
        for img in r["images"]:
            p = Path(img["path"])
            if not p.is_absolute():
                img["path"] = str((plan_path.parent / p).resolve())
        reqs.append(r)
    doc = {k: v for k, v in plan.items() if k != "requests"}
    doc["plan_id"] = plan_id or f"{plan['plan_id']}-retry"
    if budget:
        doc["budget"] = budget
    doc["requests"] = reqs
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("batch")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", default=None, help="USD=x,CNY=y; default: the original plan's budget")
    ap.add_argument("--plan-id", default=None)
    ap.add_argument("--plan", default=None, help="original plan file (default: <batch>/plan.json)")
    ap.add_argument("--also", action="append", default=[], help="earlier retry batch whose usable observations count")
    a = ap.parse_args(argv)
    doc = retry_plan(a.batch, plan_path=a.plan, also=a.also, budget=parse_budget(a.budget) if a.budget else None,
                     plan_id=a.plan_id)
    if not doc["requests"]:
        print(json.dumps({"retry_requests": 0}))
        return 0
    atomic_json(Path(a.out), doc)
    print(json.dumps({"retry_requests": len(doc["requests"]), "plan_id": doc["plan_id"], "out": a.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
