import copy
import glob
import json
import math
import time
from pathlib import Path

import pytest

from quoteguard import metrics as M

GOLD_DIR = Path(__file__).resolve().parents[1] / "data" / "gold" / "dev"


def gold(tid):
    return json.load(open(GOLD_DIR / f"{tid}.json"))


def perfect(g, method="M4", config="ds_flash"):
    """A MethodOutput that reproduces the gold answer."""
    d = g["decision"]
    per = {}
    for cid, pc in g["per_cand"].items():
        hidden = [i.split(".", 1)[1] for i in pc["hidden_fee_ids"]]
        per[cid] = {
            "cost_cny": pc["total_cny"] if pc["determined"] else None,
            "ddp_level": pc["ddp_levels"][0] if len(set(pc["ddp_levels"])) == 1 else None,
            "claim_status": {str(i): c["gold_status"] for i, c in enumerate(pc["claims"])} or None,
            "hidden_fees": ["OVW" if h == "surcharges" else h for h in hidden],
            "conflicts": [i.split(".", 1)[1] for i in pc["conflict_ids"]],
            "gaps": [i.split(".", 1)[1] for i in pc["missing_ids"]],
        }
    return {"task_id": g["task_id"], "method": method, "config_id": config, "decision": d["type"],
            "choice": d["choice"] if d["type"] == "choose" else None,
            "ask_fields": list(d["ask_fields"]), "per_cand": per, "parse_ok": True}


def find(pred):
    for f in sorted(glob.glob(str(GOLD_DIR / "*.json"))):
        g = json.load(open(f))
        if pred(g):
            return g
    pytest.skip("no suitable gold")


def test_perfect_output_scores_full():
    for f in sorted(glob.glob(str(GOLD_DIR / "*.json"))):
        g = json.load(open(f))
        r = M.score_task(perfect(g), g)
        assert r["decision_correct"] == 1
        assert r["fp"] == 0 and r["fn"] == 0
        assert r["claim_correct"] == r["claim_n"]
        assert r["cost_missing"] == 0 and r["cost_abs_sum"] == 0
        assert r["cost_exact"] == r["cost_n"]
        if g["decision"]["type"] == "choose":
            assert r["out_of_feasible"] == 0 and r["regret_abs"] == 0
        else:
            assert r["ask_cover"] == 1.0


def test_wrong_choice_regret():
    g = find(lambda g: g["decision"]["type"] == "choose"
             and any(p["feasible"] and p["total_cny"] != g["decision"]["opt_cost_cny"] for p in g["per_cand"].values()))
    opt = g["decision"]["choice"]
    other = next(c for c, p in g["per_cand"].items() if p["feasible"] and c != opt)
    out = perfect(g)
    out["choice"] = other
    r = M.score_task(out, g)
    assert r["decision_correct"] == 0
    exp = float(g["per_cand"][other]["total_cny"]) - float(g["decision"]["opt_cost_cny"])
    assert r["regret_abs"] == pytest.approx(exp)
    assert r["regret_rel"] == pytest.approx(exp / float(g["decision"]["opt_cost_cny"]))
    assert r["out_of_feasible"] == 0


def test_choice_outside_feasible_set():
    g = find(lambda g: g["decision"]["type"] == "choose" and any(not p["feasible"] for p in g["per_cand"].values()))
    bad = next(c for c, p in g["per_cand"].items() if not p["feasible"])
    out = perfect(g)
    out["choice"] = bad
    r = M.score_task(out, g)
    assert r["decision_correct"] == 0 and r["out_of_feasible"] == 1 and r["regret_abs"] is None


def test_ask_when_gold_chooses_and_choose_when_gold_asks():
    gc = find(lambda g: g["decision"]["type"] == "choose")
    out = perfect(gc)
    out.update(decision="ask", choice=None, ask_fields=["C1.dim_divisor"])
    r = M.score_task(out, gc)
    assert r["decision_correct"] == 0 and r["false_ask"] == 1 and r["regret_abs"] is None
    ga = find(lambda g: g["decision"]["type"] == "ask")
    out = perfect(ga)
    out.update(decision="choose", choice="C1", ask_fields=[])
    r = M.score_task(out, ga)
    assert r["decision_correct"] == 0 and r["missed_ask"] == 1 and r["ask_cover"] == 0.0
    assert r["regret_abs"] is None            # ask tasks carry no regret


def test_ask_needs_overlap_with_gold_fields():
    ga = find(lambda g: g["decision"]["type"] == "ask")
    out = perfect(ga)
    out["ask_fields"] = ["C9.nothing"]
    assert M.score_task(out, ga)["decision_correct"] == 0
    out["ask_fields"] = ["C9.nothing"] + list(ga["decision"]["ask_fields"])
    r = M.score_task(out, ga)
    assert r["decision_correct"] == 1 and r["ask_cover"] == 1.0
    out["ask_fields"] = [f.replace("C", "报价", 1) for f in ga["decision"]["ask_fields"]]
    assert M.score_task(out, ga)["decision_correct"] == 1


def test_risk_triples_tp_fp_fn():
    g = find(lambda g: any(p["hidden_fee_ids"] for p in g["per_cand"].values()))
    cid = next(c for c, p in g["per_cand"].items() if p["hidden_fee_ids"])
    n_gold = len(M.gold_triples(g))
    out = perfect(g)
    out["per_cand"][cid]["hidden_fees"] = []                    # miss one
    out["per_cand"][cid]["conflicts"].append("dim_divisor")     # spurious (maybe already gold)
    r = M.score_task(out, g)
    gt = M.gold_triples(g)
    spurious = (cid, "conflict", "dim_divisor") not in gt
    assert r["fn_hidden_fee"] >= 1
    assert r["fp_conflict"] == (1 if spurious else 0)
    assert r["tp"] + r["fn"] == n_gold


def test_inspection_not_a_hidden_fee():
    g = gold("T-dev-001")
    out = perfect(g)
    out["per_cand"]["C1"]["hidden_fees"] = ["INSP"]
    assert M.score_task(out, g)["fp"] == 0


def test_ddp_risk_only_on_determined():
    g = find(lambda g: any(len(set(p["ddp_levels"])) > 1 for p in g["per_cand"].values()))
    und = next(c for c, p in g["per_cand"].items() if len(set(p["ddp_levels"])) > 1)
    out = perfect(g)
    out["per_cand"][und]["ddp_level"] = "high"
    r = M.score_task(out, g)
    assert r["fp_ddp_risk"] == 0
    def pos(p):
        return len(set(p["ddp_levels"])) == 1 and p["ddp_levels"][0] in ("verify", "high")
    g2 = find(lambda g: any(pos(p) for p in g["per_cand"].values()))
    c2 = next(c for c, p in g2["per_cand"].items() if pos(p))
    out = perfect(g2)
    assert M.score_task(out, g2)["tp_ddp_risk"] >= 1
    out["per_cand"][c2]["ddp_level"] = "none"
    r = M.score_task(out, g2)
    assert r["fn_ddp_risk"] >= 1
    out["per_cand"][c2]["ddp_level"] = "需核实"          # Chinese label counts as positive
    assert M.score_task(out, g2)["fn_ddp_risk"] == 0


def test_ddp_scoring_uses_ddp_levels_not_determined_flag():
    pc_multi = {"determined": True, "ddp_levels": ["none", "high"], "ddp_level": "none"}
    pc_one = {"determined": False, "ddp_levels": ["verify"], "ddp_level": "verify"}
    g = {"per_cand": {"C1": {**pc_multi, "hidden_fee_ids": [], "conflict_ids": [], "missing_ids": []},
                      "C2": {**pc_one, "hidden_fee_ids": [], "conflict_ids": [], "missing_ids": []}}}
    assert M.gold_triples(g) == {("C2", "ddp_risk", "ddp")}
    out = {"per_cand": {"C1": {"ddp_level": "high"}, "C2": {"ddp_level": "高"}}}
    assert M.output_triples(out, g) == {("C2", "ddp_risk", "ddp")}


def test_claim_status_counts_and_chinese_labels():
    g = find(lambda g: any(p["claims"] for p in g["per_cand"].values()))
    cid = next(c for c, p in g["per_cand"].items() if p["claims"])
    out = perfect(g)
    assert M.score_task(out, g)["claim_correct"] == M.score_task(out, g)["claim_n"] >= 1
    out["per_cand"][cid]["claim_status"] = {"0": "与宣称矛盾" if g["per_cand"][cid]["claims"][0]["gold_status"] != "contradicted" else "有证据"}
    r = M.score_task(out, g)
    assert r["claim_correct"] == r["claim_n"] - 1
    out["per_cand"][cid]["claim_status"] = None
    assert M.score_task(out, g)["claim_correct"] <= r["claim_n"] - 1


def test_cost_error_absolute_and_relative():
    g = gold("T-dev-001")
    out = perfect(g)
    tot = float(g["per_cand"]["C1"]["total_cny"])
    out["per_cand"]["C1"]["cost_cny"] = tot + 100
    out["per_cand"]["C2"]["cost_cny"] = None
    r = M.score_task(out, g)
    assert r["cost_missing"] == 1
    assert r["cost_abs_sum"] == pytest.approx(100)
    assert r["cost_rel_sum"] == pytest.approx(100 / tot)
    assert r["cost_exact"] == r["cost_n"] - 2


def test_parse_failure_is_all_wrong():
    g = find(lambda g: g["decision"]["type"] == "choose" and M.gold_triples(g))
    out = {"task_id": g["task_id"], "method": "M0", "config_id": "x", "parse_ok": False, "decision": None}
    r = M.score_task(out, g)
    assert r["parse_ok"] == 0 and r["decision_correct"] == 0
    assert r["tp"] == 0 and r["fp"] == 0 and r["fn"] == len(M.gold_triples(g))
    assert r["cost_missing"] == r["cost_n"]


def test_f1_from_counts():
    assert M.f1_from_counts(2, 1, 1) == pytest.approx(2 / 3)
    assert M.f1_from_counts(0, 0, 0) is None


# ------------------------------------------------------------------ lexicon
@pytest.mark.parametrize("text,expected", [
    ("报价2要另收燃油附加费", {("C2", "hidden_fee", "fuel_pct")}),
    ("报价1有住宅派送费和偏远地区附加费，容易被忽略", {("C1", "hidden_fee", "surcharges")}),
    ("报价3脚注里还有私宅投递费、边远地区派送费", {("C3", "hidden_fee", "surcharges")}),
    ("报价1: Residential and Remote area surcharge in footnote", {("C1", "hidden_fee", "surcharges")}),
    ("报价2另加ISF申报费", {("C2", "hidden_fee", "isf_fee_cny")}),
    ("报价4还要加收清关费", {("C4", "hidden_fee", "broker_fee_cny")}),
    ("报价1的包税价偏低，可能事后补税", {("C1", "ddp_risk", "ddp")}),
    ("报价3说 DDP 但价格太低，风险很大", {("C3", "ddp_risk", "ddp")}),
    ("报价2的体积重除数前后矛盾", {("C2", "conflict", "dim_divisor")}),
    ("报价1燃油费率两处写法不一致", {("C1", "conflict", "fuel_pct")}),
    ("报价2没有写最低计费重", {("C2", "gap", "min_kg_shipment")}),
    ("报价3未说明是否包税", {("C3", "gap", "ddp")}),
])
def test_lexicon_samples(text, expected):
    assert set(M.map_free_text_risks({"free_text_risks": [text]})) == expected


def test_lexicon_skips_unattributed_and_contingent():
    assert M.map_free_text_risks({"free_text_risks": ["燃油附加费要注意"]}) == []
    assert M.map_free_text_risks({"free_text_risks": ["报价1可能产生查验费"]}) == []
    tr, un = M.map_free_text_risks_detail({"risks": ["燃油附加费要注意"]})
    assert tr == [] and un == ["燃油附加费要注意"]


def test_lexicon_mapping_and_multiple_refs():
    out = {"free_text_risks": ["报价1和报价3都要另收燃油"]}
    got = M.map_free_text_risks(out, {"报价1": "C1", "报价3": "C4"})
    assert set(got) == {("C1", "hidden_fee", "fuel_pct"), ("C4", "hidden_fee", "fuel_pct")}
    assert M.LEXICON["version"] == M.LEXICON_VERSION
    assert M.LEXICON["map"] is M.map_free_text_risks


def test_free_text_scored_through_score_task():
    g = find(lambda g: any("fuel_pct" in i for p in g["per_cand"].values() for i in p["hidden_fee_ids"]))
    cid = next(c for c, p in g["per_cand"].items() if any("fuel_pct" in i for i in p["hidden_fee_ids"]))
    k = cid[1:]
    out = {"task_id": g["task_id"], "method": "M0", "config_id": "x", "decision": "choose",
           "choice": g["decision"]["choice"], "parse_ok": True,
           "free_text_risks": [f"报价{k}的燃油附加费另收"]}
    r = M.score_task(out, g)
    assert r["tp_hidden_fee"] == 1 and r["fp"] == 0


# ------------------------------------------------------------------ aggregation and endpoints
def make_rows(acc, f1p, configs=M.MAIN_CONFIGS, n_tasks=12, layers=("explicit", "implicit", "conflict", "insufficient")):
    """Synthetic scored rows. acc[(method, config)] = correct fraction; f1p[method] = (tp, fp, fn) per unit."""
    rows = []
    for method in ("M0", "M4"):
        for cfg in configs:
            for i in range(n_tasks):
                a = acc[(method, cfg)]
                correct = 1 if (i % 10) < round(a * 10) else 0
                tp, fp, fn = f1p[method]
                rows.append({"task_id": f"T{i:02d}", "method": method, "config_id": cfg,
                             "layer": layers[i % 4], "decision_correct": correct, "gold_type": "choose",
                             "tp": tp + (i % 2), "fp": fp, "fn": fn, "parse_ok": 1})
    return rows


def test_aggregate_counts_and_ci():
    rows = []
    for i in range(10):
        rows.append({"task_id": f"T{i}", "method": "M4", "config_id": "c", "layer": "implicit",
                     "decision_correct": 1 if i < 7 else 0, "gold_type": "choose",
                     "tp": 2, "fp": 1, "fn": 1, "parse_ok": 1})
    t = M.aggregate(rows, B=300)
    assert len(t) == 1
    r = t[0]
    assert r["decision_correct"] == pytest.approx(0.7)
    assert r["micro_f1"] == pytest.approx(2 * 20 / (40 + 10 + 10))
    assert r["precision"] == pytest.approx(20 / 30) and r["recall"] == pytest.approx(20 / 30)
    assert r["decision_correct_lo"] <= 0.7 <= r["decision_correct_hi"]
    assert r["n_tasks"] == 10 and r["regret_abs_mean"] is None
    assert M.aggregate(rows, by=("method",), ci=False)[0]["n_rows"] == 10


def test_endpoints_expected_direction():
    acc = {(m, c): 0.9 if m == "M4" else 0.4 for m in ("M0", "M4") for c in M.MAIN_CONFIGS}
    rows = make_rows(acc, {"M4": (6, 1, 1), "M0": (2, 3, 5)}, n_tasks=40)
    res = M.primary_endpoints(rows, sims=2000, B=500)
    assert res["E1"]["mean_difference"] > 0.3 and res["E1"]["p"] < 0.01
    assert res["E1"]["n"] == 30 and res["E1"]["n_pairs"] == 6 * 30   # explicit layer excluded; unit = task
    assert res["E2"]["difference"] > 0.2 and res["E2"]["p"] < 0.01
    assert res["E2"]["n"] == 40
    assert set(res["p_holm"]) == {"E1", "E2", "E3"}
    assert all(res["p_holm"][k] >= res["p_raw"][k] for k in res["p_raw"])
    assert res["reject"]["E1"] and res["reject"]["E2"]
    assert res["E3"]["noninferior_unadjusted"] is True and res["conclusion"]["E3"]["reject"] and res["E3"]["p"] < 0.05
    assert res["E3"]["n_clusters"] == 30
    assert set(res["E3"]["tango"]) == {"ds_flash_vs_ds_pro", "qwen_plus_vs_qwen_max"}


def test_endpoint1_no_difference_and_exact():
    acc = {(m, c): 0.6 for m in ("M0", "M4") for c in M.MAIN_CONFIGS}
    rows = make_rows(acc, {"M4": (3, 1, 1), "M0": (3, 1, 1)})
    res = M.endpoint1(rows, sims=2000)
    assert res["mean_difference"] == 0 and res["p"] == pytest.approx(1.0, abs=0.01)
    res2 = M.endpoint2(rows, sims=2000)
    assert res2["difference"] == pytest.approx(0) and res2["p"] > 0.5


def test_endpoint3_inferior_case():
    acc = {(m, c): 0.9 for m in ("M0", "M4") for c in M.MAIN_CONFIGS}
    acc[("M4", "ds_flash")] = 0.3
    acc[("M4", "qwen_plus")] = 0.3
    rows = make_rows(acc, {"M4": (3, 1, 1), "M0": (3, 1, 1)})
    e3 = M.endpoint3(rows, B=500)
    assert e3["noninferior_unadjusted"] is False and e3["p"] > 0.5 and e3["estimate"] < -0.4
    assert not any(t["p"] < 0.05 for t in e3["tango"].values())


def test_primary_endpoints_needs_layers():
    rows = [{"task_id": "T", "method": "M0", "config_id": "ds_pro", "layer": None, "decision_correct": 1,
             "tp": 0, "fp": 0, "fn": 0}]
    with pytest.raises(ValueError):
        M.primary_endpoints(rows, sims=10, B=10)


def test_end_to_end_on_gold_files():
    """Perfect M4 vs wrong-choice M0 on real gold files: M4 wins on E1."""
    gs = [json.load(open(f)) for f in sorted(glob.glob(str(GOLD_DIR / "*.json")))]
    rows = []
    for g in gs:
        for cfg in M.MAIN_CONFIGS:
            r4 = M.score_task(perfect(g, "M4", cfg), g)
            bad = perfect(g, "M0", cfg)
            bad.update(decision="choose", choice="C1" if g["decision"]["choice"] != "C1" else "C2",
                       ask_fields=[], per_cand={})
            r0 = M.score_task(bad, g)
            for r in (r4, r0):
                r["layer"] = "implicit"
            rows += [r4, r0]
    res = M.primary_endpoints(rows, sims=3000, B=300)
    assert res["E1"]["acc_M4"] == 1.0 and res["E1"]["acc_M0"] < 0.2
    assert res["E1"]["p"] < 0.01
    assert res["E2"]["f1_M4"] == 1.0 and res["E2"]["f1_M0"] == 0.0
    assert not math.isnan(res["E3"]["lower"])


def test_attach_layers(tmp_path):
    (tmp_path / "T1.json").write_text(json.dumps({"layer": "conflict"}))
    rows = [{"task_id": "T1", "x": 1}]
    got = M.attach_layers(rows, tmp_path)
    assert got[0]["layer"] == "conflict" and "layer" not in rows[0]
    real = Path(__file__).resolve().parents[1] / "data" / "tasks" / "dev" / "tasks"
    if (real / "T-dev-001.json").exists():
        assert M.attach_layers([{"task_id": "T-dev-001"}], real)[0]["layer"] in ("explicit", "implicit", "conflict", "insufficient")


def test_hidden_fee_is_recall_only():
    g = gold("T-dev-001")      # no hidden fees in gold for C1
    out = perfect(g)
    out["per_cand"]["C1"]["hidden_fees"] = ["fuel_pct", "ISF"]
    r = M.score_task(out, g)
    assert r["fp_hidden_fee"] == 0 and r["fp"] == 0 and r["fp_unscored_hidden_fee"] == 2
    out["per_cand"]["C1"]["conflicts"] = ["dim_divisor"]
    assert M.score_task(out, g)["fp"] == 1
    t = M.metric_values([r])
    assert t["precision_hidden_fee"] is None and t["f1_hidden_fee"] is None


# ------------------------------------------------------------------ review fixes
def test_method_case_and_zero_pairs():
    g = gold("T-dev-001")
    out = perfect(g, method="m4")
    assert M.score_task(out, g)["method"] == "M4"
    rows = [dict(M.score_task(perfect(g, "m0", "ds_pro"), g), layer="implicit")]
    with pytest.raises(ValueError):
        M.primary_endpoints(rows, sims=10, B=10)      # no M4 rows: no pairs


def test_m0_ask_fields_from_questions():
    g = find(lambda g: g["decision"]["type"] == "ask" and len(g["decision"]["ask_fields"]) == 1)
    cid, field = g["decision"]["ask_fields"][0].split(".")
    asked = {"dim_divisor": "体积重除数", "min_kg_shipment": "整票最低计费重", "broker_fee_cny": "清关费",
             "ddp": "是否包税", "fuel_pct": "燃油附加费", "isf_fee_cny": "ISF申报费", "surcharges": "附加费"}[field]
    out = {"task_id": g["task_id"], "method": "M0", "config_id": "x", "parse_ok": True, "decision": "ask",
           "choice": None, "ask_fields": [], "questions": [f"报价{cid[1:]}的{asked}是多少？"], "risks": []}
    r = M.score_task(out, g)
    assert r["decision_correct"] == 1 and r["ask_fields_used"] == [g["decision"]["ask_fields"][0]]
    out["questions"] = [f"报价{cid[1:]}的有效期多久？"]
    assert M.score_task(out, g)["decision_correct"] == 0
    out["questions"] = [f"{asked}是多少？"]                   # no quote named
    assert M.score_task(out, g)["decision_correct"] == 0
    out.update(method="M1", ask_fields=["???"], questions=[f"请确认报价{cid[1:]}的{asked}"])
    assert M.score_task(out, g)["decision_correct"] == 1
    out.update(questions=[], quote_map={"报价7": cid}, risks=[f"报价7的{asked}没有写明"])
    assert M.score_task(out, g)["decision_correct"] == 1
    out.update(method="M4", ask_fields=[], risks=[f"报价{cid[1:]}的{asked}没有写明"])
    assert M.score_task(out, g)["decision_correct"] == 0   # no fallback for pipeline methods
    assert M.LEXICON["version"] == "1.1" and M.LEXICON["ask_fields"] is M.map_ask_fields


def test_pipeline_scored_despite_parse_failure_but_not_llm():
    g = find(lambda g: g["decision"]["type"] == "choose")
    out = perfect(g, "M3")
    out.update(parse_ok=False, diagnostics={"extraction_failures": {"C1": True, "C2": False}})
    r = M.score_task(out, g)
    assert r["decision_correct"] == 1 and r["parse_failed"] == 1 and r["extraction_failures"] == 1
    out["method"] = "M1"
    r = M.score_task(out, g)
    assert r["decision_correct"] == 0 and r["fn"] == len(M.gold_triples(g))
    t = M.metric_values([M.score_task(perfect(g, "M3"), g), M.score_task(dict(out, method="M3"), g)])
    assert t["parse_failure_rate"] == 0.5 and t["extraction_failures"] == 1


def test_claim_keys_zero_based():
    g = find(lambda g: any(p["claims"] for p in g["per_cand"].values()))
    cid = next(c for c, p in g["per_cand"].items() if p["claims"])
    out = perfect(g)
    st = g["per_cand"][cid]["claims"][0]["gold_status"]
    out["per_cand"][cid]["claim_status"] = {1: st}        # key 1 is the second claim, none exists
    assert M.score_task(out, g)["claim_correct"] < M.score_task(perfect(g), g)["claim_correct"]
    out["per_cand"][cid]["claim_status"] = {"claim1": st}
    assert M.score_task(out, g)["claim_correct"] == M.score_task(perfect(g), g)["claim_correct"]


def test_permutation_unit_is_task():
    """All configs of a task flip together: 4 tasks, M4 better everywhere, exact p = 2/16."""
    rows = []
    for i in range(4):
        for cfg in M.MAIN_CONFIGS:
            for m, c in (("M4", 1), ("M0", 0)):
                rows.append({"task_id": f"T{i}", "method": m, "config_id": cfg, "layer": "implicit",
                             "decision_correct": c, "tp": 1 if m == "M4" else 0, "fp": 0, "fn": 0 if m == "M4" else 1})
    e1 = M.endpoint1(rows)
    assert e1["method"] == "exact" and e1["n"] == 4 and e1["p"] == pytest.approx(2 / 16)
    assert M.endpoint2(rows)["p"] == pytest.approx(2 / 16)


# ------------------------------------------------------------------ end to end with real pipeline outputs
def _e2e_env():
    from quoteguard import methods_llm as ml, methods_pipeline as mp
    from quoteguard.inputs import QuoteInput, TaskInput
    from quoteguard.params import load_snapshot
    from quoteguard.schema import Task, QuoteTerms
    from quoteguard.taskgen import load_inputs, model_registry_snapshot
    root = Path(__file__).resolve().parents[1]
    inp = load_inputs()
    snap = load_snapshot(root / "data" / "params" / "tariff_snapshot_2026-10-09.json")
    reg = model_registry_snapshot(inp["registry"], "dev")
    return ml, mp, QuoteInput, TaskInput, Task, QuoteTerms, inp, snap, reg, root


def test_end_to_end_real_output_formats():
    ml, mp, QuoteInput, TaskInput, Task, QuoteTerms, inp, snap, reg, root = _e2e_env()
    tasks_dir = root / "data" / "tasks" / "dev" / "tasks"
    rows = []
    for f in sorted(tasks_dir.glob("*.json")):
        t = Task.from_dict(json.load(open(f, encoding="utf-8")))
        g = json.load(open(GOLD_DIR / f.name, encoding="utf-8"))
        ti = TaskInput(t.task_id, "货物说明", [QuoteInput(k, c.cand_id, "text", f"报价原文 {c.cand_id}", [], True)
                                            for k, c in enumerate(t.candidates, 1)])
        ex = {}
        for c in t.candidates:
            terms = QuoteTerms.from_dict(c.presentation["terms"])
            terms.quote_entity = c.presentation.get("quote_entity")
            ex[c.cand_id] = terms
        m4 = mp.run_m4(t, ti, ex, snap, inp["structures"], inp["freight_floor"], reg, {}, inp["value_sets"])
        text = '分析如下。\n{"choice": "报价1", "risks": ["报价1的燃油附加费另收"], "questions": []}'
        m0 = ml.to_method_output(ml.parse_final_json(text), ti, "m0", "x", [], text)
        for cfg in M.MAIN_CONFIGS:
            for o in (m4, m0):
                rows.append(M.score_task(dict(o, config_id=cfg), g))
    rows = M.attach_layers(rows, tasks_dir)
    assert {r["method"] for r in rows} == {"M0", "M4"} and all(r["layer"] for r in rows)
    res = M.primary_endpoints(rows, sims=500, B=200)
    assert res["E1"]["n"] > 0 and res["E2"]["n"] == 24 and res["E3"]["n_clusters"] > 0
    assert res["E1"]["acc_M4"] > res["E1"]["acc_M0"]
    assert res["E2"]["f1_M4"] > res["E2"]["f1_M0"]
    assert set(res["conclusion"]) == {"E1", "E2", "E3"}
    assert M.aggregate(rows, ci=False)
