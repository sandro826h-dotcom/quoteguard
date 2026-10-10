"""Prompt templates of the method ladder (verbatim from docs/plan/方法实现规格.md section 4).

Placeholders are written {name}; `fill` substitutes them in one pass, so braces inside JSON examples and
substituted text are left untouched.
"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")

TRANSCRIBE = r'''请逐字转写这张图片中的全部文字。按从上到下、从左到右的顺序输出，每个表格行占一行，单元格之间用“|”分隔。不要总结，不要补充图片中没有的内容。'''

M0 = r'''我是做亚马逊的卖家，这票货要从国内发到美国，下面是货的情况和几家货代给我的报价。帮我看看选哪家最划算，有没有什么坑。

【货物】
{shipment_text}

【报价】
{quotes_text}

最后请单独用一行 JSON 给出结论，格式为：{"choice": "报价编号或‘需要追问’", "risks": ["风险1", "风险2"], "questions": ["要问货代的问题"]}'''

M1 = r'''你是跨境物流成本审计员。请按下列步骤审计每一份报价，再按决策规则给出结论。

【货物】
{shipment_text}

【参数】（日期 {snapshot_date}）
汇率：1 美元 = {fx} 元人民币。
该商品的美国进口关税合计税率：{duty_rate}（MFN {mfn} + 301 清单 {s301} + 新 301 {s301_2026}）。
MPF：正式报关（货值超过 2500 美元）按货值 0.3464%，最低 34.58 美元、最高 670.86 美元；非正式报关 2.77 美元。HMF：仅海运，货值 0.125%。
不包税运价参照下限：{freight_floor_text}

【登记库查询快照】
{registry_text}

【步骤】
1. 逐份列出报价条款：重量档与单价、整票与单箱最低计费重、计泡系数、计重进位、附加费（含脚注与附表中的费用）、燃油、报关费、ISF 费、包税范围与进口商主体、时效、有效期、资质宣称。
2. 找出前后矛盾的条款和缺少的必需条款（重量档、计泡系数、最低计费重、有效期、不含项、包税条款；快递另加燃油；海运另加 ISF）。
3. 计算每份报价的计费重与卖家需支付的确定到仓成本（元），或然费用（如查验费）单列不计入。不包税时卖家另付关税、MPF、海运另付 HMF。
4. 包税报价：税费下限 =（关税 + MPF，海运加 HMF）÷ 计费重。报价自身收费 ÷ 计费重低于税费下限为“高”；低于税费下限加参照下限为“需核实”；否则“无”。
5. 资质宣称：对照登记库快照判定为“有证据”“证据不足”“与宣称矛盾”“与本次运输不相关”（规则见下）。
6. 决策规则：满足时效且包税风险不为“高”的报价构成可行集，选确定成本最低者；若矛盾或缺失的条款在合理取值范围内会改变最优选择，不作推荐，改为追问，并列出要追问的条款。

【计算与判定规则】
- 计费重：每箱取实重、体积重（长×宽×高÷计泡系数）、单箱最低计费重三者中最大者，按计重进位步长向上取整，乘以箱数后求和，再与整票最低计费重取大。
- 重量档：取下限不超过计费重的最高一档，整票按该档单价计。
- 燃油附加费只按运费计。住宅派送费、偏远地区费只在地址条件满足时计入。报价未写出的可选费用（报关费、各项附加费、空运与快递的 ISF 等）按 0 计，不算缺失。
- 关税按货值计，不含运费。包税只免除报价写明的税项，其余税项由卖家另付（例如只包关税时卖家另付 MPF，海运另付 HMF）。
- 快递与海运没有参照下限时，不设“需核实”档。
- 资质：宣称未写管辖地时两个登记库都查，任一库有记录即“有证据”；记录已注销、类型不符或编号不符为“与宣称矛盾”；空运或快递援引海运执照、认证主体与报价主体不一致、以中国海关认证证明美国清关能力，为“与本次运输不相关”；FMC 的无船承运人须为 NVOCC 类型；海关企业信用记录不能证明无船承运业务备案。
- 追问判定中“合理取值范围”指：{value_sets_text}；前后矛盾的条款取原文两个值。

【报价】
{quotes_text}

最后请单独用一行 JSON 给出结论，格式为：
{"choice": "报价编号或‘需要追问’", "ask_fields": ["报价编号.条款名"], "per_quote": {"报价1": {"cost_cny": 数字或null, "ddp_level": "无|需核实|高", "claims": ["有证据|证据不足|与宣称矛盾|与本次运输不相关"], "hidden_fees": ["费用名"], "conflicts": ["条款名"], "gaps": ["条款名"]}}, "questions": ["要问货代的问题"]}'''

EXTRACT = r'''请从下面这份货代报价中抽取条款，只抽取原文写明或能由表头唯一推出的内容，不要推测。每个条款给出状态和原文片段。

状态：stated（原文写明）、implied（由表头或注释唯一推出）、missing（原文没有）、conflicting（原文两处写法不同，两个值都给出）。
原文片段必须逐字摘自报价原文。

【报价原文】
{quote_text}

只输出一个 JSON 对象，键为下列条款名，每个值为 {"status": ..., "value": ..., "quotes": ["原文片段"]}：
mode（express/air/sea）、rate_tiers（[{"min_kg": 数字, "price": 数字, "currency": "CNY|USD"}]）、min_kg_shipment、min_kg_carton、dim_divisor、rounding_kg、surcharges（[{"code": 原文费用名, "basis": "per_kg|per_ctn|per_shpt|pct_freight", "amount": 数字, "currency": ..., "condition": "always|residential|remote|ctn_longest_side_gt|ctn_kg_gt", "threshold": 数字或null, "contingent": true/false}]）、fuel_pct（小数）、includes、excludes、ddp（{"is_ddp": true/false, "scope": "duty|duty_mpf|all", "duty_terms": "included|actual|capped", "cap_cny_per_kg": 数字或null, "ior": "forwarder|seller|unspecified"}）、broker_fee_cny、isf_fee_cny、transit_days（[最少, 最多]）、validity、liability、claims（[{"type": 原文资质说法, "entity": 主体, "reg_no": 编号或null, "jurisdiction": "US|CN|null"}]）、quote_entity（报价主体名称）。
conflicting 时 value 为两个值的列表。'''



def placeholders(template: str) -> list:
    """Placeholder names of a template in order of first appearance."""
    seen = []
    for m in _PLACEHOLDER.finditer(template):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def fill(template: str, **kw) -> str:
    """Substitute every placeholder; a missing or unused keyword raises KeyError."""
    names = placeholders(template)
    missing = [n for n in names if n not in kw]
    if missing:
        raise KeyError(f"missing placeholders: {missing}")
    extra = [k for k in kw if k not in names]
    if extra:
        raise KeyError(f"unused keywords: {extra}")
    return _PLACEHOLDER.sub(lambda m: str(kw[m.group(1)]), template)
