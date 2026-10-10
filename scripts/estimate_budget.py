"""Offline estimate of the formal experiment: calls, tokens and cost (no model call, no network).

Method
- Call counts: orchestrate plans (stage A transcription, stage B M0/M1/extract) are built for the dev tasks with
  the mock executor standing in for stage A, then scaled by test_tasks / dev_tasks. ds_flash_think runs M1 only.
  M2-M4 share the extraction calls; the M4 review reuses the partner's extraction (no extra calls).
- Input tokens: plan prompt bytes times tokens per byte measured on the recorded pilot observations
  (per config where observed, otherwise pooled over configs of the same call type). Extraction calls of image
  quotes and the M0/M1 calls that contain them are corrected for the placeholder text the mock transcription left.
  Transcription input uses the observed image-call tokens of the vision config.
- Output tokens: observed mean per (config, call type); unobserved configs use the maximum observed for the type.
- Upper bound: output = max_tokens of the request, input = prompt bytes times the maximum observed tokens per byte.
- Prices: the rates file used by the pilots (runs/pilot_inputs/rates.json), per million tokens, the same
  table the executor reads for cost = (input*rate_in + output*rate_out) / 1e6.

Usage:  PYTHONPATH=src python scripts/estimate_budget.py
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard import orchestrate as orch  # noqa: E402
from quoteguard.llm import load_models  # noqa: E402
from quoteguard.params import load_snapshot  # noqa: E402
from quoteguard.taskgen import DEFAULT_SNAPSHOT_PATH  # noqa: E402

CONFIGS = ["ds_pro", "ds_flash", "qwen_max", "qwen_plus", "glm", "kimi"]
THINK = "ds_flash_think"
PROVIDER_NAME = {"deepseek": "DeepSeek 官方", "dashscope": "阿里云百炼", "siliconflow": "硅基流动"}
FREE_QUOTA = 1_000_000
OBS_DIRS = (sorted(glob.glob(str(ROOT / "runs/pilota-*-live"))) + sorted(glob.glob(str(ROOT / "runs/pilotb-*-live")))
            + sorted(glob.glob(str(ROOT / "runs/step0*-live"))))
TYPES = ("M0", "M1", "extract", "transcribe")


def nbytes(r):
    return len((r.get("system") or "").encode()) + len(r["text"].encode())


def load_pilot():
    """[(config, type, run input tokens, output tokens, prompt bytes or None)] from the recorded observations."""
    plans = {}
    for d in OBS_DIRS:
        p = Path(d) / "plan.json"
        if p.exists():
            for r in json.loads(p.read_text(encoding="utf-8"))["requests"]:
                plans[r["run_key"]] = r
    rows = []
    for d in OBS_DIRS:
        for f in sorted(Path(d, "observations").glob("*.json")):
            o = json.loads(f.read_text(encoding="utf-8"))
            if o.get("method") not in TYPES:
                continue
            c = o.get("cost_estimate") or {}
            if c.get("input_tokens") is None:
                continue
            r = plans.get(o["run_key"])
            rows.append({"config": o["config_id"], "type": o["method"], "in": c["input_tokens"],
                         "out": c["output_tokens"], "bytes": nbytes(r) if r else None})
    return rows


def build_dev_requests(tmp: Path, rates_path: Path, tasks_dir: Path, data_dir: Path):
    kw = dict(data_dir=data_dir)
    budget = "USD=100,CNY=1000"
    rates = json.loads(rates_path.read_text(encoding="utf-8"))

    def build(configs, methods, out, trans=None):
        return orch.build_stage_plans(tasks_dir, configs, methods, "est", "dev", {"USD": 100, "CNY": 1000},
                                      rates, out, transcription_batches=trans, **kw)
    out1 = tmp / "est1"
    pa = build(CONFIGS, list(orch.METHODS), out1)["A"]
    mock = orch.run_stage(pa, "mock", tmp / "mock", batch_id="mockA")
    p1 = build(CONFIGS, list(orch.METHODS), out1, [mock])
    out2 = tmp / "est2"
    p2 = build([THINK], ["M1"], out2, [mock])
    reqs = {}
    for k, p in (("A", pa), ("B", p1["B"]), ("T", p2["B"])):
        reqs[k] = json.loads(Path(p).read_text(encoding="utf-8"))["requests"]
    return reqs, rates


def summarize(vals):
    return (statistics.mean(vals), max(vals)) if vals else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "data/tasks/dev/tasks"))
    ap.add_argument("--data-dir", default=str(ROOT / "data/taskgen/dev_v3"))
    ap.add_argument("--rates", default=str(ROOT / "runs/pilot_inputs/rates.json"))
    ap.add_argument("--test-tasks", type=int, default=48)
    ap.add_argument("--out", default=str(ROOT / "runs/derived/budget_estimate_20261010"))
    ap.add_argument("--margin", type=float, default=0.10)
    a = ap.parse_args()

    models = load_models(ROOT / "configs/models.yaml")
    rates = json.loads(Path(a.rates).read_text(encoding="utf-8"))
    missing_price = []
    if THINK not in rates:
        rates[THINK] = dict(rates["ds_flash"])      # same model deepseek-flash, same list price
        missing_price.append(f"{THINK}: no own entry, deepseek-flash price (ds_flash) applied")
    snap = load_snapshot(DEFAULT_SNAPSHOT_PATH)
    fx = float(snap.fx_cny_per_usd())

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rpath = tmp / "rates.json"
        rpath.write_text(json.dumps(rates), encoding="utf-8")
        reqs, _ = build_dev_requests(tmp, rpath, Path(a.tasks), Path(a.data_dir))
    n_dev = len({r["task_id"] for r in reqs["B"]})
    scale = a.test_tasks / n_dev

    # ---- pilot statistics
    pilot = load_pilot()
    obs = collections.defaultdict(list)
    for r in pilot:
        obs[(r["config"], r["type"])].append(r)
    ratio_cfg, ratio_pool_mean, ratio_pool_max = {}, {}, {}
    for t in ("M0", "M1", "extract"):
        allr = [r["in"] / r["bytes"] for r in pilot if r["type"] == t and r["bytes"]]
        ratio_pool_mean[t], ratio_pool_max[t] = statistics.mean(allr), max(allr)
        for (c, tt), rows in obs.items():
            rr = [x["in"] / x["bytes"] for x in rows if tt == t and x["bytes"]]
            if rr:
                ratio_cfg[(c, t)] = (statistics.mean(rr), max(rr))
    out_max_type = {t: max(r["out"] for r in pilot if r["type"] == t) for t in TYPES}
    in_mean_type = {t: statistics.mean(r["in"] for r in pilot if r["type"] == t) for t in TYPES}

    def out_tok(cfg, t):
        rows = obs.get((cfg, t))
        if rows:
            return statistics.mean(x["out"] for x in rows), "observed"
        return out_max_type[t], "type max (no observation)"

    # ---- image-quote correction
    img = {(r["task_id"], r["cand_id"]) for r in reqs["A"]}
    corr = {}
    for c in CONFIGS:
        ex_c = [r for r in reqs["B"] if r["config_id"] == c and r["method"] == "extract"]
        base = statistics.mean(nbytes(r) for r in ex_c if (r["task_id"], r["cand_id"]) not in img)
        corr[c] = {"base": base, "delta": collections.defaultdict(float)}
        for r in ex_c:
            if (r["task_id"], r["cand_id"]) in img:
                corr[c]["delta"][r["task_id"]] += base - nbytes(r)

    def eff_bytes(r):
        c, t = r["config_id"], r["method"]
        if t == "extract":
            if (r["task_id"], r["cand_id"]) in img:
                return corr[c]["base"] if c in corr else nbytes(r)
            return nbytes(r)
        if t in ("M0", "M1") and c in corr:
            return nbytes(r) + corr[c]["delta"].get(r["task_id"], 0.0)
        return nbytes(r)

    # ---- rows: (config, type) -> counts and tokens (test scale)
    rows = []
    vision_of = {c: models[c].vision_config_id for c in CONFIGS}
    for cfg in CONFIGS + [THINK]:
        src = reqs["T"] if cfg == THINK else reqs["B"]
        for t in ("M0", "M1", "extract"):
            rs = [r for r in src if r["config_id"] == cfg and r["method"] == t]
            if not rs:
                continue
            base_cfg = "ds_flash" if cfg == THINK else cfg
            rm, rx = ratio_cfg.get((base_cfg, t), (ratio_pool_mean[t], ratio_pool_max[t]))
            rx = ratio_pool_max[t]
            tot_b = sum(eff_bytes(r) for r in rs)
            o_mean, o_src = out_tok(base_cfg, t)
            if cfg == THINK:
                o_mean, o_src = out_max_type["M1"], "M1 type max (thinking output not observed)"
            n = len(rs) * scale
            rows.append({"config": cfg, "type": t, "provider": models[cfg].provider, "calls": n,
                         "in_point": tot_b * rm * scale, "out_point": o_mean * n,
                         "in_upper": tot_b * rx * scale, "out_upper": sum(r["max_tokens"] for r in rs) * scale,
                         "out_source": o_src,
                         "avg_in_per_call": tot_b * rm / len(rs), "max_tokens": rs[0]["max_tokens"]})
    # transcription: once per shared vision config
    for v in sorted({vision_of[c] for c in CONFIGS}):
        rs = [r for r in reqs["A"] if r["config_id"] == v]
        orows = obs.get((v, "transcribe"), [])
        i_mean = statistics.mean(x["in"] for x in orows)
        i_max = max(x["in"] for x in orows)
        o_mean = statistics.mean(x["out"] for x in orows)
        n = len(rs) * scale
        rows.append({"config": v, "type": "transcribe", "provider": models[v].provider, "calls": n,
                     "in_point": i_mean * n, "out_point": o_mean * n, "in_upper": i_max * n,
                     "out_upper": sum(r["max_tokens"] for r in rs) * scale,
                     "out_source": f"observed (n={len(orows)})", "avg_in_per_call": i_mean,
                     "max_tokens": rs[0]["max_tokens"]})

    for r in rows:
        rt = rates[r["config"]]
        cur = rt["currency"]
        r["currency"] = cur
        r["cost_point"] = (r["in_point"] * rt["input"] + r["out_point"] * rt["output"]) / 1e6
        r["cost_upper"] = (r["in_upper"] * rt["input"] + r["out_upper"] * rt["output"]) / 1e6
        k = fx if cur == "USD" else 1.0
        r["cny_point"] = r["cost_point"] * k
        r["cny_upper"] = r["cost_upper"] * k

    def agg(rs):
        keys = ["calls", "in_point", "out_point", "in_upper", "out_upper", "cny_point", "cny_upper"]
        return {k: sum(x[k] for x in rs) for k in keys}

    by_provider = {}
    for p in PROVIDER_NAME:
        rs = [r for r in rows if r["provider"] == p]
        by_provider[PROVIDER_NAME[p]] = agg(rs)
    quota = {}
    for m in ("qwen_max", "qwen_plus", "qwen_vl"):
        rs = [r for r in rows if r["config"] == m]
        quota[m] = {"model": models[m].model, "tokens_point": sum(r["in_point"] + r["out_point"] for r in rs),
                    "tokens_upper": sum(r["in_upper"] + r["out_upper"] for r in rs), "free_quota": FREE_QUOTA}
    total = agg(rows)
    with_margin = {"cny_point": total["cny_point"] * (1 + a.margin), "cny_upper": total["cny_upper"] * (1 + a.margin)}
    usd_part = sum(r["cost_point"] for r in rows if r["currency"] == "USD")
    usd_up = sum(r["cost_upper"] for r in rows if r["currency"] == "USD")
    cny_part = sum(r["cost_point"] for r in rows if r["currency"] == "CNY")
    cny_up = sum(r["cost_upper"] for r in rows if r["currency"] == "CNY")

    result = {
        "basis": {"dev_tasks": n_dev, "test_tasks": a.test_tasks, "scale": scale, "fx_cny_per_usd": fx,
                  "fx_note": "snapshot value (assumed, not a market quote)", "repeats": 1,
                  "pilot_observations_used": len(pilot),
                  "rates_file": str(Path(a.rates).relative_to(ROOT)) if Path(a.rates).is_relative_to(ROOT) else a.rates,
                  "rates": {k: rates[k] for k in CONFIGS + ["qwen_vl", "glm_v", THINK]},
                  "tokens_per_byte_mean": ratio_pool_mean, "tokens_per_byte_max": ratio_pool_max,
                  "missing_price": missing_price,
                  "missing_observation": sorted(f"{c}:{t}" for c in CONFIGS for t in ("M0", "M1", "extract")
                                                if (c, t) not in obs) +
                  [f"{THINK}:M1 (thinking output)"]},
        "rows": rows, "by_provider": by_provider, "free_quota": quota,
        "native_currency": {"USD_point": usd_part, "USD_upper": usd_up, "CNY_point": cny_part, "CNY_upper": cny_up},
        "total": total, "total_with_margin": with_margin, "margin": a.margin,
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "estimate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "estimate.md").write_text(render_md(result), encoding="utf-8")
    print(json.dumps({"total": total, "with_margin": with_margin, "by_provider": by_provider}, ensure_ascii=False,
                     indent=1))


def f0(x):
    return f"{x:,.0f}"


def render_md(R):
    b = R["basis"]
    L = ["# 正式实验预算估算（离线，未调用模型）", "",
         f"- 任务数：测试集 {b['test_tasks']}，dev {b['dev_tasks']}，外推系数 {b['scale']:.2f}；每次调用 1 个重复。",
         f"- 汇率：{b['fx_cny_per_usd']} 元/美元（参数快照取值，假定值）。价格表：{b['rates_file']}（每百万 token）。",
         f"- 预试验观测 {b['pilot_observations_used']} 条；全部数字为估算值。", "",
         "## 一、配置 × 调用类型", "",
         "| 配置 | 类型 | 调用数 | 输入 tok(点) | 输出 tok(点) | 费用(点,原币) | 输入 tok(上界) | 输出 tok(上界) | 费用(上界,原币) | 币种 | 输出来源 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in R["rows"]:
        L.append(f"| {r['config']} | {r['type']} | {r['calls']:.0f} | {f0(r['in_point'])} | {f0(r['out_point'])} | "
                 f"{r['cost_point']:.3f} | {f0(r['in_upper'])} | {f0(r['out_upper'])} | {r['cost_upper']:.3f} | "
                 f"{r['currency']} | {r['out_source']} |")
    L += ["", "## 二、按服务商合计（人民币）", "",
          "| 服务商 | 调用数 | 输入 tok(点) | 输出 tok(点) | 点估计(元) | 上界(元) |", "|---|---|---|---|---|---|"]
    for k, v in R["by_provider"].items():
        L.append(f"| {k} | {v['calls']:.0f} | {f0(v['in_point'])} | {f0(v['out_point'])} | "
                 f"{v['cny_point']:.2f} | {v['cny_upper']:.2f} |")
    L += ["", "百炼千问为免费额度，费用按标价折算。每个模型免费额度 100 万 token。", "",
          "| 模型 | 名称 | token 点估计 | token 上界 | 免费额度 | 点估计/额度 | 上界/额度 |", "|---|---|---|---|---|---|---|"]
    for k, v in R["free_quota"].items():
        L.append(f"| {k} | {v['model']} | {f0(v['tokens_point'])} | {f0(v['tokens_upper'])} | {f0(v['free_quota'])} | "
                 f"{v['tokens_point'] / v['free_quota']:.2f} | {v['tokens_upper'] / v['free_quota']:.2f} |")
    t, m = R["total"], R["total_with_margin"]
    n = R["native_currency"]
    L += ["", "## 三、总计", "",
          f"- 调用数 {t['calls']:.0f}；输入 {f0(t['in_point'])} tok，输出 {f0(t['out_point'])} tok（点估计）。",
          f"- 点估计 {t['cny_point']:.2f} 元；上界 {t['cny_upper']:.2f} 元。",
          f"- 加 {R['margin']:.0%} 重试余量：点估计 {m['cny_point']:.2f} 元；上界 {m['cny_upper']:.2f} 元。",
          f"- 原币：美元 {n['USD_point']:.2f}（上界 {n['USD_upper']:.2f}），人民币 {n['CNY_point']:.2f}（上界 {n['CNY_upper']:.2f}）。",
          "", "## 四、缺失项", ""]
    L += [f"- 缺价格：{x}" for x in b["missing_price"]] or ["- 价格表无缺项。"]
    L += [f"- 缺观测（输出取同类型最大值）：{x}" for x in b["missing_observation"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
