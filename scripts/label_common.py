"""标签导入的共用部分：读初标 JSON、解析表格单元格中的取值与出处、出处子串核对、规范化比较。

被 scripts/import_adjudication.py 与 scripts/import_review13.py 使用。出处核对规则同规范第九节：片段在去除首尾空白、
连续空白合并后须是匿名件正文的子串；PDF 正文取 pdftotext -layout 输出，片段注页码时只在该页查找。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from quoteguard.schema import _TERM_CODECS, LabelRecord, SeedQuote, _enc  # noqa: F401

FIELDS = ["channel", "mode", "rate_tiers", "min_kg_shipment", "min_kg_carton", "dim_divisor", "rounding_kg",
          "surcharges", "fuel_pct", "includes", "excludes", "ddp", "broker_fee_cny", "isf_fee_cny",
          "transit_days", "validity", "liability", "claims"]
TEXT_FIELDS = ("channel", "mode", "liability")
STATUSES = ("stated", "implied", "missing", "conflicting")
# 由标注人判断、可能与单元清单不同的元数据（队友接手说明第 6 条）；其余元数据以 data/seeds/units.json 为准
JUDGED_META = ("page_date", "price_form", "date_status")


class ImportError_(Exception):
    """输入不完整或不一致；消息逐条列出问题。"""

    def __init__(self, problems: list):
        super().__init__("\n".join(problems))
        self.problems = problems


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 规范化比较
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _canon(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        v = str(v)
    if isinstance(v, str):
        s = v.strip()
        if _NUM.match(s):
            d = Decimal(s).normalize()
            return format(d, "f")
        return s
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in sorted(v.items())}
    return v


def canon_value(v) -> str:
    """取值的规范文本，用于判断两份标签或标签与裁决是否相同（数值 5 与 5.0 视为相同）。"""
    return json.dumps(_canon(_plain(v)), ensure_ascii=False, sort_keys=True)


def _plain(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def canon_term(name: str, v) -> str:
    """条款取值的规范文本：先按 schema 解码再编码，补齐默认键（如档位 unit、附加费 condition），
    使省略默认键的写法与写全的写法比较为相同。解码失败时按原值比较。"""
    dec = _TERM_CODECS.get(name)
    if v is not None and dec is not None:
        try:
            v = [None if x is None else dec(x) for x in v] if _is_pair(name, v) else dec(v)
            v = _enc(v)
        except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
            pass
    return canon_value(v)


def _is_pair(name: str, v) -> bool:
    """conflicting 的两个候选（列表），与本身就是列表的字段区分：列表字段的候选各自是列表。"""
    if not isinstance(v, list) or len(v) != 2:
        return False
    if name in ("rate_tiers", "surcharges", "claims"):
        return all(isinstance(x, list) for x in v)
    return name not in ("includes", "excludes", "transit_days", "validity")


def same_field(a: dict, b: dict, name: str) -> bool:
    if a.get("status") != b.get("status"):
        return False
    if a.get("status") == "conflicting" and name in ("includes", "excludes", "transit_days", "validity"):
        return canon_value(a.get("value")) == canon_value(b.get("value"))
    return canon_term(name, a.get("value")) == canon_term(name, b.get("value"))


# ---------------------------------------------------------------- 初标 JSON
def load_records(path: Path) -> dict:
    """读一份初标：可以是目录（其中每个 *.json）、记录列表、{"labels": [...]}、以 seed_id 为键的字典或单条记录。
    返回 {seed_id: 记录字典}。"""
    path = Path(path)
    items = []
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"), parse_float=Decimal)
        if isinstance(d, dict) and "seed_id" in d:
            items.append(d)
        elif isinstance(d, dict) and isinstance(d.get("labels") or d.get("records"), list):
            items.extend(d.get("labels") or d.get("records"))
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    v = dict(v)
                    v.setdefault("seed_id", k)
                    items.append(v)
        elif isinstance(d, list):
            items.extend(d)
    out = {}
    for r in items:
        sid = r.get("seed_id")
        if sid in out:
            raise ImportError_([f"{path}: seed_id {sid} 出现两次"])
        out[sid] = r
    return out


def field_of(rec: dict, name: str):
    """记录中的一个条款字段；未出现或 status 为空（空白表中的“还没填”）时返回 None。"""
    f = (rec.get("terms") or {}).get(name)
    if not isinstance(f, dict) or not f.get("status"):
        return None
    return {"status": f["status"], "value": f.get("value"), "spans": list(f.get("spans") or [])}


# ---------------------------------------------------------------- 表格单元格
def cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def cell_date(v):
    t = cell_text(v)
    if not t:
        return None
    t = t.replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if not m:
        raise ValueError(f"日期无法识别：{t!r}")
    return date(int(m[1]), int(m[2]), int(m[3])).isoformat()


def parse_value(name: str, raw):
    """把表格中填写的取值解析为标签取值。JSON 文本按 JSON 读（数值保留为 Decimal 精度），
    其余按原文字符串；channel、mode、liability 只有以“[”开头（冲突的两个候选）时才按 JSON 读。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return str(Decimal(repr(raw)).normalize()) if isinstance(raw, float) else str(raw)
    s = str(raw).strip()
    if name in TEXT_FIELDS and not s.startswith("["):
        return s
    try:
        v = json.loads(s, parse_float=Decimal)
    except json.JSONDecodeError:
        return s
    if isinstance(v, (int, Decimal)) and not isinstance(v, bool):
        return s
    return v


_SPAN_HEAD = re.compile(r"^\[(?P<doc>[A-Za-z0-9_\-]+)(?:\s+p(?P<page>\d+))?\]\s*(?P<quote>.*)$")


def parse_spans(raw, default_doc: str) -> list:
    """原文依据：每行一个片段，可写成“[Q01 p1] 片段”或“[Q02] 片段”，不写方括号时文件取该单元的原件。"""
    out = []
    for line in cell_text(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        m = _SPAN_HEAD.match(line)
        if m:
            s = {"doc_id": m["doc"], "kind": "text", "quote": m["quote"].strip()}
            if m["page"]:
                s["page"] = int(m["page"])
        else:
            s = {"doc_id": default_doc, "kind": "text", "quote": line}
        out.append(s)
    return out


def dedup_spans(spans: list) -> list:
    seen, out = set(), []
    for s in spans:
        k = (s.get("doc_id"), s.get("page"), norm(s.get("quote", "")))
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _hkey(s) -> str:
    return re.sub(r"\s+", "", cell_text(s)).lower()


def find_header(ws, must: list, scan: int = 10):
    """在前 scan 行中找表头行：该行须同时含 must 中每组别名之一。返回 (行号, {规范化表头: 列号})。"""
    for r in range(1, min(scan, ws.max_row) + 1):
        heads = {_hkey(c.value): c.column for c in ws[r] if cell_text(c.value)}
        if all(any(_hkey(a) in heads for a in group) for group in must):
            return r, heads
    return None, {}


def col(heads: dict, aliases) -> int | None:
    for a in aliases:
        if _hkey(a) in heads:
            return heads[_hkey(a)]
    return None


# ---------------------------------------------------------------- 出处核对
class Docs:
    """匿名件正文（规范化），按 doc_id 缓存；同时核对匿名件哈希与单元清单的 anon_sha256。"""

    def __init__(self, anon_dir: Path):
        self.anon_dir = Path(anon_dir)
        self._cache: dict = {}

    def path(self, doc: str) -> Path | None:
        for ext in ("pdf", "txt"):
            p = self.anon_dir / f"{doc}.{ext}"
            if p.exists():
                return p
        return None

    def text(self, doc: str):
        if doc not in self._cache:
            p = self.path(doc)
            if p is None:
                self._cache[doc] = None
            elif p.suffix == ".pdf":
                raw = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True,
                                     check=True).stdout
                pages = [norm(x) for x in raw.split("\f")]
                self._cache[doc] = (" ".join(pages), pages)
            else:
                self._cache[doc] = (norm(p.read_text(encoding="utf-8")), None)
        return self._cache[doc]

    def check(self, seed_id: str, name: str, span: dict) -> str | None:
        """片段通过时返回 None，否则返回问题描述。"""
        doc = span.get("doc_id")
        t = self.text(doc)
        if t is None:
            return f"{seed_id}.{name}: 找不到匿名件 {doc}"
        full, pages = t
        hay = full
        if span.get("page") and pages is not None:
            if span["page"] > len(pages):
                return f"{seed_id}.{name}: {doc} 没有第 {span['page']} 页"
            hay = pages[span["page"] - 1]
        if span.get("kind", "text") != "text":
            return None
        if not norm(span.get("quote", "")):
            return f"{seed_id}.{name}: 出处片段为空（{doc}）"
        if norm(span.get("quote", "")) not in hay:
            pg = f" p{span['page']}" if span.get("page") else ""
            return f"{seed_id}.{name}: 出处不是 {doc}{pg} 的子串：{span.get('quote')!r}"
        return None


def check_anon_hashes(docs: Docs, units: dict, seed_ids) -> list:
    problems = []
    for sid in seed_ids:
        u = units[sid]
        p = docs.path(u["source_id"])
        if p is None:
            problems.append(f"{sid}: 找不到匿名件 {u['source_id']}")
        elif u.get("anon_sha256") and sha256_file(p) != u["anon_sha256"]:
            problems.append(f"{sid}: 匿名件 {p.name} 的 sha256 与 units.json 的 anon_sha256 不一致")
    return problems


def check_record_spans(docs: Docs, rec: dict) -> list:
    out = []
    for name, f in rec["terms"].items():
        for s in f.get("spans", []):
            msg = docs.check(rec["seed_id"], name, s)
            if msg:
                out.append(msg)
    return out


# ---------------------------------------------------------------- 组装与写出
def build_record(seed_id: str, annotator: str, annotated_at: str, minutes, initial_label_model, meta: dict,
                 terms: dict, notes: dict) -> tuple[dict, list]:
    """组装 LabelRecord 并做 schema 校验，返回 (JSON 字典, 问题列表)。"""
    rec = {"seed_id": seed_id, "annotator": annotator, "annotated_at": annotated_at,
           "minutes_spent": str(minutes), "initial_label_model": initial_label_model, "meta": meta,
           "terms": {n: {"status": f["status"], "value": _plain(f["value"]), "spans": f["spans"]}
                     for n, f in terms.items()},
           "notes": _plain(notes)}
    try:
        lr = LabelRecord.from_dict(rec)
        lr.validate()
    except (ValueError, TypeError, KeyError, InvalidOperation) as e:
        return rec, [f"{seed_id}: schema 校验失败：{e}"]
    return lr.to_dict(), []


def units_by_id(units_path: Path) -> dict:
    return {u["seed_id"]: u for u in json.loads(Path(units_path).read_text(encoding="utf-8"))}


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
