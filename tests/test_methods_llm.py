import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from quoteguard import executor, prompts
from quoteguard import methods_llm as M
from quoteguard import taskgen as tg
from quoteguard.inputs import QuoteInput, TaskInput, build_task_input, render_task
from quoteguard.llm import load_models
from quoteguard.params import load_snapshot
from quoteguard.schema import Task

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "plan" / "方法实现规格.md"
TASK_DIR = ROOT / "data" / "tasks" / "dev" / "tasks"


# ---------------------------------------------------------------- prompts
def spec_blocks():
    return re.findall(r"```\n(.*?)\n```", SPEC.read_text(encoding="utf-8"), re.S)


def test_prompts_verbatim_from_spec():
    b = spec_blocks()
    assert (prompts.TRANSCRIBE, prompts.M0, prompts.M1, prompts.EXTRACT) == (b[1], b[2], b[3], b[4])


def test_fill():
    assert prompts.fill(prompts.TRANSCRIBE) == prompts.TRANSCRIBE
    out = prompts.fill(prompts.M0, shipment_text="S {quotes_text}", quotes_text="Q")
    assert "S {quotes_text}" in out and '{"choice"' in out and "{shipment_text}" not in out
    with pytest.raises(KeyError):
        prompts.fill(prompts.M0, shipment_text="x")
    with pytest.raises(KeyError):
        prompts.fill(prompts.M0, shipment_text="x", quotes_text="y", extra="z")
    assert prompts.placeholders(prompts.EXTRACT) == ["quote_text"]
    assert prompts.fill(prompts.EXTRACT, quote_text="q").count("q") >= 1


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def models():
    return load_models()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    inp = tg.load_inputs()
    snap = load_snapshot(tg.DEFAULT_SNAPSHOT_PATH)
    t = None
    for f in sorted(TASK_DIR.glob("*.json")):
        cand = Task.from_dict(json.loads(f.read_text(encoding="utf-8")))
        if any(c.presentation["modality"] == "image" for c in cand.candidates):
            t = cand
            break
    assert t is not None
    res = render_task(t, tmp_path_factory.mktemp("r"))
    return {"inp": inp, "snap": snap, "task": t, "ti": build_task_input(t, res),
            "reg": tg.model_registry_snapshot(inp["registry"], "dev")}


def obs_for(req, text, valid=True):
    return {"run_key": req["run_key"], "task_id": req["task_id"], "stage": req["stage"],
            "cand_id": req["cand_id"], "config_id": req["config_id"], "text": text, "valid": valid}


def plan_for(requests, rates_cfgs, tmp_path):
    rates = {c: {"currency": "CNY", "input": 1, "output": 2} for c in rates_cfgs}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "t", "kind": "dev", "budget": {"CNY": 10}, "rates": rates,
                             "requests": requests}, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- transcription
def test_transcription_requests(env, models, tmp_path):
    ti = env["ti"]
    reqs = M.transcription_requests(ti, "ds_pro", models, experiment="dev1")
    assert len(reqs) == len(ti.image_quotes()) >= 1
    for r, q in zip(reqs, ti.image_quotes()):
        assert r["method"] == "transcribe" and r["stage"] == "transcribe"
        assert r["config_id"] == models["ds_pro"].vision_config_id == "ds_flash"
        assert r["cand_id"] == q.cand_id and r["max_tokens"] == 1200
        assert r["text"] == prompts.TRANSCRIBE
        assert [i["path"] for i in r["images"]] == [str(Path(p).resolve()) for p in q.images]
        assert all(len(i["sha256"]) == 64 and i["mime"] == "image/png" for i in r["images"])
    plan = plan_for(reqs, ["ds_flash"], tmp_path)
    assert len(executor.load_plan(plan, models)["requests"]) == len(reqs)


def test_multipage_transcription_one_request(env, models):
    ti = TaskInput("T", "s", [QuoteInput(1, "C1", "image", None, [str(p) for p in env["ti"].image_quotes()[0].images] * 2)])
    reqs = M.transcription_requests(ti, "qwen_plus", models, experiment="e")
    assert len(reqs) == 1 and len(reqs[0]["images"]) == 2
    assert reqs[0]["config_id"] == "qwen_vl"


def test_apply_transcriptions(env, models):
    ti = env["ti"]
    reqs = M.transcription_requests(ti, "ds_pro", models, experiment="dev1")
    obs = [obs_for(reqs[0], "<|begin_of_box|>FWD-1 | 价格<|end_of_box|>\n")]
    obs += [obs_for(r, "x", valid=False) for r in reqs[1:]]
    new = M.apply_transcriptions(ti, obs, "ds_flash")
    assert not ti.image_quotes() == [] and new.image_quotes() == []   # original untouched
    q0 = next(q for q in new.quotes if q.cand_id == reqs[0]["cand_id"])
    assert q0.text == "FWD-1 | 价格" and q0.transcribed and not q0.transcript_failed and q0.images
    for r in reqs[1:]:
        q = next(q for q in new.quotes if q.cand_id == r["cand_id"])
        assert q.text == "[图片转写失败]" and q.transcript_failed
    assert "报价1" in new.quotes_text()
    # a dict keyed by run_key works and a missing observation fails the quote
    new2 = M.apply_transcriptions(ti, {}, "ds_flash")
    assert all(q.text == "[图片转写失败]" for q in new2.quotes if q.modality == "image")
    # observations of other configs are ignored when filtered
    new3 = M.apply_transcriptions(ti, obs, vision_config_id="other")
    assert all(q.transcript_failed for q in new3.quotes if q.modality == "image")


# ---------------------------------------------------------------- M0 / M1 requests
def test_m0_m1_requests_pass_load_plan(env, models, tmp_path):
    ti0 = env["ti"]
    reqs = M.transcription_requests(ti0, "qwen_max", models, experiment="dev1")
    ti = M.apply_transcriptions(ti0, [obs_for(r, "转写") for r in reqs], "qwen_vl")
    r0 = M.m0_request(ti, "qwen_max", "dev1")
    hts = env["task"].shipment.goods[0].hts
    r1 = M.m1_request(ti, "qwen_max", "dev1", snapshot=env["snap"], freight_floor=env["inp"]["freight_floor"],
                      hts=hts, registry_snapshot=env["reg"], value_sets=env["inp"]["value_sets"])
    assert r0["max_tokens"] == 4000 and r1["max_tokens"] == 12000
    assert r0["method"] == "M0" and r1["method"] == "M1" and r0["images"] == r1["images"] == []
    for r in (r0, r1):
        assert ti.shipment_text in r["text"] and ti.quotes_text() in r["text"]
        assert not re.search(r"\{[a-z_0-9]+\}", r["text"])
    assert "汇率：1 美元 = 6.733 元人民币" in r1["text"] and "SYN-" in r1["text"]
    assert "计泡系数 5000 或 6000" in r1["text"] and "{value_sets_text}" not in r1["text"]
    assert "之一；前后矛盾的条款取原文两个值" in r1["text"] and "。；" not in r1["text"]
    assert "海关企业信用记录不能证明无船承运业务备案" in r1["text"]
    plan = plan_for([r0, r1], ["qwen_max"], tmp_path)
    assert len(executor.load_plan(plan, models)["requests"]) == 2
    assert r0["run_key"] == executor.make_run_key("dev1", "M0", "qwen_max", ti.task_id, "answer", "ALL", 0)


def test_apply_transcriptions_requires_vision_config(env):
    with pytest.raises(TypeError):
        M.apply_transcriptions(env["ti"], [])


def test_requests_refuse_untranscribed_images(env):
    with pytest.raises(ValueError):
        M.m0_request(env["ti"], "qwen_max", "dev1")


# ---------------------------------------------------------------- parse_final_json
def test_parse_final_json_formats():
    assert M.parse_final_json('分析...\n{"choice": "报价2", "risks": []}') == {"choice": "报价2", "risks": []}
    fenced = '结论如下\n```json\n{"choice": "报价1", "questions": ["a"]}\n```\n'
    assert M.parse_final_json(fenced)["choice"] == "报价1"
    two = '先 {"choice": "报价1"} 后来更正\n{"choice": "需要追问", "ask_fields": ["报价2.计泡系数"]}'
    assert M.parse_final_json(two)["choice"] == "需要追问"
    nested = '{"choice": "报价1", "per_quote": {"报价1": {"cost_cny": 1}}}'
    assert M.parse_final_json(nested)["choice"] == "报价1"
    noise = '{坏的} {"choice": "报价3"} 末尾 {oops'
    assert M.parse_final_json(noise) == {"choice": "报价3"}
    assert M.parse_final_json("没有 JSON") is None
    assert M.parse_final_json('{"choice": "报价1", "risks": [') is None
    assert M.parse_final_json('{"a": 1}') is None
    assert M.parse_final_json('{"a": 1}', require_key=None) == {"a": 1}
    assert M.parse_final_json(None) is None


# ---------------------------------------------------------------- to_method_output
def make_ti(n=3):
    return TaskInput("T-x", "s", [QuoteInput(k, f"C{k}", "text", f"q{k}", [], True) for k in range(1, n + 1)])


def test_output_choose_m0():
    ti = make_ti()
    p = M.parse_final_json('{"choice": "报价2", "risks": ["燃油另收", "ISF"], "questions": ["有效期？"]}')
    o = M.to_method_output(p, ti, "m0", "ds_pro", ["k1"])
    assert o["method"] == "M0"
    assert (o["decision"], o["choice"], o["parse_ok"], o["calls"]) == ("choose", "C2", True, ["k1"])
    assert o["risks"] == ["燃油另收", "ISF"] and o["questions"] == ["有效期？"]
    assert set(o["per_cand"]) == {"C1", "C2", "C3"} and o["per_cand"]["C1"]["cost_cny"] is None
    assert o["task_id"] == "T-x" and o["cost"] == {"USD": 0.0, "CNY": 0.0}


def test_output_ask_m1_full():
    ti = make_ti()
    p = {"choice": "需要追问",
         "ask_fields": ["报价2.计泡系数", "报价3.包税条款", "报价1.min_kg_shipment", "C3.ddp", "报价9.计泡系数", "杂项"],
         "per_quote": {
             "报价1": {"cost_cny": 1234.5, "ddp_level": "无", "claims": ["有证据", "与宣称矛盾"],
                      "hidden_fees": ["超重费"], "conflicts": [], "gaps": []},
             "报价2": {"cost_cny": "约 9,876.50 元", "ddp_level": "需核实", "claims": ["证据不足"],
                      "hidden_fees": [], "conflicts": ["有效期"], "gaps": ["计泡系数", "最低计费重"]},
             "报价3": {"cost_cny": None, "ddp_level": "高", "claims": ["与本次运输不相关"]},
             "报价7": {"cost_cny": 1}},
         "questions": ["q"]}
    o = M.to_method_output(p, ti, "m1", "ds_pro", ["a", "b"])
    assert o["decision"] == "ask" and o["choice"] is None and o["parse_ok"]
    assert o["ask_fields"] == ["C2.dim_divisor", "C3.ddp", "C1.min_kg_shipment", "C3.ddp", "报价9.计泡系数", "杂项"]
    c1, c2, c3 = (o["per_cand"][c] for c in ("C1", "C2", "C3"))
    assert c1["cost_cny"] == Decimal("1234.5") and c1["ddp_level"] == "none"
    assert c1["claim_status"] == {0: "supported", 1: "contradicted"} and c1["hidden_fees"] == ["超重费"]
    assert c2["cost_cny"] == Decimal("9876.50") and c2["ddp_level"] == "verify"
    assert c2["claim_status"] == {0: "insufficient"} and c2["conflicts"] == ["validity"]
    assert c2["gaps"] == ["dim_divisor", "min_kg_shipment"]
    assert c3["cost_cny"] is None and c3["ddp_level"] == "high" and c3["claim_status"] == {0: "irrelevant"}


def test_output_choice_variants():
    ti = make_ti()
    for choice, cand in (("报价 3", "C3"), ("2", "C2"), ("C1", "C1"), ("报价1", "C1")):
        o = M.to_method_output({"choice": choice}, ti, "m0", "c", [])
        assert o["choice"] == cand and o["decision"] == "choose"
    o = M.to_method_output({"choice": "需要追问（信息不足）"}, ti, "m0", "c", [])
    assert o["decision"] == "ask"


def test_choice_parsing_rules():
    ti = make_ti(4)
    cases = {
        "**报价二**": ("choose", "C2"), "报价2（比报价1便宜，但需确认有效期）": ("choose", "C2"),
        "报价十": None, "`报价3`": ("choose", "C3"), "报价四": ("choose", "C4"),
        "报价1或报价2": ("ask", None), "报价1、报价3": ("ask", None),
        "如果时效放宽选报价2": ("ask", None), "若ISF另收则报价1": ("ask", None),
        "假如无燃油选报价3": ("ask", None), "取决于包税条款，报价2或报价4": ("ask", None),
        "**需要追问**": ("ask", None),
    }
    for raw, exp in cases.items():
        o = M.to_method_output({"choice": raw}, ti, "M0", "c", [])
        if exp is None:
            assert not o["parse_ok"], raw
        else:
            assert (o["decision"], o["choice"], o["parse_ok"]) == (exp[0], exp[1], True), raw
    assert M._num("十二") == 12 and M._num("二十") == 20 and M._num("三十五") == 35 and M._num("7") == 7
    assert M._num("两") == 2 and M._num("百") is None


def test_ask_field_delimiters_and_keywords():
    ti = make_ti(3)
    for raw, exp in {"报价1.dim_divisor": "C1.dim_divisor", "报价2的计泡系数": "C2.dim_divisor",
                     "报价3-燃油附加费": "C3.fuel_pct", "报价1—ISF费用": "C1.isf_fee_cny",
                     "报价二：最低计费重": "C2.min_kg_shipment", "报价1.整票起运重量": "C1.min_kg_shipment",
                     "报价2 报关手续费": "C2.broker_fee_cny", "报价3.是否包税": "C3.ddp",
                     "**报价1.有效期限**": "C1.validity", "报价2.重量档单价": "C2.rate_tiers",
                     "报价1.单箱最低计费": "C1.min_kg_carton", "报价1.奇怪条款": "C1.奇怪条款"}.items():
        o = M.to_method_output({"choice": "需要追问", "ask_fields": [raw]}, ti, "M1", "c", [])
        assert o["ask_fields"] == [exp], raw
    assert M.term_name("清关费") == "broker_fee_cny" and M.term_name("体积重") == "dim_divisor"


def test_output_parse_failures():
    ti = make_ti()
    o = M.to_method_output(None, ti, "m1", "c", ["k"], raw_text="乱码")
    assert o["parse_ok"] is False and o["parse_error"] and o["raw_text"] == "乱码"
    assert o["decision"] is None and o["choice"] is None and o["calls"] == ["k"]
    o = M.to_method_output({"choice": "报价99"}, ti, "m0", "c", [])
    assert o["parse_ok"] is False and "报价99" in o["parse_error"]
    o = M.to_method_output({"risks": []}, ti, "m0", "c", [])
    assert o["parse_ok"] is False
    # malformed optional fields do not break parsing
    o = M.to_method_output({"choice": "报价1", "ask_fields": "报价1.有效期", "per_quote": {"报价1": {"cost_cny": "abc", "claims": "x"}}},
                           ti, "m1", "c", [])
    assert o["parse_ok"] and o["ask_fields"] == ["C1.validity"] and o["quote_map"]["报价1"] == "C1"
    assert o["per_cand"]["C1"]["cost_cny"] is None and o["per_cand"]["C1"]["claim_status"] is None


def test_term_name_map():
    assert M.term_name("体积重除数") == "dim_divisor" and M.term_name("ISF") == "isf_fee_cny"
    assert M.term_name("ddp") == "ddp" and M.term_name("奇怪条款") == "奇怪条款"


def test_dedupe_requests(env, models):
    reqs = M.transcription_requests(env["ti"], "ds_pro", models, experiment="e")
    reqs2 = M.transcription_requests(env["ti"], "ds_flash", models, experiment="e")
    assert [r["run_key"] for r in reqs] == [r["run_key"] for r in reqs2]
    assert M.dedupe_requests(reqs + reqs2) == reqs
    bad = dict(reqs[0], prompt_sha256="0" * 64)
    with pytest.raises(ValueError):
        M.dedupe_requests(reqs + [bad])
