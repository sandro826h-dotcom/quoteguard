import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_results", ROOT / "scripts" / "make_results.py")
mr = importlib.util.module_from_spec(_spec)
sys.modules["make_results"] = mr
_spec.loader.exec_module(mr)

TASKS = {   # id: (layer, gold type, modality, mode, n_missing)
    "T1": ("explicit", "choose", "text", "air", 0),
    "T2": ("explicit", "choose", "table", "air", 0),
    "T3": ("implicit", "choose", "image", "sea", 0),
    "T4": ("implicit", "choose", "text", "sea", 0),
    "T5": ("conflict", "ask", "table", "air", 0),
    "T6": ("conflict", "choose", "image", "air", 0),
    "T7": ("insufficient", "ask", "text", "air", 1),
    "T8": ("insufficient", "choose", "table", "sea", 0),
}
# decision_correct per (config, method) over T1..T8
CORRECT = {
    ("ds_pro", "M0"): [1, 1, 1, 0, 0, 1, 0, 1],
    ("ds_pro", "M4"): [1, 1, 1, 0, 1, 1, 1, 1],
    ("ds_flash", "M0"): [1, 0, 0, 0, 0, 0, 1, 1],
    ("ds_flash", "M4"): [1, 1, 1, 1, 1, 1, 1, 1],
}
FX = 7.0


def _row(cfg, method, tid, correct):
    layer, gtype, _mod, _mode, _nm = TASKS[tid]
    m = method.upper()
    ask = gtype == "ask"
    return {
        "task_id": tid, "method": m, "config_id": cfg, "layer": layer, "gold_type": gtype,
        "decision_correct": correct, "false_ask": int(gtype == "choose" and not correct),
        "ask_cover": float(correct) if ask else None, "out_of_feasible": None if ask else 0,
        "regret_abs": None if ask else (0.0 if correct else 10.0), "regret_rel": None if ask else (0.0 if correct else 0.02),
        "tp": 1 if m.startswith("M4") else 0, "fp": 1 if m.startswith("M4") else 0, "fn": 1,
        "tp_hidden_fee": 0, "fn_hidden_fee": 1, "fp_hidden_fee": 0,
        "tp_conflict": 1 if m.startswith("M4") else 0, "fp_conflict": 0, "fn_conflict": 0 if m.startswith("M4") else 1,
        "tp_gap": 0, "fp_gap": 1 if m.startswith("M4") else 0, "fn_gap": 0,
        "tp_ddp_risk": 0, "fp_ddp_risk": 0, "fn_ddp_risk": 0, "claim_n": 2, "claim_correct": 1,
        "cost_n": 2, "cost_missing": 0, "cost_abs_sum": 20.0 if m == "M2" else 10.0, "cost_rel_sum": 0.1, "cost_exact": 1,
        "n_calls": 1, "cost_usd": 0.01 if cfg.startswith("ds") else 0.0, "cost_cny": 0.0 if cfg.startswith("ds") else 0.1,
        "parse_ok": 1, "parse_failed": 0, "call_failed": 0,
    }


def _gold(tid):
    layer, gtype, _m, _mode, nmiss = TASKS[tid]
    pc = {"conflict_ids": ["C1.fuel_pct"] if layer == "conflict" else [],
          "missing_ids": ["C1.isf_fee_cny"] * nmiss, "hidden_fee_ids": [], "ddp_level": "none", "claim_statuses": []}
    pc2 = {"conflict_ids": [], "missing_ids": [], "hidden_fee_ids": [], "ddp_level": "none", "claim_statuses": []}
    return {"task_id": tid, "decision": {"type": gtype}, "per_cand": {"C1": pc, "C2": pc2}}


def _task(tid):
    layer, _g, mod, mode, _n = TASKS[tid]
    return {"task_id": tid, "layer": layer, "shipment": {"mode": mode},
            "candidates": [{"presentation": {"modality": mod}}, {"presentation": {"modality": "text"}}]}


def _jdump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def build(tmp: Path, correct=None, endpoints=None):
    correct = correct or CORRECT
    methods = ["M0", "M2", "M4", "M4_trig"]
    for tid in TASKS:
        _jdump(tmp / "tasks" / f"{tid}.json", _task(tid))
        _jdump(tmp / "gold" / f"{tid}.json", _gold(tid))
    scores, outputs = [], []
    plan_reqs, obs = [], {"deepseek-A": [], "dashscope-A": [], "deepseek-A-retry": []}
    ledger = []
    for cfg in ("ds_pro", "ds_flash"):
        sd, od = tmp / f"scores_{cfg}", tmp / f"outputs_{cfg}"
        rows = []
        for m in methods:
            base = m.replace("_trig", "")
            vals = correct.get((cfg, base)) or correct[(cfg, "M4")]
            for i, tid in enumerate(TASKS):
                rows.append(_row(cfg, m, tid, vals[i]))
                rk = f"F|{base}|{cfg}|{tid}|answer|ALL|r0"
                full = m == "M4"
                rev = ["C1", "C2"] if full else (["C1"] if TASKS[tid][0] in ("conflict", "insufficient") else [])
                _jdump(od / m / cfg / f"{tid}.json", {"task_id": tid, "config_id": cfg, "method": m, "calls": [rk],
                                                       "failed_calls": [], "diagnostics": {"review": {"reviewed": rev}}})
                if m in ("M0", "M2") or m == "M4":
                    if rk not in {r["run_key"] for r in plan_reqs}:
                        plan_reqs.append({"run_key": rk, "config_id": cfg})
                        prov = "deepseek" if cfg.startswith("ds") else "dashscope"
                        o = {"run_key": rk, "config_id": cfg, "provider": prov, "valid": True, "text": "x", "stage": "answer",
                             "finish_reason": "length" if (cfg == "ds_pro" and tid == "T1" and m == "M0") else "stop",
                             "usage": {"prompt_tokens": 100, "completion_tokens": 50}, "latency_ms": 1000.0,
                             "prompt_sha256": "h", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z"}
                        obs["deepseek-A" if prov == "deepseek" else "dashscope-A"].append(o)
                        ledger.append({"batch_id": "deepseek-A" if prov == "deepseek" else "dashscope-A", "run_key": rk,
                                       "currency": "USD" if prov == "deepseek" else "CNY", "amount": 0.01 if prov == "deepseek" else 0.1,
                                       "kind": "observation"})
        _jdump(sd / "rows.json", rows)
        if endpoints is not None and cfg == "ds_pro":
            _jdump(sd / "endpoints.json", endpoints)
        scores.append(str(sd))
        outputs.append(str(od))
    # a planned call that never returned, and its retry observation (still no usable answer is not needed: retry supplies one)
    miss_rk = "F|M0|ds_pro|T-missing|answer|ALL|r0"
    plan_reqs.append({"run_key": miss_rk, "config_id": "ds_pro"})
    miss2 = "F|M0|ds_pro|T-missing2|answer|ALL|r0"
    plan_reqs.append({"run_key": miss2, "config_id": "ds_pro"})
    obs["deepseek-A-retry"].append({"run_key": miss2, "config_id": "ds_pro", "provider": "deepseek", "valid": True, "text": "y",
                                    "stage": "answer", "finish_reason": "stop", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                                    "latency_ms": 1.0, "prompt_sha256": "h", "finished_at": "2026-01-02T00:00:00Z"})
    ledger.append({"batch_id": "deepseek-A-retry", "run_key": miss2, "currency": "USD", "amount": 0.5, "kind": "observation"})
    ledger.append({"batch_id": "pilot-x", "run_key": "other", "currency": "USD", "amount": 9.0, "kind": "observation"})
    batches = []
    for b, lst in obs.items():
        for i, o in enumerate(lst):
            _jdump(tmp / b / "observations" / f"{i:04d}.json", o)
        _jdump(tmp / b / "manifest.json", {"batch_id": b})
        batches.append(str(tmp / b))
    _jdump(tmp / "deepseek-A" / "plan.json", {"requests": plan_reqs})
    (tmp / "ledger.jsonl").write_text("\n".join(json.dumps(e) for e in ledger), encoding="utf-8")
    _jdump(tmp / "snapshot.json", {"fx": {"CNY_per_USD": {"value": str(FX), "date": "2026-10-09", "cite": "c"}}})
    return {"scores": scores, "outputs": outputs, "tasks": str(tmp / "tasks"), "gold": str(tmp / "gold"), "batches": batches,
            "ledger": str(tmp / "ledger.jsonl"), "fx": str(tmp / "snapshot.json")}


def argv_of(inp, out, label="pilot_flowtest", B=200, extra=()):
    return (["--scores", *inp["scores"], "--outputs", *inp["outputs"], "--tasks", inp["tasks"], "--gold", inp["gold"],
             "--batches", *inp["batches"], "--ledger", inp["ledger"], "--fx-snapshot", inp["fx"], "--out", str(out),
             "--label", label, "--B", str(B), "--seed", "7", *extra])


def table(res, tid):
    return next(t for t in res.tables if t["id"] == tid)


@pytest.fixture()
def fx(tmp_path):
    inp = build(tmp_path / "in")
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))
    return inp, res


# ------------------------------------------------------------------ table 5-2 and confidence intervals
def test_table_5_2_hand_calculation(fx):
    _inp, res = fx
    t = table(res, "5-2")
    rows = {r[0]: r for r in t["rows"]}
    # non-explicit tasks T3..T8
    assert rows["ds_pro"][1] == "50.0%"       # M0: 3 of 6
    assert rows["ds_flash"][1] == "33.3%"     # M0: 2 of 6
    assert rows["ds_pro"][5] == "83.3%"       # M4: 5 of 6
    assert rows["ds_flash"][5] == "100.0%"
    assert rows["ds_pro"][2] == "无数据"       # no M1 rows
    merged = res.ctx["acc_merged"]
    assert merged["M0"]["est"] == pytest.approx(5 / 12) and merged["M0"]["n"] == 12
    assert merged["M4"]["est"] == pytest.approx(11 / 12)
    assert rows["合并（95% CI）"][1].startswith("41.7% [")
    assert "非显式三层 6 个测试任务" in t["title"]


def test_ci_reproducible_under_fixed_seed(tmp_path):
    inp = build(tmp_path / "in")
    r1 = mr.run(mr.parse_args(argv_of(inp, tmp_path / "o1")))
    r2 = mr.run(mr.parse_args(argv_of(inp, tmp_path / "o2")))
    assert table(r1, "5-2")["rows"] == table(r2, "5-2")["rows"]
    assert table(r1, "5-4")["rows"] == table(r2, "5-4")["rows"]
    a = mr.boot([{"task_id": f"T{i}", "decision_correct": i % 2} for i in range(8)], mr.f_acc, 300, 5)
    b = mr.boot([{"task_id": f"T{i}", "decision_correct": i % 2} for i in range(8)], mr.f_acc, 300, 5)
    c = mr.boot([{"task_id": f"T{i}", "decision_correct": i % 2} for i in range(8)], mr.f_acc, 300, 6)
    assert a == b and a[0] == 0.5 and a[1] <= 0.5 <= a[2]
    assert c[0] == a[0]


def test_figure_data_by_layer(fx):
    _inp, res = fx
    lm = res.ctx["layer_method"]
    assert lm[("explicit", "M0")][0] == pytest.approx(3 / 4) and lm[("explicit", "M0")][3] == 4
    assert lm[("conflict", "M4")][0] == 1.0
    assert lm[("explicit", "M1")][0] is None
    t = table(res, "5-2f")
    assert [r[0] for r in t["rows"]] == ["显式层", "隐式层", "冲突层", "信息不足层"]


def test_modality_groups(fx):
    _inp, res = fx
    t = {r[0]: r for r in table(res, "5-3")["rows"]}
    # image tasks among non-explicit: T3, T6 (M0 ds_pro 1,1; ds_flash 0,0) -> 50%
    assert t["含图片"][1] == "2" and t["含图片"][2].startswith("50.0%")
    # table tasks without image: T5, T8
    assert t["含表格不含图片"][1] == "2"
    assert t["只含文字"][1] == "2"      # T4, T7 (T1 is explicit)


# ------------------------------------------------------------------ other tables
def test_cost_table_uses_fx_and_observations(fx):
    _inp, res = fx
    t = table(res, "5-6")
    r = next(x for x in t["rows"] if x[0] == "ds_pro" and x[1] == "M0")
    assert r[2] == "1.0" and r[3] == "150" and r[4] == "0.0700" and r[5] == "1.0"
    assert res.fx["cny_per_usd"] == FX
    q = next(x for x in t["rows"] if x[0] == "ds_flash" and x[1] == "M4")
    assert q[6] == "0.0700"


def test_execution_table_counts_and_spend(fx):
    _inp, res = fx
    t = table(res, "5-1")
    rows = {r[0]: r for r in t["rows"]}
    ds = rows["DeepSeek 官方"]
    # 3 methods x 8 tasks x 2 DeepSeek configs = 48 answered keys, plus two planned keys that were never answered;
    # one of them is answered by the retry batch
    assert ds[2] == "50" and ds[3] == "49" and ds[4] == "1" and ds[5] == "1"
    assert "pilot-x" in " ".join(t["notes"])           # pilot batch excluded from spend
    assert "USD" not in ds[6] and "美元" in ds[6]
    assert t["data"]["spend"]["deepseek"]["USD"] == pytest.approx(0.01 * 48 + 0.5)
    b = table(res, "5-1b")
    r = next(x for x in b["rows"] if x[0] == "ds_pro")
    assert r[1] == "25" and r[2] == "1" and r[3] == "4.0%"


def test_risk_table_and_missing_metric(fx):
    _inp, res = fx
    t = {r[0]: r for r in table(res, "5-4")["rows"]}
    assert t["无依据宣称的采纳率"][1] == mr.NA
    # M4: tp_conflict 16 rows/ (16 + 0 fp) -> conflict+gap F1: tp 16, fp 16, fn 0
    assert t["冲突与缺口检出 F1"][5] == f"{2 * 16 / (2 * 16 + 16):.3f}"
    assert any("无依据宣称" in u for u in res.uncovered)


def test_ask_table(fx):
    _inp, res = fx
    t = {r[0]: r for r in table(res, "5-5")["rows"]}
    # conflict+insufficient, M0: ask tasks T5, T7 -> ds_pro 0,0 ; ds_flash 0,1 -> 1/4
    assert t["正确拒答率（决定性任务）"][1].startswith("25.0%")
    assert t["正确拒答率（决定性任务）"][2] == "无数据"


def test_review_table(fx):
    _inp, res = fx
    s = res.ctx["review"]
    assert s["M4"]["outputs"] == 16 and s["M4"]["candidates"] == 32 and s["M4"]["reviewed"] == 32
    assert s["M4_TRIG"]["reviewed"] == 8      # C1 only, 4 conflict/insufficient tasks x 2 configs
    assert s["M4"]["gold_risk_not_reviewed"] == 0
    assert s["M4_TRIG"]["gold_risk_not_reviewed"] == 0     # risk sits on C1, which is reviewed
    t = table(res, "5-7")
    assert t["rows"][1][1] == "100.0%" and t["rows"][1][2] == "25.0%"


# ------------------------------------------------------------------ endpoints and rules
def test_missing_endpoints_are_flagged(fx):
    _inp, res = fx
    assert "评分输出未含主终点" in table(res, "5-9")["rows"][0][1]
    rules = table(res, "5-10")["rows"]
    assert rules[0][1] == "评分输出未含主终点" and rules[0][3] == "无输入，未判定"
    assert any("主终点" in u for u in res.uncovered)


def test_endpoints_table_and_rules(tmp_path):
    ep = {"E1": {"acc_M4": 0.9, "acc_M0": 0.5, "mean_difference": 0.4, "method": "monte_carlo"},
          "E2": {"f1_M4": 0.6, "f1_M0": 0.3, "difference": 0.3, "method": "monte_carlo"},
          "E3": {"estimate": -0.01, "lower": -0.06, "margin": 0.05, "tango": {"a_vs_b": {"theta_hat": -0.01, "p": 0.2}}},
          "p_raw": {"E1": 0.001, "E2": 0.02, "E3": 0.2}, "p_holm": {"E1": 0.003, "E2": 0.04, "E3": 0.2},
          "reject": {"E1": True, "E2": False, "E3": False}, "alpha": 0.05}
    inp = build(tmp_path / "in", endpoints=ep)
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))
    t = table(res, "5-9")
    assert t["rows"][0][3] == "0.003" and t["rows"][0][4] == "True"
    assert any("Tango" in n for n in t["notes"])
    rules = {r[0]: r for r in table(res, "5-10")["rows"]}
    assert rules["主终点 1 是否显著"][1] == "显著" and rules["主终点 1 是否显著"][3] == "不触发"
    assert rules["主终点 2 是否显著"][1] == "不显著" and rules["主终点 2 是否显著"][3] == mr.RULE_ACTIONS[2]
    assert rules["主终点 3 是否非劣"][1] == "未达到非劣" and rules["主终点 3 是否非劣"][3] == mr.RULE_ACTIONS[3]


def test_rule_m0_explicit_threshold(tmp_path):
    # default fixture: M0 explicit accuracy 3/4 -> rule 4 not triggered
    inp = build(tmp_path / "a")
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "oa")))
    r = {x[0]: x for x in table(res, "5-10")["rows"]}["M0 显式层正确率 ≥ 90%"]
    assert r[1] == "否" and r[3] == "不触发" and "75.0%" in r[2]
    c2 = {k: list(v) for k, v in CORRECT.items()}
    c2[("ds_flash", "M0")][1] = 1          # explicit M0 now 4/4
    inp2 = build(tmp_path / "b", correct=c2)
    res2 = mr.run(mr.parse_args(argv_of(inp2, tmp_path / "ob")))
    r2 = {x[0]: x for x in table(res2, "5-10")["rows"]}["M0 显式层正确率 ≥ 90%"]
    assert r2[1] == "是" and r2[3] == mr.RULE_ACTIONS[4] and "100.0%" in r2[2]


def test_rule_cost_pair_and_precision(fx):
    _inp, res = fx
    cp = res.ctx["cost_pair"]
    assert cp["est"] == pytest.approx(10 / 2 - 20 / 2) and cp["n_tasks"] == 8     # M4 abs 5 per cand, M2 10
    r = {x[0]: x for x in table(res, "5-10")["rows"]}
    assert r["M4 对 M2 核价误差差值 CI 含 0"][1] == "不含 0"
    # M4 precision over conflict/gap/ddp: tp 16, fp 16 -> 0.5 < 0.7
    assert r["M4 风险标记精确率 < 0.7"][1] == "是" and "0.500" in r["M4 风险标记精确率 < 0.7"][2]
    assert r["思考组 M1 ≥ M4"][1] == "未运行"
    assert r["真实集字段准确率 < 0.8"][1] == "未运行"


def test_real_accuracy_rule(tmp_path):
    inp = build(tmp_path / "in")
    _jdump(tmp_path / "real.json", {"field_accuracy": {"ds_pro": 0.9, "glm": 0.7}})
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out", extra=["--real-accuracy", str(tmp_path / "real.json")])))
    r = {x[0]: x for x in table(res, "5-10")["rows"]}["真实集字段准确率 < 0.8"]
    assert r[1] == "是" and r[3] == mr.RULE_ACTIONS[8]


# ------------------------------------------------------------------ errors, label, files
def test_overlapping_configs_raise(tmp_path):
    inp = build(tmp_path / "in")
    inp["scores"].append(inp["scores"][0])
    with pytest.raises(mr.MakeResultsError, match="重叠"):
        mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))


def test_missing_scores_dir_raises(tmp_path):
    inp = build(tmp_path / "in")
    inp["scores"] = [str(tmp_path / "nope")]
    with pytest.raises(mr.MakeResultsError):
        mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))


def _snapshot(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*") if p.is_file())


def test_main_writes_all_files_and_leaves_inputs_untouched(tmp_path):
    inp = build(tmp_path / "in")
    before = _snapshot(tmp_path / "in")
    out = tmp_path / "out"
    assert mr.main(argv_of(inp, out)) == 0
    assert _snapshot(tmp_path / "in") == before
    for stem in ("fig5_e1_accuracy_by_layer", "fig6_e2_risk"):
        for ext in ("png", "pdf", "svg"):
            assert (out / f"{stem}.{ext}").stat().st_size > 1000
    md = (out / "tables.md").read_text("utf-8")
    assert "流程测试" in md and "## 未覆盖项" in md
    assert "—" not in md and "–" not in md
    for tid in ("5-1", "5-2", "5-7", "5-10"):
        assert (out / "tables" / f"{tid}.csv").exists()
        assert json.loads((out / "tables" / f"{tid}.json").read_text("utf-8"))["id"] == tid
    man = json.loads((out / "manifest.json").read_text("utf-8"))
    assert man["seed"] == 7 and man["B"] == 200 and man["label"] == "pilot_flowtest"
    assert man["inputs"]["ledger"][0]["sha256"] and man["inputs"]["scores"][0]["key_files"]["rows.json"]
    assert "git" in man and "command" in man


def test_formal_label_has_no_flowtest_mark(tmp_path):
    inp = build(tmp_path / "in")
    out = tmp_path / "out"
    mr.main(argv_of(inp, out, label="formal"))
    assert "流程测试" not in (out / "tables.md").read_text("utf-8")


def test_think_group_table(tmp_path):
    inp = build(tmp_path / "in")
    r = _row("ds_flash_think", "M1", "T3", 1)
    r["method"] = "M1"
    _jdump(tmp_path / "ts" / "rows.json", [r])
    _jdump(tmp_path / "to" / "M1" / "ds_flash_think" / "T3.json",
           {"task_id": "T3", "config_id": "ds_flash_think", "method": "M1", "calls": ["F|M0|ds_flash|T3|answer|ALL|r0"],
            "failed_calls": []})
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out", extra=["--think-scores", str(tmp_path / "ts"),
                                                                    "--think-outputs", str(tmp_path / "to")])))
    t = table(res, "5-6b")
    assert t["rows"][0][0] == "ds_flash_think" and t["rows"][0][2] == "100.0%"
    rule = {x[0]: x for x in table(res, "5-10")["rows"]}["思考组 M1 ≥ M4"]
    assert rule[1] == "是" and rule[3] == mr.RULE_ACTIONS[7]      # 100% >= ds_flash M4 100%


# ------------------------------------------------------------------ helpers
def test_formatters():
    assert mr.fpct(0.4167) == "41.7%" and mr.fpct(None) == mr.EMPTY and mr.fpct(float("nan")) == mr.EMPTY
    assert mr.fnum(0.12345) == "0.123" and mr.fnum(None) == mr.EMPTY
    assert mr.fci(0.5, 0.25, 0.75) == "50.0% [25.0%, 75.0%]"
    assert mr.fci(2.0, 1.0, 3.0, "num") == "2.00 [1.00, 3.00]"
    assert mr.fci(0.5, None, None) == "50.0%"
    assert mr.ordered_configs(["qwen_plus", "x", "ds_pro", "ds_flash_think", "glm"]) == ["ds_pro", "glm", "qwen_plus", "x"]
    assert mr.cost_cny({"cost_cny": 1.0, "cost_usd": 2.0}, 7.0) == 15.0
    assert mr.cost_cny({"cost_cny": None, "cost_usd": None}, 7.0) == 0.0


def test_modality_group_and_loaders(tmp_path):
    img = {"candidates": [{"presentation": {"modality": "table"}}, {"presentation": {"modality": "image"}}]}
    tab = {"candidates": [{"presentation": {"modality": "table"}}, {"presentation": {"modality": "text"}}]}
    txt = {"candidates": [{"presentation": {"modality": "text"}}]}
    assert (mr.modality_group(img), mr.modality_group(tab), mr.modality_group(txt)) == ("含图片", "含表格不含图片", "只含文字")
    _jdump(tmp_path / "T1.json", _task("T1"))
    assert mr.load_tasks(tmp_path, ["T1"])["T1"]["group"] == "只含文字"
    with pytest.raises(mr.MakeResultsError):
        mr.load_tasks(tmp_path, ["T9"])
    with pytest.raises(mr.MakeResultsError):
        mr.load_gold(tmp_path, ["T9"])
    _jdump(tmp_path / "snap.json", {"fx": {"CNY_per_USD": {"value": "6.7330", "date": "2026-10-09"}}})
    assert mr.load_fx(tmp_path / "snap.json", None)["cny_per_usd"] == 6.733
    assert mr.load_fx(None, 7.1)["cny_per_usd"] == 7.1
    assert mr.load_ledger(None) == [] and mr.load_ledger(tmp_path / "none.jsonl") == []


def test_usage_helpers():
    body = json.dumps({"usage": {"completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 4}}})
    assert mr.usage_from_raw(body)["completion_tokens"] == 9
    sse = 'data: {"choices": []}\n\ndata: {"usage": {"completion_tokens_details": {"reasoning_tokens": 7}}}\n\ndata: [DONE]\n'
    assert mr.reasoning_tokens({"raw_body": sse}) == 7
    assert mr.reasoning_tokens({"raw_body": body}) == 4
    assert mr.reasoning_tokens({"raw_body": "oops"}) is None
    assert mr.obs_tokens({"usage": {"prompt_tokens": 3, "completion_tokens": 4}}) == 7
    assert mr.obs_tokens({"cost_estimate": {"input_tokens": 5, "output_tokens": 6}}) == 11
    assert mr.obs_tokens({}) is None


def test_render_helpers(tmp_path):
    t = {"id": "x", "title": "T", "columns": ["a", "b"], "rows": [["1|2", "3"]], "notes": ["n"]}
    md = mr.md_table(t)
    assert "1\\|2" in md and "注：n" in md
    assert mr.csv_text(t).splitlines()[0] == "a,b"
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("hi")
    d1 = mr.tree_digest(tmp_path / "d")
    assert d1["files"] == 1
    (tmp_path / "d" / "f.txt").write_text("ho")
    assert mr.tree_digest(tmp_path / "d")["tree_sha256"] != d1["tree_sha256"]
    assert len(mr.sha256_file(tmp_path / "d" / "f.txt")) == 64
    assert set(mr.git_info()) == {"commit", "dirty"}


def test_preset_fills_formal_paths():
    a = mr.parse_args(["--preset", "formal"])
    assert a.label == "formal" and a.scores == [str(ROOT / "runs/derived/formal/scores")]
    assert a.tasks == str(ROOT / "data/tasks/test/tasks") and a.ledger == str(ROOT / "runs/spend_ledger.jsonl")
    assert a.B == 9999 and a.seed == 20261011 and a.out == str(ROOT / "output/results/formal")
    p = mr.parse_args(["--preset", "pilot"])
    assert p.label == "pilot_flowtest" and len(p.scores) == 2


# ------------------------------------------------------------------ integration on the recomputed pilot data
PILOT = ROOT / "runs/derived/pilot_rescore_20261010_fullreview"


@pytest.mark.skipif(not (PILOT / "fullreview_summary.json").exists() or not (ROOT / "runs/pilota-A-live").exists(),
                    reason="pilot recomputation data not present")
def test_pilot_table_5_7_matches_fullreview_summary(tmp_path):
    args = mr.parse_args(["--preset", "pilot", "--out", str(tmp_path / "out"), "--B", "200"])
    res = mr.run(args)
    summary = json.loads((PILOT / "fullreview_summary.json").read_text("utf-8"))
    keys = ("outputs", "decisions_correct", "conflict_detected", "conflict_missed", "gap_detected", "gap_missed",
            "false_positives_conflict_gap_ddp", "gold_risk_candidates", "gold_risk_not_reviewed")
    for m, sm in (("M4", "M4"), ("M4_TRIG", "M4_trig")):
        for k in keys:
            want = sum(summary[g][sm][k] for g in summary)
            assert res.ctx["review"][m][k] == want, (m, k)
    assert table(res, "5-7")["rows"][1][1] == "100.0%"
    assert any("未运行" in u and "思考组" in u for u in res.uncovered)
    assert any("流程测试" in t["title"] for t in res.tables)


# ------------------------------------------------------------------ review round: reconciliation with metrics, think group, misc
def test_values_match_metrics_metric_values(tmp_path):
    inp = build(tmp_path / "in")
    rows = mr.load_rows([Path(p) for p in inp["scores"]])
    for m in ("M0", "M2", "M4"):
        rs = [r for r in rows if r["method"] == m]
        mv = mr.metrics.metric_values(rs)
        assert mr.f_acc(rs) == pytest.approx(mv["decision_correct"])
        assert mr.f_micro_f1(rs) == pytest.approx(mv["micro_f1"])
        assert mr.f_cost_abs(rs) == pytest.approx(mv["cost_abs_mean"])
        assert mr.f_regret_abs(rs) == pytest.approx(mv["regret_abs_mean"])
        assert mr.f_ask_cover(rs) == pytest.approx(mv["ask_cover_mean"])
        assert mr.f_ask_rate(rs) == pytest.approx(mv["correct_ask_rate"])
        assert mr.f_false_ask(rs) == pytest.approx(mv["false_ask_rate"])
        assert mr.f_recall(rs) == pytest.approx(mv["recall"])
    # merged-row confidence interval reproduces metrics.aggregate for the same seed and B
    ne = [r for r in rows if r["method"] == "M0" and r["layer"] != "explicit"]
    agg = mr.metrics.aggregate(ne, by=("method",), B=200, seed=7)[0]
    est, lo, hi = mr.boot(ne, mr.f_acc, 200, 7)
    assert (est, lo, hi) == pytest.approx((agg["decision_correct"], agg["decision_correct_lo"], agg["decision_correct_hi"]))


def test_regret_cost_risk_and_false_ask_numbers(tmp_path):
    inp = build(tmp_path / "in")
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))
    rows = mr.load_rows([Path(p) for p in inp["scores"]])
    ne = [r for r in rows if r["layer"] != "explicit"]
    # 5-2r: M0 non-explicit regrets 0,10,0,0 and 10,10,10,0 -> mean 5.0, 8 rows with a recommendation
    r = {x[0]: x for x in table(res, "5-2r")["rows"]}["M0"]
    agg = mr.metrics.aggregate([x for x in ne if x["method"] == "M0"], by=("method",), B=200, seed=7)[0]
    assert r[1] == "8" and r[2] == mr.fci(5.0, agg["regret_abs_mean_lo"], agg["regret_abs_mean_hi"], "num") and r[4] == "500.0"
    # 5-2c: M2 10.00 per candidate, M4 5.00, paired difference -5.00 with a degenerate interval
    c = {x[0]: x for x in table(res, "5-2c")["rows"]}
    allm4 = mr.metrics.aggregate([x for x in rows if x["method"] == "M4"], by=("method",), B=200, seed=7)[0]
    assert c["M2"][3].startswith("10.00 [") and c["M4"][3] == mr.fci(
        5.0, allm4["cost_abs_mean_lo"], allm4["cost_abs_mean_hi"], "num")
    assert c["M4 减 M2（配对）"][3] == "-5.00 [-5.00, -5.00]"
    # 5-4f precision uses the three flagged categories and equals the rule-6 figure
    f = {x[0]: x for x in table(res, "5-4f")["rows"]}
    assert f["M4"][1] == "0.500 [0.500, 0.500]"
    assert res.ctx["risk_prec_cg"]["M4"] == 0.5
    assert {x[0]: x for x in table(res, "5-10")["rows"]}["M4 风险标记精确率 < 0.7"][2].endswith("0.500")
    assert f["M0"][1] == mr.EMPTY      # no flagged predictions: precision undefined
    # 5-5 false-refusal rate: conflict and insufficient choose tasks T6, T8; M0 wrong on one of four rows
    a = {x[0]: x for x in table(res, "5-5")["rows"]}["误拒率（非决定性任务）"]
    assert a[1].startswith("25.0%")


def test_think_outputs_scored_in_memory(tmp_path):
    inp = build(tmp_path / "in")
    # a real MethodOutput-like dict for the think group scored against the synthetic gold
    gold = _gold("T3")
    gold["decision"] = {"type": "choose", "choice": "C1", "ask_fields": [], "opt_cost_cny": "100"}
    for c in gold["per_cand"].values():
        c.update(determined=False, claims=[], feasible=True, total_cny="100")
    _jdump(Path(inp["gold"]) / "T3.json", gold)
    out = {"task_id": "T3", "config_id": "ds_flash_think", "method": "M1", "decision": "choose", "choice": "C1", "parse_ok": True,
           "per_cand": {}, "calls": ["F|M0|ds_flash|T3|answer|ALL|r0"], "failed_calls": [], "cost": {"USD": 0.02, "CNY": 0.0}}
    _jdump(tmp_path / "to" / "M1" / "ds_flash_think" / "T3.json", out)
    before = _snapshot(tmp_path / "to")
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out", extra=["--think-outputs", str(tmp_path / "to")])))
    assert _snapshot(tmp_path / "to") == before and not (tmp_path / "to" / "scores").exists()
    t = table(res, "5-6b")
    assert t["rows"][0][0] == "ds_flash_think" and t["rows"][0][3] == "0.1400"
    assert not any("思考组" in u and "未运行" in u for u in res.uncovered)
    rows = mr.score_outputs_in_memory(mr.load_outputs([tmp_path / "to"]), Path(inp["gold"]), Path(inp["tasks"]))
    assert rows[0]["layer"] == "implicit" and rows[0]["n_calls"] == 1 and rows[0]["cost_usd"] == 0.02


def test_out_inside_runs_rejected_and_argv_recorded(tmp_path):
    inp = build(tmp_path / "in")
    with pytest.raises(SystemExit):
        mr.parse_args(argv_of(inp, ROOT / "runs" / "x"))
    out = tmp_path / "out"
    argv = argv_of(inp, out)
    mr.main(argv)
    man = json.loads((out / "manifest.json").read_text("utf-8"))
    assert man["argv"] == argv


def test_undefined_endpoint_pvalues(tmp_path):
    ep = {"E1": {}, "E2": {}, "E3": {"estimate": 0, "lower": 0}, "p_raw": {"E1": None, "E2": None, "E3": None},
          "p_holm": {"E1": None, "E2": None, "E3": None}, "reject": {"E1": None, "E2": None, "E3": None}}
    inp = build(tmp_path / "in", endpoints=ep)
    res = mr.run(mr.parse_args(argv_of(inp, tmp_path / "out")))
    rules = {r[0]: r for r in table(res, "5-10")["rows"]}
    assert rules["主终点 1 是否显著"][1] == "无法计算，未判定" and rules["主终点 3 是否非劣"][3] == "无输入，未判定"
    assert "任一配置" in " ".join(table(res, "5-10")["notes"])
