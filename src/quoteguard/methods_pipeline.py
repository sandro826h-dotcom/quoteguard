"""Extraction-based pipeline methods M2, M3 and M4.

Stages: extraction requests -> parse and normalise -> deterministic pricing (calc_py) ->
M3: DDP gate and claim evidence -> M4: source check, conflict and gap lists, risk-triggered review by a
partner configuration, grid-based choose/ask decision.

This module builds executor requests and processes model text; it never calls a model.
The extractions handed to run_m2/run_m3/run_m4 map cand_id to the raw response text, the parsed dict or a
QuoteTerms object. Method outputs are plain dicts shaped like methods_llm.to_method_output.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from . import locate, prompts
from .calc_py import landed_cost
from .decide import GAP_FIELDS, CandView, TaskView, decide
from .gate import claim_status, ddp_from_breakdown
from .methods_llm import _blank_cand, _request
from .questions import make_questions
from .render import LEX
from .schema import (_TERM_CODECS, CURRENCIES, DDP, BASES, CONDITIONS, DDP_SCOPES, DUTY_TERMS, IOR, MODES, Claim,
                     Field, QuoteTerms, RateTier, Span, Surcharge, _check_term_value)

EXTRACT_MAX_TOKENS = 2500
D0 = Decimal(0)
DEFAULT_ROUNDING_KG = Decimal(1)
DEFAULT_DIM_DIVISOR = {"express": Decimal(5000), "air": Decimal(6000), "sea": Decimal(6000)}
REQUIRED_BASE = ("rate_tiers", "dim_divisor", "min_kg_shipment", "validity", "excludes", "ddp")
REVIEW_PARTNER = {"ds_flash": "qwen_plus", "qwen_plus": "ds_flash", "ds_pro": "qwen_max", "qwen_max": "ds_pro",
                  "glm": "kimi", "kimi": "glm"}
# terms compared between the first and the review extraction
COMPARE_FIELDS = ("rate_tiers", "min_kg_shipment", "min_kg_carton", "dim_divisor", "rounding_kg", "surcharges",
                  "fuel_pct", "ddp", "broker_fee_cny", "isf_fee_cny", "transit_days")
# cost-relevant conflicts the decision grid of decide.py does not know; handled by an outer loop
NONGRID_COST = ("rate_tiers", "min_kg_carton", "rounding_kg", "transit_days")
# conflicting terms that do not enter any cost; collapsed to the first value
INERT_FIELDS = ("channel", "mode", "includes", "excludes", "validity", "liability", "claims")
TERM_NAMES = tuple(n for n in _TERM_CODECS if n != "channel")


# ====================================================================== lexicon
def _key(s) -> str:
    return " ".join(str(s or "").split()).casefold()


# 通用词表，2026-10-10 事先写定，与模板池无关。
# 下面所有归一化词表由 LEX["dev"] 与这份通用词表构成；LEX["test"] 的措辞不得进入任何归一化词表。
GENERIC_ZERO = ("不收", "不收取", "不另收", "不设", "为0", "为零")
GENERIC_CONTINGENT = ("按实际发生", "实报实销", "如产生", "如发生")
GENERIC_SURCHARGE = {"RES": ("住宅", "residential"), "REM": ("偏远", "remote"),
                     "OVS": ("超长", "oversize"), "OVW": ("超重", "overweight"),
                     "INSP": ("查验",)}
GENERIC_FUEL = ("燃油",)
GENERIC_CLAIM = ("fmc", "nvocc", "aeo")


def _dev_lexicons():
    return list(LEX["dev"].values())


def _build_lexicon():
    names, types, modes = {}, {}, {}
    for lx in _dev_lexicons():
        for code, text in lx["names"].items():
            names[_key(text)] = code
        for code, text in lx["types"].items():
            types[_key(text)] = code
        for code, text in lx["mode"].items():
            modes[_key(text)] = code
    return names, types, modes


SURCHARGE_NAMES, CLAIM_TYPES, MODE_WORDS = _build_lexicon()
SURCHARGE_KEYWORDS = tuple((code, words) for code, words in GENERIC_SURCHARGE.items())
CODE_DEFAULTS = {"RES": ("residential", "per_ctn"), "REM": ("remote", "per_kg"),
                 "OVS": ("ctn_longest_side_gt", "per_ctn"), "OVW": ("ctn_kg_gt", "per_ctn"),
                 "INSP": ("always", "per_shpt")}
# (marker, type) in the order tested; the markers are the generic abbreviations plus dev wordings
CLAIM_KEYWORDS = (("MOT_NVOCC", ("交通部",)), ("AEO_CN", ("aeo", "高级认证")), ("FMC_OTI", ("fmc",)),
                  ("NVOCC", ("nvocc", "无船承运")))
MODE_KEYWORDS = (("express", ("快递", "courier", "express")), ("air", ("空运", "air")),
                 ("sea", ("海运", "sea", "ocean")))
UNKNOWN_CLAIM = "UNKNOWN"


def surcharge_code(name) -> Optional[str]:
    """Internal surcharge code for a fee name as written in a quote; None when it maps to nothing."""
    k = _key(name)
    if not k:
        return None
    if k.upper() in CODE_DEFAULTS:
        return k.upper()
    if k in SURCHARGE_NAMES:
        return SURCHARGE_NAMES[k]
    for code, words in SURCHARGE_KEYWORDS:
        if any(w in k for w in words):
            return code
    return None


def claim_type_code(text) -> str:
    """Internal claim type for a qualification as written in a quote; UNKNOWN when it maps to nothing."""
    k = _key(text)
    if k.upper() in ("FMC_OTI", "NVOCC", "MOT_NVOCC", "AEO_CN"):
        return k.upper()
    if k in CLAIM_TYPES:
        return CLAIM_TYPES[k]
    for code, words in CLAIM_KEYWORDS:
        if any(w in k for w in words):
            return code
    return UNKNOWN_CLAIM


def mode_code(text) -> Optional[str]:
    k = _key(text)
    if k in MODES:
        return k
    if k in MODE_WORDS:
        return MODE_WORDS[k]
    for code, words in MODE_KEYWORDS:
        if any(w in k for w in words):
            return code
    return None


# ====================================================================== requests
def extraction_requests(task_input, config_id: str, experiment: str, repeat: int = 1,
                        seed: Optional[int] = None) -> list:
    """One extraction request per quote (image quotes use their transcript)."""
    out = []
    for q in task_input.quotes:
        text = prompts.fill(prompts.EXTRACT, quote_text=task_input.quote_text(q))
        out.append(_request(task_input, experiment, "extract", config_id, "extract", q.cand_id, repeat, seed,
                            text, [], EXTRACT_MAX_TOKENS))
    return out


def review_partner(config_id: str) -> str:
    try:
        return REVIEW_PARTNER[config_id]
    except KeyError:
        raise ValueError(f"no review partner for config {config_id!r}") from None


def review_requests(task_input, config_id: str, experiment: str, cand_ids, repeat: int = 1,
                    seed: Optional[int] = None) -> list:
    """Independent extraction by the partner configuration, only for the listed (triggered) quotes.
    Kept for ad-hoc use; the formal flow reuses the partner's own M2 extraction instead.

    The request carries reviewed_for = config_id so the cost can be booked to the reviewed configuration.
    """
    partner = review_partner(config_id)
    wanted = list(cand_ids)
    out = []
    for q in task_input.quotes:
        if q.cand_id not in wanted:
            continue
        text = prompts.fill(prompts.EXTRACT, quote_text=task_input.quote_text(q))
        r = _request(task_input, experiment, "review", partner, "review", q.cand_id, repeat, seed, text, [],
                     EXTRACT_MAX_TOKENS)
        r["reviewed_for"] = config_id
        out.append(r)
    return out


# ====================================================================== parsing
def parse_extraction(text) -> dict:
    """The JSON object of an extraction response (fenced or bare). Raises ValueError when there is none.

    Among the top-level objects of the text the one with the most known clause names is taken.
    """
    if not isinstance(text, str):
        raise ValueError("extraction response is not text")
    dec = json.JSONDecoder()
    best, best_n = None, 0
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
        if isinstance(obj, dict):
            n = sum(1 for k in obj if k in TERM_NAMES or k == "quote_entity")
            if n > best_n:
                best, best_n = obj, n
        i = end
    if best is None:
        raise ValueError("no extraction JSON object found")
    return best


# ---------------------------------------------------------------- value converters
ZEROABLE = ("min_kg_shipment", "min_kg_carton", "fuel_pct", "broker_fee_cny", "isf_fee_cny")
# zero wordings: the generic list plus the zero values of the dev lexicon tables
_DEV_ZERO_VALUES = tuple(sorted({lx["L"][n + "0"][1] for lx in _dev_lexicons() for n in ZEROABLE if n + "0" in lx["L"]}))
ZERO_QUOTE_WORDS = tuple(dict.fromkeys(GENERIC_ZERO + tuple(w for w in _DEV_ZERO_VALUES if len(w) > 1)))
_ZERO_WORDS = tuple(dict.fromkeys(GENERIC_ZERO + _DEV_ZERO_VALUES))
_ZERO_CLAUSE_KEYS = {"fuel_pct": ("燃油", "fsc"), "broker_fee_cny": ("清关", "customs"),
                     "isf_fee_cny": ("isf",), "min_kg_shipment": ("最低",), "min_kg_carton": ("最低",)}
_NEGATED_ZERO = ("不免", "未免")
_ZERO_CLAUSE_EXCLUDE = {"min_kg_shipment": ("单箱", "每箱")}       # a per-carton minimum is not the shipment minimum
_CLAUSE_SPLIT = re.compile(r"[，；、。\n]")


def _num(v) -> Optional[Decimal]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            d = Decimal(str(v))
        except InvalidOperation:
            return None
        return d if d.is_finite() else None
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", "").replace("，", ""))
        return Decimal(m.group()) if m else None
    return None


def _zeroable(v) -> Optional[Decimal]:
    d = _num(v)
    if d is None and isinstance(v, str) and _key(v) in _ZERO_WORDS:
        return D0
    return d


def _fuel(v) -> Optional[Decimal]:
    d = _zeroable(v)
    if d is None:
        return None
    if isinstance(v, str) and ("%" in v or "％" in v):
        return d / 100
    return d / 100 if d >= 1 else d      # 1 and above are percentages (an integer 1 is 1%)


def _bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        k = _key(v).replace(" ", "")
        if k in ("false", "no", "否", "0", "dap") or k.startswith(("不", "未", "无")) or "不含税" in k \
                or "不包" in k or "另计" in k:
            return False
        if k in ("true", "yes", "是", "1") or any(w in k for w in ("包税", "双清", "ddp", "含税")):
            return True
    return None


# 通用词表，2026-10-10 事先写定，与模板池无关：标准术语与单位
GENERIC_UNITS_TERMS = ("duty", "carton", "公斤", "rmb")
SCOPE_ALL_WORDS = ("hmf", "全部", "全包", "全税", "全额", "all")
SCOPE_DUTY_WORDS = ("关税", "tariff", "duty")
CURRENCY_USD_WORDS = ("usd", "美元", "$", "美金")
CURRENCY_CNY_WORDS = ("cny", "rmb", "人民币", "元", "¥", "￥")
# dev-lexicon wordings of the importer and the jurisdictions, plus fixed generic ones
IOR_WORDS = {**{lx["ior_w"][c]: c for lx in _dev_lexicons() for c in lx["ior_w"]}, "客户": "seller"}
JURISDICTION_WORDS = {**{w: c for lx in _dev_lexicons() for c, w in lx["juris"].items()},
                      "中国": "CN"}


def _scope(v) -> Optional[str]:
    """DDP scope from the enum or a wording; None when it maps to nothing."""
    k = _key(v).replace(" ", "").replace("＋", "+")
    if k in DDP_SCOPES:
        return k
    if not k:
        return None
    if any(w in k for w in SCOPE_ALL_WORDS):
        return "all"
    if "mpf" in k:
        return "duty_mpf"
    if any(w in k for w in SCOPE_DUTY_WORDS):
        return "duty"
    return None


def _currency(v, default: Optional[str] = "CNY") -> Optional[str]:
    k = _key(v)
    if k.upper() in CURRENCIES:
        return k.upper()
    if any(w in k for w in CURRENCY_USD_WORDS):
        return "USD"
    if any(w in k for w in CURRENCY_CNY_WORDS):
        return "CNY"
    return default


def _strlist(v) -> Optional[list]:
    if v is None:
        return None
    if isinstance(v, str):
        parts = [p.strip() for p in re.split(r"[、；;，,\n]", v) if p.strip()]
        return parts
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return None


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(v))
    if not m:
        raise ValueError("bad date")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()


def _validity(v):
    if isinstance(v, str):
        found = re.findall(r"\d{4}\D+\d{1,2}\D+\d{1,2}", v)
        v = found if len(found) == 2 else None
    if not isinstance(v, list) or len(v) != 2:
        return None
    try:
        return [_iso(x) for x in v]
    except (ValueError, TypeError):
        return None


def _transit(v):
    if isinstance(v, str):
        nums = re.findall(r"\d+", v)
        v = [int(n) for n in nums[:2]] if nums else None
    elif isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        v = [v, v]
    if isinstance(v, list) and len(v) == 1:
        v = [v[0], v[0]]
    if not isinstance(v, list) or len(v) != 2:
        return None
    lo, hi = _num(v[0]), _num(v[1])
    if lo is None or hi is None:
        return None
    lo, hi = int(lo), int(hi)
    return [lo, hi] if 0 <= lo <= hi else None


def _tiers(v):
    if not isinstance(v, list) or not v:
        return None
    out = []
    for t in v:
        if not isinstance(t, dict):
            return None
        lo, price = _num(t.get("min_kg", 0)), _num(t.get("price"))
        if lo is None or price is None:
            return None
        out.append(RateTier(lo, _num(t.get("max_kg")), price, _currency(t.get("currency")) or "CNY"))
    out.sort(key=lambda x: x.min_kg)
    if len({t.min_kg for t in out}) != len(out):
        return None
    return out


_BASIS_WORDS = (("per_kg", ("per_kg", "kg", "公斤", "千克")), ("per_ctn", ("per_ctn", "箱", "件", "ctn", "carton")),
                ("per_shpt", ("per_shpt", "票", "单", "shpt", "shipment")), ("pct_freight", ("pct_freight", "运费", "%")))


def _basis(v) -> Optional[str]:
    k = _key(v)
    if k in BASES:
        return k
    for b, words in _BASIS_WORDS:
        if k and any(w in k for w in words):
            return b
    return None


_CONT_MARKERS = tuple(dict.fromkeys(
    tuple(lx["cont"].strip(" ，,") for lx in _dev_lexicons()) + GENERIC_CONTINGENT))


def _clauses(quote: str) -> list:
    """Clauses of a quote (split at ，；、。 and line breaks). A clause that begins with a contingency marker
    continues the previous clause ("fee 30 元，按实际发生收取")."""
    out = []
    for c in _CLAUSE_SPLIT.split(quote or ""):
        if not c.strip():
            continue
        if out and any(_key(c).startswith(m.casefold()) for m in _CONT_MARKERS):
            out[-1] += "，" + c
        else:
            out.append(c)
    return out


def _contingent_evidence(name: str, quotes) -> bool:
    """A clause that names this fee carries a contingency marker (charged only when incurred)."""
    k = _key(name)
    if not k:
        return False
    return any(m.casefold() in _key(c) for q in quotes for c in _clauses(q) if k in _key(c)
               for m in _CONT_MARKERS)


def _zero_by_quotes(name: str, quotes) -> bool:
    """A single clause names the term, states a zero wording and no non-zero number."""
    keys = _ZERO_CLAUSE_KEYS.get(name)
    if not keys:
        return False
    for q in quotes:
        for c in _CLAUSE_SPLIT.split(q):
            k = _key(c)
            if not any(w in k for w in keys) or not any(w in k for w in ZERO_QUOTE_WORDS):
                continue
            if any(w in k for w in _NEGATED_ZERO + _ZERO_CLAUSE_EXCLUDE.get(name, ())):
                continue
            if any(Decimal(n) != 0 for n in re.findall(r"\d+(?:\.\d+)?", k.replace(",", ""))):
                continue
            return True
    return False


def _surcharges(v, notes: list, quotes=()):
    if not isinstance(v, list):
        return None
    out = []
    for s in v:
        if not isinstance(s, dict):
            return None
        name = str(s.get("code") or "").strip()
        code = surcharge_code(name)
        if code is None:
            notes.append(f"unmapped surcharge name: {name!r}")
        basis = _basis(s.get("basis")) or (CODE_DEFAULTS[code][1] if code else None)
        amount = _zeroable(s.get("amount"))
        if basis is None or amount is None:
            notes.append(f"surcharge dropped (basis or amount unreadable): {name!r}")
            continue
        if basis == "pct_freight":
            if amount >= 1:
                amount = amount / 100
            currency = None
        else:
            currency = _currency(s.get("currency"))
        cond = _key(s.get("condition"))
        if cond not in CONDITIONS:
            cond = CODE_DEFAULTS[code][0] if code else "always"
        thr = _num(s.get("threshold"))
        if cond.startswith("ctn_") and (thr is None or basis != "per_ctn"):
            notes.append(f"surcharge {name!r}: condition {cond} without usable threshold, read as always")
            cond, thr = "always", None
        # The model flag alone is not trusted (models mark every conditional fee as contingent): a fee is
        # contingent when its quote says it is charged only when incurred, or it is the inspection fee.
        flag = _bool(s.get("contingent"))
        contingent = _contingent_evidence(name, quotes) or (code == "INSP" and flag is not False)
        out.append(Surcharge(code or name or UNKNOWN_CLAIM, basis, amount, currency, cond,
                             thr if cond.startswith("ctn_") else None, contingent))
    return out


def _ddp(v):
    if not isinstance(v, dict):
        return None
    is_ddp = _bool(v.get("is_ddp"))
    if is_ddp is None:
        return None
    ior = _key(v.get("ior"))
    ior = ior if ior in IOR else IOR_WORDS.get(
        ior, "unspecified")
    if not is_ddp:
        return DDP(False, "duty", "actual", None, ior)
    scope = _scope(v.get("scope"))
    if scope is None:
        return None          # an unreadable scope is a missing term, never a silent default
    terms = _key(v.get("duty_terms"))
    terms = terms if terms in DUTY_TERMS else "included"
    cap = _num(v.get("cap_cny_per_kg"))
    if terms == "capped" and cap is None:
        return None
    return DDP(True, scope, terms, cap if terms == "capped" else None, ior)


def _claims(v, entity: Optional[str], notes: list):
    if not isinstance(v, list):
        return None
    out = []
    for c in v:
        if not isinstance(c, dict):
            return None
        text = str(c.get("type") or "").strip()
        if not text:
            continue
        code = claim_type_code(text)
        if code == UNKNOWN_CLAIM:
            notes.append(f"unknown claim type: {text!r}")
        ent = str(c.get("entity") or "").strip() or entity or "未注明"
        jur = _key(c.get("jurisdiction")).upper()
        jur = jur if jur in ("US", "CN") else JURISDICTION_WORDS.get(str(c.get("jurisdiction") or "").strip())
        reg = c.get("reg_no")
        out.append(Claim(code, ent, str(reg).strip() if reg not in (None, "", "null") else None, jur))
    return out


def _convert(name: str, v, entity, notes, quotes=()):
    """Plain value of one term, or None when it cannot be read."""
    if name == "mode":
        return mode_code(v) if v is not None else None
    if name == "rate_tiers":
        return _tiers(v)
    if name in ("min_kg_shipment", "min_kg_carton", "broker_fee_cny", "isf_fee_cny"):
        return _zeroable(v)
    if name in ("dim_divisor", "rounding_kg"):
        return _num(v)
    if name == "fuel_pct":
        return _fuel(v)
    if name == "surcharges":
        return _surcharges(v, notes, quotes)
    if name in ("includes", "excludes"):
        return _strlist(v)
    if name == "ddp":
        return _ddp(v)
    if name == "transit_days":
        return _transit(v)
    if name == "validity":
        return _validity(v)
    if name == "liability":
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False, sort_keys=True)
        return str(v).strip() if v not in (None, "") else None
    if name == "claims":
        return _claims(v, entity, notes)
    return None


_LIST_VALUED = ("rate_tiers", "surcharges", "includes", "excludes", "claims", "transit_days", "validity")


def _entry(raw):
    """(status, value, quotes) of one extraction entry."""
    if isinstance(raw, dict) and ("status" in raw or "value" in raw):
        status = _key(raw.get("status"))
        value = raw.get("value")
        q = raw.get("quotes", raw.get("quote"))
    else:
        status, value, q = "", raw, None
    quotes = [q] if isinstance(q, str) else [x for x in q if isinstance(x, str)] if isinstance(q, list) else []
    return status, value, [x for x in quotes if x.strip()]


def _field(name: str, raw, entity, notes) -> Field:
    status, value, quotes = _entry(raw)
    if status not in ("stated", "implied", "missing", "conflicting"):
        status = "stated" if value is not None else "missing"
    if status != "missing" and value is None and name in ZEROABLE and _zero_by_quotes(name, quotes):
        value = 0      # "stated" with no value and a quote saying the fee or minimum does not apply
    if status == "missing" or value is None:
        return Field(None, "missing", [])
    spans = [Span("extraction", "text", q) for q in quotes]
    try:
        if status == "conflicting":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("conflicting value is not a pair")
            if name in _LIST_VALUED and not all(isinstance(x, (list, dict)) or name in ("includes", "excludes")
                                                for x in value):
                raise ValueError("conflicting list-valued term without two lists")
            if name in ZEROABLE and _zero_by_quotes(name, quotes):
                value = [0 if x is None else x for x in value]     # a null side that the quote reads as "none"
            vals = [_convert(name, x, entity, notes, quotes) for x in value]
            if any(x is None for x in vals):
                raise ValueError("unreadable conflicting value")
            for x in vals:
                _check_term_value(name, x)
            return Field(vals, "conflicting", spans)
        val = _convert(name, value, entity, notes, quotes)
        if val is None:
            raise ValueError("unreadable value")
        _check_term_value(name, val)
        return Field(val, status, spans)
    except ValueError as e:
        notes.append(f"{name}: {e}")
        return Field(None, "missing", [])


_FEE_ROUTES = (("fuel_pct", ("燃油", "fsc")),
               ("broker_fee_cny", ("清关", "customs filing", "customs clearance", "customs broker")),
               ("isf_fee_cny", ("isf",)))
_NOT_A_FEE_TERM = ("延误", "仓储", "storage", "demurrage", "detention", "delay")


def _route_of(name):
    k = _key(name)
    if any(w in k for w in _NOT_A_FEE_TERM):
        return None
    return next((t for t, words in _FEE_ROUTES if any(w in k for w in words)), None)


def _target_values(f: Field) -> list:
    if f.status == "missing":
        return []
    return list(f.value) if f.status == "conflicting" else [f.value]


def _reroute_fees(fields: dict, notes: list, fx=None) -> None:
    """Fuel, customs filing and ISF fees that the model listed among the surcharges go to their own terms.

    Left in the surcharge list they would be charged twice next to the dedicated term. The entry leaves the
    list when it can be moved (fuel: percentage of freight; filing and ISF: per shipment, always, CNY or USD
    converted at the snapshot rate fx). When the term is already given with the same value the entry is
    merged into it; with another value the term becomes conflicting [given, moved]. An entry that cannot be
    moved stays a surcharge and is noted. A conflicting surcharge term is processed list by list.
    """
    f = fields["surcharges"]
    if f.status not in ("stated", "implied", "conflicting"):
        return
    lists = [list(x) for x in f.value] if f.status == "conflicting" else [list(f.value)]
    moves = {}          # route -> [(list index, entry, amount)]
    for li, lst in enumerate(lists):
        for sc in lst:
            route = _route_of(sc.code) if surcharge_code(sc.code) is None else None
            if route is None:
                continue
            amount = None
            if route == "fuel_pct":
                amount = sc.amount if sc.basis == "pct_freight" else None
            elif sc.basis == "per_shpt" and sc.condition == "always":
                if sc.currency == "CNY":
                    amount = sc.amount
                elif sc.currency == "USD" and fx is not None:
                    amount = sc.amount * fx
            if amount is None:
                notes.append(f"surcharge {sc.code!r} looks like {route} but cannot be moved; kept as surcharge")
                continue
            moves.setdefault(route, []).append((li, sc, amount))
    removed = set()          # (list index, id of entry)
    for route, items in moves.items():
        target = fields[route]
        have = _target_values(target)
        vals = list(have)
        for _li, _sc, amount in items:
            if amount not in vals:
                vals.append(amount)
        if len(vals) > 2:
            notes.append(f"{route}: more than two distinct values, entries kept as surcharges")
            continue
        quotes = []
        for _li, sc, _a in items:
            kn = _key(sc.code)
            quotes += [sp for sp in f.spans if kn and kn in _key(sp.quote)]
        spans = list(target.spans) + [sp for sp in quotes if all(sp.quote != t.quote for t in target.spans)]
        if len(vals) == 1:
            fields[route] = Field(vals[0], target.status if have else "stated", spans)
        else:
            fields[route] = Field(vals, "conflicting", spans)
        for li, sc, _a in items:
            removed.add((li, id(sc)))
            how = "moved to" if not have else "merged into" if len(vals) == len(have) else "conflicts with"
            notes.append(f"surcharge {sc.code!r} {how} {route}")
    if not removed:
        return
    kept = [[sc for sc in lst if (li, id(sc)) not in removed] for li, lst in enumerate(lists)]
    if f.status == "conflicting":
        if _canon_value("surcharges", kept[0]) == _canon_value("surcharges", kept[1]):
            fields["surcharges"] = Field(kept[0], "stated", f.spans)
        else:
            fields["surcharges"] = Field(kept, "conflicting", f.spans)
    else:
        fields["surcharges"] = Field(kept[0], f.status, f.spans)


def normalize(parsed: dict, mode_hint: Optional[str] = None, fx=None) -> QuoteTerms:
    """QuoteTerms from a parsed extraction.

    Status is kept as given; numbers become Decimal; a fuel_pct above 1 is read as a percentage; surcharge
    names and claim wordings map to the internal codes (unmapped surcharges keep their basis and amount,
    unmapped claims get the type UNKNOWN); a term that cannot be read becomes missing. A missing mode is
    filled from mode_hint as implied. fx (CNY per USD) lets a USD filing or ISF fee listed among the surcharges move to its own term. The result carries quote_entity and norm_notes as plain attributes.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    notes: list = []
    _, ent_val, _ = _entry(parsed.get("quote_entity"))
    entity = str(ent_val).strip() if isinstance(ent_val, str) and ent_val.strip() else None
    fields = {n: _field(n, parsed.get(n), entity, notes) for n in TERM_NAMES}
    _reroute_fees(fields, notes, fx)
    if fields["mode"].status == "missing" and mode_hint in MODES:
        fields["mode"] = Field(mode_hint, "implied", [])
    terms = QuoteTerms(**fields)
    terms.quote_entity = entity
    terms.norm_notes = notes
    return terms


def _as_terms(x, mode_hint, fx=None):
    """(QuoteTerms, error or None) from response text, parsed dict or QuoteTerms."""
    if isinstance(x, QuoteTerms):
        return copy.deepcopy(x), None
    try:
        parsed = parse_extraction(x) if isinstance(x, str) else x
        if not isinstance(parsed, dict):
            raise ValueError("extraction is not an object")
        return normalize(parsed, mode_hint, fx), None
    except ValueError as e:
        return normalize({}, mode_hint), str(e)


# ====================================================================== pricing
def required_fields(mode: str) -> tuple:
    return REQUIRED_BASE + (("fuel_pct",) if mode == "express" else ()) + (("isf_fee_cny",) if mode == "sea" else ())


def optional_fields(mode: str) -> tuple:
    """Terms whose absence means zero or not applicable (not a gap)."""
    return ("min_kg_carton", "surcharges", "broker_fee_cny") + (() if mode == "express" else ("fuel_pct",)) + (
        () if mode == "sea" else ("isf_fee_cny",))


def _zero_of(name):
    return [] if name == "surcharges" else D0


def _calc_ready(terms: QuoteTerms, mode: str):
    """Copy with calculator defaults for required inputs; the term-level mode check is switched off."""
    t = copy.deepcopy(terms)
    defaulted = list(t.defaulted())
    if t.dim_divisor.status == "missing":
        t.dim_divisor = Field(DEFAULT_DIM_DIVISOR[mode], "stated", [])
        defaulted.append("dim_divisor")
    if t.rounding_kg.status == "missing":
        t.rounding_kg = Field(DEFAULT_ROUNDING_KG, "stated", [])
        defaulted.append("rounding_kg")
    t.mode = Field(None, "missing", [])
    return t, defaulted


def _fix(terms: QuoteTerms, names, combo):
    t = copy.deepcopy(terms)
    for n, i in zip(names, combo):
        f = getattr(terms, n)
        setattr(t, n, Field(f.value[i], "stated", f.spans))
    return t


def price_terms(shipment, terms: QuoteTerms, snapshot) -> dict:
    """Deterministic cost with defaults for missing inputs. A conflicting term takes the value giving the
    lower cost. Returns {bd, terms, chosen, defaulted} or {error}."""
    names = terms.conflicting()
    best = None
    err = None
    for combo in itertools.product((0, 1), repeat=len(names)):
        t, defaulted = _calc_ready(_fix(terms, names, combo), shipment.mode)
        try:
            bd = landed_cost(shipment, t, snapshot)
        except ValueError as e:
            err = str(e)
            continue
        if best is None or bd.total_cny < best["bd"].total_cny:
            best = {"bd": bd, "terms": t, "chosen": dict(zip(names, combo)), "defaulted": defaulted}
    return best or {"error": err or "not computable"}


def hidden_fee_fields(bd) -> list:
    """Fee terms that add a positive amount to the cost."""
    out = []
    for code, field in (("fuel", "fuel_pct"), ("broker", "broker_fee_cny"), ("isf", "isf_fee_cny")):
        if bd.line(code) > 0:
            out.append(field)
    if any(a > 0 for c, a in bd.lines if c not in ("freight", "fuel", "duty", "mpf", "hmf", "broker", "isf")):
        out.append("surcharges")
    return out


def _transit_ok(terms: QuoteTerms, deadline: int) -> bool:
    td = terms.plain("transit_days")
    return td is not None and td[1] <= deadline


def _claims_of(terms: QuoteTerms) -> list:
    f = terms.claims
    return (f.value[0] if f.status == "conflicting" else f.value) or []


def _entity(terms):
    return getattr(terms, "quote_entity", None)


# ====================================================================== merging and triggers
def _canon_value(name, v):
    def dec(d):
        return None if d is None else format(d.normalize(), "f")
    if v is None:
        return None
    if isinstance(v, Decimal):
        return dec(v)
    if name == "rate_tiers":
        return tuple((dec(t.min_kg), dec(t.price), t.currency) for t in v)
    if name == "surcharges":
        return tuple(sorted((s.basis, dec(s.amount), s.currency or "", s.condition, dec(s.threshold), s.contingent)
                            for s in v))
    if name == "ddp":
        return (False,) if not v.is_ddp else (True, v.scope, v.duty_terms, dec(v.cap_cny_per_kg))
    if name == "transit_days":
        return tuple(v)
    return v


def _effective(name, f: Field, mode: str):
    """Field with the zero default for an optional term that is missing."""
    if f.status == "missing" and name in optional_fields(mode):
        return Field(_zero_of(name), "stated", [])
    return f


def merge_extractions(first: QuoteTerms, second: QuoteTerms, mode: str):
    """(merged, disagreeing fields). Compared terms with different values become conflicting; a required term
    missing in one extraction stays missing; an optional fee found in only one counts as zero in the other."""
    merged = copy.deepcopy(first)
    diff = []
    for name in COMPARE_FIELDS:
        f1, f2 = _effective(name, getattr(first, name), mode), _effective(name, getattr(second, name), mode)
        if f1.status == "conflicting":
            setattr(merged, name, f1)
            continue
        if f2.status == "conflicting":
            setattr(merged, name, f2)
            continue
        if f1.status == "missing" and f2.status == "missing":
            continue
        if f1.status == "missing" or f2.status == "missing":
            setattr(merged, name, Field(None, "missing", []))
            diff.append(name)
            continue
        if _canon_value(name, f1.value) != _canon_value(name, f2.value):
            setattr(merged, name, Field([f1.value, f2.value], "conflicting", f1.spans + f2.spans))
            diff.append(name)
        else:
            setattr(merged, name, Field(f1.value, f1.status, f1.spans + f2.spans))
    return merged, diff


def _gaps(terms: QuoteTerms, mode: str) -> list:
    return [n for n in required_fields(mode) if getattr(terms, n).status == "missing"]


def _collect(task_input, extractions, mode, fx=None):
    """{cand_id: (QuoteTerms, error)} for every quote of the task."""
    out = {}
    for q in task_input.quotes:
        if q.cand_id in extractions:
            out[q.cand_id] = _as_terms(extractions[q.cand_id], mode, fx)
        else:
            out[q.cand_id] = (normalize({}, mode), "no extraction")
    return out


def _assess(task, terms_by_cand, snapshot, freight_floor, registry, gates: bool):
    """{cand_id: info} with price, feasibility and (when gates) DDP level and claim statuses."""
    deadline = task.shipment.deadline_days
    out = {}
    for cid, terms in terms_by_cand.items():
        info = {"price": price_terms(task.shipment, terms, snapshot)}
        p = info["price"]
        info["ddp_level"] = None
        info["claim_status"] = None
        if "error" in p:
            info["feasible"] = False
        else:
            info["feasible"] = _transit_ok(p["terms"], deadline)
            if gates:
                dd = ddp_from_breakdown(task.shipment, p["terms"], snapshot, p["bd"], freight_floor)
                info["ddp_level"] = dd.level
                info["feasible"] = info["feasible"] and dd.level != "high"
        if gates:
            claims = _claims_of(terms)
            info["claim_status"] = {i: claim_status(c, task.shipment.mode, registry, _entity(terms))
                                    for i, c in enumerate(claims)}
        out[cid] = info
    return out


def _reasons(terms, info, mode) -> list:
    r = []
    if terms.conflicting():
        r.append("conflict")
    if _gaps(terms, mode) or "error" in info["price"] or terms.plain("transit_days") is None:
        r.append("gap")
    if info["ddp_level"] not in (None, "none"):
        r.append("ddp_risk")
    if "insufficient" in (info["claim_status"] or {}).values():
        r.append("claim_insufficient")
    return r


def _stage_one(task, task_input, extractions, snapshot, freight_floor, registry):
    mode = task.shipment.mode
    collected = _collect(task_input, extractions, mode, snapshot.fx_cny_per_usd())
    terms, down = {}, {}
    for q in task_input.quotes:
        t, _err = collected[q.cand_id]
        terms[q.cand_id], down[q.cand_id] = locate.verify_terms(t, task_input.quote_text(q))
    info = _assess(task, terms, snapshot, freight_floor, registry, True)
    trig = {cid: _reasons(terms[cid], info[cid], mode) for cid in terms}
    return collected, terms, down, info, {c: r for c, r in trig.items() if r}


def review_triggers(task, task_input, extractions, snapshot, freight_floor, registry_snapshot) -> dict:
    """{cand_id: [reasons]} for the quotes the review applies to: conflict, gap, ddp_risk (level not none),
    claim_insufficient. Computed on the source-checked first extraction."""
    return _stage_one(task, task_input, extractions, snapshot, freight_floor, registry_snapshot)[4]


# ====================================================================== outputs
def _new_output(task_input, method, config_id, calls) -> dict:
    return {"task_id": task_input.task_id, "method": method, "config_id": config_id, "decision": None,
            "choice": None, "ask_fields": [], "questions": [], "risks": [],
            "per_cand": {q.cand_id: _blank_cand() for q in task_input.quotes},
            "parse_ok": True, "parse_error": None, "calls": list(calls), "cost": {"USD": 0.0, "CNY": 0.0},
            "raw_text": None, "diagnostics": {}}


def _cheapest(info, order):
    rows = sorted((info[cid]["price"]["bd"].total_cny, k, cid) for k, cid in enumerate(order)
                  if info[cid]["feasible"])
    return rows[0][2] if rows else None


def _fill_per_cand(out, terms_by_cand, info, mode, with_gates, with_lists):
    for cid, terms in terms_by_cand.items():
        p = info[cid]["price"]
        pc = out["per_cand"][cid]
        if "error" not in p:
            pc["cost_cny"] = p["bd"].total_cny
            pc["hidden_fees"] = hidden_fee_fields(p["bd"])
            pc["conflict_resolved"] = dict(p["chosen"])
            pc["defaulted"] = list(p["defaulted"])
        else:
            pc["error"] = p["error"]
        if with_gates:
            pc["ddp_level"] = info[cid]["ddp_level"]
            pc["claim_status"] = info[cid]["claim_status"]
        if with_lists:
            pc["conflicts"] = terms.conflicting()
            pc["gaps"] = _gaps(terms, mode)


def _parse_status(out, collected):
    errs = [f"{cid}: {e}" for cid, (_t, e) in collected.items() if e]
    out["parse_ok"] = not errs
    out["parse_error"] = "; ".join(errs) or None
    out["diagnostics"]["extraction_failures"] = {cid: bool(e) for cid, (_t, e) in collected.items()}
    out["diagnostics"]["norm_notes"] = {cid: list(getattr(t, "norm_notes", [])) for cid, (t, _e) in collected.items()
                                        if getattr(t, "norm_notes", [])}


def _run_ladder(task, task_input, extractions, snapshot, gates, freight_floor, registry, method, config_id, calls):
    mode = task.shipment.mode
    collected = _collect(task_input, extractions, mode, snapshot.fx_cny_per_usd())
    terms = {cid: t for cid, (t, _e) in collected.items()}
    info = _assess(task, terms, snapshot, freight_floor, registry, gates)
    out = _new_output(task_input, method, config_id, calls)
    _parse_status(out, collected)
    _fill_per_cand(out, terms, info, mode, gates, False)
    choice = _cheapest(info, [q.cand_id for q in task_input.quotes])
    out["decision"] = "choose" if choice else "ask"     # an empty feasible set yields no recommendation
    out["choice"] = choice
    if choice is None:
        out["diagnostics"]["no_feasible_quote"] = True
    return out


def run_m2(task, task_input, extractions, snapshot, structures=None, *, config_id="", calls=()) -> dict:
    """M2: priced extraction, feasible set by transit time only, cheapest feasible quote; never asks.
    `structures` is accepted for interface symmetry and not used. With no feasible quote the output has
    decision "ask" without ask fields."""
    return _run_ladder(task, task_input, extractions, snapshot, False, None, None, "M2", config_id, calls)


def run_m3(task, task_input, extractions, snapshot, structures, freight_floor, registry_snapshot, *,
           config_id="", calls=()) -> dict:
    """M3: M2 plus the DDP gate (level high leaves the feasible set) and claim evidence status."""
    return _run_ladder(task, task_input, extractions, snapshot, True, freight_floor, registry_snapshot, "M3",
                       config_id, calls)


# ---------------------------------------------------------------- M4 decision
def _view_terms(terms: QuoteTerms, mode: str):
    """(terms for the decision grid, conflicts dict, nongrid conflicting names, unevaluable fields)."""
    t = copy.deepcopy(terms)
    for n in INERT_FIELDS:
        f = getattr(t, n)
        if f.status == "conflicting":
            setattr(t, n, Field(f.value[0], "stated", f.spans))
    for n in optional_fields(mode):
        if getattr(t, n).status == "missing":
            setattr(t, n, Field(_zero_of(n), "stated", []))
    if t.rounding_kg.status == "missing":
        t.rounding_kg = Field(DEFAULT_ROUNDING_KG, "stated", [])
    t.mode = Field(None, "missing", [])
    conflicts = {n: list(getattr(t, n).value) for n in GAP_FIELDS if getattr(t, n).status == "conflicting"}
    nongrid = [n for n in NONGRID_COST if getattr(t, n).status == "conflicting"]
    bad = [n for n in ("rate_tiers", "transit_days") if getattr(t, n).status == "missing"]
    return t, conflicts, nongrid, bad


def _decide_m4(task, terms_by_cand, snapshot, freight_floor, value_sets, order):
    """(type, choice, ask_fields, diagnostics)."""
    mode = task.shipment.mode
    prepared = {cid: _view_terms(t, mode) for cid, t in terms_by_cand.items()}
    unevaluable = [f"{cid}.{n}" for cid in order for n in prepared[cid][3]]
    if unevaluable:
        return "ask", None, unevaluable, {"unevaluable": unevaluable}
    nongrid = [(cid, n) for cid in order for n in prepared[cid][2]]
    if len(nongrid) > 6:
        return "ask", None, [f"{c}.{n}" for c, n in nongrid], {"too_many_nongrid_conflicts": len(nongrid)}

    def view(combo):
        cands = []
        for cid in order:
            t, conflicts, _, _ = prepared[cid]
            t = copy.deepcopy(t)
            for (c, n), i in zip(nongrid, combo):
                if c == cid:
                    f = getattr(t, n)
                    setattr(t, n, Field(f.value[i], "stated", f.spans))
            cands.append(CandView(cid, t, conflicts))
        return TaskView(task.shipment, cands, snapshot, freight_floor, value_sets)

    results = {}
    try:
        for combo in itertools.product((0, 1), repeat=len(nongrid)):
            results[combo] = decide(view(combo))
    except ValueError as e:
        gap_ids = [f"{cid}.{n}" for cid in order for n in list(prepared[cid][1]) + _gaps(terms_by_cand[cid], mode)
                   if n in GAP_FIELDS]
        return "ask", None, gap_ids, {"decide_errors": {"count": 1, "reasons": [str(e)]}}

    outcomes = {(d.type, d.choice) for d in results.values()}
    ask = []
    for d in results.values():
        for f in d.ask_fields:
            if f not in ask:
                ask.append(f)
    if len(outcomes) == 1 and next(iter(outcomes))[0] == "choose":
        d = next(iter(results.values()))
        return "choose", d.choice, [], {"grid_size": d.grid_size}
    for j, (cid, n) in enumerate(nongrid):
        key = f"{cid}.{n}"
        decisive = any(
            (results[c].type, results[c].choice) != (results[c[:j] + (1 - c[j],) + c[j + 1:]].type,
                                                     results[c[:j] + (1 - c[j],) + c[j + 1:]].choice)
            for c in results)
        if decisive and key not in ask:
            ask.append(key)
    return "ask", None, ask, {"grid_size": max(d.grid_size for d in results.values())}


def run_m4(task, task_input, extractions, snapshot, structures, freight_floor, registry_snapshot,
           review_extractions, value_sets, *, config_id="", calls=(), review_config_id=None, review_texts=None) -> dict:
    """M4: source check, conflict and gap lists, review merge for triggered quotes, choose/ask on the value grid.

    review_extractions maps cand_id to the partner's extraction (same forms as extractions); only entries of
    triggered quotes are used. review_texts maps cand_id to the quote text the reviewer extracted from (its own
    transcript for image quotes); the reviewer's source check runs against it, falling back to the reviewed
    configuration's text when absent (recorded in diagnostics["review"]["text_fallback"]). In the formal flow they are the partner configuration's own M2 extractions
    (no separate review request); review_config_id names that configuration (default: the partner of
    config_id) and is recorded in diagnostics["review"] together with the reviewed quotes. Two extractions that disagree on a compared term make it conflicting.
    """
    mode = task.shipment.mode
    order = [q.cand_id for q in task_input.quotes]
    collected, terms, down, info, trig = _stage_one(task, task_input, extractions, snapshot, freight_floor,
                                                    registry_snapshot)
    merged_diff, review_errors, reviewed, fallback = {}, {}, [], []
    review_texts = review_texts or {}
    review_extractions = review_extractions or {}
    for q in task_input.quotes:
        cid = q.cand_id
        if cid not in trig or cid not in review_extractions:
            continue
        t2, err = _as_terms(review_extractions[cid], mode, snapshot.fx_cny_per_usd())
        if err:
            review_errors[cid] = err
            continue
        reviewed.append(cid)
        if cid in review_texts:
            src = review_texts[cid]
        else:
            src = task_input.quote_text(q)
            fallback.append(cid)
        t2, _d = locate.verify_terms(t2, src)
        terms[cid], diff = merge_extractions(terms[cid], t2, mode)
        if diff:
            merged_diff[cid] = diff
    if merged_diff:
        info = _assess(task, terms, snapshot, freight_floor, registry_snapshot, True)

    out = _new_output(task_input, "M4", config_id, calls)
    _parse_status(out, collected)
    _fill_per_cand(out, terms, info, mode, True, True)
    typ, choice, ask, dg = _decide_m4(task, terms, snapshot, freight_floor, value_sets, order)
    out["decision"], out["choice"], out["ask_fields"] = typ, choice, ask
    out["questions"] = make_questions(ask, task_input) if typ == "ask" else []
    out["diagnostics"].update({"downgraded": {c: d for c, d in down.items() if d}, "triggers": trig,
                               "review_disagreements": merged_diff, "review_errors": review_errors, **dg})
    if review_config_id is None and config_id in REVIEW_PARTNER:
        review_config_id = REVIEW_PARTNER[config_id]
    out["diagnostics"].setdefault("decide_errors", {"count": 0, "reasons": []})
    out["diagnostics"]["review"] = {"config_id": review_config_id, "reviewed": reviewed, "text_fallback": fallback}
    return out
