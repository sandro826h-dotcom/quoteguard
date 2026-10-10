"""Deterministic gates: DDP plausibility (three levels) and qualification-claim evidence status.

Shared by the pipeline (M3/M4) and by the gold-answer generator.

DDP level (only for quotes with is_ddp=True):
- tax floor per kg = (duty + MPF, plus HMF for sea) / chargeable weight, computed before any DDP waiver;
- quote price per kg = the quote's own charges (freight, fuel, surcharges, broker, ISF) / chargeable weight,
  i.e. the total without the duty/MPF/HMF lines the seller still bears under the quote's DDP scope;
- high: quote < floor; verify: quote < floor + non-DDP freight reference; otherwise none.
Freight reference lookup: mode+zone, then the mode's national value, and when the mode has no value
at all (null) the verify level is not used (floor_source "none").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .calc_py import CostBreakdown, landed_cost, money
from .params import Snapshot
from .schema import Claim, Destination, QuoteTerms, Shipment

ZERO = Decimal(0)
DOCS_BASE = ["进口商主体（IOR）名称", "CBP 7501 报关单", "申报价值发票"]
DOCS_SEA_EXTRA = "ISF 申报确认"
NOT_DDP_LINES = ("duty", "mpf", "hmf")
SEA_LICENCE_TYPES = ("FMC_OTI", "NVOCC", "MOT_NVOCC")


@dataclass
class DDPResult:
    level: str                                   # none | verify | high
    floor_cny_per_kg: Optional[Decimal] = None
    quote_cny_per_kg: Optional[Decimal] = None
    docs_to_request: list = field(default_factory=list)
    freight_ref_cny_per_kg: Optional[Decimal] = None
    floor_source: str = "none"                   # zone | national | none
    chargeable_kg: Optional[Decimal] = None


# ---------------------------------------------------------------- zones and the freight reference
def zone_of(dest: Destination) -> str:
    """Destination zone by the first ZIP digit: 0-3 east, 4-7 central, 8-9 west."""
    z = dest.zip.strip()
    if not z or not z[0].isdigit():
        raise ValueError(f"cannot derive zone from zip {dest.zip!r}")
    d = int(z[0])
    return "east" if d <= 3 else "central" if d <= 7 else "west"


def _leaf_value(leaf) -> Optional[Decimal]:
    if leaf is None:
        return None
    v = leaf.get("value_cny_per_kg") if isinstance(leaf, dict) else leaf
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except Exception as e:
        raise ValueError(f"bad freight_floor value: {leaf!r}") from e
    if not d.is_finite() or d < 0:
        raise ValueError(f"bad freight_floor value: {leaf!r}")
    return d


def freight_reference(freight_floor: dict, mode: str, zone: str):
    """(value, source) with source in zone|national|none. Raises when the mode is absent (never 0)."""
    if not isinstance(freight_floor, dict) or mode not in freight_floor:
        raise ValueError(f"freight_floor has no entry for mode {mode!r}")
    m = freight_floor[mode]
    if m is None:
        return None, "none"
    if not isinstance(m, dict):
        raise ValueError(f"freight_floor[{mode!r}] must be a mapping or null")
    v = _leaf_value(m.get(zone))
    if v is not None:
        return v, "zone"
    v = _leaf_value(m.get("national"))
    if v is not None:
        return v, "national"
    if all(_leaf_value(x) is None for k, x in m.items() if k in ("east", "central", "west", "national")):
        return None, "none"
    raise ValueError(f"freight_floor[{mode!r}] has values for other zones but no national value")


# ---------------------------------------------------------------- DDP gate
def hmf_cny(shipment: Shipment, snapshot: Snapshot) -> Decimal:
    """HMF before any DDP waiver (sea only), rounded to 0.01 like a reported line."""
    if shipment.mode != "sea":
        return ZERO
    value = sum((g.unit_value_usd * g.qty for g in shipment.goods), ZERO)
    return money(value * snapshot.fee("hmf_rate") * snapshot.fx_cny_per_usd())


def tax_floor_cny(shipment: Shipment, snapshot: Snapshot, bd: CostBreakdown) -> Decimal:
    return bd.duty_floor_cny + hmf_cny(shipment, snapshot)


def ddp_from_breakdown(shipment: Shipment, terms: QuoteTerms, snapshot: Snapshot, bd: CostBreakdown,
                       freight_floor: dict) -> DDPResult:
    ddp = terms.plain("ddp")
    if ddp is None or not ddp.is_ddp:
        return DDPResult("none")
    w = bd.chargeable_kg
    floor = tax_floor_cny(shipment, snapshot, bd) / w
    quote = sum((a for c, a in bd.lines if c not in NOT_DDP_LINES), ZERO) / w
    ref, source = freight_reference(freight_floor, shipment.mode, zone_of(shipment.dest))
    if quote < floor:
        level = "high"
    elif ref is not None and quote < floor + ref:
        level = "verify"
    else:
        level = "none"
    docs = []
    if level != "none":
        docs = list(DOCS_BASE) + ([DOCS_SEA_EXTRA] if shipment.mode == "sea" else [])
    return DDPResult(level, floor, quote, docs, ref, source, w)


def ddp_check(shipment: Shipment, terms: QuoteTerms, snapshot: Snapshot, freight_floor: dict) -> DDPResult:
    ddp = terms.plain("ddp")
    if ddp is None or not ddp.is_ddp:
        return DDPResult("none")
    bd = landed_cost(shipment, terms, snapshot)
    return ddp_from_breakdown(shipment, terms, snapshot, bd, freight_floor)


# ---------------------------------------------------------------- claim evidence
def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def claim_check(claim: Claim, channel_mode: str, registry: dict, quote_entity: Optional[str] = None):
    """Returns (status, detail). detail lists the registry records consulted and the jurisdictions hit."""
    detail = {"databases": [], "records": [], "hit_jurisdictions": []}
    if claim.type == "AEO_CN":
        detail["reason"] = "Chinese customs certification does not evidence US clearance capability"
        return "irrelevant", detail
    if claim.type in SEA_LICENCE_TYPES and channel_mode in ("air", "express"):
        detail["reason"] = "sea licence cited for an air or express channel"
        return "irrelevant", detail
    if quote_entity is not None and _norm(claim.entity) != _norm(quote_entity):
        detail["reason"] = "certified entity differs from the quoting entity"
        return "irrelevant", detail

    jur = (claim.jurisdiction or "").upper() or None
    if claim.type == "FMC_OTI":
        jur = jur or "US"
    elif claim.type == "MOT_NVOCC":
        jur = jur or "CN"
    dbs = [("US", "fmc"), ("CN", "cn")] if jur is None else [(jur, "fmc" if jur == "US" else "cn")]
    consistent, found = [], []
    for jname, key in dbs:
        detail["databases"].append(key)
        for rec in registry.get(key, []) or []:
            if _norm(rec.get("name")) != _norm(claim.entity):
                continue
            if key == "cn" and rec.get("cert_type") != "mot_nvocc_filing" and claim.type != "AEO_CN":
                continue   # customs credit records do not evidence a carrier filing
            found.append(rec)
            detail["records"].append({"db": key, **rec})
            ok = rec.get("status") == "active"
            if key == "fmc":
                ok = ok and rec.get("oti_type") == "NVOCC"
            if claim.reg_no and claim.reg_no not in (rec.get("license_no"), rec.get("reg_no")):
                ok = False
            if ok:
                consistent.append(jname)
    if consistent:
        detail["hit_jurisdictions"] = sorted(set(consistent))
        return "supported", detail
    if found:
        return "contradicted", detail
    return "insufficient", detail


def claim_status(claim: Claim, channel_mode: str, registry: dict, quote_entity: Optional[str] = None) -> str:
    """supported | insufficient | contradicted | irrelevant.

    Claim types: FMC_OTI (US FMC licence), MOT_NVOCC (domestic carrier filing), NVOCC (jurisdiction
    unstated, both databases are searched), AEO_CN (Chinese customs advanced certification).
    Registry: {"fmc": [{name, license_no, oti_type, status}], "cn": [{name, reg_no, cert_type, status}]}.
    """
    return claim_check(claim, channel_mode, registry, quote_entity)[0]
