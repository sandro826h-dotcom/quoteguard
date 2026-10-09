"""Interactive local Gate 1 launcher; API keys exist only in this process tree.

Run from a trusted terminal on the computer that can reach all three API hosts:
    python scripts/run_local_live.py

The launcher prompts for keys without echo, checks each real smoke result, estimates
the 81-call cost, then requires a separate budget confirmation before the pilot.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("deepseek", "qwen", "kimi")
KEY_NAMES = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}
# Conservative rates for the planned IDs at the Beijing Qwen endpoint, checked
# against official pages on 2026-09-30. DeepSeek uses peak, cache-miss pricing.
# These are estimates and must be checked against the account's actual region/rates.
RATES = {
    "deepseek": ("USD", 1.32, 3.96),
    "qwen": ("CNY", 0.8, 2.0),
    "kimi": ("USD", 0.95, 4.0),
}


def smoke_summary(batch_dir: Path, family: str) -> dict:
    manifest = json.loads((batch_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (batch_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if manifest["status"] != "complete" or len(records) != 1 or len(manifest["actual_run_keys"]) != 1:
        raise ValueError(f"{family}: smoke batch is incomplete")
    record = records[0]
    meta = record.get("provider_metadata") or {}
    usage = meta.get("usage") or {}
    required = ("returned_model", "request_id", "finish_reason", "latency_ms")
    if (not record.get("valid") or not isinstance(record.get("parsed"), dict)
            or any(meta.get(k) is None or meta.get(k) == "" for k in required)
            or not isinstance(usage.get("prompt_tokens"), int)
            or not isinstance(usage.get("completion_tokens"), int)
            or usage["prompt_tokens"] <= 0 or usage["completion_tokens"] <= 0
            or meta["finish_reason"] == "length"):
        raise ValueError(f"{family}: response, JSON or API metadata failed smoke inspection")
    return {
        "batch_id": manifest["batch_id"],
        "requested_model": manifest["plan"]["models"][family],
        "returned_model": meta["returned_model"],
        "request_id": meta["request_id"],
        "usage": usage,
        "latency_ms": meta["latency_ms"],
        "finish_reason": meta["finish_reason"],
        "file_hashes": manifest["file_hashes"],
        "batch_dir": str(batch_dir.relative_to(ROOT) if batch_dir.is_relative_to(ROOT) else batch_dir),
    }


def estimate_pilot(smokes: dict[str, dict]) -> dict[str, dict]:
    estimates = {}
    for family in FAMILIES:
        currency, input_rate, output_rate = RATES[family]
        usage = smokes[family]["usage"]
        # 27 calls/provider; allow 30% input overhead and budget the configured
        # 800-token output ceiling. Retries and pricing changes remain possible.
        input_tokens = int(usage["prompt_tokens"] * 1.3 + 0.999)
        max_output_tokens = 800
        estimate = 27 * (input_tokens * input_rate + max_output_tokens * output_rate) / 1_000_000
        estimates[family] = {"currency": currency, "input_tokens_per_call": input_tokens,
                             "output_tokens_per_call": max_output_tokens,
                             "amount": round(estimate, 6)}
    return estimates


def run_child(args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False)
    if result.returncode:
        raise RuntimeError(f"Command stopped with exit code {result.returncode}; inspect its batch record")


def main() -> None:
    print("Enter freshly generated API keys locally. Input is hidden and never saved.")
    env = os.environ.copy()
    for family in FAMILIES:
        value = getpass.getpass(f"{family} API key: ").strip()
        if not value:
            raise SystemExit(f"Missing {family} key; no API call started")
        env[KEY_NAMES[family]] = value
        del value

    name = datetime.now(timezone.utc).strftime("live-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    parent = ROOT / "runs" / name
    parent.mkdir(parents=True, exist_ok=False)
    report_path = parent / "preflight_report.json"
    report: dict = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "status": "smoke_running", "smokes": {}, "pilot": None}
    try:
        for family in FAMILIES:
            batch_dir = parent / f"smoke-{family}"
            print(f"\nRunning one real {family} smoke call...")
            run_child(["scripts/run_gate1_pilot.py", "--providers", family, "--smoke",
                       "--batch-dir", str(batch_dir)], env)
            report["smokes"][family] = smoke_summary(batch_dir, family)
            s = report["smokes"][family]
            print(f"{family}: returned={s['returned_model']} usage={s['usage']} "
                  f"request_id={s['request_id']} latency_ms={s['latency_ms']}")

        report["estimated_pilot_cost"] = estimate_pilot(report["smokes"])
        report["status"] = "awaiting_budget_confirmation"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("\nEstimated 81-call cost (27 calls per provider; peak/uncached where applicable):")
        for family, cost in report["estimated_pilot_cost"].items():
            print(f"  {family}: {cost['currency']} {cost['amount']:.4f}")
        print("Check the account balance and current rates before proceeding.")
        if input("After the project owner confirms this budget, type CONFIRM: ").strip() != "CONFIRM":
            print(f"Stopped before the pilot. Smoke report: {report_path}")
            return

        report["budget_confirmed_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "pilot_running"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pilot_dir = parent / "pilot"
        run_child(["scripts/run_gate1_pilot.py", "--providers", ",".join(FAMILIES),
                   "--repeats", "3", "--budget-confirmed", "--batch-dir", str(pilot_dir)], env)
        manifest = json.loads((pilot_dir / "batch_manifest.json").read_text(encoding="utf-8"))
        report["pilot"] = {"batch_id": manifest["batch_id"], "status": manifest["status"],
                           "planned": len(manifest["expected_run_keys"]),
                           "actual": len(manifest["actual_run_keys"]),
                           "batch_dir": str(pilot_dir.relative_to(ROOT))}
        analysis = subprocess.run([sys.executable, "scripts/analyze_gate1_results.py",
                                   "--batch-dir", str(pilot_dir)], cwd=ROOT, env=env,
                                  text=True, capture_output=True, check=False)
        (parent / "analysis_real.txt").write_text(analysis.stdout + analysis.stderr, encoding="utf-8")
        report["status"] = "complete" if analysis.returncode == 0 else "analysis_failed"
        print(f"Pilot {report['pilot']['actual']}/{report['pilot']['planned']}; "
              f"analysis exit={analysis.returncode}. Files: {parent}")
    except Exception as exc:
        report["status"] = "stopped"
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"Stopped: {report['error']}")
        raise SystemExit(2) from None
    finally:
        # Report intentionally contains no key or raw request/response text.
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
