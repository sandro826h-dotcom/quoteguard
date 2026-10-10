"""Calculator A: landed cost in pure Python (Decimal).

Conventions (shared with calculator B):
- Every reported line is rounded to 0.01 CNY (ROUND_HALF_UP); total = sum of the rounded lines;
  per_unit = total / sum(qty), rounded. Intermediate values are not rounded.
- Rate tier: the tier with the largest min_kg <= chargeable weight is used; max_kg is display only.
  A weight below the lowest min_kg raises ValueError.
- fuel_pct applies to freight only. pct_freight surcharges are fractions of freight.
- ctn_* surcharge conditions are only valid with basis per_ctn.
- DDP: "included" waives duty; scope duty_mpf and all also waive MPF; only scope all waives HMF.
  "capped" limits the seller's duty only (MPF and HMF stay with the seller).
- duty_floor_cny = duty + MPF (HMF excluded), before any DDP waiver.
- Missing optional terms are treated as zero/absent and listed in `defaulted`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .params import Snapshot
from .schema import QuoteTerms, Shipment

CENT = Decimal("0.01")
ZERO = Decimal(0)


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class CostBreakdown:
    chargeable_kg: Decimal
    lines: list
    contingent: list
    duty_floor_cny: Decimal
    total_cny: Decimal
    per_unit_cny: Decimal
    assumptions: list = field(default_factory=list)  # assumed parameters actually used by this shipment
    snapshot_id: str = ""
    snapshot_version: str = ""
    snapshot_sha256: str = ""
    overrides: list = field(default_factory=list)    # [{name, original, value}]
    defaulted: list = field(default_factory=list)    # missing terms handled by default values
    fx_date: str = ""

    def line(self, code: str) -> Decimal:
        return sum((a for c, a in self.lines if c == code), ZERO)


def _ceil_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_CEILING) * step


def _need(terms: QuoteTerms, name: str):
    v = terms.plain(name)
    if v is None:
        raise ValueError(f"quote terms missing required field: {name}")
    return v


def chargeable_weight(shipment: Shipment, terms: QuoteTerms) -> Decimal:
    div = _need(terms, "dim_divisor")
    step = _need(terms, "rounding_kg")
    min_c = terms.plain("min_kg_carton")
    min_s = terms.plain("min_kg_shipment")
    total = ZERO
    for c in shipment.cartons:
        vol = c.length_cm * c.width_cm * c.height_cm / div
        w = max(c.kg, vol, min_c if min_c is not None else ZERO)
        total += _ceil_to(w, step) * c.n
    if min_s is not None:
        total = max(total, min_s)
    return total


def landed_cost(shipment: Shipment, terms: QuoteTerms, snapshot: Snapshot) -> CostBreakdown:
    shipment.validate()
    terms.validate()
    if terms.conflicting():
        raise ValueError(f"conflicting quote terms cannot be calculated: {terms.conflicting()}")
    if terms.plain("mode") is not None and terms.plain("mode") != shipment.mode:
        raise ValueError(f"terms mode {terms.plain('mode')!r} != shipment mode {shipment.mode!r}")
    fx = snapshot.fx_cny_per_usd()

    def cny(amount: Decimal, cur: str) -> Decimal:
        return amount * fx if cur == "USD" else amount

    w = chargeable_weight(shipment, terms)

    if any(t.unit != "per_kg" for t in _need(terms, "rate_tiers")):
        raise ValueError("only per_kg rate tiers can be calculated")

    # freight: tier with the largest min_kg not above w, applied to the whole chargeable weight
    cands = [t for t in _need(terms, "rate_tiers") if t.min_kg <= w]
    if not cands:
        raise ValueError(f"no rate tier covers chargeable weight {w} kg")
    tier = max(cands, key=lambda t: t.min_kg)
    freight = cny(tier.price, tier.currency) * w

    fuel = (terms.plain("fuel_pct") or ZERO) * freight

    n_ctn = sum(c.n for c in shipment.cartons)
    dest = shipment.dest
    lines = [("freight", freight), ("fuel", fuel)]
    contingent = []
    for s in terms.plain("surcharges") or []:
        if s.condition == "ctn_longest_side_gt":
            cnt = sum(c.n for c in shipment.cartons if max(c.length_cm, c.width_cm, c.height_cm) > s.threshold)
        elif s.condition == "ctn_kg_gt":
            cnt = sum(c.n for c in shipment.cartons if c.kg > s.threshold)
        else:
            cnt = n_ctn
        flag = {"residential": dest.addr_type == "residential", "remote": dest.remote}.get(s.condition, True)
        if s.basis == "pct_freight":
            val = s.amount * freight
        else:
            amt = cny(s.amount, s.currency)
            val = amt * w if s.basis == "per_kg" else amt if s.basis == "per_shpt" else amt * cnt
        if not flag:
            val = ZERO
        (contingent if s.contingent else lines).append((s.code, val))

    # duties and fees (USD)
    value = sum((g.unit_value_usd * g.qty for g in shipment.goods), ZERO)
    duty_usd = sum((g.unit_value_usd * g.qty * snapshot.total_duty_rate(g.hts) for g in shipment.goods), ZERO)
    formal = value > snapshot.fee("formal_entry_threshold_usd")
    if formal:
        mpf_usd = min(max(value * snapshot.fee("mpf_rate"), snapshot.fee("mpf_min_usd")), snapshot.fee("mpf_max_usd"))
    else:
        mpf_usd = snapshot.fee("mpf_informal_usd")
    sea = shipment.mode == "sea"
    hmf_usd = value * snapshot.fee("hmf_rate") if sea else ZERO

    duty_cny, mpf_cny, hmf_cny = duty_usd * fx, mpf_usd * fx, hmf_usd * fx
    duty_floor = duty_cny + mpf_cny

    ddp = terms.plain("ddp")
    seller_duty, seller_mpf, seller_hmf = duty_cny, mpf_cny, hmf_cny
    if ddp is not None and ddp.is_ddp:
        if ddp.duty_terms == "included":
            seller_duty = ZERO
            if ddp.scope in ("duty_mpf", "all"):
                seller_mpf = ZERO
            if ddp.scope == "all":
                seller_hmf = ZERO
        elif ddp.duty_terms == "capped":
            seller_duty = max(ZERO, duty_cny - ddp.cap_cny_per_kg * w)
    lines += [("duty", seller_duty), ("mpf", seller_mpf), ("hmf", seller_hmf),
              ("broker", terms.plain("broker_fee_cny") or ZERO), ("isf", terms.plain("isf_fee_cny") or ZERO)]

    lines = [(c, money(a)) for c, a in lines]
    total = sum((a for _, a in lines), ZERO)
    qty = sum(g.qty for g in shipment.goods)
    used_fees = ["formal_entry_threshold_usd"] + (["mpf_rate", "mpf_min_usd", "mpf_max_usd"] if formal
                                                  else ["mpf_informal_usd"]) + (["hmf_rate"] if sea else [])
    return CostBreakdown(
        chargeable_kg=w,
        lines=lines,
        contingent=[(c, money(a)) for c, a in contingent],
        duty_floor_cny=money(duty_floor),
        total_cny=total,
        per_unit_cny=money(total / qty),
        assumptions=snapshot.assumed_among(used_fees, [g.hts for g in shipment.goods]),
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.version,
        snapshot_sha256=snapshot.sha256,
        overrides=snapshot.overrides(),
        defaulted=terms.defaulted(),
        fx_date=snapshot.fx_date() or "",
    )
