"""承运商单元 Q07-u3（DHL Express 10:30，6 区）与 Q08-u3（UPS Worldwide Express Saver，6 区）的初标。

价格列由 carrier_zone6.json 提供（子代理逐字转写，主会话抽查），其余字段在此给出。
"""
from __future__ import annotations

from label_init_13 import DDP, SC, F, M, S, T


def _steps(rows):
    tiers, prev = [], "0"
    for r in rows:
        kg = format(float(r["kg"]), "g")
        tiers.append(T(prev, kg, format(float(r["price"]), "g"), "CNY", "per_shpt"))
        prev = kg
    return tiers


def q07_u3(data):
    d, x = "Q07", data["Q07"]
    per_kg = [T("30.1", "70", 279), T("70.1", "300", 279), T("300.1", "3000", 311)]
    terms = {
        "channel": F("stated", "Express 10:30", S(d, "Express 10:30 – 保证在标准转运工作日的上午十点半前递送。", 26)),
        "mode": F("stated", "express", S(d, "环球快递出口公布价格", 26)),
        "rate_tiers": F("stated", _steps(x["package_rows"]) + per_kg, S(d, "包裹和2.5kg以上文件", 25),
                        S(d, "环球快递出口公布价格", 26), S(d, "每公斤单价", 26)),
        "min_kg_shipment": M(), "min_kg_carton": M(),
        "dim_divisor": F("stated", "5000", S(d, "得出的总数除以5,000。", 15)),
        "rounding_kg": F("stated", "0.5", S(d, x["quotes"]["rounding"]["text"], 26)),
        "surcharges": F("stated", [SC("SVC1030", "per_shpt", 199)], S(d, "服务在环球快递出口价格基础上加收 199元。", 26)),
        "fuel_pct": M(),
        "includes": M(),
        "excludes": F("stated", ["海关关税", "清关相关费用", "增值税", "其他附加费"],
                      S(d, "此价格表以人民币计价，不包括海关关税、清关相关费用、增值税及其他附加费。", 26)),
        "ddp": F("stated", DDP(False, "duty", None),
                 S(d, "此价格表以人民币计价，不包括海关关税、清关相关费用、增值税及其他附加费。", 26)),
        "broker_fee_cny": M(), "isf_fee_cny": M(), "transit_days": M(),
        "validity": F("stated", ["2026-01-01", None], S(d, "生效日期：2026年1月1日", 32)),
        "liability": M(), "claims": M(),
    }
    notes = {"rate_tiers": "6 区出口包裹价：0.5–30kg 及 40/50/60/70kg 为整票价（per_shpt，min_kg 记上一档重量），30.1kg 起另有每公斤单价；"
                           "40–70kg 整票价等于 279 元/kg × 重量。价格列由子代理逐字转写、主会话抽查 19kg 与 30kg 两档",
             "rounding_kg": "30kg 以内按 0.5kg 累加续重，30kg 以上向上取整至整公斤",
             "surcharges": "Express 10:30 在环球快递出口价格基础上每票加收 199 元",
             "fuel_pct": "燃油附加费按月度燃油指数浮动，价格指南未给出具体百分比",
             "transit_days": "Express 10:30 承诺在标准转运工作日上午十点半前递送，无天数区间",
             "zone": "美国在 DHL 区域表中为 6 区（第 24 页“美国 6”）"}
    return terms, notes


def q08_u3(data):
    d, x = "Q08", data["Q08"]
    per_kg = [T(21, 44, "366.9"), T(45, 70, "366.9"), T(71, 99, "357.7"), T(100, 299, "357.7"),
              T(300, None, "321.3")]
    terms = {
        "channel": F("stated", "Worldwide Express Saver", S(d, "出口 – Worldwide Express Saver® ( 全球速快服务)", 18)),
        "mode": F("stated", "express", S(d, "出口 – Worldwide Express Saver® ( 全球速快服务)", 18)),
        "rate_tiers": F("stated", _steps(x["package_rows"]) + per_kg,
                        S(d, "费率以人民币计算并适用于从中国大陆", 18),
                        S(d, "适用于重量超过20公斤的货件 (每公斤运费-逆差重量#除外)", 19),
                        S(d, "21-44 134.80 198.70 197.40 201.70 261.30 366.90", 19), S(d, "300 和以上", 19)),
        "min_kg_shipment": M(), "min_kg_carton": M(),
        "dim_divisor": F("stated", "5000", S(d, "将包裹的总体积尺寸数值除以 5,000", 4)),
        "rounding_kg": M(), "surcharges": M(), "fuel_pct": M(), "includes": M(), "excludes": M(), "ddp": M(),
        "broker_fee_cny": M(), "isf_fee_cny": M(), "transit_days": M(),
        "validity": F("stated", ["2026-06-07", None], S(d, "生效日期: 2026 年 6 月 7 日", 1)),
        "liability": M(), "claims": M(),
    }
    notes = {"rate_tiers": "6 区出口非文件包裹价：0.5–20kg 为整票价（per_shpt，min_kg 记上一档重量），21kg 起为每公斤单价；"
                           "该表适用于除海南、广东、福建、江西、湖南、重庆等省市以外的始发地。10kg/25kg 箱价未记入。价格列由子代理逐字转写",
             "rounding_kg": "第 4 页计费重量取整说明在文字层中缺字（“使用 特快和速快服务，实际重量 个半公斤数”），可读部分指向取下一个半公斤，未能逐字引用，记 missing，请对照 PDF 复核",
             "ddp": "第 8 页有“本费率表不包含根据当地规定……”的关税说明，pdftotext -layout 文字层有插入空格，无法逐字引用，记 missing，请对照 PDF 复核",
             "fuel_pct": "燃油附加费按月度公布，费率指南未给出具体百分比",
             "zone": "美国在出口区域表中速快服务为 6 区（第 14 页）"}
    return terms, notes


def UNITS(data):
    return {"Q07-u3": lambda: q07_u3(data), "Q08-u3": lambda: q08_u3(data)}
