"""Single-call LLM methods: M0 (direct ask) and M1 (strong prompt), image transcription, output parsing.

This module builds executor requests and parses model text; it never calls a model.
"""
from __future__ import annotations

import copy
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from . import prompts
from .executor import make_run_key, prompt_hash
from .inputs import (BOX_MARKS, FAIL_TEXT, TaskInput, image_ref, param_text, registry_text)

MAX_TOKENS = {"transcribe": 1200, "M0": 4000, "M1": 12000}

DDP_MAP = {"无": "none", "需核实": "verify", "高": "high", "none": "none", "verify": "verify", "high": "high"}
CLAIM_MAP = {"有证据": "supported", "证据不足": "insufficient", "与宣称矛盾": "contradicted",
             "与本次运输不相关": "irrelevant", "supported": "supported", "insufficient": "insufficient",
             "contradicted": "contradicted", "irrelevant": "irrelevant"}

# Chinese and English clause names to internal term names (see the extraction prompt and render wording).
TERM_MAP = {
    "mode": "mode", "运输方式": "mode",
    "rate_tiers": "rate_tiers", "重量档": "rate_tiers", "重量档与单价": "rate_tiers", "单价": "rate_tiers",
    "运价": "rate_tiers", "价格档": "rate_tiers",
    "min_kg_shipment": "min_kg_shipment", "整票最低计费重": "min_kg_shipment", "最低计费重": "min_kg_shipment",
    "整票起计重量": "min_kg_shipment",
    "min_kg_carton": "min_kg_carton", "单箱最低计费重": "min_kg_carton",
    "dim_divisor": "dim_divisor", "计泡系数": "dim_divisor", "体积重除数": "dim_divisor", "材积除数": "dim_divisor",
    "rounding_kg": "rounding_kg", "计重进位": "rounding_kg", "计费重进位": "rounding_kg", "进位": "rounding_kg",
    "surcharges": "surcharges", "附加费": "surcharges",
    "fuel_pct": "fuel_pct", "燃油": "fuel_pct", "燃油附加费": "fuel_pct",
    "includes": "includes", "包含项": "includes", "报价包含": "includes",
    "excludes": "excludes", "不含项": "excludes", "不含费用": "excludes", "不含": "excludes",
    "ddp": "ddp", "包税": "ddp", "包税条款": "ddp", "包税范围": "ddp", "进口商主体": "ddp", "税务条款": "ddp",
    "broker_fee_cny": "broker_fee_cny", "报关费": "broker_fee_cny", "清关费": "broker_fee_cny",
    "isf_fee_cny": "isf_fee_cny", "isf": "isf_fee_cny", "isf费": "isf_fee_cny", "isf 费": "isf_fee_cny",
    "isf费用": "isf_fee_cny",
    "transit_days": "transit_days", "时效": "transit_days",
    "validity": "validity", "有效期": "validity", "报价有效期": "validity",
    "liability": "liability", "赔付": "liability", "赔偿": "liability",
    "claims": "claims", "资质": "claims", "资质宣称": "claims",
    "quote_entity": "quote_entity", "报价主体": "quote_entity",
}


KEYWORD_TERMS = (("单箱", "min_kg_carton"), ("计泡", "dim_divisor"), ("体积", "dim_divisor"),
                 ("材积", "dim_divisor"), ("最低计费", "min_kg_shipment"), ("整票", "min_kg_shipment"),
                 ("燃油", "fuel_pct"), ("isf", "isf_fee_cny"), ("报关", "broker_fee_cny"),
                 ("清关", "broker_fee_cny"), ("包税", "ddp"), ("税", "ddp"), ("有效期", "validity"),
                 ("重量档", "rate_tiers"), ("单价", "rate_tiers"), ("附加费", "surcharges"),
                 ("时效", "transit_days"), ("进位", "rounding_kg"))


def term_name(text) -> str:
    """Internal term name for a clause name (table, then keywords); unmapped names are returned stripped."""
    t = str(text).strip()
    hit = TERM_MAP.get(t) or TERM_MAP.get(t.lower())
    if hit:
        return hit
    low = t.lower()
    for kw, name in KEYWORD_TERMS:
        if kw in low:
            return name
    return t


# ---------------------------------------------------------------- requests
def _request(task_input: TaskInput, experiment, method, config_id, stage, cand_id, repeat, seed,
             text, images, max_tokens, system=None) -> dict:
    key = make_run_key(experiment, method, config_id, task_input.task_id, stage, cand_id, repeat)
    return {"run_key": key, "experiment": experiment, "method": method, "config_id": config_id,
            "task_id": task_input.task_id, "stage": stage, "cand_id": cand_id, "repeat": repeat,
            "system": system, "text": text, "images": images, "seed": seed, "max_tokens": max_tokens,
            "prompt_sha256": prompt_hash(system, text, [i["sha256"] for i in images], max_tokens)}


def transcription_requests(task_input: TaskInput, config_id: str, models: dict, *, experiment: str,
                           repeat: int = 0, seed: Optional[int] = None) -> list:
    """One transcription request per untranscribed image quote, all pages of the quote in one request.

    The request's config_id is the vision_config_id of `config_id`; configs that share a vision config
    produce identical run_keys, which the caller de-duplicates.
    """
    vision = models[config_id].vision_config_id
    if vision is None:
        raise ValueError(f"{config_id} has no vision_config_id")
    out = []
    for q in task_input.image_quotes():
        out.append(_request(task_input, experiment, "transcribe", vision, "transcribe", q.cand_id, repeat,
                            seed, prompts.TRANSCRIBE, [image_ref(p) for p in q.images],
                            MAX_TOKENS["transcribe"]))
    return out


def dedupe_requests(reqs) -> list:
    """Drop repeated run_keys, keeping the first; a repeat with a different prompt hash is an error."""
    seen = {}
    out = []
    for r in reqs:
        k = r["run_key"]
        if k in seen:
            if seen[k] != r["prompt_sha256"]:
                raise ValueError(f"run_key {k} repeated with a different prompt")
            continue
        seen[k] = r["prompt_sha256"]
        out.append(r)
    return out


def clean_transcript(text: str) -> str:
    for m in BOX_MARKS:
        text = text.replace(m, "")
    return text.strip()


def apply_transcriptions(task_input: TaskInput, observations, vision_config_id: str) -> TaskInput:
    """Copy of the task input with image quotes replaced by their transcripts.

    observations: executor observation dicts (iterable, or a dict run_key -> observation). A missing,
    invalid or empty observation becomes FAIL_TEXT.
    """
    obs = list(observations.values()) if isinstance(observations, dict) else list(observations)
    by_cand = {}
    for o in obs:
        if o.get("stage") != "transcribe" or o.get("task_id") != task_input.task_id:
            continue
        if o.get("config_id") != vision_config_id:
            continue
        by_cand[o["cand_id"]] = o
    new = copy.deepcopy(task_input)
    for q in new.quotes:
        if q.modality != "image" or q.transcribed:
            continue
        o = by_cand.get(q.cand_id)
        text = clean_transcript(o.get("text") or "") if o and o.get("valid", True) else ""
        q.transcribed = True
        q.transcript_failed = not text
        q.text = text or FAIL_TEXT
    return new


def m0_request(task_input: TaskInput, config_id: str, experiment: str, *, repeat: int = 0,
               seed: Optional[int] = None) -> dict:
    text = prompts.fill(prompts.M0, shipment_text=task_input.shipment_text,
                        quotes_text=task_input.quotes_text())
    return _request(task_input, experiment, "M0", config_id, "answer", "ALL", repeat, seed, text, [],
                    MAX_TOKENS["M0"])


def m1_request(task_input: TaskInput, config_id: str, experiment: str, *, snapshot, freight_floor: dict,
               hts: str, registry_snapshot: dict, value_sets: dict, repeat: int = 0, seed: Optional[int] = None) -> dict:
    text = prompts.fill(prompts.M1, shipment_text=task_input.shipment_text,
                        registry_text=registry_text(registry_snapshot),
                        quotes_text=task_input.quotes_text(), **param_text(snapshot, freight_floor, hts, value_sets))
    return _request(task_input, experiment, "M1", config_id, "answer", "ALL", repeat, seed, text, [],
                    MAX_TOKENS["M1"])


# ---------------------------------------------------------------- parsing
def parse_final_json(text: str, require_key: Optional[str] = "choice") -> Optional[dict]:
    """Last parseable top-level JSON object in the text (fenced or bare); None when there is none.

    With require_key, only objects holding that key count, so a nested fragment of a truncated answer
    is not mistaken for the answer.
    """
    if not isinstance(text, str):
        return None
    dec = json.JSONDecoder()
    found = None
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and (require_key is None or require_key in obj):
            found = obj
        i = end
    return found


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
              "九": 9}
_NUM = r"(?:\d+|[零〇一二两三四五六七八九十]+)"
_COND_WORDS = ("如果", "若", "假如", "取决于")


def _num(tok: str) -> Optional[int]:
    """Integer from digits or a Chinese numeral up to 99."""
    if tok.isdigit():
        return int(tok)
    if tok == "十":
        return 10
    if "十" in tok:
        head, _, tail = tok.partition("十")
        if (head and head not in _CN_DIGITS) or (tail and tail not in _CN_DIGITS):
            return None
        return (_CN_DIGITS[head] if head else 1) * 10 + (_CN_DIGITS[tail] if tail else 0)
    if len(tok) == 1 and tok in _CN_DIGITS:
        return _CN_DIGITS[tok]
    return None


def _strip_markup(s: str) -> str:
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"[*`#>~]+", "", s)
    return s.strip(" \t\r\n:：")


def _cand_by_k(task_input: TaskInput, k: Optional[int]) -> Optional[str]:
    for q in task_input.quotes:
        if q.k == k:
            return q.cand_id
    return None


def _label_to_cand(task_input: TaskInput, label) -> Optional[str]:
    """'报价2' / '报价 2' / '报价二' / '2' / 'C2' -> candidate id; None when it names no quote."""
    s = _strip_markup(str(label))
    cands = {q.cand_id for q in task_input.quotes}
    if s in cands:
        return s
    m = re.fullmatch(rf"(?:报价|quote|Quote)?\s*({_NUM})", s)
    return _cand_by_k(task_input, _num(m.group(1))) if m else None


def _parse_choice(task_input: TaskInput, raw) -> tuple:
    """(decision, cand_id, error): one quote -> choose; several, a conditional or 追问 -> ask."""
    s = _strip_markup(str(raw or ""))
    if "追问" in s or s.lower() == "ask" or any(w in s for w in _COND_WORDS):
        return "ask", None, None
    toks = [m.group(1) or m.group(2) for m in re.finditer(rf"报价\s*({_NUM})|\bC(\d+)\b", s)]
    if not toks and re.fullmatch(_NUM, s):
        toks = [s]
    cands = []
    for t in toks:
        c = _cand_by_k(task_input, _num(t))
        if c is None:
            return None, None, f"unknown choice {str(raw).strip()!r}"
        if c not in cands:
            cands.append(c)
    if not cands:
        return None, None, f"unknown choice {str(raw).strip()!r}"
    if len(cands) > 1:
        return "ask", None, None
    return "choose", cands[0], None


def _ask_field(task_input: TaskInput, raw) -> str:
    s = _strip_markup(str(raw))
    m = re.match(rf"^(报价\s*{_NUM}|C\d+)\s*[.．:：、的\-—–\s]*\s*(.*)$", s)
    if not m:
        return str(raw).strip()
    cand = _label_to_cand(task_input, m.group(1).replace(" ", ""))
    name = m.group(2).strip()
    if cand is None or not name:
        return str(raw).strip()
    return f"{cand}.{term_name(name)}"


def _dec(x) -> Optional[Decimal]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        d = Decimal(str(x))
    else:
        m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(x))
        if not m:
            return None
        try:
            d = Decimal(m.group(0).replace(",", ""))
        except InvalidOperation:
            return None
    return d if d.is_finite() else None


def _strs(x) -> list:
    if x is None:
        return []
    if isinstance(x, (str, int, float)):
        x = [x]
    return [str(v) for v in x] if isinstance(x, list) else []


def _blank_cand() -> dict:
    return {"cost_cny": None, "ddp_level": None, "claim_status": None, "hidden_fees": [], "conflicts": [],
            "gaps": []}


def to_method_output(parsed: Optional[dict], task_input: TaskInput, method: str, config_id: str,
                     run_keys: list, raw_text: Optional[str] = None) -> dict:
    """MethodOutput dict (see docs/plan/方法实现规格.md section 3) from the parsed final JSON."""
    out = {"task_id": task_input.task_id, "method": method.upper(), "config_id": config_id, "decision": None,
           "choice": None, "ask_fields": [], "questions": [], "risks": [],
           "per_cand": {q.cand_id: _blank_cand() for q in task_input.quotes},
           "quote_map": task_input.label_to_cand(),
           "parse_ok": False, "parse_error": None, "calls": list(run_keys),
           "cost": {"USD": 0.0, "CNY": 0.0}, "raw_text": raw_text}
    if not isinstance(parsed, dict) or "choice" not in parsed:
        out["parse_error"] = "no parseable final JSON"
        return out
    decision, cand, err = _parse_choice(task_input, parsed.get("choice"))
    if err:
        out["parse_error"] = err
        return out
    out["decision"], out["choice"] = decision, cand
    out["ask_fields"] = [_ask_field(task_input, f) for f in _strs(parsed.get("ask_fields"))]
    out["questions"] = _strs(parsed.get("questions"))
    out["risks"] = _strs(parsed.get("risks"))
    pq = parsed.get("per_quote")
    if isinstance(pq, dict):
        for label, v in pq.items():
            cand = _label_to_cand(task_input, label)
            if cand is None or not isinstance(v, dict):
                continue
            claims = v.get("claims")
            statuses = None
            if isinstance(claims, list):
                statuses = {i: CLAIM_MAP.get(str(c).strip(), str(c).strip()) for i, c in enumerate(claims)}
            out["per_cand"][cand] = {
                "cost_cny": _dec(v.get("cost_cny")),
                "ddp_level": DDP_MAP.get(str(v.get("ddp_level")).strip()) if v.get("ddp_level") is not None
                else None,
                "claim_status": statuses,
                "hidden_fees": _strs(v.get("hidden_fees")),
                "conflicts": [term_name(c) for c in _strs(v.get("conflicts"))],
                "gaps": [term_name(c) for c in _strs(v.get("gaps"))]}
    out["parse_ok"] = True
    return out
