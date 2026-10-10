"""Scoring of method outputs against gold answers, aggregation and the three primary endpoints.

Scoring definitions follow docs/plan/方法实现规格.md section 6. Gold layout: data/gold/<split>/<task>.json
(decision, per_cand). The layer of a task lives in the task file, so callers pass it through the
output or gold dict (key "layer") or add it to the scored row.

A risk item is a triple (cand_id, category, key). Categories: hidden_fee, conflict, gap, ddp_risk.
hidden_fee / conflict / gap keys are term names (surcharges, fuel_pct, broker_fee_cny, isf_fee_cny,
dim_divisor, min_kg_shipment, ddp); the ddp_risk key is always "ddp". ddp_risk is scored only on
candidates whose gold ddp_levels hold a single value. Contingent fees (inspection) are not hidden fees in gold and are
dropped from predictions as well.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from . import decide as _decide
from . import stats as _stats

LEXICON_VERSION = "1.1"
VARIANT_METHODS = ("M4_trig",)   # derived outputs scored like M4; never enter the primary endpoints
PIPELINE_METHODS = ("M2", "M3", "M4") + tuple(v.upper() for v in VARIANT_METHODS)
CATEGORIES = ("hidden_fee", "conflict", "gap", "ddp_risk")
RECALL_ONLY = ("hidden_fee",)
DDP_POSITIVE = ("verify", "high")
NONEXPLICIT_EXCLUDE = ("explicit",)

# ------------------------------------------------------------------ lexicon (frozen with version)
# Fee / term detection. Keys are term names used by the gold records.
FIELD_PATTERNS: dict = {
    "fuel_pct": [r"燃油", r"fuel", r"(?<![a-z])fsc(?![a-z])", r"fuel_pct"],
    "broker_fee_cny": [r"清关", r"报关费", r"customs\s*filing", r"customs\s*clearance", r"(?<![a-z])broker", r"broker_fee"],
    "isf_fee_cny": [r"(?<![a-z])isf(?![a-z])", r"isf_fee"],
    "surcharges": [r"住宅", r"私宅", r"residential", r"home\s*(site|delivery)", r"偏远", r"边远", r"remote",
                   r"outlying", r"超尺寸", r"尺寸超限", r"oversize", r"超重", r"重量超限", r"overweight",
                   r"派送费", r"投递费", r"(?<![a-z])res(?![a-z])", r"(?<![a-z])rem(?![a-z])", r"(?<![a-z])ovs(?![a-z])", r"(?<![a-z])ovw(?![a-z])"],
    "dim_divisor": [r"计泡", r"体积重", r"除数", r"除以", r"抛比", r"dim[\s_]*(factor|divisor)?", r"volumetric"],
    "min_kg_shipment": [r"最低计费", r"最低收费", r"起计", r"最低重量", r"min[\s_]*(kg|charge)"],
    "ddp": [r"包税", r"(?<![a-z])ddp(?![a-z])", r"双清", r"含税", r"税金", r"关税", r"税费"],
}
# Generic wording counts as a surcharge only when no other fee or term is named.
GENERIC_SURCHARGE = [r"附加费", r"surcharge", r"加收费用", r"额外费用"]
# Contingent fees: not hidden fees in gold.
CONTINGENT_PATTERNS = [r"查验", r"抽检", r"(?<![a-z])insp(?![a-z])", r"inspection", r"exam fee"]
# A fee word with these cues is a hidden fee even in a sentence about conflicts or gaps.
HIDDEN_CUES = [r"另收", r"另计", r"另加", r"额外", r"隐藏", r"隐性", r"加收", r"没算", r"未含", r"不含", r"附加",
               r"extra", r"hidden", r"additional"]
CONFLICT_CUES = [r"矛盾", r"冲突", r"不一致", r"前后不", r"两处", r"出入", r"对不上", r"不同", r"inconsisten",
                 r"conflict", r"contradict"]
GAP_CUES = [r"未写", r"没写", r"没有写", r"缺少", r"缺失", r"未注明", r"未说明", r"没说明", r"未标明", r"没标明",
            r"不明确", r"不清楚", r"没提", r"未提", r"没有提", r"没有注明", r"没有说明", r"没有标明", r"未给出",
            r"没给", r"未列出", r"没列", r"missing", r"not stated", r"not specified", r"unclear", r"unspecified"]
# Duty-prepaid wording and the risk cues that turn it into a risk.
DDP_WORDS = [r"包税", r"(?<![a-z])ddp(?![a-z])", r"双清", r"含税", r"税金全包", r"包干", r"税金封顶", r"税金上限"]
DDP_RISK_CUES = [r"风险", r"偏低", r"过低", r"太低", r"低于", r"不足", r"可疑", r"存疑", r"不实", r"不可信",
                 r"不靠谱", r"补税", r"另收", r"封顶", r"上限", r"亏", r"坑", r"担心", r"核实", r"确认", r"risk",
                 r"suspicious", r"too low", r"verify"]
QUOTE_REF = re.compile(r"报价\s*(\d+)|第\s*(\d+)\s*家|quote\s*(\d+)|(?<![A-Za-z])C(\d+)(?!\d)|选项\s*(\d+)", re.I)

STATUS_ALIASES = {
    "supported": "supported", "有证据": "supported",
    "insufficient": "insufficient", "证据不足": "insufficient",
    "contradicted": "contradicted", "与宣称矛盾": "contradicted",
    "irrelevant": "irrelevant", "与本次运输不相关": "irrelevant",
}
DDP_ALIASES = {"none": "none", "无": "none", "verify": "verify", "需核实": "verify", "high": "high", "高": "high"}


def _any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def detect_fields(text: str) -> list:
    """Term names mentioned in text, in FIELD_PATTERNS order."""
    found = [f for f, pats in FIELD_PATTERNS.items() if _any(pats, text)]
    if not found and _any(GENERIC_SURCHARGE, text):
        found = ["surcharges"]
    return found


def normalize_key(category: str, raw: Any) -> Optional[str]:
    """Map a model-side fee / term string to a gold key. None means drop (contingent fees)."""
    s = str(raw).strip()
    if "." in s:
        s = s.split(".", 1)[1]
    if category == "ddp_risk":
        return "ddp"
    if category == "hidden_fee" and _any(CONTINGENT_PATTERNS, s):
        return None
    if s in FIELD_PATTERNS:
        return s
    low = s.lower()
    if low in ("ddp_risk", "ddp"):
        return "ddp"
    fields = detect_fields(s)
    if category == "hidden_fee":
        fields = [f for f in fields if f != "ddp"] or fields
    return fields[0] if fields else s


def _norm_cand(raw: Any) -> str:
    s = str(raw).strip()
    m = re.fullmatch(r"(?:报价\s*|quote\s*|C)(\d+)", s, re.I)
    return f"C{m.group(1)}" if m else s


def _quote_map(task_input_mapping: Optional[Mapping]) -> dict:
    m = dict(task_input_mapping or {})
    if isinstance(m.get("quote_to_cand"), Mapping):
        m = dict(m["quote_to_cand"])
    out = {}
    for k, v in m.items():
        ks = str(k)
        mm = re.search(r"(\d+)", ks)
        if mm:
            out[int(mm.group(1))] = str(v)
    return out


def map_risk_text(text: str, qmap: Mapping) -> tuple:
    """(triples, unmapped) for one free-text risk string."""
    refs = []
    for m in QUOTE_REF.finditer(text):
        n = int(next(g for g in m.groups() if g))
        cand = qmap.get(n, f"C{n}")
        if cand not in refs:
            refs.append(cand)
    triples = []
    conflict, gap = _any(CONFLICT_CUES, text), _any(GAP_CUES, text)
    fields = detect_fields(text)
    fee_fields = [f for f in fields if f in ("fuel_pct", "broker_fee_cny", "isf_fee_cny", "surcharges")]
    contingent_only = _any(CONTINGENT_PATTERNS, text) and not fee_fields
    items = []   # (category, key)
    if _any(DDP_WORDS, text) and _any(DDP_RISK_CUES, text) and not conflict and not gap:
        items.append(("ddp_risk", "ddp"))
    if conflict or gap:
        for f in fields:
            if conflict:
                items.append(("conflict", f))
            if gap:
                items.append(("gap", f))
        if _any(HIDDEN_CUES, text):
            items.extend(("hidden_fee", f) for f in fee_fields if f not in [k for c, k in items if c == "conflict"])
    elif not contingent_only:
        items.extend(("hidden_fee", f) for f in fee_fields)
    if not refs or not items:
        return [], [text]
    return [(c, cat, key) for c in refs for cat, key in items], []


def map_free_text_risks(output: Mapping, task_input_mapping: Optional[Mapping] = None) -> list:
    """Map the free-text risks of an M0 / M1 output to risk triples.

    output["free_text_risks"] (or output["risks"]) is a list of strings; task_input_mapping maps quote
    numbers to candidate ids ({"报价1": "C1"} or {1: "C1"}; default quote k -> Ck). Strings that name no
    quote, or no recognised fee / term, are skipped and can be listed with map_free_text_risks_detail.
    """
    return map_free_text_risks_detail(output, task_input_mapping)[0]


def map_free_text_risks_detail(output: Mapping, task_input_mapping: Optional[Mapping] = None) -> tuple:
    texts = output.get("free_text_risks")
    if texts is None:
        texts = output.get("risks")
    qmap = _quote_map(task_input_mapping if task_input_mapping is not None else output.get("quote_map"))
    triples, unmapped = [], []
    for t in texts or []:
        tr, un = map_risk_text(str(t), qmap)
        for x in tr:
            if x not in triples:
                triples.append(x)
        unmapped.extend(un)
    return triples, unmapped


LEXICON = {
    "version": LEXICON_VERSION,
    "fields": FIELD_PATTERNS,
    "generic_surcharge": GENERIC_SURCHARGE,
    "contingent": CONTINGENT_PATTERNS,
    "hidden_cues": HIDDEN_CUES,
    "conflict_cues": CONFLICT_CUES,
    "gap_cues": GAP_CUES,
    "ddp_words": DDP_WORDS,
    "ddp_risk_cues": DDP_RISK_CUES,
    "map": map_free_text_risks,
}


# ------------------------------------------------------------------ triples
def gold_triples(gold: Mapping) -> set:
    out = set()
    for cid, pc in gold["per_cand"].items():
        for cat, name in (("hidden_fee", "hidden_fee_ids"), ("conflict", "conflict_ids"), ("gap", "missing_ids")):
            for item in pc.get(name) or []:
                out.add((cid, cat, item.split(".", 1)[1] if "." in item else item))
        if ddp_determined(pc) and _ddp_single(pc) in DDP_POSITIVE:
            out.add((cid, "ddp_risk", "ddp"))
    return out


def _ddp_single(pc: Mapping):
    lv = pc.get("ddp_levels")
    return lv[0] if lv and len(set(lv)) == 1 else (pc.get("ddp_level") if not lv else None)


def ddp_determined(pc: Mapping) -> bool:
    """ddp_risk is scored on a candidate only when gold has exactly one ddp level over the value grid."""
    return _ddp_single(pc) is not None


def _as_list(x) -> list:
    return [] if x is None else list(x)


def output_triples(output: Mapping, gold: Optional[Mapping] = None) -> set:
    """Predicted triples: structured per_cand fields plus mapped free text."""
    out = set()
    for cid, pc in (output.get("per_cand") or {}).items():
        cid = _norm_cand(cid)
        pc = pc or {}
        for code in _as_list(pc.get("hidden_fees")):
            k = normalize_key("hidden_fee", code)
            if k is not None:
                out.add((cid, "hidden_fee", k))
        for f in _as_list(pc.get("conflicts")):
            out.add((cid, "conflict", normalize_key("conflict", f)))
        for f in _as_list(pc.get("gaps")):
            out.add((cid, "gap", normalize_key("gap", f)))
        lvl = DDP_ALIASES.get(str(pc.get("ddp_level")).strip().lower() if pc.get("ddp_level") is not None else "",
                              pc.get("ddp_level"))
        if lvl in DDP_POSITIVE:
            out.add((cid, "ddp_risk", "ddp"))
    out.update(map_free_text_risks(output))
    if gold is not None:   # ddp_risk counts on determined candidates only
        det = {c for c, p in gold["per_cand"].items() if ddp_determined(p)}
        out = {t for t in out if t[1] != "ddp_risk" or t[0] in det}
    return out


# ------------------------------------------------------------------ scoring
def _dec(x) -> Optional[Decimal]:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except InvalidOperation:
        return None


def _norm_ask(fields) -> set:
    out = set()
    for f in _as_list(fields):
        s = str(f).strip()
        if "." in s:
            c, k = s.split(".", 1)
            out.add(f"{_norm_cand(c)}.{k.strip()}")
        else:
            out.add(s)
    return out


def _claim_pairs(gold_claims: Sequence, got: Any) -> list:
    """[(gold_status, predicted_status|None)]. Numeric keys are 0-based (key i -> gold claim i+1);
    keys written "claimN" / "<cand>-claimN" are 1-based like the gold claim ids."""
    if isinstance(got, (list, tuple)):
        got = {i: v for i, v in enumerate(got)}
    if not isinstance(got, Mapping):
        got = {}
    by_idx = {}
    for k, v in got.items():
        ks = str(k).strip()
        m = re.fullmatch(r"\d+", ks)
        if m:
            idx = int(ks)
        else:
            m = re.search(r"claim(\d+)$", ks)
            if not m:
                continue
            idx = int(m.group(1)) - 1
        by_idx[idx] = STATUS_ALIASES.get(str(v).strip(), str(v).strip())
    return [(c["gold_status"], by_idx.get(i)) for i, c in enumerate(gold_claims)]


# ------------------------------------------------------------------ ask fields for free-text methods
ASK_FIELD_NAMES = tuple(FIELD_PATTERNS)


def map_ask_fields(output: Mapping, task_input_mapping: Optional[Mapping] = None) -> list:
    """"Ck.<term>" follow-up ids read from output questions, risks and free_text_risks by the lexicon.

    Used for M0 / M1 when ask_fields is empty or maps to nothing. Texts naming no quote are skipped.
    """
    qmap = _quote_map(task_input_mapping if task_input_mapping is not None else output.get("quote_map"))
    texts = []
    for k in ("questions", "risks", "free_text_risks"):
        texts.extend(str(t) for t in (output.get(k) or []))
    out = []
    for t in texts:
        refs = []
        for m in QUOTE_REF.finditer(t):
            n = int(next(g for g in m.groups() if g))
            c = qmap.get(n, f"C{n}")
            if c not in refs:
                refs.append(c)
        for f in detect_fields(t):
            for c in refs:
                x = f"{c}.{f}"
                if x not in out:
                    out.append(x)
    return out


def _extraction_failures(output: Mapping) -> int:
    v = output.get("extraction_failures")
    if v is None:
        v = (output.get("diagnostics") or {}).get("extraction_failures")
    if isinstance(v, Mapping):
        return int(sum(bool(x) for x in v.values()))
    return int(v or 0)


def score_task(output: Mapping, gold: Mapping) -> dict:
    """Score one MethodOutput dict against one gold dict. Returns a flat row (see keys below)."""
    method = str(output.get("method")).upper() if output.get("method") is not None else None
    # a final call failure of any call the output depends on (assemble writes failed_calls) is a parse failure for
    # every method alike: decision error, no risk items, continuous metrics missing
    call_failed = bool(output.get("failed_calls"))
    parse_ok = bool(output.get("parse_ok", True)) and not call_failed
    # pipeline methods are scored whenever they produced a decision; M0 / M1 need a parsed answer
    usable = ((parse_ok or method in PIPELINE_METHODS) and output.get("decision") is not None
              and not call_failed)
    gdec = gold["decision"]
    gtype = gdec["type"]
    decision = output.get("decision") if usable else None
    choice = output.get("choice")
    ask = _norm_ask(output.get("ask_fields")) if usable else set()
    if decision == "ask" and method in ("M0", "M1"):
        valid = {a for a in ask if re.fullmatch(r"C\d+\.(%s)" % "|".join(ASK_FIELD_NAMES), a)}
        ask = valid or set(map_ask_fields(output))
    gask = _norm_ask(gdec.get("ask_fields"))
    choice_valid = choice in gold["per_cand"]

    if gtype == "choose":
        correct = decision == "choose" and choice == gdec["choice"]
    else:
        correct = decision == "ask" and bool(ask & gask)

    out_feasible = None
    reg_abs = reg_rel = None
    if gtype == "choose" and decision == "choose" and choice_valid:
        r = _decide.regret(choice, gold)
        if r == "out_of_feasible":
            out_feasible = 1
        else:
            out_feasible = 0
            reg_abs, reg_rel = float(r[0]), float(r[1])

    ask_cover = None
    if gtype == "ask":
        ask_cover = len(ask & gask) / len(gask) if gask else None
        if decision != "ask":
            ask_cover = 0.0

    pred = output_triples(output, gold) if usable else set()
    gt = gold_triples(gold)
    row = {
        "task_id": output.get("task_id", gold.get("task_id")),
        "method": method,
        "config_id": output.get("config_id"),
        "layer": output.get("layer", gold.get("layer")),
        "parse_ok": int(parse_ok),
        "parse_failed": int((not parse_ok) or _extraction_failures(output) > 0),
        "call_failed": int(call_failed),
        "n_failed_calls": len(output.get("failed_calls") or []),
        "extraction_failures": _extraction_failures(output),
        "ask_fields_used": sorted(ask),
        "gold_type": gtype,
        "decision": decision,
        "decision_correct": int(correct),
        "false_ask": int(gtype == "choose" and decision == "ask"),
        "missed_ask": int(gtype == "ask" and decision == "choose"),
        "out_of_feasible": out_feasible,
        "regret_abs": reg_abs,
        "regret_rel": reg_rel,
        "ask_cover": ask_cover,
        "regret_missing": int(gtype == "choose" and out_feasible is None),
    }
    tp = fp = fn = 0
    for cat in CATEGORIES:
        a = {t for t in pred if t[1] == cat}
        b = {t for t in gt if t[1] == cat}
        n_fp = len(a - b)
        if cat in RECALL_ONLY:      # hidden_fee: recall only, predictions outside gold are not false positives
            row[f"fp_unscored_{cat}"] = n_fp
            n_fp = 0
        row[f"tp_{cat}"], row[f"fp_{cat}"], row[f"fn_{cat}"] = len(a & b), n_fp, len(b - a)
        tp, fp, fn = tp + len(a & b), fp + n_fp, fn + len(b - a)
    row["tp"], row["fp"], row["fn"] = tp, fp, fn

    # claim status
    n_claim = n_ok = 0
    pcs = output.get("per_cand") or {}
    pcs = {_norm_cand(k): v for k, v in pcs.items()}
    for cid, pc in gold["per_cand"].items():
        gcl = pc.get("claims") or []
        if not gcl:
            continue
        got = (pcs.get(cid) or {}).get("claim_status") if usable else None
        for g, p in _claim_pairs(gcl, got):
            n_claim += 1
            n_ok += int(p == g)
    row["claim_n"], row["claim_correct"] = n_claim, n_ok

    # cost error on determined candidates
    cn = cm = 0
    abs_sum = rel_sum = 0.0
    exact = 0
    for cid, pc in gold["per_cand"].items():
        if not pc.get("determined"):
            continue
        cn += 1
        got = _dec(((pcs.get(cid) or {}).get("cost_cny"))) if usable else None
        if got is None:
            cm += 1
            continue
        g = Decimal(str(pc["total_cny"]))
        err = abs(got - g)
        abs_sum += float(err)
        rel_sum += float(err / g) if g else 0.0
        exact += int(err <= Decimal("0.01"))
    row.update(cost_n=cn, cost_missing=cm, cost_abs_sum=abs_sum, cost_rel_sum=rel_sum, cost_exact=exact)
    return row


# ------------------------------------------------------------------ aggregation
def f1_from_counts(tp: float, fp: float, fn: float) -> Optional[float]:
    den = 2 * tp + fp + fn
    return None if den == 0 else 2 * tp / den


def _sum(rows, k) -> float:
    return float(sum(r[k] for r in rows if r.get(k) is not None))


def _ratio(num, den) -> Optional[float]:
    return None if den == 0 else num / den


def _mean(rows, k) -> Optional[float]:
    v = [r[k] for r in rows if r.get(k) is not None]
    return float(np.mean(v)) if v else None


def metric_values(rows: Sequence[Mapping]) -> dict:
    """All aggregate metrics for a list of scored rows."""
    tp, fp, fn = _sum(rows, "tp"), _sum(rows, "fp"), _sum(rows, "fn")
    out = {
        "n_tasks": len({r["task_id"] for r in rows}), "n_rows": len(rows),
        "decision_correct": _mean(rows, "decision_correct"),
        "parse_ok": _mean(rows, "parse_ok"),
        "parse_failure_rate": _mean(rows, "parse_failed"),
        "call_failed_n": _sum(rows, "call_failed"),
        "call_failed_rate": _mean(rows, "call_failed"),
        "regret_missing": _sum(rows, "regret_missing"),
        "extraction_failures": _sum(rows, "extraction_failures"),
        "micro_f1": f1_from_counts(tp, fp, fn),
        "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
        "tp": tp, "fp": fp, "fn": fn,
    }
    for cat in CATEGORIES:
        t, p, n = _sum(rows, f"tp_{cat}"), _sum(rows, f"fp_{cat}"), _sum(rows, f"fn_{cat}")
        recall_only = cat in RECALL_ONLY
        out[f"f1_{cat}"] = None if recall_only else f1_from_counts(t, p, n)
        out[f"precision_{cat}"] = None if recall_only else _ratio(t, t + p)
        out[f"recall_{cat}"] = _ratio(t, t + n)
    ch = [r for r in rows if r["gold_type"] == "choose"]
    ak = [r for r in rows if r["gold_type"] == "ask"]
    out["n_choose"], out["n_ask"] = len(ch), len(ak)
    out["false_ask_rate"] = _mean(ch, "false_ask")
    out["correct_ask_rate"] = _mean(ak, "decision_correct")
    out["out_of_feasible_rate"] = _mean(rows, "out_of_feasible")
    out["regret_abs_mean"] = _mean(rows, "regret_abs")
    out["regret_rel_mean"] = _mean(rows, "regret_rel")
    out["ask_cover_mean"] = _mean(rows, "ask_cover")
    out["claim_accuracy"] = _ratio(_sum(rows, "claim_correct"), _sum(rows, "claim_n"))
    cn, cm = _sum(rows, "cost_n"), _sum(rows, "cost_missing")
    out["cost_n"], out["cost_missing"] = cn, cm
    out["cost_abs_mean"] = _ratio(_sum(rows, "cost_abs_sum"), cn - cm)
    out["cost_rel_mean"] = _ratio(_sum(rows, "cost_rel_sum"), cn - cm)
    out["cost_exact_rate"] = _ratio(_sum(rows, "cost_exact"), cn)
    return out


CI_METRICS = ("decision_correct", "micro_f1", "regret_abs_mean", "cost_abs_mean", "ask_cover_mean")


def aggregate(scored_rows: Sequence[Mapping], by: Sequence[str] = ("method", "config_id", "layer"),
              B: int = 2000, alpha: float = 0.05, seed: int = 0, ci: bool = True) -> list:
    """Table of metric values per group (list of dicts, group keys first).

    Confidence intervals (<metric>_lo / _hi) come from stats.cluster_bootstrap with tasks as clusters.
    """
    groups: dict = {}
    for r in scored_rows:
        groups.setdefault(tuple(r.get(k) for k in by), []).append(r)
    table = []
    for key in sorted(groups, key=lambda t: tuple(str(x) for x in t)):
        rows = groups[key]
        rec = dict(zip(by, key))
        rec.update(metric_values(rows))
        if ci:
            for m in CI_METRICS:
                if rec.get(m) is None:
                    rec[f"{m}_lo"] = rec[f"{m}_hi"] = None
                    continue
                res = _stats.cluster_bootstrap(rows, "task_id", lambda rs, m=m: metric_values_one(rs, m),
                                               B=B, alpha=alpha, seed=seed)
                lo, hi = res["lo"], res["hi"]
                rec[f"{m}_lo"] = None if lo is None or lo != lo else lo
                rec[f"{m}_hi"] = None if hi is None or hi != hi else hi
        table.append(rec)
    return table


def metric_values_one(rows: Sequence[Mapping], name: str) -> float:
    """One metric on a resample; NaN when undefined (ignored by the percentile via nanquantile-safe path)."""
    if name == "decision_correct":
        v = _mean(rows, "decision_correct")
    elif name == "micro_f1":
        v = f1_from_counts(_sum(rows, "tp"), _sum(rows, "fp"), _sum(rows, "fn"))
    elif name == "regret_abs_mean":
        v = _mean(rows, "regret_abs")
    elif name == "ask_cover_mean":
        v = _mean(rows, "ask_cover")
    elif name == "cost_abs_mean":
        v = _ratio(_sum(rows, "cost_abs_sum"), _sum(rows, "cost_n") - _sum(rows, "cost_missing"))
    else:
        raise ValueError(name)
    return float("nan") if v is None else v


# ------------------------------------------------------------------ primary endpoints
LOW_VS_STRONG = (("ds_flash", "ds_pro"), ("qwen_plus", "qwen_max"))
MAIN_CONFIGS = ("ds_pro", "ds_flash", "qwen_max", "qwen_plus", "glm", "kimi")
NI_MARGIN = 0.05


def _index(rows, method, configs=None, layers=None, exclude_layers=()):
    out = {}
    for r in rows:
        if str(r["method"]).upper() != method:
            continue
        if configs is not None and r["config_id"] not in configs:
            continue
        if layers is not None and r["layer"] not in layers:
            continue
        if r["layer"] in exclude_layers:
            continue
        out[(r["config_id"], r["task_id"])] = r
    return out


def _check_layers(rows):
    if any(r.get("layer") is None for r in rows):
        raise ValueError("scored rows need a layer for the primary endpoints")


def _task_units(m4, m0, value):
    """One unit per task: A and B hold the summed (count..., n) of that task's rows over all configs."""
    tasks = sorted({k[1] for k in m4} & {k[1] for k in m0})
    units = []
    for tid in tasks:
        ka = [k for k in m4 if k[1] == tid]
        kb = [k for k in m0 if k[1] == tid]
        units.append({"A": tuple(map(sum, zip(*[value(m4[k]) for k in ka]))),
                      "B": tuple(map(sum, zip(*[value(m0[k]) for k in kb])))})
    return units


def _need_pairs(name, n):
    if n == 0:
        raise ValueError(f"{name}: no paired units (check method, config_id and layer values)")


def _acc_diff(units) -> float:
    a = sum(u["A"][0] for u in units) / max(1, sum(u["A"][1] for u in units))
    b = sum(u["B"][0] for u in units) / max(1, sum(u["B"][1] for u in units))
    return a - b


def endpoint1(rows, configs=MAIN_CONFIGS, sims=99999, seed=0) -> dict:
    """Decision correctness, M4 vs M0, non-explicit layers. Unit = task: the M0 / M4 labels of the task's
    rows in all configs are exchanged together; statistic = pooled accuracy difference; two-sided."""
    m4 = _index(rows, "M4", configs, exclude_layers=NONEXPLICIT_EXCLUDE)
    m0 = _index(rows, "M0", configs, exclude_layers=NONEXPLICIT_EXCLUDE)
    units = _task_units(m4, m0, lambda r: (float(r["decision_correct"]), 1))
    _need_pairs("E1", len(units))
    res = _stats.generic_stratified_permutation(units, _acc_diff, sims=sims, seed=seed, alternative="two-sided")
    res["name"] = "E1_decision_correct_M4_vs_M0"
    res["mean_difference"] = res["observed"]
    res["acc_M4"] = _pooled_mean(m4, m0, 0)
    res["acc_M0"] = _pooled_mean(m4, m0, 1)
    res["n_pairs"] = len(set(m4) & set(m0))
    res["by_config"] = {}
    for c in sorted({k[0] for k in m4} & {k[0] for k in m0}):
        ks = [k for k in m4 if k[0] == c and k in m0]
        res["by_config"][c] = {"n": len(ks),
                               "mean_difference": float(np.mean([m4[k]["decision_correct"] - m0[k]["decision_correct"] for k in ks]))}
    return res


def _pooled_mean(m4, m0, which):
    keys = set(m4) & set(m0)
    if not keys:
        return None
    src = (m4, m0)[which]
    return float(np.mean([src[k]["decision_correct"] for k in keys]))


def _micro_f1_diff(units) -> float:
    def f(side):
        tp = sum(u[side][0] for u in units)
        fp = sum(u[side][1] for u in units)
        fn = sum(u[side][2] for u in units)
        v = f1_from_counts(tp, fp, fn)
        return 0.0 if v is None else v
    return f("A") - f("B")


def endpoint2(rows, configs=MAIN_CONFIGS, sims=99999, seed=0) -> dict:
    """Micro-F1 of risk items, M4 vs M0, all layers. Unit = task: the labels of the task's rows in all
    configs are exchanged together; statistic = pooled micro-F1 difference; two-sided."""
    m4 = _index(rows, "M4", configs)
    m0 = _index(rows, "M0", configs)
    units = _task_units(m4, m0, lambda r: (r["tp"], r["fp"], r["fn"]))
    _need_pairs("E2", len(units))
    res = _stats.generic_stratified_permutation(units, _micro_f1_diff, sims=sims, seed=seed, alternative="two-sided")
    res["name"] = "E2_risk_micro_f1_M4_vs_M0"
    res["difference"] = res.get("observed")
    res["f1_M4"] = f1_from_counts(*[sum(u["A"][i] for u in units) for i in range(3)])
    res["f1_M0"] = f1_from_counts(*[sum(u["B"][i] for u in units) for i in range(3)])
    return res


def endpoint3(rows, pairs=LOW_VS_STRONG, margin=NI_MARGIN, B=9999, alpha=0.05, seed=0) -> dict:
    """Low-cost M4 vs same-vendor strong M0, non-explicit layers.

    Primary: task-cluster bootstrap; one-sided lower bound of the accuracy difference against -margin;
    p = (1 + #{bootstrap difference <= -margin}) / (B + 1). Sensitivity: Tango score test per pair.
    """
    cluster: dict = {}
    sens = {}
    for low, strong in pairs:
        m4 = _index(rows, "M4", (low,), exclude_layers=NONEXPLICIT_EXCLUDE)
        m0 = _index(rows, "M0", (strong,), exclude_layers=NONEXPLICIT_EXCLUDE)
        b = c = n = 0
        tids = sorted(tid for (_, tid) in m4 if (strong, tid) in m0)
        for tid in tids:
            a = int(m4[(low, tid)]["decision_correct"])
            r = int(m0[(strong, tid)]["decision_correct"])
            cluster.setdefault(tid, []).append((a, r))
            b += int(a == 1 and r == 0)
            c += int(a == 0 and r == 1)
            n += 1
        if n:
            t = _stats.tango_noninferiority(b, c, n, margin)
            t.update(low=low, strong=strong, p_two_sided_equiv=None)
            sens[f"{low}_vs_{strong}"] = t
    _need_pairs("E3", len(cluster))
    lo = _stats.noninferiority_bootstrap_lower(cluster, B=B, alpha=alpha, seed=seed)
    records = [(cid, a, b) for cid, prs in cluster.items() for a, b in prs]
    boots, _ = _stats._cluster_draws(records, lambda r: r[0], lambda rs: float(np.mean([a - b for _, a, b in rs])), B, seed)
    p = float((1 + np.count_nonzero(boots <= -margin + 1e-12)) / (B + 1))
    return {"name": "E3_noninferiority", "n_clusters": lo["n_clusters"], "estimate": lo["estimate"],
            "lower": lo["lower"], "p": p, "alpha": alpha, "margin": margin, "B": B,
            "noninferior_unadjusted": bool(lo["lower"] > -margin), "tango": sens,
            "tango_all_noninferior": bool(sens) and all(t["p"] < alpha for t in sens.values())}


def primary_endpoints(scored_rows: Sequence[Mapping], sims: int = 99999, B: int = 9999, seed: int = 0,
                      alpha: float = 0.05) -> dict:
    """The three pre-specified endpoints with a Holm adjustment over their p values (E3 uses the bootstrap p)."""
    rows = list(scored_rows)
    _check_layers(rows)
    e1 = endpoint1(rows, sims=sims, seed=seed)
    e2 = endpoint2(rows, sims=sims, seed=seed)
    e3 = endpoint3(rows, B=B, seed=seed)
    ps = {"E1": e1["p"], "E2": e2["p"], "E3": e3["p"]}
    valid = {k: v for k, v in ps.items() if v is not None}
    adj = _stats.holm(valid) if valid else {}
    reject = {k: (adj[k] < alpha if k in adj else None) for k in ps}
    est = {"E1": e1["mean_difference"], "E2": e2["difference"], "E3": e3["estimate"]}
    return {"E1": e1, "E2": e2, "E3": e3, "p_raw": ps,
            "p_holm": {k: adj.get(k) for k in ps},
            "reject": reject, "alpha": alpha,
            # reported conclusions follow the Holm-adjusted decisions
            "conclusion": {k: {"reject": reject[k], "p_holm": adj.get(k), "estimate": est[k]} for k in ps}}


def attach_layers(rows: Sequence[Mapping], tasks_dir) -> list:
    """Copies of rows with "layer" read from <tasks_dir>/<task_id>.json (the task file's "layer")."""
    import json
    from pathlib import Path
    cache: dict = {}
    out = []
    for r in rows:
        tid = r["task_id"]
        if tid not in cache:
            cache[tid] = json.loads((Path(tasks_dir) / f"{tid}.json").read_text(encoding="utf-8"))["layer"]
        out.append({**r, "layer": cache[tid]})
    return out


LEXICON["ask_fields"] = map_ask_fields
