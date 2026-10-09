"""Summarize observed usage using conservative official peak, uncached prices.

This estimates costs of returned usage; it is not an account invoice.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RATES = {"deepseek": (1.32, 3.96, "USD"), "qwen": (0.8, 2.0, "CNY"), "kimi": (6.5, 27.0, "CNY")}
CAPS = {"USD": 0.60, "CNY": 3.20}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()
    totals = defaultdict(float)
    counts = Counter()
    batches = {}
    unknown = []
    for path in sorted((ROOT / "runs").glob("live-*/**/runs.jsonl")):
        batch_cost = defaultdict(float)
        responses = 0
        attempts = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("is_mock"):
                continue
            family = record["meta"]["model_family"]
            meta = record.get("provider_metadata") or {}
            attempts += meta.get("attempt", 1)
            usage = meta.get("usage") or {}
            if usage and family in RATES:
                input_rate, output_rate, currency = RATES[family]
                amount = (usage["prompt_tokens"] * input_rate + usage["completion_tokens"] * output_rate) / 1_000_000
                totals[currency] += amount
                batch_cost[currency] += amount
                counts[family] += 1
                responses += 1
            if meta.get("attempt", 1) > 1 and meta.get("http_status") not in (401, 429):
                unknown.append({"batch": record["batch_id"], "run_id": record["run_id"], "attempts": meta["attempt"]})
        batches[path.parent.relative_to(ROOT).as_posix()] = {"usage_responses": responses, "http_attempts": attempts,
                                                            "conservative_cost": dict(batch_cost)}
    report = {"pricing_checked_date": "2026-10-02", "basis": "Peak DeepSeek, no input cache discounts; observed returned usage only",
              "currency_totals": {c: round(totals[c], 8) for c in CAPS}, "usage_response_counts": dict(counts),
              "authorized_caps": CAPS, "remaining_cap": {c: round(CAPS[c] - totals[c], 8) for c in CAPS},
              "unquantified_retry_usage": unknown, "batches": batches,
              "limitations": "Not an invoice. Account discounts/taxes/settlement not inspected. Kimi 429 requests are documented as uncharged."}
    if args.json_out:
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("currency_totals", "usage_response_counts", "remaining_cap", "unquantified_retry_usage")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
