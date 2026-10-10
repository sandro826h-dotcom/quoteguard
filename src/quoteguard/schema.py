"""Data schema for QuoteGuard.

Money and weight values are held as Decimal in memory and serialised as strings.
Every class offers to_dict()/from_dict() round trips and validate(), which raises
ValueError on an invalid object.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

MODES = ("express", "air", "sea")
ADDR_TYPES = ("commercial", "residential")
SPLITS = ("dev", "test")
LAYERS = ("explicit", "implicit", "conflict", "insufficient")
SPAN_KINDS = ("text", "cell", "region")
FIELD_STATUS = ("stated", "implied", "missing", "conflicting")
CURRENCIES = ("CNY", "USD")
RATE_UNITS = ("per_kg", "per_cbm", "per_shpt")
BASES = ("per_kg", "per_ctn", "per_shpt", "pct_freight")
CONDITIONS = ("always", "residential", "remote", "ctn_longest_side_gt", "ctn_kg_gt")
DDP_SCOPES = ("duty", "duty_mpf", "all")
DUTY_TERMS = ("included", "actual", "capped")
IOR = ("forwarder", "seller", "unspecified")
GOLD_STATUS = ("supported", "insufficient", "contradicted", "irrelevant")
DECISION_TYPES = ("choose", "ask")


# ---------------------------------------------------------------- helpers
def _dec(x: Any) -> Decimal:
    if isinstance(x, bool):
        raise ValueError(f"bool is not a decimal: {x!r}")
    if isinstance(x, Decimal):
        d = x
    else:
        try:
            d = Decimal(str(x))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"not a decimal: {x!r}") from e
    if not d.is_finite():
        raise ValueError(f"non-finite decimal: {x!r}")
    return d


def _dec_opt(x: Any) -> Optional[Decimal]:
    return None if x is None else _dec(x)


def _enum(name: str, v: Any, allowed: tuple) -> None:
    if v not in allowed:
        raise ValueError(f"{name}={v!r} not in {allowed}")


def _is_str(name: str, v: Any, nonempty: bool = True) -> None:
    if not isinstance(v, str) or (nonempty and not v):
        raise ValueError(f"{name} must be a non-empty string, got {v!r}")


def _is_int(name: str, v: Any, minimum: Optional[int] = None) -> None:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{name} must be int, got {v!r}")
    if minimum is not None and v < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {v}")


def _is_bool(name: str, v: Any) -> None:
    if not isinstance(v, bool):
        raise ValueError(f"{name} must be bool, got {v!r}")


def _enc(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if isinstance(v, (list, tuple)):
        return [_enc(i) for i in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


def _need(d: dict, *keys: str) -> None:
    if not isinstance(d, dict):
        raise ValueError(f"expected dict, got {type(d).__name__}")
    miss = [k for k in keys if k not in d]
    if miss:
        raise ValueError(f"missing keys: {miss}")


def _pos(name: str, v: Decimal, strict: bool = True) -> None:
    if not isinstance(v, Decimal):
        raise ValueError(f"{name} must be Decimal")
    if (strict and v <= 0) or (not strict and v < 0):
        raise ValueError(f"{name} out of range: {v}")


class _Base:
    def to_dict(self) -> dict:
        return {f.name: _enc(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]


# ---------------------------------------------------------------- shipment
@dataclass
class Carton(_Base):
    n: int
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    kg: Decimal

    def __post_init__(self):
        self.length_cm, self.width_cm, self.height_cm, self.kg = (
            _dec(self.length_cm), _dec(self.width_cm), _dec(self.height_cm), _dec(self.kg))

    def validate(self) -> None:
        _is_int("Carton.n", self.n, 1)
        for nm in ("length_cm", "width_cm", "height_cm", "kg"):
            _pos("Carton." + nm, getattr(self, nm))

    @classmethod
    def from_dict(cls, d: dict) -> "Carton":
        _need(d, "n", "length_cm", "width_cm", "height_cm", "kg")
        return cls(d["n"], d["length_cm"], d["width_cm"], d["height_cm"], d["kg"])


@dataclass
class Goods(_Base):
    desc: str
    hts: str
    unit_value_usd: Decimal
    qty: int

    def __post_init__(self):
        self.unit_value_usd = _dec(self.unit_value_usd)

    def validate(self) -> None:
        _is_str("Goods.desc", self.desc, False)
        _is_str("Goods.hts", self.hts)
        _pos("Goods.unit_value_usd", self.unit_value_usd, strict=False)
        _is_int("Goods.qty", self.qty, 1)

    @classmethod
    def from_dict(cls, d: dict) -> "Goods":
        _need(d, "desc", "hts", "unit_value_usd", "qty")
        return cls(d["desc"], d["hts"], d["unit_value_usd"], d["qty"])


@dataclass
class Destination(_Base):
    fba_code: str
    zip: str
    addr_type: str
    remote: bool

    def validate(self) -> None:
        _is_str("Destination.fba_code", self.fba_code)
        _is_str("Destination.zip", self.zip)
        _enum("Destination.addr_type", self.addr_type, ADDR_TYPES)
        _is_bool("Destination.remote", self.remote)

    @classmethod
    def from_dict(cls, d: dict) -> "Destination":
        _need(d, "fba_code", "zip", "addr_type", "remote")
        return cls(d["fba_code"], d["zip"], d["addr_type"], d["remote"])


@dataclass
class Shipment(_Base):
    shipment_id: str
    mode: str
    cartons: list
    goods: list
    dest: Destination
    deadline_days: int
    ship_date: str

    def validate(self) -> None:
        _is_str("Shipment.shipment_id", self.shipment_id)
        _enum("Shipment.mode", self.mode, MODES)
        if not self.cartons:
            raise ValueError("Shipment needs at least one carton")
        if not self.goods:
            raise ValueError("Shipment needs at least one goods line")
        for c in self.cartons:
            if not isinstance(c, Carton):
                raise ValueError("cartons must be Carton")
            c.validate()
        for g in self.goods:
            if not isinstance(g, Goods):
                raise ValueError("goods must be Goods")
            g.validate()
        if not isinstance(self.dest, Destination):
            raise ValueError("dest must be Destination")
        self.dest.validate()
        _is_int("Shipment.deadline_days", self.deadline_days, 1)
        try:
            date.fromisoformat(self.ship_date)
        except (TypeError, ValueError) as e:
            raise ValueError(f"ship_date not ISO: {self.ship_date!r}") from e

    @classmethod
    def from_dict(cls, d: dict) -> "Shipment":
        _need(d, "shipment_id", "mode", "cartons", "goods", "dest", "deadline_days", "ship_date")
        return cls(d["shipment_id"], d["mode"], [Carton.from_dict(c) for c in d["cartons"]],
                   [Goods.from_dict(g) for g in d["goods"]], Destination.from_dict(d["dest"]),
                   d["deadline_days"], d["ship_date"])


# ---------------------------------------------------------------- evidence spans / fields
@dataclass
class Span(_Base):
    doc_id: str
    kind: str
    quote: str
    start: Optional[int] = None
    end: Optional[int] = None
    sheet: Optional[str] = None
    cell: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[list] = None

    def validate(self) -> None:
        _is_str("Span.doc_id", self.doc_id)
        _enum("Span.kind", self.kind, SPAN_KINDS)
        _is_str("Span.quote", self.quote, False)
        for nm in ("start", "end"):
            v = getattr(self, nm)
            if v is not None:
                _is_int("Span." + nm, v, 0)
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("Span.end < Span.start")
        if self.bbox is not None and (not isinstance(self.bbox, list) or len(self.bbox) != 4):
            raise ValueError("Span.bbox must be a list of 4 numbers")

    @classmethod
    def from_dict(cls, d: dict) -> "Span":
        _need(d, "doc_id", "kind", "quote")
        return cls(d["doc_id"], d["kind"], d["quote"], d.get("start"), d.get("end"),
                   d.get("sheet"), d.get("cell"), d.get("page"), d.get("bbox"))


@dataclass
class Field(_Base):
    value: Any
    status: str
    spans: list = field(default_factory=list)

    def validate(self) -> None:
        _enum("Field.status", self.status, FIELD_STATUS)
        if self.status == "missing" and self.value is not None:
            raise ValueError("Field with status 'missing' must have value None")
        if not isinstance(self.spans, list):
            raise ValueError("Field.spans must be a list")
        for s in self.spans:
            if not isinstance(s, Span):
                raise ValueError("Field.spans must hold Span")
            s.validate()

    @classmethod
    def from_dict(cls, d: dict, decode: Optional[Callable[[Any], Any]] = None) -> "Field":
        _need(d, "value", "status")
        v = d["value"]
        if decode is not None and v is not None:
            v = decode(v)
        return cls(v, d["status"], [Span.from_dict(s) for s in d.get("spans", [])])


# ---------------------------------------------------------------- quote terms
@dataclass
class RateTier(_Base):
    """Price per kg for chargeable weights from min_kg up; max_kg is informational only."""
    min_kg: Decimal
    max_kg: Optional[Decimal]
    price: Decimal
    currency: str
    unit: str = "per_kg"

    def __post_init__(self):
        self.min_kg, self.max_kg, self.price = _dec(self.min_kg), _dec_opt(self.max_kg), _dec(self.price)

    def validate(self) -> None:
        _pos("RateTier.min_kg", self.min_kg, strict=False)
        if self.max_kg is not None and self.max_kg < self.min_kg:
            raise ValueError("RateTier.max_kg < min_kg")
        _pos("RateTier.price", self.price, strict=False)
        _enum("RateTier.currency", self.currency, CURRENCIES)
        _enum("RateTier.unit", self.unit, RATE_UNITS)

    @classmethod
    def from_dict(cls, d: dict) -> "RateTier":
        _need(d, "min_kg", "max_kg", "price", "currency")
        return cls(d["min_kg"], d["max_kg"], d["price"], d["currency"], d.get("unit", "per_kg"))


@dataclass
class Surcharge(_Base):
    code: str
    basis: str
    amount: Decimal
    currency: Optional[str]
    condition: str = "always"
    threshold: Optional[Decimal] = None
    contingent: bool = False

    def __post_init__(self):
        self.amount, self.threshold = _dec(self.amount), _dec_opt(self.threshold)

    def validate(self) -> None:
        _is_str("Surcharge.code", self.code)
        _enum("Surcharge.basis", self.basis, BASES)
        _pos("Surcharge.amount", self.amount, strict=False)
        if self.basis == "pct_freight":
            # amount is a fraction of freight; currency is not used and may be empty
            if self.amount > 1:
                raise ValueError("pct_freight amount must be in [0, 1]")
            if self.currency is not None:
                _enum("Surcharge.currency", self.currency, CURRENCIES)
        else:
            _enum("Surcharge.currency", self.currency, CURRENCIES)
        _enum("Surcharge.condition", self.condition, CONDITIONS)
        _is_bool("Surcharge.contingent", self.contingent)
        if self.condition.startswith("ctn_"):
            if self.threshold is None:
                raise ValueError("ctn_* condition needs a threshold")
            if self.basis != "per_ctn":
                raise ValueError("ctn_* condition requires basis per_ctn")

    @classmethod
    def from_dict(cls, d: dict) -> "Surcharge":
        _need(d, "code", "basis", "amount", "currency")
        return cls(d["code"], d["basis"], d["amount"], d["currency"], d.get("condition", "always"),
                   d.get("threshold"), d.get("contingent", False))


@dataclass
class DDP(_Base):
    is_ddp: bool
    scope: str
    duty_terms: Optional[str]
    cap_cny_per_kg: Optional[Decimal] = None
    ior: str = "unspecified"

    def __post_init__(self):
        self.cap_cny_per_kg = _dec_opt(self.cap_cny_per_kg)

    def validate(self) -> None:
        _is_bool("DDP.is_ddp", self.is_ddp)
        _enum("DDP.scope", self.scope, DDP_SCOPES)
        if self.is_ddp:
            _enum("DDP.duty_terms", self.duty_terms, DUTY_TERMS)
        elif self.duty_terms not in (None, "actual"):
            raise ValueError("non-DDP terms need duty_terms empty or 'actual'")
        _enum("DDP.ior", self.ior, IOR)
        if self.duty_terms == "capped":
            if self.cap_cny_per_kg is None:
                raise ValueError("capped duty_terms needs cap_cny_per_kg")
            _pos("DDP.cap_cny_per_kg", self.cap_cny_per_kg, strict=False)

    @classmethod
    def from_dict(cls, d: dict) -> "DDP":
        _need(d, "is_ddp", "scope", "duty_terms")
        return cls(d["is_ddp"], d["scope"], d["duty_terms"], d.get("cap_cny_per_kg"),
                   d.get("ior", "unspecified"))


@dataclass
class Claim(_Base):
    type: str
    entity: str
    reg_no: Optional[str] = None
    jurisdiction: Optional[str] = None

    def validate(self) -> None:
        _is_str("Claim.type", self.type)
        _is_str("Claim.entity", self.entity)

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        _need(d, "type", "entity")
        return cls(d["type"], d["entity"], d.get("reg_no"), d.get("jurisdiction"))


def _dec_list_of(cls):
    return lambda v: [cls.from_dict(x) for x in v]


# attribute name -> decoder for the raw value inside the Field (None = keep as is)
_TERM_CODECS: dict = {
    "channel": None,
    "mode": None,
    "rate_tiers": _dec_list_of(RateTier),
    "min_kg_shipment": _dec,
    "min_kg_carton": _dec,
    "dim_divisor": _dec,
    "rounding_kg": _dec,
    "surcharges": _dec_list_of(Surcharge),
    "fuel_pct": _dec,
    "includes": None,
    "excludes": None,
    "ddp": DDP.from_dict,
    "broker_fee_cny": _dec,
    "isf_fee_cny": _dec,
    "transit_days": None,
    "validity": None,
    "liability": None,
    "claims": _dec_list_of(Claim),
}


def _missing() -> Field:
    return Field(None, "missing", [])


def _check_term_value(name: str, v: Any) -> None:
    """Type and range check of one (non-missing, non-conflicting) term value."""
    if name == "mode":
        _enum("QuoteTerms.mode", v, MODES)
    elif name in ("channel", "liability"):
        _is_str("QuoteTerms." + name, v, False)
    elif name in ("min_kg_shipment", "min_kg_carton", "broker_fee_cny", "isf_fee_cny"):
        _pos("QuoteTerms." + name, v, strict=False)
    elif name == "fuel_pct":
        _pos("QuoteTerms.fuel_pct", v, strict=False)
        if v > 1:
            raise ValueError("fuel_pct must be a fraction in [0, 1]")
    elif name in ("dim_divisor", "rounding_kg"):
        _pos("QuoteTerms." + name, v)
    elif name in ("rate_tiers", "surcharges", "claims"):
        cls = {"rate_tiers": RateTier, "surcharges": Surcharge, "claims": Claim}[name]
        if not isinstance(v, list) or not all(isinstance(i, cls) for i in v):
            raise ValueError(f"QuoteTerms.{name} must be a list of {cls.__name__}")
        for i in v:
            i.validate()
    elif name == "ddp":
        if not isinstance(v, DDP):
            raise ValueError("QuoteTerms.ddp must be DDP")
        v.validate()
    elif name in ("includes", "excludes"):
        if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
            raise ValueError(f"QuoteTerms.{name} must be a list of str")
    elif name == "transit_days":
        if (not isinstance(v, list) or len(v) != 2 or
                not all(isinstance(i, int) and not isinstance(i, bool) for i in v) or v[0] > v[1]):
            raise ValueError("QuoteTerms.transit_days must be [min, max] ints")
    elif name == "validity":
        if not isinstance(v, list) or len(v) != 2:
            raise ValueError("QuoteTerms.validity must be [from, to]")
        try:
            a, b = (None if x is None else date.fromisoformat(x) for x in v)
        except (TypeError, ValueError) as e:
            raise ValueError("QuoteTerms.validity must hold ISO dates (null allowed)") from e
        if a is not None and b is not None and a > b:
            raise ValueError("QuoteTerms.validity from > to")


@dataclass
class QuoteTerms(_Base):
    channel: Field = field(default_factory=_missing)
    mode: Field = field(default_factory=_missing)
    rate_tiers: Field = field(default_factory=_missing)
    min_kg_shipment: Field = field(default_factory=_missing)
    min_kg_carton: Field = field(default_factory=_missing)
    dim_divisor: Field = field(default_factory=_missing)
    rounding_kg: Field = field(default_factory=_missing)
    surcharges: Field = field(default_factory=_missing)
    fuel_pct: Field = field(default_factory=_missing)
    includes: Field = field(default_factory=_missing)
    excludes: Field = field(default_factory=_missing)
    ddp: Field = field(default_factory=_missing)
    broker_fee_cny: Field = field(default_factory=_missing)
    isf_fee_cny: Field = field(default_factory=_missing)
    transit_days: Field = field(default_factory=_missing)
    validity: Field = field(default_factory=_missing)
    liability: Field = field(default_factory=_missing)
    claims: Field = field(default_factory=_missing)

    def __post_init__(self):
        # coerce Decimal-valued attributes so direct construction equals from_dict
        for name, dec in _TERM_CODECS.items():
            f = getattr(self, name)
            if dec is _dec and isinstance(f, Field) and f.value is not None:
                if f.status == "conflicting" and isinstance(f.value, list):
                    f.value = [None if x is None else _dec(x) for x in f.value]
                else:
                    f.value = _dec(f.value)

    def conflicting(self) -> list:
        """Names of terms whose status is 'conflicting'."""
        return [n for n in _TERM_CODECS if getattr(self, n).status == "conflicting"]

    def defaulted(self) -> list:
        """Names of optional terms that are missing and treated as zero / absent in a calculation."""
        return [n for n in ("min_kg_shipment", "min_kg_carton", "surcharges", "fuel_pct", "ddp",
                            "broker_fee_cny", "isf_fee_cny") if self.plain(n) is None]

    def plain(self, name: str) -> Any:
        if name not in _TERM_CODECS:
            raise KeyError(name)
        return getattr(self, name).value

    @classmethod
    def from_plain(cls, **kw: Any) -> "QuoteTerms":
        """Build terms from plain values (status 'stated', no spans); None = missing."""
        bad = set(kw) - set(_TERM_CODECS)
        if bad:
            raise KeyError(sorted(bad))
        return cls(**{k: (_missing() if v is None else Field(v, "stated", [])) for k, v in kw.items()})

    def validate(self) -> None:
        for name in _TERM_CODECS:
            f = getattr(self, name)
            if not isinstance(f, Field):
                raise ValueError(f"QuoteTerms.{name} must be a Field")
            f.validate()
            v = f.value
            if v is None:
                continue
            if f.status == "conflicting":
                # the value holds the two normalised candidates
                if not isinstance(v, list) or len(v) != 2:
                    raise ValueError(f"QuoteTerms.{name}: a conflicting value must be a list of 2 candidates")
                continue
            _check_term_value(name, v)
        if self.rate_tiers.status != "conflicting":
            mins = [t.min_kg for t in (self.rate_tiers.value or [])]
            if len(set(mins)) != len(mins):
                raise ValueError("rate_tiers have duplicate min_kg")

    def to_dict(self) -> dict:
        out = {}
        for name in _TERM_CODECS:
            f = getattr(self, name)
            out[name] = {"value": _enc(f.value), "status": f.status, "spans": [s.to_dict() for s in f.spans]}
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "QuoteTerms":
        _need(d)
        def field_of(n, dec):
            if n not in d:
                return _missing()
            if dec is not None and d[n].get("status") == "conflicting" and isinstance(d[n].get("value"), list):
                return Field.from_dict(d[n], lambda v, dec=dec: [None if x is None else dec(x) for x in v])
            return Field.from_dict(d[n], dec)
        return cls(**{n: field_of(n, dec) for n, dec in _TERM_CODECS.items()})


# ---------------------------------------------------------------- task level
@dataclass
class Candidate(_Base):
    cand_id: str
    task_id: str
    quote_id: str
    presentation: dict
    files: list
    is_synthetic: bool
    base_seed_ids: list

    def validate(self) -> None:
        for nm in ("cand_id", "task_id", "quote_id"):
            _is_str("Candidate." + nm, getattr(self, nm))
        if not isinstance(self.presentation, dict):
            raise ValueError("Candidate.presentation must be dict")
        if not isinstance(self.files, list) or not all(isinstance(x, str) for x in self.files):
            raise ValueError("Candidate.files must be list[str]")
        _is_bool("Candidate.is_synthetic", self.is_synthetic)
        if not isinstance(self.base_seed_ids, list) or not all(isinstance(x, str) for x in self.base_seed_ids):
            raise ValueError("Candidate.base_seed_ids must be list[str]")

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        _need(d, "cand_id", "task_id", "quote_id", "presentation", "files", "is_synthetic", "base_seed_ids")
        return cls(d["cand_id"], d["task_id"], d["quote_id"], d["presentation"], d["files"],
                   d["is_synthetic"], d["base_seed_ids"])


@dataclass
class Task(_Base):
    task_id: str
    split: str
    layer: str
    shipment: Shipment
    candidates: list
    decision_rule_id: str
    param_snapshot_id: str
    generator_version: str
    rng_seed: int

    def validate(self) -> None:
        _is_str("Task.task_id", self.task_id)
        _enum("Task.split", self.split, SPLITS)
        _enum("Task.layer", self.layer, LAYERS)
        if not isinstance(self.shipment, Shipment):
            raise ValueError("Task.shipment must be Shipment")
        self.shipment.validate()
        for c in self.candidates:
            if not isinstance(c, Candidate):
                raise ValueError("Task.candidates must hold Candidate")
            c.validate()
        for nm in ("decision_rule_id", "param_snapshot_id", "generator_version"):
            _is_str("Task." + nm, getattr(self, nm))
        _is_int("Task.rng_seed", self.rng_seed)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        _need(d, "task_id", "split", "layer", "shipment", "candidates", "decision_rule_id",
              "param_snapshot_id", "generator_version", "rng_seed")
        return cls(d["task_id"], d["split"], d["layer"], Shipment.from_dict(d["shipment"]),
                   [Candidate.from_dict(c) for c in d["candidates"]], d["decision_rule_id"],
                   d["param_snapshot_id"], d["generator_version"], d["rng_seed"])


@dataclass
class Evidence(_Base):
    claim_id: str
    cand_id: str
    claim: Claim
    snapshot: dict
    gold_status: str

    def validate(self) -> None:
        _is_str("Evidence.claim_id", self.claim_id)
        _is_str("Evidence.cand_id", self.cand_id)
        if not isinstance(self.claim, Claim):
            raise ValueError("Evidence.claim must be Claim")
        self.claim.validate()
        if not isinstance(self.snapshot, dict):
            raise ValueError("Evidence.snapshot must be dict")
        _enum("Evidence.gold_status", self.gold_status, GOLD_STATUS)

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        _need(d, "claim_id", "cand_id", "claim", "snapshot", "gold_status")
        return cls(d["claim_id"], d["cand_id"], Claim.from_dict(d["claim"]), d["snapshot"], d["gold_status"])


@dataclass
class Gold(_Base):
    task_id: str
    per_cand: dict
    decision: dict
    followup_gap_ids: list
    calc_hashes: dict

    def validate(self) -> None:
        _is_str("Gold.task_id", self.task_id)
        if not isinstance(self.per_cand, dict):
            raise ValueError("Gold.per_cand must be dict")
        dec = self.decision
        if not isinstance(dec, dict):
            raise ValueError("Gold.decision must be dict")
        _need(dec, "type", "choice", "opt_cost_cny")
        _enum("Gold.decision.type", dec["type"], DECISION_TYPES)
        if dec["type"] == "choose":
            _is_str("Gold.decision.choice", dec["choice"])
        elif dec["choice"] is not None:
            raise ValueError("Gold.decision.choice must be None when type is 'ask'")
        if dec["opt_cost_cny"] is not None:
            _dec(dec["opt_cost_cny"])
        if not isinstance(self.followup_gap_ids, list):
            raise ValueError("Gold.followup_gap_ids must be list")
        if not isinstance(self.calc_hashes, dict):
            raise ValueError("Gold.calc_hashes must be dict")

    @classmethod
    def from_dict(cls, d: dict) -> "Gold":
        _need(d, "task_id", "per_cand", "decision", "followup_gap_ids", "calc_hashes")
        return cls(d["task_id"], d["per_cand"], d["decision"], d["followup_gap_ids"], d["calc_hashes"])


@dataclass
class SeedQuote(_Base):
    seed_id: str
    source_type: str
    source_url: str
    captured_at: str
    page_date: Optional[str]
    forwarder_alias: str
    modality: str
    channel: str
    raw_sha256: str
    anon_sha256: Optional[str]
    is_real: bool
    source_id: Optional[str] = None
    source_class: Optional[str] = None
    publisher_alias: Optional[str] = None
    price_form: Optional[str] = None
    date_status: Optional[str] = None
    dest_region: Optional[str] = None

    def validate(self) -> None:
        for nm in ("seed_id", "source_type", "source_url", "forwarder_alias", "modality", "channel", "raw_sha256"):
            _is_str("SeedQuote." + nm, getattr(self, nm))
        _enum("SeedQuote.source_type", self.source_type, ("web_page", "pdf", "image"))
        for nm, allowed in (("source_class", ("forwarder", "carrier_published")),
                            ("price_form", ("published_tier", "starting_price", "range")),
                            ("date_status", ("current", "expired", "unknown"))):
            if getattr(self, nm) is not None:
                _enum("SeedQuote." + nm, getattr(self, nm), allowed)
        for nm in ("source_id", "publisher_alias", "dest_region"):
            if getattr(self, nm) is not None:
                _is_str("SeedQuote." + nm, getattr(self, nm), False)
        for nm in ("captured_at", "page_date"):
            v = getattr(self, nm)
            if v is None and nm == "page_date":
                continue
            try:
                date.fromisoformat(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"SeedQuote.{nm} not ISO: {v!r}") from e
        _enum("SeedQuote.modality", self.modality, ("text", "table", "image", "pdf"))
        _is_bool("SeedQuote.is_real", self.is_real)

    @classmethod
    def from_dict(cls, d: dict) -> "SeedQuote":
        _need(d, "seed_id", "source_type", "source_url", "captured_at", "forwarder_alias", "modality",
              "channel", "raw_sha256", "is_real")
        return cls(d["seed_id"], d["source_type"], d["source_url"], d["captured_at"], d.get("page_date"),
                   d["forwarder_alias"], d["modality"], d["channel"], d["raw_sha256"],
                   d.get("anon_sha256"), d["is_real"], d.get("source_id"), d.get("source_class"),
                   d.get("publisher_alias"), d.get("price_form"), d.get("date_status"), d.get("dest_region"))


@dataclass
class LabelRecord(_Base):
    """One annotated seed quote: metadata, per-field terms and free-form notes
    (for example range_max or rate_tiers_other).

    terms maps each QuoteTerms attribute name to a Field. A field with status 'conflicting' holds a
    list of exactly 2 candidate values, each of the field's normal type (for rate_tiers, each
    candidate is a list of RateTier). Missing attribute names are treated as status 'missing'.
    """
    seed_id: str
    annotator: str
    annotated_at: str
    minutes_spent: Decimal
    initial_label_model: Optional[str]
    meta: SeedQuote
    terms: dict
    notes: dict = field(default_factory=dict)

    def __post_init__(self):
        self.minutes_spent = _dec(self.minutes_spent)
        if isinstance(self.terms, dict):
            for name in _TERM_CODECS:
                self.terms.setdefault(name, _missing())
            for name, f in self.terms.items():
                if (_TERM_CODECS.get(name) is _dec and isinstance(f, Field) and f.value is not None):
                    if f.status == "conflicting" and isinstance(f.value, list):
                        f.value = [None if x is None else _dec(x) for x in f.value]
                    else:
                        f.value = _dec(f.value)

    def validate(self) -> None:
        _is_str("LabelRecord.seed_id", self.seed_id)
        _is_str("LabelRecord.annotator", self.annotator)
        try:
            date.fromisoformat(self.annotated_at)
        except (TypeError, ValueError) as e:
            raise ValueError(f"LabelRecord.annotated_at not ISO: {self.annotated_at!r}") from e
        _pos("LabelRecord.minutes_spent", self.minutes_spent, strict=False)
        if self.initial_label_model is not None:
            _is_str("LabelRecord.initial_label_model", self.initial_label_model)
        if not isinstance(self.meta, SeedQuote):
            raise ValueError("LabelRecord.meta must be SeedQuote")
        self.meta.validate()
        if self.meta.seed_id != self.seed_id:
            raise ValueError("LabelRecord.seed_id differs from meta.seed_id")
        if not isinstance(self.terms, dict):
            raise ValueError("LabelRecord.terms must be a dict of Field")
        for name, f in self.terms.items():
            if name not in _TERM_CODECS:
                raise ValueError(f"LabelRecord.terms: unknown field {name!r}")
            if not isinstance(f, Field):
                raise ValueError(f"LabelRecord.terms[{name}] must be a Field")
            f.validate()
            if f.value is None:
                continue
            if f.status == "conflicting":
                if not isinstance(f.value, list) or len(f.value) != 2:
                    raise ValueError(f"LabelRecord.terms[{name}]: conflicting value must be a list of 2 candidates")
                for cand in f.value:
                    if cand is None:
                        raise ValueError(f"LabelRecord.terms[{name}]: conflicting candidate is None")
                    _check_term_value(name, cand)
            else:
                _check_term_value(name, f.value)
        if not isinstance(self.notes, dict):
            raise ValueError("LabelRecord.notes must be dict")

    def has_conflicts(self) -> bool:
        return any(f.status == "conflicting" for f in self.terms.values())

    def to_quote_terms(self) -> "QuoteTerms":
        """Convert to QuoteTerms; raises ValueError when any field is conflicting."""
        bad = [n for n, f in self.terms.items() if f.status == "conflicting"]
        if bad:
            raise ValueError(f"conflicting fields cannot be converted: {bad}")
        return QuoteTerms.from_dict(self._terms_dict())

    def _terms_dict(self) -> dict:
        return {n: {"value": _enc(f.value), "status": f.status, "spans": [s.to_dict() for s in f.spans]}
                for n, f in self.terms.items()}

    def to_dict(self) -> dict:
        return {"seed_id": self.seed_id, "annotator": self.annotator, "annotated_at": self.annotated_at,
                "minutes_spent": str(self.minutes_spent), "initial_label_model": self.initial_label_model,
                "meta": self.meta.to_dict(), "terms": self._terms_dict(), "notes": _enc(self.notes)}

    @classmethod
    def from_dict(cls, d: dict) -> "LabelRecord":
        _need(d, "seed_id", "annotator", "annotated_at", "minutes_spent", "meta", "terms")
        terms = {}
        for n, fd in d["terms"].items():
            if n not in _TERM_CODECS:
                raise ValueError(f"LabelRecord.terms: unknown field {n!r}")
            dec = _TERM_CODECS[n]
            if dec is not None and fd.get("status") == "conflicting" and isinstance(fd.get("value"), list):
                terms[n] = Field.from_dict(fd, lambda v, dec=dec: [None if x is None else dec(x) for x in v])
            else:
                terms[n] = Field.from_dict(fd, dec)
        return cls(d["seed_id"], d["annotator"], d["annotated_at"], d["minutes_spent"],
                   d.get("initial_label_model"), SeedQuote.from_dict(d["meta"]), terms, d.get("notes", {}))
