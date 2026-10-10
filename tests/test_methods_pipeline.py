import copy
import glob
import itertools
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from quoteguard import executor, methods_pipeline as mp, prompts
from quoteguard.calc_py import landed_cost
from quoteguard.decide import view_from_task
from quoteguard.inputs import QuoteInput, TaskInput
from quoteguard.params import load_snapshot
from quoteguard.render import LEX
from quoteguard.schema import _TERM_CODECS, Field, Gold, QuoteTerms, Span, Task
from quoteguard.taskgen import load_inputs, model_registry_snapshot, true_terms

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "params" / "tariff_snapshot_2026-10-09.json"


@pytest.fixture(scope="module")
def env():
    inp = load_inputs()
    pairs = []
    for f in sorted(glob.glob(str(ROOT / "data" / "tasks" / "dev" / "tasks" / "*.json"))):
        t = Task.from_dict(json.load(open(f, encoding="utf-8")))
        g = Gold.from_dict(json.load(open(ROOT / "data" / "gold" / "dev" / Path(f).name, encoding="utf-8")))
        pairs.append((t, g))
    return {"inp": inp, "snap": load_snapshot(SNAP), "reg": model_registry_snapshot(inp["registry"], "dev"),
            "pairs": pairs}


def ti_of(task, texts=None):
    return TaskInput(task.task_id, "货物说明", [
        QuoteInput(k, c.cand_id, "text", (texts or {}).get(c.cand_id, f"报价原文 {c.cand_id}"), [], True)
        for k, c in enumerate(task.candidates, 1)])


def shown(task):
    """Perfect extraction that keeps conflicting and missing statuses (what the quote text shows)."""
    out = {}
    for c in task.candidates:
        t = QuoteTerms.from_dict(c.presentation["terms"])
        t.quote_entity = c.presentation.get("quote_entity")
        out[c.cand_id] = t
    return out


def truth(task, gold):
    out = {}
    for c in task.candidates:
        t = true_terms(task, gold, c.cand_id)
        t.quote_entity = c.presentation.get("quote_entity")
        out[c.cand_id] = t
    return out


def raw_from(terms, quotes=None):
    """Extraction-format dict (status/value/quotes) for a QuoteTerms object."""
    d = terms.to_dict()
    out = {}
    for n, f in d.items():
        if n == "channel":
            continue
        out[n] = {"status": f["status"], "value": f["value"],
                  "quotes": (quotes or {}).get(n, [s["quote"] for s in f["spans"]])}
    out["quote_entity"] = {"status": "stated", "value": getattr(terms, "quote_entity", None), "quotes": []}
    return out


def first_where(env, pred, what):
    for t, g in env["pairs"]:
        r = pred(env, t, g)
        if r:
            return (t, g) if r is True else (t, g, r)
    raise AssertionError(f"no dev task satisfies: {what}")


def _explicit_choose(env, t, g):
    return t.layer == "explicit" and g.decision["type"] == "choose"


def _dim_gap_ask(env, t, g):
    """Insufficient-layer ask task whose only follow-up is a missing dim_divisor: returns the cand id."""
    if g.decision["type"] != "ask" or len(g.followup_gap_ids) != 1:
        return None
    cid, name = g.followup_gap_ids[0].split(".")
    return cid if name == "dim_divisor" and g.per_cand[cid]["missing_ids"] else None


def _m2_misled_by_gate(env, t, g):
    if not _explicit_choose(env, t, g) or not any(pc["ddp_level"] == "high" for pc in g.per_cand.values()):
        return False
    out = mp.run_m2(t, ti_of(t), truth(t, g), env["snap"])
    return out["choice"] != g.decision["choice"]


def _conflict_cand(env, t, g):
    """A candidate with scalar conflicting terms whose two values give different costs: (cand_id, names)."""
    if t.layer != "conflict":
        return None
    for c in t.candidates:
        terms = QuoteTerms.from_dict(c.presentation["terms"])
        names = [n for n in terms.conflicting() if n in ("broker_fee_cny", "fuel_pct", "dim_divisor", "min_kg_shipment")]
        if names and len(names) == len(terms.conflicting()):
            costs = {landed_cost(t.shipment, mp._fix(terms, names, combo), env["snap"]).total_cny
                     for combo in itertools.product((0, 1), repeat=len(names))}
            if len(costs) > 1:
                return c.cand_id, names
    return None


def _triggered_with_quiet(env, t, g):
    if t.layer not in ("explicit", "implicit") or g.decision["type"] != "choose":
        return None
    trig = mp.review_triggers(t, ti_of(t), truth(t, g), env["snap"], env["inp"]["freight_floor"], env["reg"])
    quiet = [c.cand_id for c in t.candidates if c.cand_id not in trig]
    return (trig, quiet) if trig and quiet else None


def _winner_triggered(env, t, g):
    if t.layer not in ("explicit", "implicit") or g.decision["type"] != "choose":
        return None
    trig = mp.review_triggers(t, ti_of(t), truth(t, g), env["snap"], env["inp"]["freight_floor"], env["reg"])
    return True if g.decision["choice"] in trig else None


def by_layer(env, *layers):
    return [(t, g) for t, g in env["pairs"] if t.layer in layers]


def run4(env, task, ex, review=None):
    inp = env["inp"]
    return mp.run_m4(task, ti_of(task), ex, env["snap"], inp["structures"], inp["freight_floor"], env["reg"],
                     review or {}, inp["value_sets"])


def run3(env, task, ex):
    return mp.run_m3(task, ti_of(task), ex, env["snap"], env["inp"]["structures"], env["inp"]["freight_floor"],
                     env["reg"])


# ---------------------------------------------------------------- requests and partners
def test_review_partner_map():
    assert [mp.review_partner(c) for c in ("ds_flash", "qwen_plus", "ds_pro", "qwen_max", "glm", "kimi")] == \
        ["qwen_plus", "ds_flash", "qwen_max", "ds_pro", "kimi", "glm"]
    with pytest.raises(ValueError):
        mp.review_partner("dev_vl")


def test_extraction_requests_shape_and_hash(env):
    task = env["pairs"][0][0]
    ti = ti_of(task)
    reqs = mp.extraction_requests(ti, "ds_flash", "pilot-1")
    assert [r["cand_id"] for r in reqs] == [c.cand_id for c in task.candidates]
    for r, q in zip(reqs, ti.quotes):
        assert set(executor.REQUIRED_FIELDS) <= set(r)
        assert (r["method"], r["stage"], r["max_tokens"], r["repeat"], r["images"]) == ("extract", "extract", 2500, 1, [])
        assert q.text in r["text"] and "{quote_text}" not in r["text"]
        assert r["prompt_sha256"] == executor.prompt_hash(r["system"], r["text"], [], 2500)
        assert r["run_key"] == executor.make_run_key("pilot-1", "extract", "ds_flash", task.task_id, "extract",
                                                     q.cand_id, 1)
    assert mp.extraction_requests(ti, "ds_flash", "pilot-1", repeat=2)[0]["repeat"] == 2


def test_review_requests_only_triggered_and_partner_config(env):
    task = env["pairs"][0][0]
    ti = ti_of(task)
    ids = [c.cand_id for c in task.candidates]
    reqs = mp.review_requests(ti, "ds_flash", "pilot-1", [ids[1], ids[3]])
    assert [r["cand_id"] for r in reqs] == [ids[1], ids[3]]
    assert all(r["config_id"] == "qwen_plus" and r["reviewed_for"] == "ds_flash" and r["stage"] == "review"
               for r in reqs)
    assert mp.review_requests(ti, "ds_flash", "pilot-1", []) == []


# ---------------------------------------------------------------- parsing
def test_parse_extraction_variants():
    obj = {"dim_divisor": {"status": "stated", "value": 6000, "quotes": ["除以6000"]}, "mode": {"status": "missing"}}
    assert mp.parse_extraction(json.dumps(obj)) == obj
    wrapped = "分析如下 {\"a\": 1}\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```\n完毕 {\"b\": 2}"
    assert mp.parse_extraction(wrapped) == obj
    for bad in ("没有 JSON", "{\"x\": 1}", "{broken", 5):
        with pytest.raises(ValueError):
            mp.parse_extraction(bad)


def test_normalize_roundtrips_every_dev_candidate(env):
    for t, _g in env["pairs"]:
        for c in t.candidates:
            src = QuoteTerms.from_dict(c.presentation["terms"])
            got = mp.normalize(raw_from(src), t.shipment.mode)
            a, b = src.to_dict(), got.to_dict()
            for n in _TERM_CODECS:
                if n == "channel":
                    continue
                assert (a[n]["status"], a[n]["value"]) == (b[n]["status"], b[n]["value"]), (t.task_id, c.cand_id, n)


def test_normalize_lexicon_covers_both_pools():
    seen = 0
    for pool in LEX.values():
        for lx in pool.values():
            for code, text in lx["names"].items():
                assert mp.surcharge_code(text) == code, text
                assert mp.surcharge_code(f"  {text.upper()} ") == code
                seen += 1
            for code, text in lx["types"].items():
                assert mp.claim_type_code(text) == code, text
                seen += 1
            for code, text in lx["mode"].items():
                assert mp.mode_code(text) == code, text
                seen += 1
    assert seen == 4 * (5 + 4 + 3)
    # normalize applies the mapping to whole extractions
    raw = {"surcharges": {"status": "stated", "quotes": [], "value": [
        {"code": "私宅投递费", "basis": "per_ctn", "amount": 6, "currency": "USD", "condition": "residential"},
        {"code": "Remote area 地区附加费", "basis": "per_kg", "amount": "8", "currency": "RMB", "condition": "remote"}]},
        "claims": {"status": "stated", "quotes": [], "value": [
            {"type": "FMC OTI 登记的承运人", "entity": "SYN-A", "reg_no": None, "jurisdiction": "美方"}]}}
    t = mp.normalize(raw)
    assert [s.code for s in t.plain("surcharges")] == ["RES", "REM"]
    assert t.plain("surcharges")[1].currency == "CNY"
    c = t.plain("claims")[0]
    assert (c.type, c.jurisdiction) == ("FMC_OTI", "US")


def test_normalize_unmapped_names_and_unknown_claim():
    raw = {"surcharges": {"status": "stated", "quotes": [], "value": [
        {"code": "旺季附加费", "basis": "per_kg", "amount": 1.5, "currency": "CNY"},
        {"code": "旺季附加费", "basis": "??", "amount": 1.5}]},
        "claims": {"status": "stated", "quotes": [], "value": [
            {"type": "ISO 9001 认证", "entity": "SYN-B"}, {"type": "", "entity": "SYN-B"}]}}
    t = mp.normalize(raw)
    s = t.plain("surcharges")
    assert len(s) == 1 and s[0].code == "旺季附加费" and (s[0].basis, s[0].amount, s[0].condition) == ("per_kg", D("1.5"), "always")
    assert [c.type for c in t.plain("claims")] == ["UNKNOWN"]
    assert len(t.norm_notes) >= 3


def test_normalize_numbers_fuel_and_failures():
    raw = {"fuel_pct": {"status": "stated", "value": 10, "quotes": []},
           "dim_divisor": {"status": "stated", "value": "6,000", "quotes": []},
           "min_kg_shipment": {"status": "stated", "value": "不设", "quotes": []},
           "broker_fee_cny": {"status": "stated", "value": "abc", "quotes": []},
           "isf_fee_cny": {"status": "missing", "value": 200, "quotes": []},
           "rounding_kg": {"status": "stated", "value": 0, "quotes": []},
           "transit_days": {"status": "stated", "value": "4-7天", "quotes": []},
           "validity": {"status": "stated", "value": ["2026年10月1日", "2026-10-31"], "quotes": []},
           "ddp": {"status": "stated", "value": {"is_ddp": True, "scope": "all", "duty_terms": "capped"}, "quotes": []}}
    t = mp.normalize(raw, "air")
    assert t.fuel_pct.value == D("0.1")
    assert t.dim_divisor.value == D(6000)
    assert t.min_kg_shipment.value == D(0) and t.min_kg_shipment.status == "stated"
    assert t.broker_fee_cny.status == "missing"
    assert t.isf_fee_cny.status == "missing" and t.isf_fee_cny.value is None
    assert t.rounding_kg.status == "missing"                      # zero is out of range
    assert t.transit_days.value == [4, 7]
    assert t.validity.value == ["2026-10-01", "2026-10-31"]
    assert t.ddp.status == "missing"                              # capped without a cap
    assert (t.mode.value, t.mode.status) == ("air", "implied")    # from the hint
    assert mp.normalize({"fuel_pct": {"status": "stated", "value": "10%"}}).fuel_pct.value == D("0.1")
    assert mp.normalize({"fuel_pct": {"status": "stated", "value": 0.1}}).fuel_pct.value == D("0.1")


def test_normalize_conflicting_values():
    raw = {"dim_divisor": {"status": "conflicting", "value": [5000, 6000], "quotes": ["a", "b"]},
           "fuel_pct": {"status": "conflicting", "value": [10, 0.2], "quotes": []},
           "broker_fee_cny": {"status": "conflicting", "value": 100, "quotes": []},
           "surcharges": {"status": "conflicting", "quotes": [], "value": [
               [{"code": "RES", "basis": "per_ctn", "amount": 4, "currency": "USD"}],
               [{"code": "RES", "basis": "per_ctn", "amount": 7, "currency": "USD"}]]}}
    t = mp.normalize(raw)
    assert t.dim_divisor.status == "conflicting" and t.dim_divisor.value == [D(5000), D(6000)]
    assert [s.quote for s in t.dim_divisor.spans] == ["a", "b"]
    assert t.fuel_pct.value == [D("0.1"), D("0.2")]
    assert t.broker_fee_cny.status == "missing"                   # not a pair
    assert [x[0].amount for x in t.surcharges.value] == [D(4), D(7)]
    assert sorted(t.conflicting()) == ["dim_divisor", "fuel_pct", "surcharges"]


# ---------------------------------------------------------------- M2 / M3
def test_m2_prices_match_gold_for_determined_candidates(env):
    n = 0
    for t, g in by_layer(env, "explicit", "implicit"):
        out = mp.run_m2(t, ti_of(t), truth(t, g), env["snap"], env["inp"]["structures"])
        for cid, pc in g.per_cand.items():
            assert out["per_cand"][cid]["cost_cny"] == D(pc["total_cny"]), (t.task_id, cid)
            assert {h.split(".")[1] for h in pc["hidden_fee_ids"]} <= set(out["per_cand"][cid]["hidden_fees"])
            n += 1
        assert out["parse_ok"] and out["decision"] == "choose"
    assert n > 20


def test_m2_ignores_gates_and_never_asks(env):
    t, g = first_where(env, _m2_misled_by_gate, "explicit task where M2 picks a quote the DDP gate excludes")
    out = mp.run_m2(t, ti_of(t), shown(t), env["snap"])
    assert out["choice"] != g.decision["choice"]      # the M2 choice is a high-risk DDP quote
    assert g.per_cand[out["choice"]]["ddp_level"] == "high"
    assert out["decision"] == "choose" and out["ask_fields"] == []
    assert all(pc["ddp_level"] is None and pc["claim_status"] is None for pc in out["per_cand"].values())


def test_m3_matches_gold_gates_on_explicit_and_implicit(env):
    for t, g in by_layer(env, "explicit", "implicit"):
        out = run3(env, t, truth(t, g))
        assert (out["decision"], out["choice"]) == ("choose", g.decision["choice"]), t.task_id
        for cid, pc in g.per_cand.items():
            o = out["per_cand"][cid]
            assert o["ddp_level"] == pc["ddp_level"], (t.task_id, cid)
            assert list(o["claim_status"].values()) == pc["claim_statuses"], (t.task_id, cid)
            assert o["conflicts"] == [] and o["gaps"] == []
    assert any(pc["ddp_level"] == "high" for _t, g in env["pairs"] for pc in g.per_cand.values())


def test_m2_misses_footnote_fee_and_chooses_wrongly(env):
    wrong = 0
    total = 0
    for t, g in by_layer(env, "implicit"):
        ex = truth(t, g)
        full = mp.run_m3(t, ti_of(t), ex, env["snap"], None, env["inp"]["freight_floor"], env["reg"])
        assert full["choice"] == g.decision["choice"]
        dropped = copy.deepcopy(ex)
        for cid, pc in g.per_cand.items():
            for hid in pc["hidden_fee_ids"]:
                name = hid.split(".")[1]
                setattr(dropped[cid], name, Field([] if name == "surcharges" else D(0), "stated", []))
        out = mp.run_m2(t, ti_of(t), dropped, env["snap"])
        total += 1
        wrong += out["choice"] != g.decision["choice"]
        # the dropped fee lowers the quoted cost of the candidate that carries it
        for cid, pc in g.per_cand.items():
            if pc["hidden_fee_ids"]:
                assert out["per_cand"][cid]["cost_cny"] < D(pc["total_cny"])
    assert total >= 4 and wrong >= 3


def test_m2_conflicting_term_takes_cheaper_value(env):
    t, g, (cid, names) = first_where(env, _conflict_cand, "conflict task with scalar conflicts that change the cost")
    out = mp.run_m2(t, ti_of(t), shown(t), env["snap"])
    pc = out["per_cand"][cid]
    assert pc["conflict_resolved"] and pc["conflicts"] == []
    cand = next(c for c in t.candidates if c.cand_id == cid)
    terms = QuoteTerms.from_dict(cand.presentation["terms"])
    lo = min(landed_cost(t.shipment, mp._fix(terms, names, combo), env["snap"]).total_cny
             for combo in itertools.product((0, 1), repeat=len(names)))
    assert pc["cost_cny"] == lo


def test_m2_unparseable_extraction_is_reported_and_excluded(env):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")
    bad_id = next(c.cand_id for c in t.candidates if c.cand_id != g.decision["choice"])
    ex = truth(t, g)
    ex[bad_id] = "抱歉，我无法处理"
    out = mp.run_m2(t, ti_of(t), ex, env["snap"])
    assert not out["parse_ok"] and bad_id in out["parse_error"]
    assert out["per_cand"][bad_id]["cost_cny"] is None and out["choice"] != bad_id


def test_m2_defaults_for_missing_inputs(env):
    t, g, cid = first_where(env, _dim_gap_ask, "task with a missing dim_divisor as the only follow-up")
    out = mp.run_m2(t, ti_of(t), shown(t), env["snap"])
    assert "dim_divisor" in out["per_cand"][cid]["defaulted"] and out["per_cand"][cid]["cost_cny"] is not None


# ---------------------------------------------------------------- M4 against gold
def test_method_names_are_upper_case(env):
    t, g = env["pairs"][0]
    ex = truth(t, g)
    assert mp.run_m2(t, ti_of(t), ex, env["snap"])["method"] == "M2"
    assert run3(env, t, ex)["method"] == "M3"
    assert run4(env, t, ex)["method"] == "M4"


def test_m4_perfect_extraction_matches_gold_on_all_layers(env):
    seen = set()
    for t, g in env["pairs"]:
        out = run4(env, t, shown(t))
        dec = g.decision
        assert out["decision"] == dec["type"], t.task_id
        if dec["type"] == "choose":
            assert out["choice"] == dec["choice"] and out["ask_fields"] == [] and out["questions"] == []
        else:
            assert out["choice"] is None
            assert set(out["ask_fields"]) == set(g.followup_gap_ids), t.task_id
            assert len(out["questions"]) == len(set(out["ask_fields"]))
        seen.add((t.layer, dec["type"]))
    assert {("explicit", "choose"), ("implicit", "choose"), ("conflict", "ask"), ("insufficient", "ask"),
            ("conflict", "choose"), ("insufficient", "choose")} <= seen


def test_m4_lists_conflicts_and_gaps_like_gold(env):
    for t, g in by_layer(env, "conflict", "insufficient"):
        out = run4(env, t, shown(t))
        for cid, pc in g.per_cand.items():
            o = out["per_cand"][cid]
            assert [f"{cid}.{x}" for x in o["conflicts"]] == pc["conflict_ids"], (t.task_id, cid)
            assert [f"{cid}.{x}" for x in o["gaps"]] == pc["missing_ids"], (t.task_id, cid)


def test_m4_questions_use_quote_labels(env):
    t, g, cid = first_where(env, _dim_gap_ask, "task with a missing dim_divisor as the only follow-up")
    out = run4(env, t, shown(t))
    k = [c.cand_id for c in t.candidates].index(cid) + 1
    assert out["ask_fields"] == [f"{cid}.dim_divisor"]
    assert out["questions"] == [f"报价{k}的计泡系数是 5000 还是 6000？"]


def test_m4_ddp_and_claims_match_gold_for_determined(env):
    for t, g in by_layer(env, "explicit", "implicit"):
        out = run4(env, t, truth(t, g))
        for cid, pc in g.per_cand.items():
            assert out["per_cand"][cid]["ddp_level"] == pc["ddp_level"]


# ---------------------------------------------------------------- source check inside M4
def _text_extraction(task, gold):
    """Raw extraction dicts whose quotes are real fragments of a synthetic quote text."""
    ex, texts = {}, {}
    for cid, terms in truth(task, gold).items():
        texts[cid] = f"{cid} 报价：体积重除以 6000；整票最低 21kg"
        raw = raw_from(terms, {n: ["体积重除以 6000"] for n in _TERM_CODECS})
        ex[cid] = raw
    return ex, texts


def test_m4_locate_downgrades_unverifiable_term_to_gap(env):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")
    c2 = t.candidates[1].cand_id
    ex, texts = _text_extraction(t, g)
    inp = env["inp"]
    run = lambda e: mp.run_m4(t, ti_of(t, texts), e, env["snap"], inp["structures"], inp["freight_floor"],
                              env["reg"], {}, inp["value_sets"])
    base = run(ex)
    assert base["decision"] == "choose" and base["diagnostics"]["downgraded"] == {}
    bad = copy.deepcopy(ex)
    bad[c2]["dim_divisor"]["quotes"] = ["体积重除以 5000"]          # not in the text
    out = run(bad)
    assert out["diagnostics"]["downgraded"] == {c2: ["dim_divisor"]}
    assert "dim_divisor" in out["per_cand"][c2]["gaps"]
    assert c2 in out["diagnostics"]["triggers"] and "gap" in out["diagnostics"]["triggers"][c2]
    # a core term that cannot be located leaves the quote unpriced and forces a follow-up
    bad[c2]["rate_tiers"]["quotes"] = ["不在原文里"]
    out2 = run(bad)
    assert out2["decision"] == "ask" and f"{c2}.rate_tiers" in out2["ask_fields"]
    assert out2["per_cand"][c2]["cost_cny"] is None
    # M2 and M3 do not run the source check
    assert mp.run_m2(t, ti_of(t, texts), bad, env["snap"])["per_cand"][c2]["cost_cny"] is not None


# ---------------------------------------------------------------- review trigger and merge
def test_review_triggers_cover_all_four_reasons(env):
    inp = env["inp"]
    seen = set()
    for t, g in env["pairs"]:
        trig = mp.review_triggers(t, ti_of(t), shown(t), env["snap"], inp["freight_floor"], env["reg"])
        for cid, reasons in trig.items():
            pc = g.per_cand[cid]
            if "conflict" in reasons:
                assert pc["conflict_ids"]
            if "gap" in reasons:
                assert pc["missing_ids"]
            if "ddp_risk" in reasons:
                assert pc["ddp_level"] != "none" or not pc["determined"]
            if "claim_insufficient" in reasons:
                assert "insufficient" in pc["claim_statuses"]
            seen.update(reasons)
        for cid, pc in g.per_cand.items():                    # nothing risky is left out
            risky = (pc["conflict_ids"] or pc["missing_ids"] or "insufficient" in pc["claim_statuses"]
                     or pc["ddp_level"] in ("verify", "high"))
            if risky:
                assert cid in trig, (t.task_id, cid)
    assert seen == {"conflict", "gap", "ddp_risk", "claim_insufficient"}


def test_merge_marks_disagreement_as_conflicting_and_handles_missing():
    a = QuoteTerms.from_plain(dim_divisor=D(6000), fuel_pct=D("0.1"), min_kg_shipment=D(21), transit_days=[4, 7])
    b = copy.deepcopy(a)
    b.dim_divisor = Field(D(5000), "stated", [])
    b.min_kg_shipment = Field(None, "missing", [])
    b.broker_fee_cny = Field(D(150), "stated", [])        # fee found only by the reviewer
    m, diff = mp.merge_extractions(a, b, "express")
    assert set(diff) == {"dim_divisor", "min_kg_shipment", "broker_fee_cny"}
    assert m.dim_divisor.status == "conflicting" and m.dim_divisor.value == [D(6000), D(5000)]
    assert m.min_kg_shipment.status == "missing"           # required term: stays a gap
    assert m.broker_fee_cny.status == "conflicting" and m.broker_fee_cny.value == [D(0), D(150)]
    assert m.fuel_pct.status == "stated" and m.transit_days.value == [4, 7]
    same, none = mp.merge_extractions(a, copy.deepcopy(a), "express")
    assert none == [] and same.dim_divisor.status == "stated"


def test_m4_review_disagreement_creates_conflict_and_ask(env):
    # explicit task with risky quotes: a reviewer that agrees changes nothing, one that disagrees creates a conflict
    t, g, (trig, quiet_ids) = first_where(env, _triggered_with_quiet, "choose task with triggered and quiet quotes")
    ex = truth(t, g)
    cid = next(iter(trig))
    # a reviewer that agrees changes nothing
    agree = run4(env, t, ex, {cid: copy.deepcopy(ex[cid])})
    assert agree["decision"] == "choose" and agree["choice"] == g.decision["choice"]
    assert agree["diagnostics"]["review_disagreements"] == {}
    # a reviewer that reads another divisor makes the term conflicting for that quote
    other = copy.deepcopy(ex[cid])
    cur = other.dim_divisor.value
    other.dim_divisor = Field(D(5000) if cur != D(5000) else D(6000), "stated", [])
    out = run4(env, t, ex, {cid: other})
    assert out["diagnostics"]["review_disagreements"] == {cid: ["dim_divisor"]}
    assert "dim_divisor" in out["per_cand"][cid]["conflicts"]
    # reviews of quotes that were not triggered are ignored
    quiet = quiet_ids[0]
    ignored = run4(env, t, ex, {quiet: other})
    assert ignored["diagnostics"]["review_disagreements"] == {}


def test_m4_review_finding_extra_fee_on_winner_flips_to_ask(env):
    # the winner is triggered (claim evidence insufficient); the reviewer reads a large broker fee the first
    # extraction missed, so the optimum depends on that term and the answer becomes a follow-up
    t, g = first_where(env, _winner_triggered, "choose task whose winner is triggered")
    winner = g.decision["choice"]
    first = truth(t, g)
    first[winner].broker_fee_cny = Field(D(0), "stated", [])
    trig = mp.review_triggers(t, ti_of(t), first, env["snap"], env["inp"]["freight_floor"], env["reg"])
    assert winner in trig
    review = copy.deepcopy(first[winner])
    review.broker_fee_cny = Field(D(100000), "stated", [])
    out = run4(env, t, first, {winner: review})
    assert out["per_cand"][winner]["conflicts"] == ["broker_fee_cny"]
    assert out["decision"] == "ask" and f"{winner}.broker_fee_cny" in out["ask_fields"]
    assert out["questions"] == [f"报价{[c.cand_id for c in t.candidates].index(winner) + 1}是否另收报关（清关）费？金额是多少？"]


def test_m4_nongrid_conflict_in_rate_tiers_asks_when_decisive(env):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")
    ex = truth(t, g)
    winner = g.decision["choice"]
    f = ex[winner].rate_tiers
    cheap = copy.deepcopy(f.value)
    dear = copy.deepcopy(f.value)
    for x in dear:
        x.price = x.price * 3
    ex2 = copy.deepcopy(ex)
    ex2[winner].rate_tiers = Field([cheap, dear], "conflicting", [])
    out = run4(env, t, ex2)
    assert out["decision"] == "ask" and f"{winner}.rate_tiers" in out["ask_fields"]
    assert out["per_cand"][winner]["conflicts"] == ["rate_tiers"]
    # a conflict that does not change the optimum keeps the choice
    ex3 = copy.deepcopy(ex)
    ex3[winner].rate_tiers = Field([cheap, copy.deepcopy(cheap)], "conflicting", [])
    out3 = run4(env, t, ex3)
    assert out3["decision"] == "choose" and out3["choice"] == winner


def test_m4_optional_fee_missing_is_not_a_gap(env):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")
    ex = truth(t, g)
    cid = g.decision["choice"]
    ex[cid].broker_fee_cny = Field(None, "missing", [])
    ex[cid].min_kg_carton = Field(None, "missing", [])
    out = run4(env, t, ex)
    assert out["decision"] == "choose" and out["choice"] == g.decision["choice"]
    assert out["per_cand"][cid]["gaps"] == []


# ---------------------------------------------------------------- normalisation fixes and diagnostics
def test_transit_takes_first_two_numbers():
    assert mp._transit("6-9天，最快5天") == [6, 9]
    assert mp._transit("约5天") == [5, 5]
    assert mp._transit([4, 7]) == [4, 7]
    assert mp._transit("无") is None


@pytest.mark.parametrize("text,expect", [
    ("关税+MPF", "duty_mpf"), ("duty + mpf", "duty_mpf"), ("关税＋MPF", "duty_mpf"), ("全包", "all"),
    ("全部进口环节税费", "all"), ("关税、MPF、HMF", "all"), ("仅关税", "duty"), ("duty", "duty"),
    ("all", "all"), ("含税到门", None), ("", None), (None, None)])
def test_ddp_scope_synonyms(text, expect):
    assert mp._scope(text) == expect


def test_ddp_unreadable_scope_is_missing_not_duty():
    ok = mp.normalize({"ddp": {"status": "stated", "value": {"is_ddp": True, "scope": "关税+MPF",
                                                              "duty_terms": "included"}}})
    assert ok.ddp.value.scope == "duty_mpf"
    for scope in ("神秘范围", None):
        bad = mp.normalize({"ddp": {"status": "stated", "value": {"is_ddp": True, "scope": scope}}})
        assert bad.ddp.status == "missing"
    # a non-DDP quote needs no scope
    assert mp.normalize({"ddp": {"status": "stated", "value": {"is_ddp": "不包税"}}}).ddp.value.is_ddp is False


@pytest.mark.parametrize("text,expect", [("包税", True), ("双清包税", True), ("DDP", True), ("双清", True),
                                         ("不包税", False), ("DAP", False), ("不含税", False), ("否", False),
                                         ("也许", None)])
def test_bool_wordings(text, expect):
    assert mp._bool(text) is expect


def test_integer_one_is_one_percent():
    for v, e in ((1, "0.01"), ("1", "0.01"), ("1%", "0.01"), (10, "0.1"), (0.1, "0.1"), (0, "0")):
        assert mp.normalize({"fuel_pct": {"status": "stated", "value": v}}).fuel_pct.value == D(e), v
    s = mp.normalize({"surcharges": {"status": "stated", "value": [
        {"code": "旺季附加费", "basis": "pct_freight", "amount": 1}]}})
    assert s.plain("surcharges")[0].amount == D("0.01")


def test_diagnostics_extraction_failures_and_review_record(env):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")
    ex = truth(t, g)
    bad_id = next(c.cand_id for c in t.candidates if c.cand_id != g.decision["choice"])
    ex[bad_id] = "无法解析"
    for out in (mp.run_m2(t, ti_of(t), ex, env["snap"]), run3(env, t, ex), run4(env, t, ex)):
        fails = out["diagnostics"]["extraction_failures"]
        assert set(fails) == {c.cand_id for c in t.candidates} and fails[bad_id] and sum(fails.values()) == 1
    ex2 = truth(t, g)
    out = mp.run_m4(t, ti_of(t), ex2, env["snap"], None, env["inp"]["freight_floor"], env["reg"], {},
                    env["inp"]["value_sets"], config_id="ds_flash")
    assert out["diagnostics"]["review"] == {"config_id": "qwen_plus", "reviewed": [], "text_fallback": []}
    assert out["diagnostics"]["decide_errors"] == {"count": 0, "reasons": []}
    assert not any(out["diagnostics"]["extraction_failures"].values())


def test_review_record_lists_reused_partner_extractions(env):
    t, g, (trig, _quiet) = first_where(env, _triggered_with_quiet, "choose task with triggered and quiet quotes")
    ex = truth(t, g)
    cid = next(iter(trig))
    out = mp.run_m4(t, ti_of(t), ex, env["snap"], None, env["inp"]["freight_floor"], env["reg"],
                    {cid: copy.deepcopy(ex[cid])}, env["inp"]["value_sets"], config_id="glm", review_config_id="kimi")
    assert out["diagnostics"]["review"] == {"config_id": "kimi", "reviewed": [cid], "text_fallback": [cid]}


def test_decide_failure_is_counted_with_reason(env, monkeypatch):
    t, g = first_where(env, _explicit_choose, "explicit task with a choice")

    def boom(view):
        raise ValueError("value grid too large: 99999")
    monkeypatch.setattr(mp, "decide", boom)
    out = run4(env, t, truth(t, g))
    assert out["decision"] == "ask"
    assert out["diagnostics"]["decide_errors"] == {"count": 1, "reasons": ["value grid too large: 99999"]}


def test_review_source_check_uses_reviewer_text(env):
    t, g, (trig, _quiet) = first_where(env, _triggered_with_quiet, "choose task with triggered and quiet quotes")
    cid = next(iter(trig))
    own = {c.cand_id: f"{c.cand_id} 本方转写：体积重除以 6000" for c in t.candidates}
    theirs = {c.cand_id: f"{c.cand_id} 对方转写：体积重÷6000" for c in t.candidates}
    ex = {k: raw_from(v, {n: ["体积重除以 6000"] for n in _TERM_CODECS}) for k, v in truth(t, g).items()}
    rv = {k: raw_from(v, {n: ["体积重÷6000"] for n in _TERM_CODECS}) for k, v in truth(t, g).items()}
    inp = env["inp"]

    def run(**kw):
        return mp.run_m4(t, ti_of(t, own), ex, env["snap"], inp["structures"], inp["freight_floor"], env["reg"],
                         {cid: rv[cid]}, inp["value_sets"], config_id="ds_flash", **kw)
    ok = run(review_texts=theirs)
    assert ok["diagnostics"]["review_disagreements"] == {} and ok["diagnostics"]["review"]["text_fallback"] == []
    assert ok["decision"] == "choose" and ok["choice"] == g.decision["choice"]
    # without the reviewer's own text, its fragments are checked against the other transcript and every
    # term is downgraded to missing: the fallback is recorded and the merge loses the reviewer's terms
    fb = run()
    assert fb["diagnostics"]["review"]["text_fallback"] == [cid]
    assert fb["diagnostics"]["review_disagreements"] != {}
