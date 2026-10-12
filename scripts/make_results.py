"""由组装评分的输出生成报告第五章的全部表格（Markdown、CSV、JSON）与灰度图（PNG、PDF、SVG）。

只读 runs/ 与评分输出，不写入 runs/，不调用模型，不联网。统计口径复用 quoteguard.metrics、quoteguard.stats
与 quoteguard.orchestrate（按任务整簇 bootstrap、主终点与 Holm 校正由评分阶段写入 endpoints.json）。

正式结果（冻结协议附录的默认路径）：

    PYTHONPATH=src python scripts/make_results.py --preset formal

等价于：

    PYTHONPATH=src python scripts/make_results.py --label formal --out output/results/formal \\
        --scores runs/derived/formal/scores --outputs runs/derived/formal/outputs \\
        --think-scores runs/derived/formal_think/scores --think-outputs runs/derived/formal_think/outputs \\
        --tasks data/tasks/test/tasks --gold data/gold/test \\
        --batches runs/deepseek-A runs/deepseek-A-retry runs/deepseek-B runs/deepseek-B-retry \\
                  runs/dashscope-A runs/dashscope-A-retry runs/dashscope-B runs/dashscope-B-retry \\
                  runs/siliconflow-A runs/siliconflow-A-retry runs/siliconflow-B runs/siliconflow-B-retry \\
                  runs/think-B runs/think-B-retry \\
        --ledger runs/spend_ledger.jsonl --seed 20261011 --B 9999

（预设只纳入实际存在的批次目录与思考组目录。）

流程测试（预试验重算数据，3 个任务，标注“流程测试”，不作结论）：

    PYTHONPATH=src python scripts/make_results.py --preset pilot

等价于：

    PYTHONPATH=src python scripts/make_results.py --label pilot_flowtest --out output/results/pilot_flowtest \\
        --scores runs/derived/pilot_rescore_20261010_fullreview/a/scores runs/derived/pilot_rescore_20261010_fullreview/b/scores \\
        --outputs runs/derived/pilot_rescore_20261010_fullreview/a/outputs runs/derived/pilot_rescore_20261010_fullreview/b/outputs \\
        --tasks data/tasks/dev/tasks --gold data/gold/dev \\
        --batches runs/pilota-A-live runs/pilota-B-live runs/pilota-B-retry-live \\
                  runs/pilotb-A-live runs/pilotb-B-live runs/pilotb-B-retry-live \\
        --ledger runs/spend_ledger.jsonl --seed 20261011 --B 9999

正式结果出来后把两张图复制到 docs/figures/（报告引用 ../figures/fig5_e1_accuracy_by_layer.svg 与 ../figures/fig6_e2_risk.svg）：

    cp output/results/formal/fig5_e1_accuracy_by_layer.{png,pdf,svg} output/results/formal/fig6_e2_risk.{png,pdf,svg} docs/figures/

思考组：协议附录没有对 formal_think 运行 score。给出 --think-outputs 而无 --think-scores 时，本脚本在内存中按
orchestrate.score_all 的写法评分（不写盘）；--preset formal 默认如此。

产出（--out）：tables.md、tables/<id>.csv 与 .json、fig5_e1_accuracy_by_layer.{png,pdf,svg}、
fig6_e2_risk.{png,pdf,svg}、manifest.json。评分输出里不存在的指标写“评分输出无此项”，并列入 tables.md
末尾“未覆盖项”。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import math
import statistics
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard import metrics  # noqa: E402
from quoteguard import stats as qstats  # noqa: E402
from quoteguard.orchestrate import load_observations, obs_usable, read_observations  # noqa: E402

METHODS = ("M0", "M1", "M2", "M3", "M4")
LAYERS = ("explicit", "implicit", "conflict", "insufficient")
LAYER_NAMES = {"explicit": "显式层", "implicit": "隐式层", "conflict": "冲突层", "insufficient": "信息不足层"}
REPORT_ORDER = ("ds_pro", "qwen_max", "glm", "kimi", "ds_flash", "qwen_plus")
THINK_CONFIG = "ds_flash_think"
PROVIDERS = ("deepseek", "dashscope", "siliconflow")
PROVIDER_NAMES = {"deepseek": "DeepSeek 官方", "dashscope": "阿里云百炼", "siliconflow": "硅基流动"}
SEED = 20261011
BOOT_B = 9999
PILOT_PREFIX = "流程测试（预试验重算数据，不作结论）"
NA = "评分输出无此项"
EMPTY = "无数据"
MODALITY_GROUPS = ("含图片", "含表格不含图片", "只含文字")

PILOT_ROOT = "runs/derived/pilot_rescore_20261010_fullreview"
PRESETS = {
    "formal": {
        "label": "formal", "scores": ["runs/derived/formal/scores"], "outputs": ["runs/derived/formal/outputs"],
        "think_scores": None, "think_outputs": "runs/derived/formal_think/outputs",
        "tasks": "data/tasks/test/tasks", "gold": "data/gold/test",
        "batches": [f"runs/{p}-{s}{r}" for p in PROVIDERS for s in "AB" for r in ("", "-retry")]
                   + ["runs/think-B", "runs/think-B-retry"],
        "ledger": "runs/spend_ledger.jsonl",
    },
    "pilot": {
        "label": "pilot_flowtest",
        "scores": [f"{PILOT_ROOT}/a/scores", f"{PILOT_ROOT}/b/scores"],
        "outputs": [f"{PILOT_ROOT}/a/outputs", f"{PILOT_ROOT}/b/outputs"],
        "think_scores": None, "think_outputs": None,
        "tasks": "data/tasks/dev/tasks", "gold": "data/gold/dev",
        "batches": ["runs/pilota-A-live", "runs/pilota-B-live", "runs/pilota-B-retry-live",
                    "runs/pilotb-A-live", "runs/pilotb-B-live", "runs/pilotb-B-retry-live"],
        "ledger": "runs/spend_ledger.jsonl",
    },
}

RULE_ACTIONS = {
    1: "主张收窄为“风险识别与追问”，决策正确率照实报告",
    2: "主张收窄为“核价与决策”，风险检出照实报告并做错误类型分析",
    3: "成本主张改为“每个正确决策的调用成本”对比，照实报告",
    4: "成效一节以非显式三层和真实报价集为主",
    5: "核价改进照实写，系统增量归于风险识别与追问",
    6: "报告精确率与召回率的权衡，给出阈值选择依据",
    7: "照实报告；主张改为 M4 在成本、时延和可核对出处上的优势",
    8: "照实报告，并分析错误类型，作为改进方向",
}


class MakeResultsError(ValueError):
    """Inputs that cannot be combined (overlapping configurations, missing directories)."""


# ------------------------------------------------------------------ formatting
def fpct(x: Optional[float], d: int = 1) -> str:
    return EMPTY if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{d}f}%"


def fnum(x: Optional[float], d: int = 3) -> str:
    return EMPTY if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def fci(est, lo, hi, kind: str = "pct", d: Optional[int] = None) -> str:
    if est is None or (isinstance(est, float) and math.isnan(est)):
        return EMPTY
    f = (lambda v: fpct(v, 1 if d is None else d)) if kind == "pct" else (lambda v: fnum(v, 2 if d is None else d))
    if lo is None or hi is None:
        return f(est)
    return f"{f(est)} [{f(lo)}, {f(hi)}]"


def ordered_configs(configs) -> List[str]:
    cs = [c for c in configs if c != THINK_CONFIG]
    return [c for c in REPORT_ORDER if c in cs] + sorted(c for c in cs if c not in REPORT_ORDER)


# ------------------------------------------------------------------ bootstrap helpers
def boot(rows: Sequence[dict], fn: Callable[[list], float], B: int, seed: int) -> tuple:
    """(estimate, lo, hi) of fn over rows; tasks are the clusters. Same draws and NaN handling as metrics.aggregate
    (a percentile that is NaN is reported as undefined)."""
    rows = list(rows)
    if not rows:
        return None, None, None
    est = fn(rows)
    if est is None or (isinstance(est, float) and math.isnan(est)):
        return None, None, None
    draws, _ = qstats._cluster_draws(rows, "task_id", fn, B, seed)
    lo, hi = np.quantile(draws, [0.025, 0.975])
    if lo != lo or hi != hi:
        return float(est), None, None
    return float(est), float(lo), float(hi)


def _mean_of(key: str) -> Callable[[list], float]:
    def fn(rs):
        v = [r[key] for r in rs if r.get(key) is not None]
        return float(np.mean(v)) if v else float("nan")
    return fn


def _sumk(rs, *keys) -> float:
    return float(sum(r.get(k) or 0 for r in rs for k in keys))


def _div(a: float, b: float) -> float:
    return float("nan") if b == 0 else a / b


def f_acc(rs):
    return metrics.metric_values_one(rs, "decision_correct")


def f_micro_f1(rs):
    return metrics.metric_values_one(rs, "micro_f1")


def f_precision(rs):
    """Precision of the three flagged risk categories (conflict, gap, ddp risk); hidden fees are recall-only."""
    tp = _sumk(rs, "tp_conflict", "tp_gap", "tp_ddp_risk")
    fp = _sumk(rs, "fp_conflict", "fp_gap", "fp_ddp_risk")
    return _div(tp, tp + fp)


def f_recall(rs):
    return _div(_sumk(rs, "tp"), _sumk(rs, "tp") + _sumk(rs, "fn"))


def f_ask_cover(rs):
    return metrics.metric_values_one(rs, "ask_cover_mean")


def f_regret_abs(rs):
    return metrics.metric_values_one(rs, "regret_abs_mean")


def f_ask_rate(rs):   # rows of gold-ask tasks only
    return _mean_of("decision_correct")([r for r in rs if r["gold_type"] == "ask"])


def f_false_ask(rs):
    return _mean_of("false_ask")([r for r in rs if r["gold_type"] == "choose"])


def f_cost_abs(rs):
    return metrics.metric_values_one(rs, "cost_abs_mean")


# ------------------------------------------------------------------ hashing / provenance
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(d: Path) -> dict:
    lines = []
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        lines.append(f"{f.relative_to(d).as_posix()}:{sha256_file(f)}")
    return {"files": len(lines), "tree_sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()}


def git_info(root: Path = ROOT) -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain", "--", "scripts", "src"))}


# ------------------------------------------------------------------ loading
def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def load_rows(score_dirs: Sequence[Path]) -> list:
    seen: dict = {}
    rows = []
    for d in score_dirs:
        f = Path(d) / "rows.json"
        if not f.exists():
            raise MakeResultsError(f"{f} 不存在")
        for r in _read_json(f):
            k = (str(r["method"]).upper(), r["config_id"], r["task_id"])
            if k in seen:
                raise MakeResultsError(f"配置重叠：{k} 同时出现在 {seen[k]} 与 {d}")
            seen[k] = str(d)
            rows.append(r)
    return rows


def load_endpoints(score_dirs: Sequence[Path]) -> Optional[dict]:
    for d in score_dirs:
        f = Path(d) / "endpoints.json"
        if f.exists():
            return _read_json(f)
    return None


def load_outputs(out_dirs: Sequence[Path]) -> dict:
    res: dict = {}
    for d in out_dirs:
        for f in sorted(Path(d).glob("*/*/*.json")):
            o = _read_json(f)
            k = (f.parent.parent.name, f.parent.name, f.stem)
            if k in res:
                raise MakeResultsError(f"输出重叠：{k} 出现在多个 outputs 目录")
            res[k] = o
    return res


def modality_group(task: dict) -> str:
    mods = {c["presentation"]["modality"] for c in task["candidates"]}
    if "image" in mods:
        return "含图片"
    if "table" in mods:
        return "含表格不含图片"
    return "只含文字"


def load_tasks(tasks_dir: Path, task_ids) -> dict:
    out = {}
    for tid in sorted(set(task_ids)):
        f = Path(tasks_dir) / f"{tid}.json"
        if not f.exists():
            raise MakeResultsError(f"任务文件缺失：{f}")
        t = _read_json(f)
        out[tid] = {"layer": t.get("layer"), "group": modality_group(t), "mode": (t.get("shipment") or {}).get("mode")}
    return out


def load_gold(gold_dir: Path, task_ids) -> dict:
    out = {}
    for tid in sorted(set(task_ids)):
        f = Path(gold_dir) / f"{tid}.json"
        if not f.exists():
            raise MakeResultsError(f"金标签缺失：{f}")
        out[tid] = _read_json(f)
    return out


def score_outputs_in_memory(outputs: dict, gold_dir: Path, tasks_dir: Path) -> list:
    """Rows for assembled outputs that were never scored, built like orchestrate.score_all (nothing is written)."""
    gold = load_gold(gold_dir, {k[2] for k in outputs})
    rows = []
    for k in sorted(outputs):
        o = outputs[k]
        row = metrics.score_task(o, gold[k[2]])
        row.update(cost_usd=(o.get("cost") or {}).get("USD"), cost_cny=(o.get("cost") or {}).get("CNY"),
                   n_calls=len(o.get("calls", [])))
        rows.append(row)
    return metrics.attach_layers(rows, tasks_dir)


def load_fx(snapshot: Optional[Path], override: Optional[float]) -> dict:
    if override is not None:
        return {"cny_per_usd": float(override), "source": "命令行参数 --usd-cny", "date": None}
    s = _read_json(Path(snapshot))
    fx = s["fx"]["CNY_per_USD"]
    return {"cny_per_usd": float(fx["value"]), "source": f"参数快照 {Path(snapshot).name}，中间价日期 {fx.get('date')}",
            "date": fx.get("date")}


def load_batches(batch_dirs: Sequence[Path]) -> dict:
    """Read-only view of batch directories: observations (selected and raw), planned run_keys, config->provider."""
    batch_dirs = [Path(d) for d in batch_dirs]
    raw_by_batch = {d.name: read_observations([d]) for d in batch_dirs}
    selected = load_observations(batch_dirs) if batch_dirs else {}
    provider_of_config: dict = {}
    for obs in raw_by_batch.values():
        for o in obs:
            provider_of_config.setdefault(o.get("config_id"), o.get("provider"))
    planned: dict = {}
    for d in batch_dirs:
        pf = d / "plan.json"
        if pf.exists():
            for rq in _read_json(pf).get("requests", []):
                planned.setdefault(rq["run_key"], rq["config_id"])
    return {"dirs": batch_dirs, "raw_by_batch": raw_by_batch, "selected": selected,
            "provider_of_config": provider_of_config, "planned": planned}


def load_ledger(path: Optional[Path]) -> list:
    if path is None or not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ usage helpers (copied from scripts/rescore_pilot.py)
def usage_from_raw(raw: str):
    """Last non-null usage object of a plain JSON body or an SSE stream."""
    usage = None

    def take(d):
        nonlocal usage
        if isinstance(d, dict) and isinstance(d.get("usage"), dict):
            usage = d["usage"]

    s = (raw or "").lstrip()
    if s.startswith("data:"):
        for line in raw.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                try:
                    take(json.loads(line[5:]))
                except ValueError:
                    pass
    else:
        try:
            take(json.loads(raw))
        except ValueError:
            pass
    return usage


def reasoning_tokens(o: dict) -> Optional[int]:
    u = usage_from_raw(o.get("raw_body") or "")
    rt = ((u or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    if rt is None:
        rt = (((o.get("usage") or {}).get("completion_tokens_details")) or {}).get("reasoning_tokens")
    return rt


def obs_tokens(o: dict) -> Optional[int]:
    u = o.get("usage") or {}
    if u.get("prompt_tokens") is not None or u.get("completion_tokens") is not None:
        return int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
    ce = o.get("cost_estimate") or {}
    if ce.get("input_tokens") is not None:
        return int(ce.get("input_tokens") or 0) + int(ce.get("output_tokens") or 0)
    return None


# ------------------------------------------------------------------ table container
class Result:
    def __init__(self, label: str, B: int, seed: int, fx: dict):
        self.label, self.B, self.seed, self.fx = label, B, seed, fx
        self.tables: List[dict] = []
        self.uncovered: List[str] = []
        self.figs: Dict[str, Any] = {}
        self.ctx: Dict[str, Any] = {}

    @property
    def formal(self) -> bool:
        return self.label == "formal"

    def add(self, tid: str, title: str, columns: list, rows: list, notes: Sequence[str] = (), data: Any = None):
        full = title if self.formal else f"{PILOT_PREFIX}：{title}"
        self.tables.append({"id": tid, "title": full, "columns": columns, "rows": [[str(c) for c in r] for r in rows],
                            "notes": list(notes), "data": data})

    def miss(self, text: str):
        if text not in self.uncovered:
            self.uncovered.append(text)


def cost_cny(row: dict, fx: float) -> float:
    return float(row.get("cost_cny") or 0.0) + fx * float(row.get("cost_usd") or 0.0)


# ------------------------------------------------------------------ tables
def table_execution(res: Result, bat: dict, ledger: list, outputs: dict):
    sel, planned = bat["selected"], bat["planned"]
    poc = bat["provider_of_config"]
    prov_of_key = {}
    for k, o in sel.items():
        prov_of_key[k] = o.get("provider")
    keys = set(planned) if planned else set(sel)
    def prov(k):
        if k in prov_of_key:
            return prov_of_key[k]
        return poc.get(planned.get(k))
    plan_n, valid_n, fail_n = collections.Counter(), collections.Counter(), collections.Counter()
    cfgs = collections.defaultdict(set)
    for k in keys:
        p = prov(k) or "未归类"
        plan_n[p] += 1
        o = sel.get(k)
        if o is not None and obs_usable(o):
            valid_n[p] += 1
        else:
            fail_n[p] += 1
        c = (sel.get(k) or {}).get("config_id") or planned.get(k)
        if c:
            cfgs[p].add(c)
    retry_n = collections.Counter()
    key_batch_provider = {}
    for bname, obs in bat["raw_by_batch"].items():
        for o in obs:
            key_batch_provider[(bname, o["run_key"])] = o.get("provider")
            if "retry" in bname:
                retry_n[o.get("provider") or "未归类"] += 1
    batch_names = set(bat["raw_by_batch"])
    spend: dict = collections.defaultdict(lambda: collections.defaultdict(float))
    excluded = collections.Counter()
    for e in ledger:
        if e.get("batch_id") not in batch_names:
            excluded[e.get("batch_id")] += 1
            continue
        p = key_batch_provider.get((e["batch_id"], e.get("run_key"))) or "未归类"
        spend[p][e["currency"]] += float(e["amount"])
    cur = {"USD": "美元", "CNY": "元"}

    def money(d):
        return "、".join(f"{v:.4f} {cur.get(c, c)}" for c, v in sorted(d.items())) if d else "0"
    provs = [p for p in PROVIDERS if p in plan_n or p in spend] + sorted(
        p for p in set(plan_n) | set(spend) if p not in PROVIDERS)
    rows = []
    tot_spend: dict = collections.defaultdict(float)
    for p in provs:
        for c, v in spend[p].items():
            tot_spend[c] += v
        rows.append([PROVIDER_NAMES.get(p, p), "、".join(sorted(cfgs[p])) or EMPTY, f"{plan_n[p]:,}", f"{valid_n[p]:,}",
                     f"{retry_n[p]:,}", f"{fail_n[p]:,}", money(spend[p])])
    rows.append(["合计", "", f"{sum(plan_n.values()):,}", f"{sum(valid_n.values()):,}", f"{sum(retry_n.values()):,}",
                 f"{sum(fail_n.values()):,}", money(tot_spend)])
    notes = ["计划调用：各批次计划文件中不重复的 run_key；有效观测：每个 run_key 取最早的有效观测；补跑：名称含 retry 的批次中的观测数；"
             "无有效观测：计划 run_key 无有效观测，即最终失败，按组装输出核对见表 5-1c。",
             "支出取支出账本中属于所给批次的记录，按观测的服务商归类；千问为免费额度按官方标价折算，硅基流动为代金券，DeepSeek 为现金。"]
    if excluded:
        notes.append("账本中不属于所给批次、未计入的记录：" + "、".join(f"{b} {n} 条" for b, n in sorted(excluded.items())) + "。")
    if not planned:
        notes.append("所给批次无 plan.json，计划调用按观测的 run_key 计。")
    res.add("5-1", "表 5-1 正式实验执行概况", ["服务商", "配置", "计划调用", "有效观测", "补跑", "无有效观测", "支出"], rows, notes,
            data={"plan": dict(plan_n), "valid": dict(valid_n), "retry": dict(retry_n), "fail": dict(fail_n),
                  "spend": {p: dict(v) for p, v in spend.items()}})
    res.miss("图片转写准确率（字符级编辑距离，分视觉模型）：评分输出不含该项")

    # finish_reason / reasoning tokens by config
    by_cfg = collections.defaultdict(list)
    for o in sel.values():
        by_cfg[o.get("config_id")].append(o)
    rows2 = []
    for c in sorted(by_cfg, key=lambda x: (x not in REPORT_ORDER, REPORT_ORDER.index(x) if x in REPORT_ORDER else 0, str(x))):
        obs = by_cfg[c]
        ln = sum(1 for o in obs if o.get("finish_reason") == "length")
        rts = [t for t in (reasoning_tokens(o) for o in obs) if t is not None]
        rows2.append([c, f"{len(obs):,}", f"{ln:,}", fpct(ln / len(obs)),
                      f"{statistics.median(rts):.0f}" if rts else NA, f"{max(rts):,}" if rts else NA])
    res.add("5-1b", "表 5-1 附 各配置截断比例与推理 token", ["配置", "观测数", "finish_reason 为 length", "占比", "推理 token 中位数",
                                                          "推理 token 最大值"], rows2,
            ["推理 token 取自原始响应 usage.completion_tokens_details.reasoning_tokens；响应不带该字段的配置写“评分输出无此项”。"])

    # failed calls from outputs
    st = collections.defaultdict(lambda: {"n": 0, "with": 0, "keys": set()})
    for (m, c, _t), o in outputs.items():
        s = st[(c, m)]
        s["n"] += 1
        fc = o.get("failed_calls") or []
        s["with"] += bool(fc)
        s["keys"].update(fc)
    rows3 = [[c, m, s["n"], s["with"], len(s["keys"])] for (c, m), s in sorted(
        st.items(), key=lambda kv: (kv[0][0] not in REPORT_ORDER, REPORT_ORDER.index(kv[0][0]) if kv[0][0] in REPORT_ORDER else 0,
                                    kv[0][0], kv[0][1]))]
    allkeys = set().union(*[s["keys"] for s in st.values()]) if st else set()
    res.add("5-1c", "表 5-1 附 各配置与方法的最终失败调用（按组装输出）", ["配置", "方法", "输出数", "含失败调用的输出数", "失败调用数（去重）"],
            rows3, [f"全部输出中不重复的失败调用共 {len(allkeys)} 个。"], data={"unique_failed": sorted(allkeys)})


def _rows_of(rows, **kw):
    out = []
    for r in rows:
        if all((r.get(k) in v if isinstance(v, (set, tuple, list)) else r.get(k) == v) for k, v in kw.items()):
            out.append(r)
    return out


def table_accuracy(res: Result, rows: list, tasks: dict):
    ne = [r for r in rows if r["layer"] not in metrics.NONEXPLICIT_EXCLUDE]
    cfgs = ordered_configs({r["config_id"] for r in rows})
    present = {m for m in METHODS if any(r["method"] == m for r in rows)}
    for m in METHODS:
        if m not in present:
            res.miss(f"方法 {m} 在评分输出中没有行，表 5-2、5-3、5-4 对应列写“{EMPTY}”")
    for c in REPORT_ORDER:
        if c not in cfgs:
            res.miss(f"配置 {c} 在评分输出中没有行，表中不列出")
    ntasks = len({r["task_id"] for r in ne})
    table = []
    body = []
    for c in cfgs:
        line = [c]
        for m in METHODS:
            rs = _rows_of(ne, config_id=c, method=m)
            line.append(fpct(f_acc(rs)) if rs else EMPTY)
            table.append({"config": c, "method": m, "n": len(rs), "acc": f_acc(rs) if rs else None})
        body.append(line)
    merged = ["合并（95% CI）"]
    mdata = {}
    for m in METHODS:
        rs = _rows_of(ne, method=m)
        est, lo, hi = boot(rs, f_acc, res.B, res.seed)
        mdata[m] = {"est": est, "lo": lo, "hi": hi, "n": len(rs)}
        merged.append(fci(est, lo, hi) if rs else EMPTY)
    body.append(merged)
    res.ctx["acc_merged"] = mdata
    res.add("5-2", f"表 5-2 决策正确率（非显式三层 {ntasks} 个测试任务）", ["配置"] + list(METHODS), body,
            [f"{EMPTY}：该配置或方法在评分输出中没有对应行。合并行为各配置合并，方括号内为按任务整簇 bootstrap 的 95% CI（B = {res.B}，seed = {res.seed}）。"
             "单配置格不给 CI。"], data={"cells": table, "merged": mdata, "n_tasks": ntasks})

    # figure data: layer x method
    figd = {}
    body = []
    for lay in LAYERS:
        line = [LAYER_NAMES[lay]]
        for m in METHODS:
            rs = _rows_of(rows, layer=lay, method=m)
            est, lo, hi = boot(rs, f_acc, res.B, res.seed)
            figd[(lay, m)] = (est, lo, hi, len(rs))
            line.append(fci(est, lo, hi) if rs else EMPTY)
        body.append(line)
    res.figs["e1"] = figd
    res.ctx["layer_method"] = figd
    res.add("5-2f", "图 5-1 数据 各层级、各方法的决策正确率（各配置合并）", ["层级"] + list(METHODS), body,
            [f"方括号内为按任务整簇 bootstrap 的 95% CI。{EMPTY}：该层级无任务或方法无评分行。"],
            data={f"{k[0]}|{k[1]}": {"est": v[0], "lo": v[1], "hi": v[2], "n": v[3]} for k, v in figd.items()})

    # modality
    groups = {r["task_id"]: tasks[r["task_id"]]["group"] for r in rows}
    gm = collections.defaultdict(dict)
    body = []
    gdata = {}
    for g in MODALITY_GROUPS:
        gtasks = sorted(t for t, gg in groups.items() if gg == g and any(
            r["task_id"] == t for r in ne))
        line = [g, len(gtasks)]
        for m in ("M0", "M1", "M4"):
            rs = [r for r in ne if r["method"] == m and groups.get(r["task_id"]) == g]
            est, lo, hi = boot(rs, f_acc, res.B, res.seed)
            gdata[f"{g}|{m}"] = {"est": est, "lo": lo, "hi": hi, "n": len(rs)}
            line.append(fci(est, lo, hi) if rs else EMPTY)
        body.append(line)
    res.add("5-3", f"表 5-3 决策正确率按报价模态分列（任务内含图片、表格报价与只含文字报价的任务分组，{len(cfgs)} 个配置合并）",
            ["任务分组", "任务数", "M0", "M1", "M4"], body,
            ["分组按任务内候选报价的呈现方式判定：任一报价为图片则归入含图片；否则任一为表格则归入含表格不含图片；其余为只含文字。"
             "统计范围为非显式三层。"], data=gdata)
    res.ctx["modality"] = gdata


def table_regret_cost(res: Result, rows: list):
    ne = [r for r in rows if r["layer"] not in metrics.NONEXPLICIT_EXCLUDE]
    body, data = [], {}
    fr = f_regret_abs
    for m in METHODS:
        rs = _rows_of(ne, method=m)
        if not rs:
            continue
        est, lo, hi = boot(rs, fr, res.B, res.seed)
        rel = _mean_of("regret_rel")(rs)
        oest, olo, ohi = boot(rs, _mean_of("out_of_feasible"), res.B, res.seed)
        nsel = sum(1 for r in rs if r.get("regret_abs") is not None or r.get("out_of_feasible") == 1)
        data[m] = {"n_choice": nsel, "regret_abs": est, "lo": lo, "hi": hi, "regret_rel": None if rel != rel else rel,
                   "oof": oest, "oof_lo": olo, "oof_hi": ohi}
        body.append([m, nsel, fci(est, lo, hi, "num"), fpct(None if rel != rel else rel),
                     fnum(None if est is None else 100 * est, 1), fci(oest, olo, ohi)])
    res.add("5-2r", "决策遗憾与可行集外比例（按方法合并，非显式三层）",
            ["方法", "给出推荐的评分行数（配置 × 任务）", "每票多付金额（元）", "占最优成本比例", "每百票多付金额（元）", "选中可行集外报价的比例"], body,
            ["每票多付金额与占比只统计推荐落在可行集内的任务；方括号内为 95% CI（按任务整簇 bootstrap）；给出推荐的评分行指推荐了候选集内某个报价的行，不含候选集外的无效选择，可行集外比例的分母为这些行。"],
            data=data)

    # costing error
    body, data = [], {}
    for m in ("M2", "M3", "M4"):
        rs = _rows_of(rows, method=m)
        if not rs:
            continue
        est, lo, hi = boot(rs, f_cost_abs, res.B, res.seed)
        cn, cm = _sumk(rs, "cost_n"), _sumk(rs, "cost_missing")
        rel = _div(_sumk(rs, "cost_rel_sum"), cn - cm)
        ex = _div(_sumk(rs, "cost_exact"), cn)
        data[m] = {"abs": est, "lo": lo, "hi": hi, "rel": None if rel != rel else rel, "cost_n": cn, "cost_missing": cm}
        body.append([m, int(cn), int(cm), fci(est, lo, hi, "num"), fpct(None if rel != rel else rel), fpct(None if ex != ex else ex)])
    # paired M4 - M2 over (config, task)
    paired = None
    m2 = {(r["config_id"], r["task_id"]): r for r in rows if r["method"] == "M2"}
    m4 = {(r["config_id"], r["task_id"]): r for r in rows if r["method"] == "M4"}
    recs = collections.defaultdict(lambda: {"task_id": None, "a_sum": 0.0, "a_n": 0.0, "b_sum": 0.0, "b_n": 0.0})
    for k in sorted(set(m2) & set(m4)):
        t = recs[k[1]]
        t["task_id"] = k[1]
        a, b = m4[k], m2[k]
        t["a_sum"] += a["cost_abs_sum"] or 0.0
        t["a_n"] += (a["cost_n"] or 0) - (a["cost_missing"] or 0)
        t["b_sum"] += b["cost_abs_sum"] or 0.0
        t["b_n"] += (b["cost_n"] or 0) - (b["cost_missing"] or 0)
    if recs:
        def fdiff(rs):
            a = _div(sum(r["a_sum"] for r in rs), sum(r["a_n"] for r in rs))
            b = _div(sum(r["b_sum"] for r in rs), sum(r["b_n"] for r in rs))
            return a - b
        est, lo, hi = boot(list(recs.values()), fdiff, res.B, res.seed)
        paired = {"est": est, "lo": lo, "hi": hi, "n_tasks": len(recs)}
        body.append(["M4 减 M2（配对）", "", "", fci(est, lo, hi, "num"), "", ""])
    else:
        res.miss("M4 对 M2 核价误差的配对差值：评分输出缺少 M2 或 M4 的配对行")
    data["M4_minus_M2"] = paired
    res.ctx["cost_pair"] = paired
    res.add("5-2c", "核价误差（已定候选，全部层级）", ["方法", "已定候选数", "无核价结果数", "绝对误差均值（元）", "相对误差均值", "核价完全正确率"], body,
            ["无核价结果的候选不进入误差均值，单独计数。M4 减 M2 为同一（配置，任务）配对后按任务整簇 bootstrap 的差值及 95% CI。"], data=data)
    res.miss("反事实任务的字段准确率、出处准确率、出处降级率与归一化词表覆盖率：评分输出不含这些指标")


def table_risk(res: Result, rows: list):
    cfgs = ordered_configs({r["config_id"] for r in rows})
    metr = [
        ("风险项检出 micro-F1", "f1"), ("隐性费用召回率", "hf_rec"), ("冲突与缺口检出 F1", "cg_f1"),
        ("包税风险精确率", "ddp_p"), ("包税风险召回率", "ddp_r"), ("资质证据状态准确率", "claim"), ("无依据宣称的采纳率", "adopt"),
    ]

    def val(rs, key):
        if key == "f1":
            return f_micro_f1(rs)
        if key == "hf_rec":
            return _div(_sumk(rs, "tp_hidden_fee"), _sumk(rs, "tp_hidden_fee") + _sumk(rs, "fn_hidden_fee"))
        if key == "cg_f1":
            tp, fp, fn = (_sumk(rs, "tp_conflict", "tp_gap"), _sumk(rs, "fp_conflict", "fp_gap"), _sumk(rs, "fn_conflict", "fn_gap"))
            v = metrics.f1_from_counts(tp, fp, fn)
            return float("nan") if v is None else v
        if key == "ddp_p":
            return _div(_sumk(rs, "tp_ddp_risk"), _sumk(rs, "tp_ddp_risk") + _sumk(rs, "fp_ddp_risk"))
        if key == "ddp_r":
            return _div(_sumk(rs, "tp_ddp_risk"), _sumk(rs, "tp_ddp_risk") + _sumk(rs, "fn_ddp_risk"))
        if key == "claim":
            return _div(_sumk(rs, "claim_correct"), _sumk(rs, "claim_n"))
        raise KeyError(key)

    body, data = [], {}
    ntasks = len({r["task_id"] for r in rows})
    for name, key in metr:
        line = [name]
        for m in METHODS:
            rs = _rows_of(rows, method=m)
            if key == "adopt":
                line.append(NA)
                continue
            if not rs:
                line.append(EMPTY)
                continue
            if key == "f1":
                est, lo, hi = boot(rs, f_micro_f1, res.B, res.seed)
                line.append(fci(est, lo, hi, "num", 3))
                data[f"{key}|{m}"] = {"est": est, "lo": lo, "hi": hi}
            else:
                v = val(rs, key)
                line.append(fnum(v))
                data[f"{key}|{m}"] = None if v != v else v
        body.append(line)
    res.miss("无依据宣称的采纳率：评分输出不含该项（评分行只有资质状态准确率）")
    res.add("5-4", f"表 5-4 风险检出（全部 {ntasks} 个测试任务，{len(cfgs)} 个配置合并）", ["指标"] + list(METHODS), body,
            ["micro-F1 的方括号为按任务整簇 bootstrap 的 95% CI。隐性费用只计召回；包税风险“高”与“需核实”都算阳性。"], data=data)

    # figure data: precision / recall / F1 by method with CI
    figd = {}
    for m in METHODS:
        rs = _rows_of(rows, method=m)
        figd[m] = {"precision": boot(rs, f_precision, res.B, res.seed), "recall": boot(rs, f_recall, res.B, res.seed),
                   "f1": boot(rs, f_micro_f1, res.B, res.seed), "n": len(rs)}
    res.figs["e2"] = figd
    res.ctx["risk_prec_cg"] = {}
    for m in METHODS:
        v = f_precision(_rows_of(rows, method=m))
        res.ctx["risk_prec_cg"][m] = None if v != v else v
    body = []
    for m in METHODS:
        d = figd[m]
        if not d["n"]:
            continue
        body.append([m, fci(*d["precision"], "num", 3), fci(*d["recall"], "num", 3), fci(*d["f1"], "num", 3)])
    res.add("5-4f", "图 5-2 数据 各方法风险项检出的精确率、召回率与 micro-F1", ["方法", "精确率", "召回率", "micro-F1"], body,
            ["精确率按冲突、缺口、包税风险三类计（隐性费用只计召回，无误报），与表 5-10 规则 6 同口径；召回率与 micro-F1 为全部风险项的原口径；方括号内为 95% CI。"], data={m: figd[m] for m in METHODS})


def table_ask(res: Result, rows: list, tasks: dict, gold: dict):
    sel = [r for r in rows if r["layer"] in ("conflict", "insufficient")]
    ntasks = len({r["task_id"] for r in sel})
    ms = ("M0", "M1", "M4")
    for m in ms:
        if not any(r["method"] == m for r in sel):
            res.miss(f"表 5-5 的 {m} 列：评分输出没有冲突层与信息不足层的 {m} 行")
    items = (("正确拒答率（决定性任务）", f_ask_rate), ("误拒率（非决定性任务）", f_false_ask),
             ("追问清单覆盖率", f_ask_cover))
    body, data = [], {}
    for name, fn in items:
        line = [name]
        for m in ms:
            rs = _rows_of(sel, method=m)
            if not rs:
                line.append(EMPTY)
                continue
            est, lo, hi = boot(rs, fn, res.B, res.seed)
            data[f"{name}|{m}"] = {"est": est, "lo": lo, "hi": hi}
            line.append(fci(est, lo, hi))
        body.append(line)
    res.add("5-5", f"表 5-5 拒答与追问（冲突层与信息不足层，共 {ntasks} 个测试任务）", ["指标"] + list(ms), body,
            ["决定性任务指正确答案为追问的任务，非决定性任务指正确答案为推荐的任务；追问清单覆盖率按决定性任务计；方括号内为 95% CI。"], data=data)

    body = []
    for lay in ("conflict", "insufficient"):
        for name, fn in items:
            line = [LAYER_NAMES[lay], name]
            for m in ms:
                rs = _rows_of(sel, method=m, layer=lay)
                line.append(fpct(fn(rs)) if rs else EMPTY)
            body.append(line)
    res.add("5-5b", "表 5-5 附 按层级分列", ["层级", "指标"] + list(ms), body, [])

    def grouped(title, tid, keyfn, rows_in, label):
        keys = sorted({keyfn(r) for r in rows_in if keyfn(r) is not None}, key=str)
        body = []
        for k in keys:
            for name, fn in items[:2]:
                rs_all = [r for r in rows_in if keyfn(r) == k]
                if not rs_all:
                    continue
                nt = len({r["task_id"] for r in rs_all if r["gold_type"] == ("ask" if "拒答" in name else "choose")})
                line = [k, name, nt]
                for m in ms:
                    rs = _rows_of(rs_all, method=m)
                    line.append(fpct(fn(rs)) if rs else EMPTY)
                body.append(line)
        res.add(tid, title, [label, "指标", "任务数"] + list(ms), body, [])

    def gap_count(r):
        g = gold.get(r["task_id"])
        if g is None or r["layer"] != "insufficient":
            return None
        n = sum(len(pc.get("missing_ids") or []) for pc in g["per_cand"].values())
        return f"{n} 个缺口"
    grouped("表 5-5 附 按缺口数量分列（信息不足层）", "5-5c", gap_count, sel, "缺口数量")
    grouped("表 5-5 附 按运输方式分列", "5-5d", lambda r: tasks[r["task_id"]]["mode"], sel, "运输方式")
    res.miss("表 5-5 按缺口字段分组的结果：评分行不含缺口字段分组，未生成")


def _per_output_usage(o: dict, sel: dict) -> tuple:
    tok = lat = 0
    miss = 0
    for k in o.get("calls") or []:
        ob = sel.get(k)
        if ob is None:
            miss += 1
            continue
        t = obs_tokens(ob)
        tok += t or 0
        lat += float(ob.get("latency_ms") or 0.0)
    return tok, lat / 1000.0, miss


def _cost_lines(rows: list, outputs: dict, sel: dict, fx: float, keyset) -> list:
    """Per (config, method) cost line; keyset limits the (config, method) pairs."""
    groups = collections.defaultdict(list)
    for r in rows:
        k = (r["config_id"], r["method"])
        if k in keyset:
            groups[k].append(r)
    out = []
    for (c, m), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] not in REPORT_ORDER and kv[0][0] != THINK_CONFIG,
                                                           REPORT_ORDER.index(kv[0][0]) if kv[0][0] in REPORT_ORDER else 9,
                                                           kv[0][0], kv[0][1])):
        toks, lats, miss = [], [], 0
        for r in rs:
            o = outputs.get((r["method"], r["config_id"], r["task_id"]))
            if o is None:
                miss += 1
                continue
            t, l, ms_ = _per_output_usage(o, sel)
            toks.append(t)
            lats.append(l)
            miss += ms_
        cost = [cost_cny(r, fx) for r in rs]
        correct = sum(r["decision_correct"] for r in rs)
        out.append({"config": c, "method": m, "n": len(rs), "calls": float(np.mean([r["n_calls"] or 0 for r in rs])),
                    "tokens": float(np.mean(toks)) if toks else None, "cost": float(np.mean(cost)),
                    "latency": float(statistics.median(lats)) if lats else None,
                    "cost_per_correct": (sum(cost) / correct) if correct else None, "correct": correct,
                    "missing_calls": miss, "acc": float(np.mean([r["decision_correct"] for r in rs]))})
    return out


def table_cost(res: Result, rows: list, outputs: dict, bat: dict, think_rows: list, think_outputs: dict):
    fx = res.fx["cny_per_usd"]
    sel = bat["selected"]
    keyset = {(r["config_id"], r["method"]) for r in rows if r["method"] in METHODS}
    lines = _cost_lines(rows, outputs, sel, fx, keyset)
    body = []
    for d in lines:
        body.append([d["config"], d["method"], fnum(d["calls"], 1), EMPTY if d["tokens"] is None else f"{d['tokens']:,.0f}",
                     fnum(d["cost"], 4), EMPTY if d["latency"] is None else fnum(d["latency"], 1),
                     EMPTY if d["cost_per_correct"] is None else fnum(d["cost_per_correct"], 4)])
    miss_calls = sum(d["missing_calls"] for d in lines)
    notes = [f"成本取组装输出的 cost 字段，含按配置全额记账的转写与 M4 复核方抽取；美元部分按 {fx} 元/美元折算（{res.fx['source']}）。",
             "每任务 token 为输出所依赖调用的输入与输出 token 之和；时延为输出所依赖各调用时延之和（执行器在同一服务商内顺序调用）取各任务中位数。",
             "每个正确决策的成本为该配置、方法下全部任务的总成本除以决策正确的任务数。统计范围为全部层级。"]
    if miss_calls:
        notes.append(f"{miss_calls} 个依赖调用在所给批次中找不到观测，其 token 与时延未计入。")
    res.add("5-6", "表 5-6 每次审计的调用成本与时延", ["配置", "方法", "每任务调用数", "每任务 token", "每任务成本（元）", "每任务串行时延之和的中位数（秒）",
                                                   "每个正确决策的成本（元）"], body, notes, data={"lines": lines, "fx": res.fx})
    res.ctx["cost_lines"] = lines

    # think group
    ne_think = [r for r in think_rows if r["layer"] not in metrics.NONEXPLICIT_EXCLUDE and r["method"] == "M1"]
    tl = []
    if think_rows:
        tl = _cost_lines(think_rows, think_outputs, sel, fx, {(THINK_CONFIG, "M1")})
    base = [d for d in lines if d["config"] == "ds_flash" and d["method"] in ("M1", "M4")]
    body = []
    accs = {}
    ne_all = [r for r in rows if r["layer"] not in metrics.NONEXPLICIT_EXCLUDE]
    for d in tl + base:
        src = ne_think if d["config"] == THINK_CONFIG else _rows_of(ne_all, config_id=d["config"], method=d["method"])
        a = f_acc(src) if src else None
        accs[(d["config"], d["method"])] = a
        body.append([d["config"], d["method"], fpct(a), fnum(d["cost"], 4), EMPTY if d["latency"] is None else fnum(d["latency"], 1),
                     EMPTY if d["cost_per_correct"] is None else fnum(d["cost_per_correct"], 4),
                     EMPTY if d["tokens"] is None else f"{d['tokens']:,.0f}"])
    res.ctx["think_acc"] = accs.get((THINK_CONFIG, "M1"))
    res.ctx["dsflash_m4_acc"] = accs.get(("ds_flash", "M4"))
    if not think_rows:
        res.miss("思考组 ds_flash_think 的 M1 对比（表 5-6 附）：未运行，未提供 --think-outputs 或 --think-scores")
    res.add("5-6b", "表 5-6 附 思考模式组与 ds_flash 的对比", ["配置", "方法", "决策正确率（非显式）", "每任务成本（元）", "每任务串行时延之和的中位数（秒）",
                                                              "每个正确决策的成本（元）", "每任务 token"], body,
            ["决策正确率为点估计，范围为非显式三层。"], data={"lines": tl + base})


def table_review(res: Result, rows: list, outputs: dict, gold: dict):
    fx = res.fx["cny_per_usd"]

    def gold_risk(pc) -> bool:
        return bool(pc["conflict_ids"] or pc["missing_ids"] or pc["hidden_fee_ids"]
                    or (pc["ddp_level"] not in (None, "none")) or pc["claim_statuses"])

    stats = {}
    for m in ("M4", "M4_TRIG"):
        rs = [r for r in rows if r["method"] == m]
        if not rs:
            stats[m] = None
            continue
        reviewed_n = cand_n = risk_n = unreviewed = 0
        for (om, c, t), o in outputs.items():
            if om.upper() != m:
                continue
            g = gold[t]
            rev = set(((o.get("diagnostics") or {}).get("review") or {}).get("reviewed") or [])
            cand_n += len(g["per_cand"])
            reviewed_n += len(rev & set(g["per_cand"]))
            for cid, pc in g["per_cand"].items():
                if gold_risk(pc):
                    risk_n += 1
                    unreviewed += int(cid not in rev)
        stats[m] = {
            "outputs": len(rs), "decisions_correct": int(sum(r["decision_correct"] or 0 for r in rs)),
            "conflict_detected": int(sum(r["tp_conflict"] for r in rs)), "conflict_missed": int(sum(r["fn_conflict"] for r in rs)),
            "gap_detected": int(sum(r["tp_gap"] for r in rs)), "gap_missed": int(sum(r["fn_gap"] for r in rs)),
            "false_positives_conflict_gap_ddp": int(sum(r["fp_conflict"] + r["fp_gap"] + r["fp_ddp_risk"] for r in rs)),
            "gold_risk_candidates": risk_n, "gold_risk_not_reviewed": unreviewed,
            "candidates": cand_n, "reviewed": reviewed_n,
            "cost_per_task": float(np.mean([cost_cny(r, fx) for r in rs])),
        }
    res.ctx["review"] = stats

    def cell(m, f):
        s = stats[m.replace("M4_trig", "M4_TRIG")]
        return EMPTY if s is None else f(s)
    cols = ["M4", "M4_trig"]
    specs = [
        ("评分任务数（配置 × 任务）", lambda s: s["outputs"]),
        ("复核比例（已复核候选 / 全部候选）", lambda s: fpct(_div(s["reviewed"], s["candidates"]))),
        ("检出的冲突数", lambda s: s["conflict_detected"]),
        ("漏检的冲突数", lambda s: s["conflict_missed"]),
        ("复核发现的缺口数", lambda s: s["gap_detected"]),
        ("漏检的缺口数", lambda s: s["gap_missed"]),
        ("冲突、缺口、包税风险误报项数", lambda s: s["false_positives_conflict_gap_ddp"]),
        ("有风险而未复核的报价数", lambda s: f"{s['gold_risk_not_reviewed']} / {s['gold_risk_candidates']}"),
        ("决策正确率", lambda s: f"{fpct(_div(s['decisions_correct'], s['outputs']))}（{s['decisions_correct']}/{s['outputs']}）"),
        ("每任务成本（元）", lambda s: fnum(s["cost_per_task"], 4)),
    ]
    body = [[name] + [cell(m, f) for m in cols] for name, f in specs]
    if stats["M4_TRIG"] is None or stats["M4"] is None:
        res.miss("表 5-7 需要 M4 与 M4_trig 两种方法的评分行，缺少的列写“无数据”")
    res.add("5-7", "表 5-7 全部复核与只复核触发报价的对比", ["指标", "M4（全部复核）", "M4_trig（只复核触发报价）"], body,
            ["有风险而未复核的报价数一栏的分母为金标签中含风险项的候选报价数；统计范围为全部层级、全部配置。"], data=stats)


def table_endpoints(res: Result, ep: Optional[dict]):
    cols = ["终点", "估计", "原始 p", "Holm 校正 p", "是否拒绝原假设", "说明"]
    if not ep:
        res.miss("主终点：评分输出未含主终点（score 未加 --endpoints 或无 endpoints.json）")
        res.add("5-9", "表 5-9 主终点", cols, [["E1、E2、E3", "评分输出未含主终点", "", "", "", ""]], [])
        return
    if "error" in ep:
        res.miss(f"主终点：评分阶段报错（{ep['error']}）")
        res.add("5-9", "表 5-9 主终点", cols, [["E1、E2、E3", f"评分输出未含主终点（{ep['error']}）", "", "", "", ""]], [])
        return

    def p(x):
        return EMPTY if x is None else f"{x:.4g}"
    e1, e2, e3 = ep["E1"], ep["E2"], ep["E3"]
    rej = ep.get("reject", {})
    body = [
        ["E1 决策正确率 M4 对 M0（非显式三层）", f"M4 {fpct(e1.get('acc_M4'))}，M0 {fpct(e1.get('acc_M0'))}，差值 {fpct(e1.get('mean_difference'))}",
         p(ep["p_raw"].get("E1")), p(ep["p_holm"].get("E1")), ("无法计算" if rej.get("E1") is None else rej.get("E1")), f"置换检验（{e1.get('method')}），双侧"],
        ["E2 风险项 micro-F1 M4 对 M0", f"M4 {fnum(e2.get('f1_M4'))}，M0 {fnum(e2.get('f1_M0'))}，差值 {fnum(e2.get('difference'))}",
         p(ep["p_raw"].get("E2")), p(ep["p_holm"].get("E2")), ("无法计算" if rej.get("E2") is None else rej.get("E2")), f"置换检验（{e2.get('method')}），双侧"],
        ["E3 低成本档 M4 对同厂商强模型档 M0 的非劣效", f"差值均值 {fpct(e3.get('estimate'))}，单侧 95% 下界 {fpct(e3.get('lower'))}（界值 {fpct(-e3.get('margin', 0.05))}）",
         p(ep["p_raw"].get("E3")), p(ep["p_holm"].get("E3")), ("无法计算" if rej.get("E3") is None else rej.get("E3")), "按任务整簇 bootstrap，单侧"],
    ]
    notes = []
    for k, t in (e3.get("tango") or {}).items():
        notes.append(f"Tango 敏感性分析 {k}：差值 {fpct(t.get('theta_hat'))}，单侧 p = {p(t.get('p'))}。")
    res.add("5-9", "表 5-9 主终点", cols, [[str(c) for c in r] for r in body], notes, data=ep)
    res.ctx["endpoints"] = ep


def table_rules(res: Result, ep: Optional[dict], real_acc: Optional[dict]):
    rows = []
    ok_ep = bool(ep) and "error" not in (ep or {})
    rej = (ep or {}).get("reject", {}) if ok_ep else {}
    ph = (ep or {}).get("p_holm", {}) if ok_ep else {}

    def add(n, name, result, basis, trig):
        act = RULE_ACTIONS[n] if trig else ("不触发" if trig is False else "无输入，未判定")
        rows.append([name, result, basis, act])

    for n, key, name in ((1, "E1", "主终点 1 是否显著"), (2, "E2", "主终点 2 是否显著")):
        if not ok_ep:
            add(n, name, "评分输出未含主终点", "", None)
        elif rej.get(key) is None or ph.get(key) is None:
            add(n, name, "无法计算，未判定", "评分输出中该终点的 p 值或判定为空", None)
        else:
            sig = bool(rej.get(key))
            add(n, name, "显著" if sig else "不显著", f"Holm 校正 p = {ph.get(key):.4g}" if ph.get(key) is not None else "", not sig)
    if not ok_ep:
        add(3, "主终点 3 是否非劣", "评分输出未含主终点", "", None)
    elif rej.get("E3") is None or ph.get("E3") is None:
        add(3, "主终点 3 是否非劣", "无法计算，未判定", "评分输出中该终点的 p 值或判定为空", None)
    else:
        e3 = ep["E3"]
        ni = bool(rej.get("E3"))
        add(3, "主终点 3 是否非劣", "非劣" if ni else "未达到非劣",
            f"差值均值 {fpct(e3.get('estimate'))}，单侧下界 {fpct(e3.get('lower'))}，界值 {fpct(-e3.get('margin', 0.05))}；Holm 校正 p = {ph.get('E3'):.4g}"
            if ph.get("E3") is not None else "", not ni)
    lm = res.ctx.get("layer_method", {})
    v = lm.get(("explicit", "M0"))
    if v is None or v[0] is None:
        add(4, "M0 显式层正确率 ≥ 90%", "无数据", "评分输出没有显式层的 M0 行", None)
    else:
        add(4, "M0 显式层正确率 ≥ 90%", "是" if v[0] >= 0.9 else "否", f"M0 显式层正确率 {fpct(v[0])}（{v[3]} 行）", v[0] >= 0.9)
    cp = res.ctx.get("cost_pair")
    if not cp or cp["lo"] is None:
        add(5, "M4 对 M2 核价误差差值 CI 含 0", "无数据", "评分输出缺少 M2 或 M4 的配对行", None)
    else:
        inc = cp["lo"] <= 0 <= cp["hi"]
        add(5, "M4 对 M2 核价误差差值 CI 含 0", "包含 0" if inc else "不含 0",
            f"差值 {cp['est']:.2f} 元，95% CI [{cp['lo']:.2f}, {cp['hi']:.2f}]", inc)
    pr = (res.ctx.get("risk_prec_cg") or {}).get("M4")
    if pr is None:
        add(6, "M4 风险标记精确率 < 0.7", "无数据", "M4 无冲突、缺口、包税风险预测", None)
    else:
        add(6, "M4 风险标记精确率 < 0.7", "是" if pr < 0.7 else "否", f"M4 冲突、缺口、包税风险三类合并精确率 {pr:.3f}", pr < 0.7)
    ta, ma = res.ctx.get("think_acc"), res.ctx.get("dsflash_m4_acc")
    if ta is None or ma is None:
        add(7, "思考组 M1 ≥ M4", "未运行", "未提供思考组评分输入或缺少 ds_flash 的 M4 行", None)
    else:
        add(7, "思考组 M1 ≥ M4", "是" if ta >= ma else "否", f"ds_flash_think M1 {fpct(ta)}，ds_flash M4 {fpct(ma)}（非显式点估计）", ta >= ma)
    if not real_acc:
        add(8, "真实集字段准确率 < 0.8", "未运行", "未提供真实报价集结果", None)
    else:
        vals = {k: float(v) for k, v in real_acc.items()}
        lowest = min(vals.values())
        add(8, "真实集字段准确率 < 0.8", "是" if lowest < 0.8 else "否",
            "各配置字段准确率 " + "、".join(f"{k} {v:.3f}" for k, v in sorted(vals.items())), lowest < 0.8)
    res.add("5-10", "表 5-10 事前决策规则的执行情况", ["规则", "判定结果", "依据", "处理"], rows,
            ["阈值与处理口径按 docs/plan/AIC_最终方案.md 第七节；处理列在规则触发时写方案规定的处理，不触发写“不触发”。规则 8 以任一配置的字段准确率低于 0.8 即触发。"],
            data=rows)


# ------------------------------------------------------------------ figures
GRAYS = {"M0": "#FFFFFF", "M1": "#D9D9D9", "M2": "#B0B0B0", "M3": "#808080", "M4": "#2B2B2B"}
HATCH = {"M0": "", "M1": "", "M2": "//", "M3": "..", "M4": ""}


def _setup_mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    warnings.filterwarnings("ignore", message="Glyph .* missing from font")
    plt.rcParams.update({"font.family": "Noto Serif CJK SC", "font.size": 9, "pdf.fonttype": 42, "svg.fonttype": "path",
                         "axes.unicode_minus": False, "svg.hashsalt": "quoteguard"})
    return plt


def _save(fig, out: Path, stem: str):
    fig.savefig(out / f"{stem}.png", dpi=300, bbox_inches="tight", metadata={"Software": None})
    fig.savefig(out / f"{stem}.pdf", bbox_inches="tight", metadata={"CreationDate": None, "Creator": None, "Producer": None})
    fig.savefig(out / f"{stem}.svg", bbox_inches="tight", metadata={"Date": None})


def draw_e1(res: Result, out: Path):
    plt = _setup_mpl()
    d = res.figs.get("e1", {})
    methods = [m for m in METHODS if any(d.get((l, m), (None,))[0] is not None for l in LAYERS)] or list(METHODS)
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    w = 0.8 / len(methods)
    for i, lay in enumerate(LAYERS):
        if all(d.get((lay, m), (None,))[0] is None for m in methods):
            ax.text(i, 0.04, "无数据", ha="center", va="bottom", fontsize=7, color="#555555")
    for j, m in enumerate(methods):
        for i, lay in enumerate(LAYERS):
            est, lo, hi, n = d.get((lay, m), (None, None, None, 0))
            x = i + (j - (len(methods) - 1) / 2) * w
            if est is None:
                if any(d.get((lay, k), (None,))[0] is not None for k in methods):
                    ax.text(x, 0.02, "无数据", ha="center", va="bottom", fontsize=5, color="#555555")
                continue
            ax.bar(x, est, w * 0.92, color=GRAYS[m], edgecolor="black", linewidth=0.7, hatch=HATCH[m],
                   label=m if m not in ax.get_legend_handles_labels()[1] else None)
            if est == 0:
                ax.plot([x - w * 0.46, x + w * 0.46], [0, 0], color="black", linewidth=1.2, zorder=4)
            if lo is not None and hi is not None:
                ax.errorbar(x, est, yerr=[[est - lo], [hi - est]], color="black", capsize=2, linewidth=0.8, zorder=5)
    ax.set_xticks(range(len(LAYERS)))
    ax.set_xticklabels([LAYER_NAMES[l] for l in LAYERS])
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("决策正确率")
    ax.yaxis.set_major_formatter(lambda v, _p: f"{v:.0%}")
    ax.spines[["top", "right"]].set_visible(False)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), ncol=len(uniq), frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.12))
    if not res.formal:
        ax.set_title(PILOT_PREFIX, fontsize=9, pad=26)
    _save(fig, out, "fig5_e1_accuracy_by_layer")
    plt.close(fig)


def draw_e2(res: Result, out: Path):
    plt = _setup_mpl()
    d = res.figs.get("e2", {})
    methods = [m for m in METHODS if d.get(m, {}).get("n")] or list(METHODS)
    series = (("precision", "精确率", "#FFFFFF", ""), ("recall", "召回率", "#A0A0A0", "//"), ("f1", "micro-F1", "#737373", ""))
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    w = 0.8 / len(series)
    for j, (key, name, color, hatch) in enumerate(series):
        for i, m in enumerate(methods):
            est, lo, hi = d.get(m, {}).get(key, (None, None, None))
            x = i + (j - 1) * w
            if est is None:
                ax.text(x, 0.02, "无数据", rotation=90, ha="center", va="bottom", fontsize=6, color="#555555")
                continue
            ax.bar(x, est, w * 0.92, color=color, edgecolor="black", linewidth=0.7, hatch=hatch, label=name if i == 0 else None)
            if lo is not None and hi is not None:
                ax.errorbar(x, est, yerr=[[est - lo], [hi - est]], color="black", capsize=2, linewidth=0.8)
    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels(methods)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("风险项检出")
    ax.spines[["top", "right"]].set_visible(False)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys(), ncol=len(uniq), frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.12))
    if not res.formal:
        ax.set_title(PILOT_PREFIX, fontsize=9, pad=26)
    _save(fig, out, "fig6_e2_risk")
    plt.close(fig)


# ------------------------------------------------------------------ rendering
def md_table(t: dict) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    lines = [f"### {t['title']}", "", "| " + " | ".join(esc(c) for c in t["columns"]) + " |",
             "|" + "|".join("---" for _ in t["columns"]) + "|"]
    for r in t["rows"]:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    if not t["rows"]:
        lines.append("| " + " | ".join([EMPTY] + [""] * (len(t["columns"]) - 1)) + " |")
    for n in t["notes"]:
        lines += ["", f"注：{n}"]
    return "\n".join(lines) + "\n"


def csv_text(t: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(t["columns"])
    w.writerows(t["rows"])
    return buf.getvalue()


ORDER = ["5-1", "5-1b", "5-1c", "5-2", "5-2f", "5-3", "5-2r", "5-2c", "5-4", "5-4f", "5-5", "5-5b", "5-5c", "5-5d", "5-6", "5-6b",
         "5-7", "5-9", "5-10"]


def write_outputs(res: Result, out: Path, manifest: dict):
    out.mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)
    tabs = sorted(res.tables, key=lambda t: ORDER.index(t["id"]) if t["id"] in ORDER else 99)
    head = ["# 第五章结果表", ""]
    if not res.formal:
        head += [f"> {PILOT_PREFIX}。本目录由预试验重算数据生成，只用于检验生成流程，表中数字不代表正式实验结果。", ""]
    head += [f"生成时间 {manifest['generated_at']}，seed = {res.seed}，B = {res.B}，标签 {res.label}。", ""]
    parts = ["\n".join(head)]
    for t in tabs:
        parts.append(md_table(t))
        (out / "tables" / f"{t['id']}.csv").write_text(csv_text(t), encoding="utf-8")
        (out / "tables" / f"{t['id']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    unc = ["## 未覆盖项", "", "以下内容评分输出中没有，表中相应位置写“评分输出无此项”：", ""]
    unc += [f"- {u}" for u in res.uncovered] or ["- 无"]
    parts.append("\n".join(unc) + "\n")
    parts.append("## 图的归档\n\n正式结果出来后，把两张图复制到 docs/figures/（报告引用 ../figures/fig5_e1_accuracy_by_layer.svg 与 "
                 "../figures/fig6_e2_risk.svg）：\n\n```\ncp output/results/formal/fig5_e1_accuracy_by_layer.{png,pdf,svg} "
                 "output/results/formal/fig6_e2_risk.{png,pdf,svg} docs/figures/\n```\n")
    (out / "tables.md").write_text("\n".join(parts), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def build_manifest(args, res: Result, inputs: dict, argv: Optional[Sequence[str]] = None) -> dict:
    argv = list(sys.argv[1:] if argv is None else argv)
    m = {"label": res.label, "seed": res.seed, "B": res.B, "argv": argv, "command": "python scripts/make_results.py " + " ".join(argv),
         "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "git": git_info(),
         "fx": res.fx, "inputs": {}}
    for k, v in inputs.items():
        if v is None:
            continue
        entries = []
        for p in (v if isinstance(v, (list, tuple)) else [v]):
            p = Path(p)
            if p.is_file():
                entries.append({"path": str(p), "sha256": sha256_file(p)})
            elif p.is_dir():
                e = {"path": str(p), **tree_digest(p)}
                if k in ("scores", "think_scores"):
                    e["key_files"] = {f.name: sha256_file(f) for f in sorted(p.glob("*.json"))}
                entries.append(e)
        m["inputs"][k] = entries
    return m


# ------------------------------------------------------------------ driver
def run(args) -> Result:
    score_dirs = [Path(p) for p in args.scores]
    out_dirs = [Path(p) for p in args.outputs]
    for d in score_dirs + out_dirs + [Path(args.tasks), Path(args.gold)]:
        if not d.exists():
            raise MakeResultsError(f"输入目录不存在：{d}")
    rows = [r for r in load_rows(score_dirs) if r["method"] in METHODS or r["method"] == "M4_TRIG"]
    if not rows:
        raise MakeResultsError("评分输出没有任何行")
    ep = load_endpoints(score_dirs)
    outputs = load_outputs(out_dirs)
    tasks = load_tasks(Path(args.tasks), {r["task_id"] for r in rows})
    gold = load_gold(Path(args.gold), {r["task_id"] for r in rows})
    bat = load_batches([Path(p) for p in args.batches])
    ledger = load_ledger(Path(args.ledger)) if args.ledger else []
    think_rows, think_outputs = [], {}
    if args.think_outputs:
        think_outputs = load_outputs([Path(p) for p in args.think_outputs])
    if args.think_scores:
        think_rows = load_rows([Path(p) for p in args.think_scores])
    elif think_outputs:
        think_rows = score_outputs_in_memory(think_outputs, Path(args.gold), Path(args.tasks))
    real_acc = None
    if args.real_accuracy:
        real_acc = _read_json(Path(args.real_accuracy)).get("field_accuracy")
    fx = load_fx(Path(args.fx_snapshot) if args.fx_snapshot else None, args.usd_cny)
    res = Result(args.label, args.B, args.seed, fx)
    main_rows = [r for r in rows if r["method"] in METHODS and r["config_id"] != THINK_CONFIG]
    all_rows = [r for r in rows if r["config_id"] != THINK_CONFIG]
    table_execution(res, bat, ledger, outputs)
    table_accuracy(res, main_rows, tasks)
    table_regret_cost(res, main_rows)
    table_risk(res, main_rows)
    table_ask(res, main_rows, tasks, gold)
    table_cost(res, main_rows, outputs, bat, think_rows, think_outputs)
    table_review(res, all_rows, outputs, gold)
    table_endpoints(res, ep)
    table_rules(res, ep, real_acc)
    for item in ("真实报价集抽取结果（报告 5.7、表 5-8）：由真实报价集运行另行产出，本脚本不含",
                 "M0、M1 自由文本映射的人工抽检一致率：人工抽检记录，不在评分输出中",
                 "效益分析（报告 5.9）所需的人工逐份核算对比：估算口径文件，不在评分输出中"):
        res.miss(item)
    return res


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--scores", nargs="+")
    ap.add_argument("--outputs", nargs="+")
    ap.add_argument("--tasks")
    ap.add_argument("--gold")
    ap.add_argument("--batches", nargs="+")
    ap.add_argument("--ledger")
    ap.add_argument("--think-scores", nargs="+", dest="think_scores")
    ap.add_argument("--think-outputs", nargs="+", dest="think_outputs")
    ap.add_argument("--label")
    ap.add_argument("--out")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--B", type=int, default=BOOT_B)
    ap.add_argument("--fx-snapshot", dest="fx_snapshot", default=str(ROOT / "data/params/tariff_snapshot_2026-10-09.json"))
    ap.add_argument("--usd-cny", dest="usd_cny", type=float, default=None)
    ap.add_argument("--real-accuracy", dest="real_accuracy", default=None,
                    help="JSON {\"field_accuracy\": {config: value}} of the real quote set run (optional)")
    a = ap.parse_args(argv)
    if a.preset:
        p = PRESETS[a.preset]
        def _abs(v):
            return [str(ROOT / x) for x in v] if isinstance(v, list) else str(ROOT / v)
        for key in ("scores", "outputs", "tasks", "gold", "ledger"):
            if getattr(a, key) is None:
                setattr(a, key, _abs(p[key]))
        if a.label is None:
            a.label = p["label"]
        if a.batches is None:
            a.batches = [str(ROOT / b) for b in p["batches"] if (ROOT / b).exists()]
        for key in ("think_scores", "think_outputs"):
            if getattr(a, key) is None and p[key] and (ROOT / p[key]).exists():
                setattr(a, key, [str(ROOT / p[key])])
    for key in ("scores", "outputs", "tasks", "gold"):
        if getattr(a, key) is None:
            ap.error(f"需要 --{key} 或 --preset")
    if a.batches is None:
        a.batches = []
    if a.label is None:
        a.label = "pilot_flowtest"
    if a.out is None:
        a.out = str(ROOT / "output/results" / a.label)
    if a.think_scores and not a.think_outputs:
        ap.error("--think-scores 需要 --think-outputs")
    out_abs = Path(a.out).resolve()
    if (ROOT / "runs").resolve() in (out_abs, *out_abs.parents):
        ap.error("--out 不能位于 runs/ 之下")
    return a


def main(argv=None) -> int:
    argv_used = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv_used)
    res = run(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(args, res, {
        "scores": args.scores, "outputs": args.outputs, "tasks": args.tasks, "gold": args.gold, "batches": args.batches,
        "ledger": args.ledger, "think_scores": args.think_scores, "think_outputs": args.think_outputs}, argv_used)
    write_outputs(res, out, manifest)
    draw_e1(res, out)
    draw_e2(res, out)
    print(json.dumps({"out": str(out), "tables": len(res.tables), "uncovered": len(res.uncovered)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
