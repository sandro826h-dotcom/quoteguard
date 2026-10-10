"""Follow-up questions to the forwarder, one per gap term, from fixed templates."""
from __future__ import annotations

import re

from .methods_llm import term_name

TEMPLATES = {
    "dim_divisor": "{q}的计泡系数是 5000 还是 6000？",
    "min_kg_shipment": "{q}整票有没有最低计费重？如果有，是多少公斤？",
    "min_kg_carton": "{q}单箱有没有最低计费重？如果有，是多少公斤？",
    "rounding_kg": "{q}的计费重按多少公斤进位？",
    "fuel_pct": "{q}是否另收燃油附加费？费率是多少？",
    "ddp": "{q}是否包含进口关税和税费？包含哪些税项（关税、MPF、HMF），进口商（IOR）是谁，税金是否有上限？",
    "broker_fee_cny": "{q}是否另收报关（清关）费？金额是多少？",
    "isf_fee_cny": "{q}是否另收 ISF 申报费？金额是多少？",
    "surcharges": "{q}的附加费（住宅派送、偏远地区、超尺寸、超重等）各按多少收取，是否适用于本票货物？",
    "rate_tiers": "{q}各重量档的单价是多少，币种是什么？",
    "transit_days": "{q}从发运到仓库的时效是多少天？",
    "validity": "{q}的报价有效期是什么时候？",
    "excludes": "{q}有哪些费用不包含在报价内？",
    "includes": "{q}的报价包含哪些费用？",
    "mode": "{q}走的是快递、空运还是海运？",
    "liability": "{q}的货损赔付条款是什么？",
    "claims": "{q}宣称的资质对应的主体名称和登记编号是什么？",
}
GENERIC = "{q}的“{name}”条款具体是什么？请给出明确说明。"
_SPLIT = re.compile(r"^(?P<cand>[^.．]+)[.．](?P<name>.+)$")


def question_for(ask_field: str, task_input) -> str:
    """Question for one ask field of the form '<cand_id>.<term>' (Chinese term names are mapped)."""
    s = str(ask_field).strip()
    m = _SPLIT.match(s)
    if m:
        cand, name = m.group("cand").strip(), m.group("name").strip()
        label = task_input.cand_to_label().get(cand, cand)
    else:
        label, name = "该报价", s
    term = term_name(name)
    return TEMPLATES.get(term, GENERIC).format(q=label, name=name)


def make_questions(ask_fields, task_input) -> list:
    """Questions for the ask fields in order, without repeats."""
    out = []
    for f in ask_fields:
        q = question_for(f, task_input)
        if q not in out:
            out.append(q)
    return out
