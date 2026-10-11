"""Claude 对 13 个非盲标真实报价单元的初标（规范第八节第 1 条），生成 LabelRecord JSON 并做出处子串自检。

用法：PYTHONPATH=src python3 scripts/label_init_13.py <输出目录> [carrier_zone6.json]
出处片段在去除首尾空白、连续空白合并后须是匿名件正文的子串（PDF 正文取 pdftotext -layout 输出）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from quoteguard.schema import LabelRecord, SeedQuote

ROOT = Path(__file__).resolve().parent.parent
ANON = ROOT / "data" / "seeds" / "anonymous"
MODEL = "Claude（Anthropic，不属于 6 个实验配置），2026-10-12"
DATE = "2026-10-12"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


_TEXT: dict = {}


def doc_text(doc: str) -> tuple[str, list]:
    """(全文规范化文本, 各页规范化文本)。"""
    if doc not in _TEXT:
        p = ANON / f"{doc}.pdf"
        if p.exists():
            raw = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True,
                                 check=True).stdout
            pages = [norm(x) for x in raw.split("\f")]
        else:
            pages = [norm((ANON / f"{doc}.txt").read_text(encoding="utf-8"))]
        _TEXT[doc] = (" ".join(pages), pages)
    return _TEXT[doc]


def S(doc, quote, page=None):
    d = {"doc_id": doc, "kind": "text", "quote": quote}
    if page is not None:
        d["page"] = page
    return d


def F(status, value=None, *spans):
    return {"status": status, "value": value, "spans": list(spans)}


def T(lo, hi, price, cur="CNY", unit="per_kg"):
    return {"min_kg": str(lo), "max_kg": None if hi is None else str(hi), "price": str(price),
            "currency": cur, "unit": unit}


def SC(code, basis, amount, cur="CNY", condition="always", threshold=None, contingent=False):
    return {"code": code, "basis": basis, "amount": str(amount), "currency": cur, "condition": condition,
            "threshold": None if threshold is None else str(threshold), "contingent": contingent}


def DDP(is_ddp, scope, duty_terms, ior="unspecified"):
    return {"is_ddp": is_ddp, "scope": scope, "duty_terms": duty_terms, "cap_cny_per_kg": None, "ior": ior}


DDP_ALL = DDP(True, "all", "included")
M = lambda: F("missing")  # noqa: E731


# ---------------------------------------------------------------- 货代单元
def q01_u2():
    d = "Q01"
    return {
        "channel": F("stated", "标准空派", S(d, "标准空派 （不包税－USD ，汇率：6.9）（材积/6000）", 1)),
        "mode": F("stated", "air", S(d, "标准空派 （不包税－USD ，汇率：6.9）（材积/6000）", 1)),
        "rate_tiers": F("stated", [T(12, 20, "5.94", "USD"), T(21, 70, "5.65", "USD"), T(71, 100, "5.58", "USD"),
                                   T(101, 300, "5.43", "USD"), T(301, 500, "5.29", "USD"),
                                   T(500, None, "5.29", "USD")],
                        S(d, "标准空派 末端区域 12~20KG 21-70KG 71-100KG 101-300KG 301-500KG 500KG+", 1),
                        S(d, "美中（邮编4-7开头） 5.94 5.65 5.58 5.43 5.29 5.29", 1)),
        "min_kg_shipment": M(),
        "min_kg_carton": F("stated", "12", S(d, "单箱不低于 12KG，单箱计费重低于 12KG 按12KG计费", 1)),
        "dim_divisor": F("stated", "6000", S(d, "快递派送材积计算方式为长 *宽*高/6000", 1)),
        "rounding_kg": M(),
        "surcharges": F("stated", [SC("OVW", "per_ctn", 245, condition="ctn_kg_gt", threshold=22),
                                   SC("OVS", "per_ctn", 170, condition="ctn_longest_side_gt", threshold=118),
                                   SC("REM", "per_ctn", 40, condition="remote"),
                                   SC("REM_SUPER", "per_ctn", 60, condition="remote")],
                        S(d, "单件实重超过 22KG, 将加收 RMB245/ 箱的附加费", 1),
                        S(d, "或者最长边超过 118CM ，或者第二最长边长度超过 74CM, 将加收 RMB 170/箱的附加费", 1),
                        S(d, "偏远地址附加费 RMB40/ 件，最低RMB180/ 票", 1),
                        S(d, "超偏远地址附加费 RMB60/ 件，最低RMB250/ 票", 1)),
        "fuel_pct": M(),
        "includes": F("stated", ["门到门派送"], S(d, "以上为门到门自税价格", 1)),
        "excludes": F("stated", ["关税（自税）"], S(d, "以上为门到门自税价格", 1),
                      S(d, "（不包税－USD ，汇率：6.9）", 1)),
        "ddp": F("stated", DDP(False, "duty", None), S(d, "（不包税－USD ，汇率：6.9）", 1),
                 S(d, "以上为门到门自税价格", 1)),
        "broker_fee_cny": F("stated", "350", S(d, "自抬头报关代理费 RMB350/ 票(USD 52/shpt)", 1)),
        "isf_fee_cny": M(),
        "transit_days": F("stated", [10, 15], S(d, "美中（邮编4-7开头） 5.94 5.65 5.58 5.43 5.29 5.29", 1),
                          S(d, "(货入货代国内仓至 FBA签收，自然日 )", 1)),
        "validity": F("stated", ["2026-02-04", None], S(d, "有效期 ：2026年02月04日14:00 起生效至下一", 1), S(d, "有效期：2026年02月04日14:00起生效至下一期更新", 2)),
        "liability": F("stated", "UPS/卡车提取前丢失按采购价值赔偿，最高 RMB40/KG，不退运费；提取后丢失每票最高 100USD，不退运费",
                       S(d, "UPS/ 卡车提取前 ：货物若丢失 ，按照采购价值赔偿 （最高不超过 RMB 40/KG) ，不退运费", 1),
                       S(d, "UPS/卡车提取后 ：货物若丢失 ，赔偿标准为每票货物最高赔偿不超过 100USD ，不退运费", 1)),
        "claims": M(),
    }, {"rate_tiers_cny_table": "第 3 页另有同一渠道的 CNY 价格表（美中 41.00/39.00/38.50/37.50/36.50/36.50），按汇率 6.9 与 USD 表一致；初标取第 1 页 USD 表",
        "transit_days": "参考时效 10-15 天，自然日，货入国内仓至 FBA 签收；该行价格与时效在同一行",
        "broker_fee_cny": "自抬头报关代理费；另有自抬头 BOND 清关操作费 RMB1100/票（使用卖家进口商时）",
        "surcharges": "偏远最低 RMB180/票、超偏远最低 RMB250/票；超尺寸另有围长条件（260–320CM 收 170/箱，>320CM 收 1750/箱）"}


def q02_u3():
    d = "Q02"
    return {
        "channel": F("stated", "美国(EXX.合德.ZIM)海卡", S(d, "美国(EXX.合德.ZIM)海卡")),
        "mode": F("stated", "sea", S(d, "美国(EXX.合德.ZIM)海卡")),
        "rate_tiers": F("stated", [T(0, None, "4.76")], S(d, "¥4.76/kg起 · 15-19天提柜"),
                        S(d, "义乌收货最低档起价（RMB/kg 含税）")),
        "min_kg_shipment": F("stated", "12", S(d, "12kg起收")),
        "min_kg_carton": M(), "dim_divisor": M(), "rounding_kg": M(), "surcharges": M(), "fuel_pct": M(),
        "includes": F("stated", ["含税"], S(d, "义乌收货最低档起价（RMB/kg 含税）")),
        "excludes": M(),
        "ddp": F("implied", DDP(True, "duty", "included"), S(d, "义乌收货最低档起价（RMB/kg 含税）")),
        "broker_fee_cny": M(), "isf_fee_cny": M(),
        "transit_days": F("stated", [15, 19], S(d, "¥4.76/kg起 · 15-19天提柜")),
        "validity": M(), "liability": M(), "claims": M(),
    }, {"price_form": "starting_price：记起价 4.76，档位下限无原文，记 0",
        "min_kg_shipment": "“12kg起收”出现在“美国海运专线”产品卡，该卡涵盖 EXX/合德/ZIM 快船；是否适用于海卡请复核",
        "ddp": "原文只写“含税”，包税范围未说明，按最小口径 duty 记 implied，请复核",
        "transit_days": "15-19 天提柜（到港提柜，不是签收）",
        "validity": "原文“具体以系统最新报价为准”，无有效期"}


def q03_u3():
    d = "Q03"
    return {
        "channel": F("stated", "美森海卡专线", S(d, "美森海卡专线 美森正班船卡车派送，KG计费 价格超低7.5元/KG，除6000不分泡。")),
        "mode": F("stated", "sea", S(d, "美森海卡专线 美森正班船卡车派送")),
        "rate_tiers": F("stated", [T(0, None, "7.5")], S(d, "KG计费 价格超低7.5元/KG"),
                        S(d, "以下美国海派收费为RMB人民币一公斤为标准。")),
        "min_kg_shipment": M(), "min_kg_carton": M(),
        "dim_divisor": F("stated", "6000", S(d, "美森海卡专线 美森正班船卡车派送，KG计费 价格超低7.5元/KG，除6000不分泡。")),
        "rounding_kg": M(), "surcharges": M(), "fuel_pct": M(), "includes": M(), "excludes": M(), "ddp": M(),
        "broker_fee_cny": M(), "isf_fee_cny": M(), "transit_days": M(), "validity": M(), "liability": M(),
        "claims": M(),
    }, {"price_form": "starting_price：“价格超低7.5元/KG”按起价记，档位下限无原文，记 0",
        "ddp": "页面标题写“双清包税报价表”，导航中同组的“普船海卡CBM包税”“美东海卡CBM包税”明写包税，本渠道未写；后文条款（含油、包税、UPS/FedEx 派送）针对海派渠道。无法唯一确定，记 missing",
        "dim_divisor": "“除6000不分泡”：体积重除数 6000，不分泡",
        "terms_scope": "页面第 88 行起的计费与赔偿条款写明针对 UPS/FedEx 派送的海派渠道，未用于本海卡单元"}


def q04_u1():
    d = "Q04"
    return {
        "channel": F("stated", "美国空派超大件专线", S(d, "美国空派超大件专线 双清包税门到门物流")),
        "mode": F("stated", "air", S(d, "美国空派超大件专线 双清包税门到门物流")),
        "rate_tiers": F("stated", [T(150, 200, 78), T(201, 300, 70), T(301, 500, 69), T(501, 1000, 67),
                                   T(1001, None, 66)],
                        S(d, "派送区域 150KG+ (RMB/KG) 201KG+ (RMB/KG) 301KG+ (RMB/KG) 501KG+ (RMB/KG) 1001KG+ (RMB/KG) 提取时效"),
                        S(d, "全美48州 78 70 69 67 66 ★")),
        "min_kg_shipment": M(),
        "min_kg_carton": F("stated", "150", S(d, "单件不足150KG按150KG计费")),
        "dim_divisor": F("stated", "6000", S(d, "计费标准： 材积÷6000，体积重与实际重取大。")),
        "rounding_kg": M(),
        "surcharges": F("stated", [SC("BAT", "per_shpt", 200, contingent=True),
                                   SC("FORKLIFT", "per_ctn", 950, contingent=True)],
                        S(d, "带电产品： 可出带电产品（必须申报并在清单备注“带电”），加收200元/票"),
                        S(d, "叉车费： 国外收货无卸货平台，需加收950元/件叉车费。")),
        "fuel_pct": M(),
        "includes": F("stated", ["空运费", "报关", "美国清关", "关税", "末端派送"],
                      S(d, "价格包含空运费、报关、美国清关、关税及末端派送。")),
        "excludes": M(),
        "ddp": F("stated", DDP_ALL, S(d, "双清包税DDP，免费Bond清关"),
                 S(d, "价格包含空运费、报关、美国清关、关税及末端派送。")),
        "broker_fee_cny": F("stated", "350", S(d, "一般贸易报关：350元/票，续页费50元/页")),
        "isf_fee_cny": M(),
        "transit_days": F("stated", [12, 15], S(d, "出仓后12-15工作日左右签收")),
        "validity": F("stated", [None, "2026-12-31"], S(d, "有效期至 2026年12月31日")),
        "liability": M(), "claims": M(),
    }, {"rate_tiers": "档位写作“150KG+、201KG+…”，上限按下一档起点减 1 记",
        "min_kg_carton": "原文“单件不足150KG按150KG计费”",
        "surcharges": "偏远地址附加费按 UPS/卡车公司账单实报实销，无金额，未记入；品名超 5 个加收 50 元/个，未记入",
        "broker_fee_cny": "“一般贸易报关”为可选报关方式；价格已含报关，两者不在同一条款，未按冲突记，请复核",
        "transit_days": "工作日"}


def _q05(region_quote, lo, hi, transit):
    d = "Q05"
    return {
        "channel": F("stated", "美森快船 CLX", S(d, "一、美森快船 CLX（美西15–20天，最快最稳）")),
        "mode": F("stated", "sea", S(d, "F04美国FBA海运专线（DDP双清包税）")),
        "rate_tiers": F("stated", [T(101, 500, lo)], S(d, region_quote),
                        S(d, "101–500kg：标准价（上表）")),
        "min_kg_shipment": M(), "min_kg_carton": M(),
        "dim_divisor": F("stated", "6000", S(d, "体积重 = 长×宽×高(cm) ÷ 6000（1CBM=167KG）")),
        "rounding_kg": M(),
        "surcharges": F("stated", [SC("OVW", "per_ctn", 120, condition="ctn_kg_gt", threshold=22),
                                   SC("OVS", "per_ctn", 120, condition="ctn_longest_side_gt", threshold=120)],
                        S(d, "超长超重费：单箱>22kg / 单边>120cm，120元/件")),
        "fuel_pct": F("implied", "0", S(d, "燃油/旺季附加费（报价已打包）")),
        "includes": F("stated", ["国内提货/入仓", "报关", "港杂", "海运费", "美国清关", "关税", "燃油/旺季附加费",
                                 "末端FBA派送"],
                      S(d, "国内提货/入仓+报关+港杂"), S(d, "美森/以星/普船海运费"),
                      S(d, "美国自营清关 + 关税 + 自有Bond"), S(d, "燃油/旺季附加费（报价已打包）"),
                      S(d, "末端FBA派送（UPS/卡派双选择）")),
        "excludes": F("stated", ["超长超重费", "海关查验费（违规申报时）", "亚马逊预约等待费"],
                      S(d, "超长超重费：单箱>22kg / 单边>120cm，120元/件"),
                      S(d, "海关查验费：正常查验F04承担，违规申报除外"),
                      S(d, "亚马逊预约等待费：卡派偶发，实报实销")),
        "ddp": F("stated", DDP_ALL, S(d, "费用包含（DDP双清包税，一口价全包）")),
        "broker_fee_cny": M(), "isf_fee_cny": M(),
        "transit_days": transit,
        "validity": M(), "liability": M(), "claims": M(),
    }, {"range_max": str(hi), "price_form": f"range：{lo}–{hi} 元/kg，记下限，上限记 range_max",
        "rate_tiers": "表列价为 101kg+ 标准价；原文另有“100kg 以下上浮1–2元/kg”“500kg+ / 1CBM+ 再降0.5–1元/kg”，为相对区间，未记为档位",
        "fuel_pct": "原文写燃油附加费已打包进报价，按 0 记 implied",
        "ddp": "“美国自营清关 + 关税 + 自有Bond”；进口商身份未写明，ior 记 unspecified",
        "validity": "原文“2026.5 价格”“2026.5 执行”，无起止日期"}


def q05_u1():
    return _q05("美西（8/9 邮编）：12–15 元/kg（101kg+ 大货价）", 12, 15,
                F("stated", [15, 20], S("Q05", "一、美森快船 CLX（美西15–20天，最快最稳）")))


def q05_u2():
    return _q05("美中：14–17 元/kg", 14, 17, M())


def _q06(region, transit_quote, transit, conclusion_quote, lo, hi, alt=None):
    d = "Q06"
    if alt:
        alt_quote, alt_lo, alt_hi = alt
        rt = F("conflicting", [[T(21, None, lo)], [T(21, None, alt_lo)]], S(d, conclusion_quote),
               S(d, "2. 价格（普货，21kg+，DDP双清包税，2026.6）："), S(d, alt_quote),
               S(d, "3）价格差异（普货21kg+）"))
    else:
        rt = F("stated", [T(21, None, lo)], S(d, conclusion_quote), S(d, "1）普货空派（21kg+）"))
    notes = {"range_max": str(hi), "price_form": f"range：{lo}–{hi} 元/kg，记下限"}
    if alt:
        notes["range_max"] = [str(hi), str(alt_hi)]
        notes["rate_tiers"] = (f"同一页“价格（普货，21kg+）”与“收费标准”写 {lo}–{hi} 元/kg，“三、美西 vs 美东 区别详解 3）价格差异"
                               f"（普货21kg+）”写 {alt_lo}–{alt_hi} 元/kg，按第七节记 conflicting，两个候选的 range_max 依次为 {hi}、{alt_hi}")
    return {
        "channel": F("stated", "普货空派", S(d, "1）普货空派（21kg+）")),
        "mode": F("stated", "air", S(d, "1）普货空派（21kg+）")),
        "rate_tiers": rt,
        "min_kg_shipment": M(), "min_kg_carton": M(),
        "dim_divisor": F("stated", "6000", S(d, "体积重：长×宽×高÷6000，实重/体积重取大")),
        "rounding_kg": M(), "surcharges": M(), "fuel_pct": M(),
        "includes": F("stated", ["空运费", "清关", "关税", "派送"], S(d, "二、收费标准（DDP全包：空运费+清关+关税+派送）")),
        "excludes": M(),
        "ddp": F("stated", DDP_ALL, S(d, "二、收费标准（DDP全包：空运费+清关+关税+派送）")),
        "broker_fee_cny": M(), "isf_fee_cny": M(),
        "transit_days": F("stated", transit, S(d, transit_quote)),
        "validity": M(), "liability": M(), "claims": M(),
    }, notes | {"transit_days": "直飞标准空派，门到门签收天数", "validity": "原文“2026.6”，无起止日期",
                "dim_divisor": "另写“分泡30%–50%”，分泡比例为区间，未记入"}


def q06_u1():
    return _q06("美西", "美西（洛杉矶LAX/ONT）：3–5天签收", [3, 5], "美西：53–57元/kg", 53, 57,
                ("美西：37–40元/kg（最便宜）", 37, 40))


def q06_u2():
    return _q06("美中", "美中（芝加哥ORD）：5–7天签收", [5, 7], "美中：55–59元/kg", 55, 59)


def q06_u3():
    return _q06("美东", "美东（纽约JFK/亚特兰大ATL）：6–8天签收", [6, 8], "美东：56–62元/kg", 56, 62,
                ("美东：41–45元/kg，比美西贵3–5元/kg", 41, 45))


def q16_u2():
    d = "Q16"
    return {
        "channel": F("stated", "CSN 急速达 大陆直飞-普货", S(d, "CSN 急速达 大陆直飞-普货")),
        "mode": F("stated", "air", S(d, "美国空派限时速达、经济达、空卡超大件")),
        "rate_tiers": F("stated", [T(12, 20, 65), T(21, 70, 62), T(71, 100, 59), T(101, None, 57)],
                        S(d, "渠道 区域 12-20KG 21-70KG 71-100KG 101KG+ 目的机场（非固定） 参考时效 重货优惠 CSN"),
                        S(d, "(邮编0-6开头) 65 62 59 57 JFK/EWR/MIA")),
        "min_kg_shipment": M(),
        "min_kg_carton": F("stated", "10", S(d, "单件计费重量不低于10KG,不足10KG按10KG计费")),
        "dim_divisor": M(), "rounding_kg": M(),
        "surcharges": F("stated", [SC("RES", "per_ctn", 25, condition="residential"),
                                   SC("REM", "per_kg", 2, condition="remote"),
                                   SC("REM_SUPER", "per_ctn", 120, condition="remote"),
                                   SC("OVW", "per_ctn", 320, condition="ctn_kg_gt", threshold=22),
                                   SC("OVS", "per_ctn", 210, condition="ctn_longest_side_gt", threshold=118)],
                        S(d, "非FBA地址 私人地址 25元/件 最低消费150/票"),
                        S(d, "地址偏远 一般偏远 RMB2/KG， 最低180/票"),
                        S(d, "超级偏远 120RMB/件"),
                        S(d, "超重 1、单件实重超22KG；2、实重超40KG起不接 RMB320元/件"),
                        S(d, "超长 1、最长边超含118CM、或次长超含73CM、属于超长")),
        "fuel_pct": M(),
        "includes": F("stated", ["双清", "含税", "到门派送"],
                      S(d, "价格包含： DDP双清含税到门RMB全包价（价格表仅做参考，以最终确认价格为准）")),
        "excludes": M(),
        "ddp": F("stated", DDP_ALL, S(d, "价格包含： DDP双清含税到门RMB全包价")),
        "broker_fee_cny": M(), "isf_fee_cny": M(),
        "transit_days": F("implied", [2, 4], S(d, "LAX/SFO 2-4工作日提取")),
        "validity": M(),
        "liability": F("stated", "交货后至交服务商前丢失按 RMB40/KG 赔偿，最高不超 100 美金/件，不退运费；交服务商后丢失按服务商官方标准，最高不超 100 美金/件，不退运费",
                       S(d, "甲方按RMB40/KG赔偿给乙方，最高不超100美金/件（不退还货物的托运运费"),
                       S(d, "赔偿金额按服务商官方赔偿标准方案执行，最高赔偿不超100美金每件")),
        "claims": M(),
    }, {"transit_days": "参考时效“2-4工作日提取”写在邮编7-9开头那一行，表格合并单元格，0-6 开头行沿用，记 implied；工作日、到提取",
        "surcharges": "超长另有“短边相加*2+长边≥263CM且<325CM”条件，同为 RMB210/件；私人地址最低 150/票；一般偏远最低 180/票；产品附加费 +7/+4 RMB/kg 针对特定品类，未记入",
        "dim_divisor": "原文未写体积重除数；“1:100表价-1”等为货形比重优惠",
        "delay_compensation": "超 5 个工作日未提取补偿 0.5RMB/KG/天，上限 2RMB/KG",
        "validity": "更新日期 2026-10-09，未写有效期"}


def q17_u1():
    d = "Q17"
    return {
        "channel": F("stated", "美森CLX 正班快递派", S(d, "美森CLX 正班快递派")),
        "mode": F("implied", "sea", S(d, "美森CLX 正班快递派")),
        "rate_tiers": F("stated", [T(12, 20, "18.5"), T(21, 50, "17.5"), T(51, 70, "16.5"), T(71, 99, "16.5"),
                                   T(100, None, "15.5")],
                        S(d, "渠道 区域 12kg+ 21kg+ 51kg+ 71kg+ 100kg+ 参考时效 货形比重"),
                        S(d, "美西（8、9） 18.5 17.5 16.5 16.5 15.5 六截三开")),
        "min_kg_shipment": M(),
        "min_kg_carton": F("stated", "12", S(d, "一票多件单件最低计费重按12KG计费")),
        "dim_divisor": F("stated", "6000", S(d, "材质重计算方式:长cm*宽cm*高cm/6000")),
        "rounding_kg": M(),
        "surcharges": F("stated", [SC("RES", "per_ctn", 30, condition="residential"),
                                   SC("NONFBA_COMM", "per_kg", "0.5", contingent=True),
                                   SC("REM", "per_ctn", 30, condition="remote"),
                                   SC("REM_SUPER", "per_kg", 3, condition="remote"),
                                   SC("OVW", "per_ctn", 200, condition="ctn_kg_gt", threshold="22.5"),
                                   SC("OVS", "per_ctn", 200, condition="ctn_longest_side_gt", threshold=120)],
                        S(d, "非亚马逊、沃尔码商业地址+0.5元/KG，私人住宅地址+30元/件，最低消费100元/票。"),
                        S(d, "偏远地址+30元/件，超偏远地址+3元/KG。"),
                        S(d, "1.单件实重超22.5KG 不超30KG，加收RMB200/件；"),
                        S(d, "2.最长边大于120cm 不超150CM，加收RMB200/件；")),
        "fuel_pct": M(),
        "includes": F("stated", ["双清", "到门派送"], S(d, "价格包含： DDP双清含税到门RMB全包价")),
        "excludes": M(),
        "ddp": F("conflicting", [DDP_ALL, DDP(False, "duty", "actual")],
                 S(d, "价格包含： DDP双清含税到门RMB全包价"), S(d, "6.关税实报实销")),
        "broker_fee_cny": F("conflicting", ["200", "350"], S(d, "1.可接一般贸易报关，报关费200元/单"),
                            S(d, "所有美线渠道，一般贸易报关件，报关费350元/单")),
        "isf_fee_cny": M(), "transit_days": M(), "validity": M(),
        "liability": F("stated", "运输中未提取或丢失最高 40RMB/KG，不退运费；交 UPS/FEDEX 后丢失按官方标准，最高 100USD/票，不退运费",
                       S(d, "在运输过程中如单号未提取或货物丢失按最高不超过40RMB/KG赔偿，不退运费"),
                       S(d, "确认丢失按FEDEX/UPS官方赔偿标准(最高不超100USD/票) 不退运费")),
        "claims": M(),
    }, {"rate_tiers": "档位写作“12kg+、21kg+、51kg+、71kg+、100kg+”，上限按下一档起点减 1 记",
        "min_kg_shipment": "原文“单票一件的最低计费21kg；一票多件……单件最低计费重按12KG”，单票最低计费重按件数而定，无法用单一数值表示，记 missing",
        "ddp": "页首“DDP双清含税到门RMB全包价”与注意事项“6.关税实报实销”冲突，按第七节记 conflicting",
        "broker_fee_cny": "附加说明“报关费200元/单”与“报关”条款“报关费350元/单”冲突",
        "surcharges": "非亚马逊、沃尔玛商业地址 +0.5 元/KG，条件不在枚举内，记 contingent；私人住宅最低消费 100 元/票；超尺寸另有围长 266–310CM 条件，同为 RMB200/件",
        "transit_days": "原文为时效赔付承诺：开船离境后次日起算，超 13 天提取，第 15 天起退 0.5 元/KG/天，最高 5 元/KG；无时效区间",
        "mode": "页面栏目为“美国海运”，渠道名为美森 CLX 正班船"}


# ---------------------------------------------------------------- 承运商单元（价格列由 carrier_zone6.json 提供）
def carrier(doc, data, channel_quote, extra):
    rows = data["package_rows"]
    tiers, prev = [], 0
    for r in rows:
        kg = float(r["kg"])
        tiers.append(T(prev if prev == 0 else prev, kg, r["price"], "CNY", "per_shpt"))
        prev = kg
    for r in data.get("per_kg_rows", []):
        lo, hi = [x.strip().replace(",", "") for x in r["range"].replace("*", "").split("-")]
        tiers.append(T(lo, hi, r["price"], "CNY", "per_kg"))
    return tiers


def build(seed_id, fn, units, carrier_data=None):
    terms, notes = fn() if carrier_data is None else fn(carrier_data)
    meta = units[seed_id]
    rec = {"seed_id": seed_id, "annotator": "claude_initial", "annotated_at": DATE, "minutes_spent": "0",
           "initial_label_model": MODEL, "meta": meta, "terms": terms, "notes": notes}
    LabelRecord.from_dict(rec).validate()
    bad = []
    for name, f in terms.items():
        for s in f["spans"]:
            full, pages = doc_text(s["doc_id"])
            q = norm(s["quote"])
            hay = pages[s["page"] - 1] if s.get("page") else full
            if q not in hay:
                bad.append((name, s["quote"], s.get("page")))
    return rec, bad


UNITS = {"Q01-u2": q01_u2, "Q02-u3": q02_u3, "Q03-u3": q03_u3, "Q04-u1": q04_u1, "Q05-u1": q05_u1,
         "Q05-u2": q05_u2, "Q06-u1": q06_u1, "Q06-u2": q06_u2, "Q06-u3": q06_u3, "Q16-u2": q16_u2,
         "Q17-u1": q17_u1}


def main(argv):
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    units = {u["seed_id"]: u for u in json.loads((ROOT / "data/seeds/units.json").read_text(encoding="utf-8"))}
    extra = {}
    if len(argv) > 2:
        import carrier_units  # noqa: F401  (scripts/carrier_units.py)
        extra = carrier_units.UNITS(json.loads(Path(argv[2]).read_text(encoding="utf-8")))
    total_bad = 0
    for sid, fn in list(UNITS.items()) + list(extra.items()):
        rec, bad = build(sid, fn, units)
        (out / f"{sid}.claude_initial.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                                        encoding="utf-8")
        for b in bad:
            print(f"SPAN NOT FOUND {sid} {b}")
        total_bad += len(bad)
    print(f"done, span failures: {total_bad}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main(sys.argv))
