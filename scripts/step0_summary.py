"""Summarise a step 0 batch directory into step0_summary.json and step0_summary.md."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard.util import atomic_json  # noqa: E402


def summarise(batch: Path) -> dict:
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    obs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((batch / "observations").glob("*.json"))]
    obs.sort(key=lambda o: o["run_key"])
    calls = []
    for o in obs:
        u = o.get("usage") or {}
        calls.append({
            "run_key": o["run_key"], "config_id": o["config_id"], "requested_model": o["model"]["requested"],
            "returned_model": o["model"]["returned"], "http_status": o["http_status"],
            "latency_ms": o["latency_ms"], "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"), "finish_reason": o["finish_reason"],
            "has_reasoning_content": o.get("has_reasoning_content"), "valid": o.get("valid"),
            "retries": max(0, len(o.get("attempts") or []) - 1), "text_head": (o["text"] or "")[:80],
            "cost": o["cost_estimate"]["amount"], "currency": o["cost_estimate"]["currency"],
            "is_mock": o["is_mock"]})

    def text_of(config_id, repeat):
        for o in obs:
            if o["config_id"] == config_id and o["method"] == "think" and o["repeat"] == repeat:
                return o["text"]
        return None
    seed_check = {}
    for cid in ("ds_flash", "qwen_plus"):
        a, b = text_of(cid, 1), text_of(cid, 2)
        seed_check[cid] = {"first": a, "second": b, "identical": (a == b) if a is not None and b is not None else None}
    kimi = [o for o in obs if o["config_id"] == "kimi" and o["method"] == "think"]
    kimi_lat = [o["latency_ms"] for o in kimi]
    kimi_summary = {"n": len(kimi), "median_latency_ms": statistics.median(kimi_lat) if kimi_lat else None,
                    "retries": sum(max(0, len(o.get("attempts") or []) - 1) for o in kimi)}
    cost: dict[str, float] = {}
    for o in obs:
        for c in (o["cost_estimate"], o.get("possibly_billed_cost")):
            if c:
                cost[c["currency"]] = cost.get(c["currency"], 0.0) + c["amount"]
    return {"batch_id": manifest["batch_id"], "status": manifest["status"], "stop": manifest.get("stop"),
            "completed": manifest.get("completed"), "total": manifest.get("total"), "calls": calls,
            "seed_check": seed_check, "kimi_latency": kimi_summary,
            "total_cost_estimate": {k: round(v, 6) for k, v in cost.items()}}


def to_markdown(s: dict) -> str:
    L = [f"# Step 0 summary ({s['batch_id']})", "",
         f"Status: {s['status']}; completed {s['completed']}/{s['total']}; "
         f"estimated cost {json.dumps(s['total_cost_estimate'])}", "",
         "| run_key | returned model | HTTP | latency ms | prompt tok | completion tok | finish | reasoning | retries | text |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for c in s["calls"]:
        text = c["text_head"].replace("|", "/").replace("\n", " ")
        L.append(f"| {c['run_key'].replace('|', '/')} | {c['returned_model']} | {c['http_status']} | {c['latency_ms']} | "
                 f"{c['prompt_tokens']} | {c['completion_tokens']} | {c['finish_reason']} | "
                 f"{c['has_reasoning_content']} | {c['retries']} | {text} |")
    L += ["", "## Seed repeatability"]
    for cid, v in s["seed_check"].items():
        L.append(f"- {cid}: identical={v['identical']} (first={v['first']!r}, second={v['second']!r})")
    k = s["kimi_latency"]
    L += ["", "## Kimi latency", f"- n={k['n']}, median={k['median_latency_ms']} ms, retries={k['retries']}", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", help="batch directory, e.g. runs/step0-live")
    a = ap.parse_args(argv)
    batch = Path(a.batch)
    s = summarise(batch)
    atomic_json(batch / "step0_summary.json", s)
    (batch / "step0_summary.md").write_text(to_markdown(s), encoding="utf-8")
    print(to_markdown(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
