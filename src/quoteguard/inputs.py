"""Model-facing task input: whitelisted shipment text, rendered quotes, registry and parameter text.

Only the rendered quote (text, markdown view of tables, PNG pages of images) and the whitelisted shipment
fields reach a model. Layers, term statuses, conflicts, fee locations, template ids, seeds, pool names and
all gold content stay out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .gate import _leaf_value
from .params import Snapshot
from .render import RenderResult, RenderSpec, render
from .schema import Task
from .util import canonical_json, sha256_file, sha256_text

FAIL_TEXT = "[图片转写失败]"
BOX_MARKS = ("<|begin_of_box|>", "<|end_of_box|>")
FORBIDDEN_WORDS = ("layer", "explicit", "implicit", "conflict", "insufficient", "missing", "conflicting",
                   "fee_location", "footnote", "annex", "template", "status", "true_values", "gold",
                   "dev-", "test-")

_MODE_CN = {"express": "快递", "air": "空运", "sea": "海运"}
_SHIP_MODE_CN = {"express": "快递", "air": "空派", "sea": "海派"}
_OTI_CN = {"NVOCC": "NVOCC（无船承运人）", "OFF": "OFF（海运货代，非无船承运人）"}
_DDP_CN = {"none": "不包税", "all": "全包", "duty_mpf": "包关税与 MPF", "duty": "只包关税"}
_ZONE_CN = {"east": "东部", "central": "中部", "west": "西部", "national": "全国"}
_STATUS_CN = {"active": "有效", "revoked": "已吊销"}
_CERT_CN = {"mot_nvocc_filing": "交通运输部无船承运备案", "aeo_advanced": "海关高级认证企业"}


@dataclass
class QuoteInput:
    k: int
    cand_id: str
    modality: str
    text: Optional[str] = None                 # None while an image quote is untranscribed
    images: list = field(default_factory=list)  # PNG pages in page order (image quotes only)
    transcribed: bool = False
    transcript_failed: bool = False

    @property
    def label(self) -> str:
        return f"报价{self.k}"


@dataclass
class TaskInput:
    task_id: str
    shipment_text: str
    quotes: list

    def label_to_cand(self) -> dict:
        return {q.label: q.cand_id for q in self.quotes}

    def cand_to_label(self) -> dict:
        return {q.cand_id: q.label for q in self.quotes}

    def image_quotes(self) -> list:
        return [q for q in self.quotes if q.modality == "image" and not q.transcribed]

    def quote_text(self, q: QuoteInput) -> str:
        if q.text is None:
            raise ValueError(f"{q.label} is an untranscribed image quote")
        return q.text

    def quotes_text(self) -> str:
        return "\n\n".join(f"【{q.label}】\n{self.quote_text(q)}" for q in self.quotes)


# ---------------------------------------------------------------- rendering
def render_task(task: Task, outdir) -> dict:
    """Render every candidate of the task into outdir/<cand_id>; returns {cand_id: RenderResult}."""
    out = {}
    for c in task.candidates:
        spec = RenderSpec.from_dict(dict(c.presentation, cand_id=c.cand_id))
        out[c.cand_id] = render(spec, Path(outdir) / c.cand_id)
    return out


def _page_no(path: str) -> int:
    m = re.search(r"_(\d+)\.png$", path)
    return int(m.group(1)) if m else 1


def png_pages(files) -> list:
    """PNG files of one image quote in page order (<id>.png, <id>_2.png, ...)."""
    pngs = [str(f) for f in files if str(f).lower().endswith(".png")]
    return sorted(pngs, key=lambda p: (_page_no(p), p))


# ---------------------------------------------------------------- shipment text
def _n(x) -> str:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    s = format(d.normalize(), "f")
    return s


def _dest_line(d) -> str:
    kind = "住宅" if d.addr_type == "residential" else "商业"
    remote = "是" if d.remote else "否"
    head = "收货地址" if d.fba_code.upper().startswith("NONFBA") else f"目的仓 {d.fba_code}"
    return f"{head}，邮编 {d.zip}，地址类型：{kind}，是否偏远地区：{remote}。"


def shipment_text(task: Task) -> str:
    s = task.shipment
    goods = []
    total_value = Decimal(0)
    for g in s.goods:
        total_value += g.unit_value_usd * g.qty
        goods.append(f"{g.desc}，{g.qty} 件，单价 {_n(g.unit_value_usd)} 美元")
    n_ctn = sum(c.n for c in s.cartons)
    ctns = "；".join(f"{c.n} 箱，每箱 {_n(c.length_cm)}×{_n(c.width_cm)}×{_n(c.height_cm)} cm，"
                     f"{_n(c.kg)} kg" for c in s.cartons)
    return "\n".join([
        "品名与件数：" + "；".join(goods) + "。",
        f"货值合计：{_n(total_value)} 美元。",
        f"运输方式：{_SHIP_MODE_CN[s.mode]}。",
        f"箱数与箱规：共 {n_ctn} 箱，{ctns}。",
        _dest_line(s.dest),
        f"时效要求：发运后 {s.deadline_days} 天内到仓。",
        f"发运日期：{s.ship_date}。",
    ])


def build_task_input(task: Task, render_results: dict) -> TaskInput:
    quotes = []
    for k, c in enumerate(task.candidates, start=1):
        r: RenderResult = render_results[c.cand_id]
        modality = c.presentation["modality"]
        if modality == "image":
            pages = png_pages(r.files)
            if not pages:
                raise ValueError(f"{c.cand_id}: image quote without PNG")
            quotes.append(QuoteInput(k, c.cand_id, modality, None, pages, False))
        else:
            quotes.append(QuoteInput(k, c.cand_id, modality, r.text_view.strip(), [], True))
    return TaskInput(task.task_id, shipment_text(task), quotes)


def image_ref(path: str) -> dict:
    return {"path": str(Path(path).resolve()), "sha256": sha256_file(path), "mime": "image/png"}


# ---------------------------------------------------------------- registry and parameters
def registry_text(snapshot: dict) -> str:
    """Model-facing registry snapshot as text tables."""
    lines = ["美国 FMC 持证名单（OTI）", "| 名称 | 许可证号 | 类型 | 状态 |", "|---|---|---|---|"]
    for r in snapshot["fmc"]:
        if not isinstance(r["oti_type"], str):
            raise TypeError(f"oti_type of {r['name']!r} must be a string, got {r['oti_type']!r}")
        lines.append(f"| {r['name']} | {r['license_no']} | {_OTI_CN.get(r['oti_type'], r['oti_type'])} | {_STATUS_CN.get(r['status'], r['status'])} |")
    lines += ["", "国内登记（交通运输部无船承运备案、海关企业信用）",
              "| 名称 | 登记号 | 类别 | 状态 |", "|---|---|---|---|"]
    for r in snapshot["cn"]:
        lines.append(f"| {r['name']} | {r['reg_no']} | {_CERT_CN.get(r['cert_type'], r['cert_type'])} | "
                     f"{_STATUS_CN.get(r['status'], r['status'])} |")
    lines += ["", "名单之外的主体视为未登记。"]
    return "\n".join(lines)


def _pct(d: Decimal) -> str:
    return _n(d * 100) + "%"


def freight_floor_text(freight_floor: dict) -> str:
    """Non-DDP freight reference per mode and zone, as plain text (no mode is singled out)."""
    parts = []
    for mode in ("express", "air", "sea"):
        if mode not in freight_floor:
            continue
        vals = []
        for zone in ("east", "central", "west", "national"):
            v = _leaf_value((freight_floor[mode] or {}).get(zone))
            if v is not None:
                vals.append(f"{_ZONE_CN[zone]} {_n(v)} 元/kg")
        parts.append(f"{_MODE_CN[mode]}：" + ("；".join(vals) if vals else "无参照值"))
    return "；".join(parts) + "。按邮编首位判定区域：0–3 东部，4–7 中部，8–9 西部，无对应区域值时用全国值。"


def _join(items: list) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else "、".join(items[:-1]) + " 或 " + items[-1]


def value_sets_text(value_sets: dict) -> str:
    """Plausible-value ranges of the gap-prone clauses, from value_sets.yaml (generator keys excluded).

    No final punctuation: the M1 template continues with a semicolon.
    """
    ddp = []
    for o in value_sets["ddp"]:
        if o["id"] not in _DDP_CN:
            raise ValueError(f"unknown ddp option {o['id']!r}")
        ddp.append(_DDP_CN[o["id"]])
    fuel = [_n(Decimal(str(v)) * 100) + "%" if Decimal(str(v)) else "0" for v in value_sets["fuel_pct"]]
    return (f"计泡系数 {_join([_n(v) for v in value_sets['dim_divisor']])}；"
            f"燃油附加费 {_join(fuel)}；"
            f"整票最低计费重 {_join([_n(v) for v in value_sets['min_kg_shipment']])} kg；"
            f"ISF 申报费 {_join([_n(v) for v in value_sets['isf_fee_cny']])} 元；"
            f"报关费 {_join([_n(v) for v in value_sets['broker_fee_cny']])} 元；"
            f"包税条款为{'、'.join(ddp)}之一")


def mpf_text(snapshot: Snapshot) -> str:
    """The MPF / HMF sentence of the M1 parameter section, from the snapshot."""
    return (f"MPF：正式报关（货值超过 {_n(snapshot.fee('formal_entry_threshold_usd'))} 美元）按货值 "
            f"{_pct(snapshot.fee('mpf_rate'))}，最低 {_n(snapshot.fee('mpf_min_usd'))} 美元、最高 "
            f"{_n(snapshot.fee('mpf_max_usd'))} 美元；非正式报关 {_n(snapshot.fee('mpf_informal_usd'))} 美元。"
            f"HMF：仅海运，货值 {_pct(snapshot.fee('hmf_rate'))}。")


def param_text(snapshot: Snapshot, freight_floor: dict, hts: str, value_sets: dict) -> dict:
    """Placeholder values of the M1 parameter section."""
    comps = {c[0]: c[1] for c in snapshot.duty_components(hts)}
    mfn = comps.get("mfn", Decimal(0))
    s2026 = comps.get("s301_2026", Decimal(0))
    s301 = sum((v for k, v in comps.items() if k.startswith("s301") and k != "s301_2026"), Decimal(0))
    return {"snapshot_date": snapshot.retrieved_at, "fx": _n(snapshot.fx_cny_per_usd()),
            "duty_rate": _pct(snapshot.total_duty_rate(hts)), "mfn": _pct(mfn), "s301": _pct(s301),
            "s301_2026": _pct(s2026), "freight_floor_text": freight_floor_text(freight_floor),
            "value_sets_text": value_sets_text(value_sets)}


# ---------------------------------------------------------------- freezing
def freeze_inputs(task: Task, render_results: dict, outdir, *, registry_snapshot: Optional[dict] = None,
                  snapshot: Optional[Snapshot] = None, freight_floor: Optional[dict] = None,
                  value_sets: Optional[dict] = None) -> dict:
    """Write the model-facing input of one task to disk; returns {relative path: sha256}.

    Files: tasks/<task_id>.json (shipment text, quote label to candidate map, per-quote text or PNG paths
    with sha256, parameter text when snapshot and freight_floor are given), registry_text.txt when a
    registry snapshot is given.
    """
    out = Path(outdir)
    ti = build_task_input(task, render_results)
    quotes = []
    for q in ti.quotes:
        item = {"label": q.label, "cand_id": q.cand_id, "modality": q.modality}
        if q.modality == "image":
            item["images"] = [{"path": str(p), "sha256": sha256_file(p)} for p in q.images]
        else:
            item["text"] = q.text
            item["text_sha256"] = sha256_text(q.text)
        quotes.append(item)
    doc = {"task_id": ti.task_id, "shipment_text": ti.shipment_text, "label_to_cand": ti.label_to_cand(),
           "quotes": quotes}
    if snapshot is not None and freight_floor is not None and value_sets is not None:
        doc["param_text"] = param_text(snapshot, freight_floor, task.shipment.goods[0].hts, value_sets)
    files = {}
    tdir = out / "tasks"
    tdir.mkdir(parents=True, exist_ok=True)
    tp = tdir / f"{task.task_id}.json"
    tp.write_text(canonical_json(doc), encoding="utf-8")
    files[f"tasks/{tp.name}"] = sha256_file(tp)
    if registry_snapshot is not None:
        rp = out / "registry_text.txt"
        rp.write_text(registry_text(registry_snapshot), encoding="utf-8")
        files[rp.name] = sha256_file(rp)
    return files
