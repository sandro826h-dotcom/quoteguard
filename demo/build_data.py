"""Build demo/data/cases.json and demo/data/assets/ from the recorded pilot observations.

Offline only: no model call, no network. The recorded observations of the pilot batches are re-assembled with the
repository code (assemble, parse_extraction, normalize, source check, review merge, price_terms, gates), and the
result is exported as plain JSON for the static pages. The three exported cases are synthetic dev-set tasks.

Usage:  PYTHONPATH=src python3 demo/build_data.py

The script reads runs/ and never writes there (render files go to a temporary directory). Before it finishes it
checks the exported M4 decision, choice, questions and per-quote costs against the stored re-scoring in
runs/derived/pilot_rescore_20261010_fullreview and exits with an error when they differ.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import openpyxl  # noqa: E402

from quoteguard import gate, locate  # noqa: E402
from quoteguard import methods_llm as ml  # noqa: E402
from quoteguard import methods_pipeline as mp  # noqa: E402
from quoteguard.calc_py import landed_cost  # noqa: E402
from quoteguard.orchestrate import Context, assemble, load_observations  # noqa: E402
from quoteguard.schema import Task  # noqa: E402
from quoteguard.util import sha256_file  # noqa: E402

DEMO = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data/taskgen/dev_v3"
TASKS_DIR = ROOT / "data/tasks/dev/tasks"
GOLD_DIR = ROOT / "data/gold/dev"
DERIVED = ROOT / "runs/derived/pilot_rescore_20261010_fullreview"
SEED = 20261010

CASES = [
    {"id": "A", "task": "T-dev-004", "exp": "pilota", "configs": ["ds_flash", "qwen_plus"], "dir": "a",
     "batches": ["pilota-A-live", "pilota-B-live", "pilota-B-retry-live"], "layer_cn": "冲突",
     "scenario": "4 份空派报价；报价1和报价4的整票最低计费重在正文与备注中前后不一，最优报价取决于报价4的真实取值。"},
    {"id": "B", "task": "T-dev-010", "exp": "pilota", "configs": ["ds_flash", "qwen_plus"], "dir": "a",
     "batches": ["pilota-A-live", "pilota-B-live", "pilota-B-retry-live"], "layer_cn": "隐含",
     "scenario": "4 份快递表格报价；燃油附加费、清关费等隐性费用不在表头单价里，另有报价不满足时效。"},
    {"id": "C", "task": "T-dev-020", "exp": "pilotb", "configs": ["glm", "kimi"], "dir": "b",
     "batches": ["pilotb-A-live", "pilotb-B-live", "pilotb-B-retry-live"], "layer_cn": "信息不足",
     "scenario": "4 份空派报价；报价1的包税价格低于税费下限，报价2的包税条款缺失，信息不足时应追问而不是直接选。"},
]

FIELD_ORDER = ["quote_entity", "mode", "rate_tiers", "min_kg_shipment", "min_kg_carton", "dim_divisor",
               "rounding_kg", "fuel_pct", "surcharges", "broker_fee_cny", "isf_fee_cny", "ddp", "includes",
               "excludes", "transit_days", "validity", "liability", "claims"]
FIELD_CN = {
    "quote_entity": "报价主体", "mode": "运输方式", "rate_tiers": "计费方式与阶梯单价",
    "min_kg_shipment": "整票最低计费重", "min_kg_carton": "单箱最低计费重", "dim_divisor": "计泡系数",
    "rounding_kg": "计重进位", "fuel_pct": "燃油附加费", "surcharges": "附加费", "broker_fee_cny": "报关（清关）费",
    "isf_fee_cny": "ISF 申报费", "ddp": "包税范围与进口商（IOR）", "includes": "报价包含", "excludes": "报价不含",
    "transit_days": "时效", "validity": "有效期", "liability": "赔付", "claims": "资质宣称",
}
STATUS_CN = {"stated": "明示", "implied": "隐含", "missing": "未写明", "conflicting": "前后矛盾"}
MODE_CN = {"express": "快递", "air": "空运", "sea": "海运"}
SHIP_MODE_CN = {"express": "快递", "air": "空派", "sea": "海派"}
MODALITY_CN = {"text": "文字", "table": "表格", "image": "图片"}
LINE_CN = {"freight": "运费", "fuel": "燃油附加费", "duty": "关税", "mpf": "MPF（商品处理费）",
           "hmf": "HMF（港口维护费）", "broker": "报关（清关）费", "isf": "ISF 申报费", "RES": "住宅派送费",
           "REM": "偏远地区附加费", "OVS": "超尺寸费", "OVW": "超重费", "INSP": "查验费"}
SURCHARGE_CN = {"RES": "住宅派送费", "REM": "偏远地区附加费", "OVS": "超尺寸费", "OVW": "超重费", "INSP": "查验费"}
DDP_LEVEL_CN = {"none": "不标", "verify": "需核实", "high": "高", None: "不适用"}
CLAIM_STATUS_CN = {"supported": "有证据", "insufficient": "证据不足", "contradicted": "证据与宣称矛盾",
                   "irrelevant": "证据与本次运输不相关"}
CLAIM_TYPE_CN = {"NVOCC": "NVOCC（无船承运人）", "FMC_OTI": "美国 FMC 备案", "MOT_NVOCC": "交通部无船承运备案",
                 "AEO_CN": "海关 AEO 高级认证", "UNKNOWN": "类型未识别"}
CLAIM_REASON_CN = {
    "Chinese customs certification does not evidence US clearance capability": "中国海关认证不能证明美国清关能力",
    "sea licence cited for an air or express channel": "海运类许可被用于空运或快递渠道",
    "certified entity differs from the quoting entity": "被认证主体与报价主体不一致",
}
TRIGGER_CN = {"conflict": "报价内存在冲突字段", "gap": "存在缺口字段", "ddp_risk": "包税价格需核实",
              "claim_insufficient": "资质宣称证据不足"}
SCOPE_CN = {"duty": "只含关税", "duty_mpf": "含关税和 MPF", "all": "含全部进口环节税费"}
DUTY_TERMS_CN = {"included": "税金由报价承担", "actual": "税金按实际发生另算", "capped": "税金设有上限"}
IOR_CN = {"forwarder": "货代", "seller": "卖家", "unspecified": "未指明"}
COND_CN = {"always": "", "residential": "送住宅地址", "remote": "偏远地址"}
BASIS_UNIT = {"per_kg": "/kg", "per_ctn": "/箱", "per_shpt": "/票"}


# ---------------------------------------------------------------- formatting
def dec(x) -> str:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return format(d.normalize(), "f")


def money_s(x) -> str:
    return format(Decimal(str(x)), "f")


def cur_cn(c) -> str:
    return {"CNY": "元", "USD": "美元"}.get(c, c or "")


def fmt_surcharge(s) -> str:
    name = SURCHARGE_CN.get(s.code, s.code)
    if s.basis == "pct_freight":
        amt = f"运费的 {dec(s.amount * 100)}%"
    else:
        amt = f"{dec(s.amount)} {cur_cn(s.currency)}{BASIS_UNIT.get(s.basis, '')}"
    cond = s.condition
    if cond == "ctn_longest_side_gt":
        c = f"单箱最长边超 {dec(s.threshold)} cm"
    elif cond == "ctn_kg_gt":
        c = f"单箱超 {dec(s.threshold)} kg"
    else:
        c = COND_CN.get(cond, cond)
    out = f"{name}：{amt}"
    if c:
        out += f"（{c}）"
    if s.contingent:
        out += "，按实际发生收取"
    return out


def fmt_value(name: str, v) -> str:
    """Readable string of one normalised term value."""
    if v is None:
        return ""
    if name == "mode":
        return MODE_CN.get(v, str(v))
    if name == "rate_tiers":
        return "；".join(f"{dec(t.min_kg)} kg 起 {dec(t.price)} {cur_cn(t.currency)}/kg" for t in v)
    if name in ("min_kg_shipment", "min_kg_carton"):
        return "无最低计费重" if Decimal(str(v)) == 0 else f"{dec(v)} kg"
    if name == "dim_divisor":
        return dec(v)
    if name == "rounding_kg":
        return f"{dec(v)} kg"
    if name == "fuel_pct":
        return "不收" if Decimal(str(v)) == 0 else f"{dec(Decimal(str(v)) * 100)}%（运费的比例）"
    if name == "surcharges":
        return "；".join(fmt_surcharge(s) for s in v) if v else "无"
    if name in ("broker_fee_cny", "isf_fee_cny"):
        return "不收" if Decimal(str(v)) == 0 else f"{dec(v)} 元/票"
    if name == "ddp":
        if not v.is_ddp:
            return f"不包税（DAP），进口商：{IOR_CN.get(v.ior, v.ior)}"
        s = f"包税（DDP），{SCOPE_CN.get(v.scope, v.scope)}，{DUTY_TERMS_CN.get(v.duty_terms, v.duty_terms)}"
        if v.duty_terms == "capped" and v.cap_cny_per_kg is not None:
            s += f"（{dec(v.cap_cny_per_kg)} 元/kg）"
        return s + f"，进口商：{IOR_CN.get(v.ior, v.ior)}"
    if name in ("includes", "excludes"):
        return "、".join(v) if v else "无"
    if name == "transit_days":
        return f"{v[0]} 天" if v[0] == v[1] else f"{v[0]}–{v[1]} 天"
    if name == "validity":
        return f"{v[0] or '—'} 至 {v[1] or '—'}"
    if name == "claims":
        if not v:
            return "无资质宣称"
        return "；".join(f"{CLAIM_TYPE_CN.get(c.type, c.type)}，主体 {c.entity}"
                        + (f"，登记号 {c.reg_no}" if c.reg_no else "") for c in v)
    return str(v)


def field_text(name: str, f) -> str:
    if f.status == "missing":
        return ""
    if f.status == "conflicting":
        return " ｜ ".join(fmt_value(name, x) for x in f.value)
    return fmt_value(name, f.value)


def get_field(terms, name: str):
    """Field object of a term, with quote_entity as a pseudo field built from the plain attribute."""
    if name == "quote_entity":
        e = getattr(terms, "quote_entity", None)
        from quoteguard.schema import Field
        return Field(e, "stated" if e else "missing", [])
    return getattr(terms, name)


# ---------------------------------------------------------------- helpers
def labelled(items: list, cid_label: dict) -> list:
    return [cid_label.get(c, c) for c in items]


def shipment_view(task: Task) -> dict:
    s = task.shipment
    value = sum((g.unit_value_usd * g.qty for g in s.goods), Decimal(0))
    n_ctn = sum(c.n for c in s.cartons)
    d = s.dest
    nonfba = d.fba_code.upper().startswith("NONFBA")
    return {
        "goods": [{"desc": g.desc, "hts": g.hts, "qty": g.qty, "unit_value_usd": dec(g.unit_value_usd)} for g in s.goods],
        "total_value_usd": dec(value), "mode": s.mode, "mode_cn": SHIP_MODE_CN[s.mode],
        "cartons": [{"n": c.n, "length_cm": dec(c.length_cm), "width_cm": dec(c.width_cm), "height_cm": dec(c.height_cm),
                     "kg": dec(c.kg)} for c in s.cartons],
        "n_cartons": n_ctn, "gross_kg": dec(sum((c.kg * c.n for c in s.cartons), Decimal(0))),
        "dest": {"code": "收货地址" if nonfba else d.fba_code, "zip": d.zip,
                 "addr_type_cn": "住宅地址" if d.addr_type == "residential" else "商业地址",
                 "remote_cn": "偏远地区" if d.remote else "非偏远地区"},
        "ship_date": s.ship_date, "deadline_days": s.deadline_days,
    }


def read_xlsx(path) -> list:
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb:
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        while rows and not any(rows[-1]):
            rows.pop()
        out.append({"name": ws.title, "rows": rows})
    return out


def obs_models(obs, keys):
    req, ret = set(), set()
    for k in keys:
        o = obs.get(k)
        if o and o.get("model"):
            req.add(o["model"].get("requested"))
            ret.add(o["model"].get("returned"))
    return sorted(x for x in req if x), sorted(x for x in ret if x)


def run_ext(ti, cfg, exp, obs):
    reqs = mp.extraction_requests(ti, cfg, exp, repeat=1, seed=SEED)
    return {r["cand_id"]: obs[r["run_key"]]["text"] for r in reqs if r["run_key"] in obs}, [r["run_key"] for r in reqs]


def cost_range(shipment, terms, snapshot):
    """(min, max) of the total over both values of every conflicting term; None when nothing conflicts."""
    names = terms.conflicting()
    if not names:
        return None
    import itertools
    totals = []
    for combo in itertools.product((0, 1), repeat=len(names)):
        t, _d = mp._calc_ready(mp._fix(terms, names, combo), shipment.mode)
        try:
            totals.append(landed_cost(shipment, t, snapshot).total_cny)
        except ValueError:
            continue
    if not totals or min(totals) == max(totals):
        return None
    return [money_s(min(totals)), money_s(max(totals))]


def spans_view(f, text: str) -> list:
    seen, out = set(), []
    for s in f.spans:
        q = " ".join(str(s.quote).split())
        if not q or q in seen:
            continue
        seen.add(q)
        out.append({"text": s.quote, "verified": locate.verify(s.quote, text)})
    return out


def pair_values(name, f, spans):
    """Two readable values of a conflicting term, each with the quotes that go with it when they pair up."""
    vals = [fmt_value(name, x) for x in f.value]
    qs = [s["text"] for s in spans]
    if len(qs) == 2:
        return [{"value": vals[0], "quotes": [qs[0]]}, {"value": vals[1], "quotes": [qs[1]]}]
    return [{"value": vals[0], "quotes": qs}, {"value": vals[1], "quotes": qs}]


# ---------------------------------------------------------------- main build
def build(work: Path) -> dict:
    # a task directory with only the three exported tasks keeps assemble() small
    tdirs = {}
    for c in CASES:
        d = tdirs.setdefault(c["dir"], work / f"tasks_{c['dir']}")
        d.mkdir(exist_ok=True)
        shutil.copy(TASKS_DIR / f"{c['task']}.json", d / f"{c['task']}.json")
    assets = DEMO / "data" / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir(parents=True)

    cases_out, m4_exports = [], {}
    groups = {}
    for c in CASES:
        groups.setdefault(c["dir"], c)
    outputs = {}
    for gk, g in groups.items():
        out = work / f"asm_{gk}"
        batches = [str(ROOT / "runs" / b) for b in g["batches"]]
        res = assemble(str(tdirs[gk]), str(GOLD_DIR), batches, g["configs"], out, methods=("M0", "M4"),
                       experiment=g["exp"], data_dir=DATA_DIR, seed=SEED)
        print(f"assemble {gk}: outputs={res['n_outputs']} missing={len(res['missing_observations'])} "
              f"observations={res['n_observations']}")
        if res["missing_observations"]:
            raise SystemExit(f"missing observations in group {gk}: {res['missing_observations'][:3]}")
        outputs[gk] = out / "outputs"

    for case in CASES:
        ctx = Context(str(tdirs[case["dir"]]), work / f"ctx_{case['id']}", data_dir=DATA_DIR)
        task = next(t for t in ctx.tasks if t.task_id == case["task"])
        rr, ti0 = ctx.prepare(task)
        reg = ctx.registry_snapshot(task)
        obs = load_observations([str(ROOT / "runs" / b) for b in case["batches"]])
        obs_list = list(obs.values())
        gold = json.loads((GOLD_DIR / f"{case['task']}.json").read_text("utf-8"))
        cid_label = ti0.cand_to_label()
        order = [q.cand_id for q in ti0.quotes]
        cand_by_id = {c.cand_id: c for c in task.candidates}

        quotes_out = []
        for q in ti0.quotes:
            cand = cand_by_id[q.cand_id]
            pres = cand.presentation
            files = [Path(f) for f in rr[q.cand_id].files]
            main = next(f for f in files if f.suffix in (".txt", ".xlsx", ".png"))
            asset_name = f"{case['task']}_{q.cand_id}{main.suffix}"
            shutil.copy(main, assets / asset_name)
            item = {"cand_id": q.cand_id, "label": q.label, "modality": q.modality,
                    "modality_cn": MODALITY_CN[q.modality], "alias": pres["forwarder_alias"],
                    "entity": pres["quote_entity"],
                    "file": {"name": asset_name, "path": f"data/assets/{asset_name}", "sha256": sha256_file(main)}}
            if q.modality == "text":
                item["original"] = {"kind": "text", "text": main.read_text("utf-8").strip()}
            elif q.modality == "table":
                item["original"] = {"kind": "table", "sheets": read_xlsx(main)}
            else:
                item["original"] = {"kind": "image", "src": f"data/assets/{asset_name}"}
            quotes_out.append(item)

        configs_out = {}
        for cfg in case["configs"]:
            partner = mp.review_partner(cfg)
            vcfg = ctx.models[cfg].vision_config_id
            pvcfg = ctx.models[partner].vision_config_id
            ti = ml.apply_transcriptions(ti0, obs_list, vcfg)
            ti_p = ml.apply_transcriptions(ti0, obs_list, pvcfg)
            ext, ekeys = run_ext(ti, cfg, case["exp"], obs)
            pext, pkeys = run_ext(ti_p, partner, case["exp"], obs)
            m4 = json.loads((outputs[case["dir"]] / "M4" / cfg / f"{case['task']}.json").read_text("utf-8"))
            m0 = json.loads((outputs[case["dir"]] / "M0" / cfg / f"{case['task']}.json").read_text("utf-8"))
            mode = task.shipment.mode
            fx = ctx.snapshot.fx_cny_per_usd()

            # first extraction, source-checked; review merge as run_m4 does it
            collected, terms, down, _info, trig = mp._stage_one(task, ti, ext, ctx.snapshot, ctx.freight_floor, reg)
            first = {cid: collected[cid][0] for cid in order}
            partner_terms, merged_diff = {}, {}
            for q in ti.quotes:
                cid = q.cand_id
                t2, err = mp._as_terms(pext[cid], mode, fx)
                if err:
                    raise SystemExit(f"review extraction failed for {cid}: {err}")
                partner_terms[cid] = t2
                t2v, _d = locate.verify_terms(t2, ti_p.quote_text(ti_p.quotes[order.index(cid)]))
                terms[cid], diff = mp.merge_extractions(terms[cid], t2v, mode)
                if diff:
                    merged_diff[cid] = diff
            info = mp._assess(task, terms, ctx.snapshot, ctx.freight_floor, reg, True)
            if merged_diff != {k: v for k, v in m4["diagnostics"]["review_disagreements"].items()}:
                raise SystemExit(f"review disagreements differ from M4 output ({case['id']}/{cfg})")

            per_q = []
            for q in ti.quotes:
                cid = q.cand_id
                text = ti.quote_text(q)
                ptext = ti_p.quote_text(ti_p.quotes[order.index(cid)])
                pc = m4["per_cand"][cid]
                t1, tm, tp = first[cid], terms[cid], partner_terms[cid]
                i = info[cid]
                price = i["price"]
                # consistency of the replicated pipeline with the assembled M4 output
                assert tm.conflicting() == pc["conflicts"], (case["id"], cfg, cid, "conflicts")
                assert ("error" not in price) and money_s(price["bd"].total_cny) == pc["cost_cny"], (case["id"], cfg, cid, "cost")
                assert i["ddp_level"] == pc["ddp_level"], (case["id"], cfg, cid, "ddp")

                fields = []
                for name in FIELD_ORDER:
                    f = get_field(t1, name)
                    fp = get_field(tp, name)
                    spans = spans_view(f, text)
                    row = {"key": name, "label": FIELD_CN[name], "status": f.status, "status_cn": STATUS_CN[f.status],
                           "value": field_text(name, f), "quotes": spans,
                           "downgraded": name in down[cid],
                           "gap": name in pc["gaps"], "defaulted": name in pc["defaulted"],
                           "review_value": field_text(name, fp), "review_status": fp.status,
                           "review_status_cn": STATUS_CN[fp.status], "review_differs": name in merged_diff.get(cid, [])}
                    if name == "quote_entity":
                        row["quotes"] = []
                    fields.append(row)

                conflicts = []
                for name in tm.conflicting():
                    f = getattr(tm, name)
                    f1 = getattr(t1, name)
                    origin = "intra" if f1.status == "conflicting" else "review"
                    sp = spans_view(f, text)
                    resolved = pc["conflict_resolved"].get(name)
                    conflicts.append({
                        "key": name, "label": FIELD_CN[name], "origin": origin,
                        "origin_cn": "同一份报价内两处表述不一致" if origin == "intra" else f"两个模型抽取不一致（{cfg} 与 {partner}）",
                        "values": pair_values(name, f, sp), "resolved_idx": resolved,
                        "in_cost": name not in mp.INERT_FIELDS})
                # ddp numbers
                ddp_term = tm.plain("ddp") if tm.ddp.status != "conflicting" else None
                p_terms = price["terms"]
                bd = price["bd"]
                dd = gate.ddp_from_breakdown(task.shipment, p_terms, ctx.snapshot, bd, ctx.freight_floor)
                floor_all = gate.tax_floor_cny(task.shipment, ctx.snapshot, bd) / bd.chargeable_kg
                dd_used = p_terms.plain("ddp")
                ddp_out = {
                    "level": dd.level, "level_cn": DDP_LEVEL_CN[dd.level], "is_ddp": bool(dd_used and dd_used.is_ddp),
                    "ddp_text": field_text("ddp", t1.ddp) if t1.ddp.status != "missing" else "",
                    "ddp_missing": tm.ddp.status == "missing", "ddp_conflicting": tm.ddp.status == "conflicting",
                    "chargeable_kg": dec(bd.chargeable_kg), "tax_floor_cny_per_kg": money_s(floor_all.quantize(Decimal("0.01"))),
                    "quote_cny_per_kg": None if not (dd_used and dd_used.is_ddp) else money_s(dd.quote_cny_per_kg.quantize(Decimal("0.01"))),
                    "freight_ref_cny_per_kg": None if dd.freight_ref_cny_per_kg is None else money_s(dd.freight_ref_cny_per_kg),
                    "floor_source": dd.floor_source,
                    "docs": list(dd.docs_to_request),
                    "quotes": spans_view(t1.ddp, text) if t1.ddp.status != "missing" else []}

                # claims
                claims_out = []
                cl_field = tm.claims
                claims = mp._claims_of(tm)
                ent = mp._entity(tm)
                for k, cl in enumerate(claims):
                    st, detail = gate.claim_check(cl, mode, reg, ent)
                    claims_out.append({
                        "type": cl.type, "type_cn": CLAIM_TYPE_CN.get(cl.type, cl.type), "entity": cl.entity,
                        "reg_no": cl.reg_no, "status": st, "status_cn": CLAIM_STATUS_CN[st],
                        "reason": CLAIM_REASON_CN.get(detail.get("reason", ""), detail.get("reason", "")),
                        "databases": detail["databases"],
                        "records": [{"db": r["db"], "name": r["name"], "no": r.get("license_no") or r.get("reg_no"),
                                     "kind": r.get("oti_type") or r.get("cert_type"), "status": r["status"]}
                                    for r in detail["records"]],
                        "quotes": spans_view(cl_field, text)})
                # cost
                bad_transit = None
                td = p_terms.plain("transit_days")
                if td is None or td[1] > task.shipment.deadline_days:
                    bad_transit = td
                reasons = []
                if td is None:
                    reasons.append("时效未写明")
                elif td[1] > task.shipment.deadline_days:
                    reasons.append(f"时效最长 {td[1]} 天，超过要求的 {task.shipment.deadline_days} 天")
                if i["ddp_level"] == "high":
                    reasons.append("包税价格低于税费下限，包税风险为高")
                cost_out = {
                    "chargeable_kg": dec(bd.chargeable_kg),
                    "lines": [{"code": c_, "label": LINE_CN.get(c_, c_), "amount": money_s(a)} for c_, a in bd.lines],
                    "contingent": [{"code": c_, "label": LINE_CN.get(c_, c_), "amount": money_s(a)} for c_, a in bd.contingent],
                    "total": money_s(bd.total_cny), "per_unit": money_s(bd.per_unit_cny),
                    "range": cost_range(task.shipment, tm, ctx.snapshot),
                    "feasible": bool(i["feasible"]), "infeasible_reasons": reasons,
                    "transit": None if td is None else td, "deadline_days": task.shipment.deadline_days,
                    "defaulted": [FIELD_CN.get(n, n) for n in price["defaulted"]]}
                assert bool(i["feasible"]) == (not reasons), (case["id"], cfg, cid, "feasible", reasons)
                per_q.append({
                    "cand_id": cid, "label": q.label,
                    "transcript": q.text if q.modality == "image" else None,
                    "model_text": text, "review_text": ptext if q.modality == "image" else None,
                    "fields": fields, "conflicts": conflicts,
                    "gaps": [{"key": n, "label": FIELD_CN.get(n, n)} for n in pc["gaps"]],
                    "defaulted": [{"key": n, "label": FIELD_CN.get(n, n)} for n in pc["defaulted"]],
                    "hidden_fees": [{"key": n, "label": FIELD_CN.get(n, n)} for n in pc["hidden_fees"]],
                    "triggers": [TRIGGER_CN.get(r, r) for r in m4["diagnostics"]["triggers"].get(cid, [])],
                    "norm_notes": m4["diagnostics"]["norm_notes"].get(cid, []),
                    "ddp": ddp_out, "claims": claims_out, "cost": cost_out})

            # decision
            feas = [(Decimal(p_["cost"]["total"]), p_["cand_id"]) for p_ in per_q if p_["cost"]["feasible"]]
            feas.sort()
            if m4["decision"] == "choose":
                ch = m4["choice"]
                best = next(a for a, c_ in feas if c_ == ch)
                second = next((a for a, c_ in feas if c_ != ch), None)
                reason = (f"在满足时效且包税风险不为“高”的 {len(feas)} 份报价中，{cid_label[ch]}的确定成本最低，"
                          f"为 {money_s(best)} 元" + (f"，比次低报价低 {money_s(second - best)} 元。" if second is not None else "。"))
            else:
                names = [f"{cid_label.get(f.split('.')[0], f.split('.')[0])}的{FIELD_CN.get(f.split('.', 1)[1], f.split('.', 1)[1])}"
                         for f in m4["ask_fields"]]
                reason = ("未决字段会改变最优选择或使可行性无法判断，先追问再选：" + "、".join(names) + "。") if names else \
                    "没有可行报价可推荐，先追问。"
            risks = []
            for p_ in per_q:
                lab = p_["label"]
                for cf in p_["conflicts"]:
                    risks.append({"cand": p_["cand_id"], "kind": "冲突",
                                  "text": f"{lab}的{cf['label']}有两种取值：{cf['values'][0]['value']} 与 {cf['values'][1]['value']}"})
                for g_ in p_["gaps"]:
                    risks.append({"cand": p_["cand_id"], "kind": "缺口", "text": f"{lab}未写明{g_['label']}"})
                if p_["ddp"]["level"] in ("verify", "high"):
                    risks.append({"cand": p_["cand_id"], "kind": "包税",
                                  "text": f"{lab}包税风险为“{p_['ddp']['level_cn']}”：包税单价 {p_['ddp']['quote_cny_per_kg']} 元/kg，"
                                          f"税费下限 {p_['ddp']['tax_floor_cny_per_kg']} 元/kg"})
                for cl in p_["claims"]:
                    if cl["status"] in ("insufficient", "contradicted"):
                        risks.append({"cand": p_["cand_id"], "kind": "资质", "text": f"{lab}的资质宣称：{cl['status_cn']}"})
                for r_ in p_["cost"]["infeasible_reasons"]:
                    risks.append({"cand": p_["cand_id"], "kind": "不可行", "text": f"{lab}：{r_}"})

            def gold_match(m):
                g = gold["decision"]
                if m["decision"] != g["type"]:
                    return False
                return m["choice"] == g["choice"] if g["type"] == "choose" else sorted(m["ask_fields"]) == sorted(g["ask_fields"])
            req_m, ret_m = obs_models(obs, ekeys)
            preq_m, pret_m = obs_models(obs, pkeys)
            vreq, vret = obs_models(obs, [k for k in m4["calls"] if "|transcribe|" in k and f"|{vcfg}|" in k])
            first_obs = obs[ekeys[0]]
            decision = {
                "type": m4["decision"], "choice": m4["choice"], "choice_label": cid_label.get(m4["choice"]) if m4["choice"] else None,
                "ask_fields": m4["ask_fields"], "questions": m4["questions"], "m4_risks": m4["risks"],
                "reason": reason, "risks": risks, "triggers": m4["diagnostics"]["triggers"],
                "match_gold": gold_match(m4)}
            m4_exports[(case["id"], cfg)] = {"decision": m4["decision"], "choice": m4["choice"], "questions": m4["questions"],
                                             "cost": {c_: pc_["cost_cny"] for c_, pc_ in m4["per_cand"].items()},
                                             "exported_cost": {p_["cand_id"]: p_["cost"]["total"] for p_ in per_q},
                                             "task": case["task"], "dir": case["dir"], "cfg": cfg}
            configs_out[cfg] = {
                "config": cfg,
                "model": {"requested": req_m, "returned": ret_m, "provider": ctx.models[cfg].provider,
                          "review_config": partner, "review_requested": preq_m, "review_returned": pret_m,
                          "review_provider": ctx.models[partner].provider,
                          "vision_config": vcfg, "vision_requested": vreq, "vision_returned": vret,
                          "recorded_at": first_obs.get("finished_at")},
                "n_calls": len(m4["calls"]), "cost": m4["cost"],
                "quotes": per_q, "decision": decision,
                "m0": {"decision": m0["decision"], "choice": m0["choice"], "choice_label": cid_label.get(m0["choice"]) if m0["choice"] else None,
                       "questions": m0["questions"], "risks": m0["risks"], "match_gold": gold_match(m0)}}

        gold_out = {
            "decision": gold["decision"]["type"], "choice": gold["decision"]["choice"],
            "ask_fields": gold["decision"]["ask_fields"], "oracle_choice": gold["decision"].get("oracle_choice"),
            "per_cand": {c_: {"total_cny": v["total_cny"], "ddp_level": v["ddp_level"], "feasible": v["feasible"],
                              "conflict_ids": v["conflict_ids"], "missing_ids": v["missing_ids"],
                              "hidden_fee_ids": v["hidden_fee_ids"]} for c_, v in gold["per_cand"].items()}}
        cases_out.append({
            "id": case["id"], "task_id": case["task"], "layer": json.loads((TASKS_DIR / f"{case['task']}.json").read_text("utf-8"))["layer"],
            "layer_cn": case["layer_cn"], "scenario": case["scenario"], "is_synthetic": True,
            "shipment": shipment_view(task), "quotes": quotes_out, "gold": gold_out, "configs": configs_out,
            "registry": {"snapshot_id": reg["snapshot_id"], "is_synthetic": reg["is_synthetic"]}})
    return {"meta": {"title": "运价明镜 QuoteGuard", "note": "dev 集合成任务；预试验记录离线回放",
                     "param_snapshot": ctx.snapshot.snapshot_id, "fx_date": ctx.snapshot.fx_date(),
                     "fx_cny_per_usd": dec(ctx.snapshot.fx_cny_per_usd()), "data_dir": "data/taskgen/dev_v3"},
            "cases": cases_out}, m4_exports


def check_consistency(exports: dict) -> None:
    """Exported M4 results against the stored re-scoring; any difference stops the script."""
    bad = []
    for (cid, cfg), e in sorted(exports.items()):
        ref = json.loads((DERIVED / e["dir"] / "outputs" / "M4" / cfg / f"{e['task']}.json").read_text("utf-8"))
        if (e["decision"], e["choice"], e["questions"]) != (ref["decision"], ref["choice"], ref["questions"]):
            bad.append(f"{cid}/{cfg}: decision, choice or questions differ")
        ref_cost = {k: v["cost_cny"] for k, v in ref["per_cand"].items()}
        if e["cost"] != ref_cost or e["exported_cost"] != ref_cost:
            bad.append(f"{cid}/{cfg}: per-quote cost differs")
    if bad:
        raise SystemExit("consistency check failed:\n  " + "\n  ".join(bad))
    print(f"consistency check passed: {len(exports)} (case, config) outputs equal the stored re-scoring")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="demo_build_"))
    try:
        data, exports = build(work)
        check_consistency(exports)
        (DEMO / "data").mkdir(exist_ok=True)
        (DEMO / "data" / "cases.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        for c in data["cases"]:
            for cfg, v in c["configs"].items():
                d = v["decision"]
                print(f"case {c['id']} {c['task_id']} {cfg}: M4 {d['type']} {d['choice'] or d['ask_fields']} "
                      f"| gold {c['gold']['decision']} {c['gold']['choice'] or c['gold']['ask_fields']} "
                      f"| match={d['match_gold']} | M0 {v['m0']['decision']} {v['m0']['choice']} match={v['m0']['match_gold']}")
        print("wrote", DEMO / "data" / "cases.json")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
