"""Counterfactual task generator: shipments, candidate quotes, layer operations, gold answers.

Everything is deterministic in (seed, input files). Per layer the generator applies only that layer's
operation (no stacking):
- explicit: all terms stated in the body.
- implicit: at least one fee that changes the optimum sits in a footnote or annex (terms stay stated).
- conflict: candidate terms are given twice with different values (status conflicting).
- insufficient: candidate terms are missing.
Decisive / non-decisive follow-up status is verified with decide.decide over the value grid.
A task is redrawn (at most MAX_ATTEMPTS times) until the 3% margin and the layer targets hold.

Task files never contain answers; answers are written to a separate directory.

Design notes:
- All candidates of one task share the shipment's transport mode (the calculators require terms mode ==
  shipment mode); they differ in channel structure, price level, DDP and clauses.
- Missing terms are scalar terms and DDP (dim_divisor, fuel_pct, broker_fee_cny, isf_fee_cny,
  min_kg_shipment, ddp). Surcharges can only be conflicting: two tables that differ in the amount of one
  surcharge (low and high value from value_sets), because the schema has no per-surcharge status.
- Follow-up fields are the gaps whose values alone change the optimum (ids "<cand>.<field>"). The
  gold opt_cost_cny of a choose task is the cost under the hidden true values; for a conflict the true
  value is one of the two stated values, chosen at random and stored in the gold only.
- presentation.quote_entity (a SYN- name) is the quoting entity; claim "entity mismatch" scenarios
  compare it with the certified entity.
- Fields that must never reach a model input (the input builder is a separate module): Task.layer, the
  status of every term, presentation.conflicts, fee_location, template_id, pool, wording ids, lang_mix,
  quote_entity as a structured field, Task.rng_seed, Candidate.base_seed_ids, Candidate.files, all Gold
  content and the generator inputs (registry generator_aux). Models see the rendered quote and the
  whitelisted shipment fields only (cartons, goods, destination, deadline, ship date).
- Quotas: in the conflict and insufficient layers at most 40% (floored) of the decisive tasks are decided by
  ddp; the same share (rounded half up) of the non-decisive tasks gets a ddp gap. Missing terms are
  limited to the required-term list of the specification (value_sets eligible_fields).
- After a task is drawn, every gap candidate is rendered: a conflicting term must have two sources and a
  missing term none; failures are redrawn. Retries and swallowed exceptions are counted in the manifest.
- Inputs carry source_status. 'pending_calibration' blocks the test split; the other states
  (calibrated, estimated, synthetic_assumption, no_qualifying_units) are allowed and recorded.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import platform
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

import yaml

from .calc_py import landed_cost
from .calc_xlsx import landed_cost_xlsx
from .decide import (GAP_FIELDS, CandView, TaskView, cand_rows, decide, decode_term, evaluate, value_set)
from .gate import claim_check, hmf_cny
from .params import Snapshot, load_snapshot
from .render import RenderSpec, render
from .schema import (DDP, Candidate, Carton, Claim, Destination, Evidence, Field, Gold, Goods, QuoteTerms,
                     RateTier, Shipment, Surcharge, Task, _enc)
from .util import atomic_json, canonical_json, sha256_file, sha256_text

GENERATOR_VERSION = "taskgen-0.1"
DECISION_RULE_ID = "DR-2026-10-10"
MAX_ATTEMPTS = 2000
TAXES = ("关税", "MPF", "HMF")
SCOPE_TAXES = {"duty": {"关税"}, "duty_mpf": {"关税", "MPF"}, "all": {"关税", "MPF", "HMF"}}
MISSING_FIELDS_SPEC = {"express": {"dim_divisor", "min_kg_shipment", "ddp", "fuel_pct"},
                       "air": {"dim_divisor", "min_kg_shipment", "ddp"},
                       "sea": {"dim_divisor", "min_kg_shipment", "ddp", "isf_fee_cny"}}
DDP_SHARE = Decimal("0.4")
NON_COST_CODES = ("freight", "fuel", "duty", "mpf", "hmf", "broker", "isf")
MIN_MARGIN = Decimal("0.03")
FEE_FIELDS = ("surcharges", "fuel_pct", "broker_fee_cny", "isf_fee_cny")
LAYER_ORDER = ("explicit", "implicit", "conflict", "insufficient")
MODALITIES = ("text", "table", "image")
PENDING = "pending_calibration"
D = Decimal
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data" / "taskgen"
DEFAULT_SNAPSHOT_PATH = ROOT / "data" / "params" / "tariff_snapshot_2026-10-09.json"
DEFAULT_TEMPLATE_POOL = {
    pool: [f"{pool}-{m}-{i}" for m in MODALITIES for i in (1, 2, 3)] for pool in ("dev", "test")
}


class PendingCalibrationError(ValueError):
    """Raised when a test set would be generated from placeholder (uncalibrated) inputs."""


class VerificationError(RuntimeError):
    """Raised when the two calculators disagree."""


# ---------------------------------------------------------------- inputs
def load_inputs(data_dir=DEFAULT_DATA_DIR) -> dict:
    data_dir = Path(data_dir)
    names = {"structures": "structures.yaml", "value_sets": "value_sets.yaml",
             "registry": "registry_synthetic.yaml", "freight_floor": "freight_floor.yaml"}
    out = {"yaml_sha256": {}}
    for key, fn in names.items():
        p = data_dir / fn
        with open(p, encoding="utf-8") as f:
            out[key] = yaml.safe_load(f)
        out["yaml_sha256"][fn] = sha256_file(p)
    return out


def pending_items(structures: dict, freight_floor: dict) -> list:
    """Paths of values still marked pending_calibration."""
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            if PENDING in (o.get("source"), o.get("status"), o.get("source_status")):
                found.append(path)
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk({"freight_floor": freight_floor}, "")
    walk({"structures": structures}, "")
    return found


ALLOWED_SOURCE_STATUS = ("calibrated", "estimated", "synthetic_assumption", "no_qualifying_units", PENDING)


def validate_structures(structures: dict, freight_floor: dict) -> None:
    """Every structure item carries a known source_status; non-DDP tier prices respect the freight reference."""
    from .gate import freight_reference

    def chk(path, o):
        st = o.get("source_status")
        if st not in ALLOWED_SOURCE_STATUS:
            raise ValueError(f"{path}: source_status {st!r} not in {ALLOWED_SOURCE_STATUS}")
        if "source_seed_ids" not in o:
            raise ValueError(f"{path}: source_seed_ids missing")

    for c in structures["commodities"]:
        chk(f"commodities.{c['key']}", c)
    for k in ("destinations", "private_destinations", "claims"):
        chk(f"source_meta.{k}", structures["source_meta"][k])
    for mode, m in structures["modes"].items():
        for ch in m["channels"]:
            chk(f"modes.{mode}.{ch['id']}", ch)
            ref = None
            if mode in freight_floor:
                ref, _ = freight_reference(freight_floor, mode, "national")
            if ref is not None:
                low = min(D(str(t["price"][0])) for t in ch["tiers"])
                if low < ref:
                    raise ValueError(f"{ch['id']}: tier price {low} below the freight reference {ref}")


CODE_FILES = ("gate.py", "decide.py", "taskgen.py", "calc_py.py", "calc_xlsx.py", "schema.py", "params.py",
              "util.py", "render.py")


def code_file_hashes() -> dict:
    return {n: sha256_file(Path(__file__).parent / n) for n in CODE_FILES}


def generator_code_sha256() -> str:
    h = hashlib.sha256()
    for n in CODE_FILES:
        h.update((Path(__file__).parent / n).read_bytes())
    return h.hexdigest()


def _run(cmd) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=ROOT)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def environment_info() -> dict:
    """Git commit, whether the working tree is clean, and tool versions (None when unavailable)."""
    status = _run(["git", "status", "--porcelain"])
    return {"git_commit": _run(["git", "rev-parse", "HEAD"]),
            "git_clean": None if status is None else status == "",
            "python": platform.python_version(), "pyyaml": yaml.__version__,
            "libreoffice": _run(["soffice", "--version"])}


# ---------------------------------------------------------------- random helpers
def _draw(rng, lo, hi, step) -> Decimal:
    lo_d, hi_d, st = D(str(lo)), D(str(hi)), D(str(step))
    a = int((lo_d / st).to_integral_value(ROUND_CEILING))
    b = int((hi_d / st).to_integral_value(ROUND_FLOOR))
    return st * rng.randint(a, b)


def _weighted(rng, weights: dict):
    keys = sorted(weights)
    tot = sum(weights[k] for k in keys)
    r = rng.random() * tot
    acc = 0
    for k in keys:
        acc += weights[k]
        if r < acc:
            return k
    return keys[-1]


def _q(x: Decimal, step="0.1") -> Decimal:
    return x.quantize(D(step), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- shipment and candidates
@dataclass
class _Cand:
    cid: str
    alias: str
    entity: str
    arch_id: str
    seed_ids: list
    terms: QuoteTerms                  # fully specified (true) terms
    shown: Optional[QuoteTerms] = None
    conflicts: dict = field(default_factory=dict)       # field -> [v_body, v_other]
    true_values: dict = field(default_factory=dict)     # field -> value used for the gold cost
    fee_location: dict = field(default_factory=dict)
    claims: list = field(default_factory=list)
    presentation: dict = field(default_factory=dict)


def _draw_shipment(rng, structures, mode) -> Shipment:
    com = rng.choice(structures["commodities"])
    ncart = rng.randint(*com["cartons"])
    upc = rng.randint(*com["units_per_ctn"])
    dims = rng.choice(com["carton_cm"])
    kg = _q(D(str(com["unit_kg"])) * upc, "0.1")
    uv = _draw(rng, com["unit_value_usd"][0], com["unit_value_usd"][1], "0.1")
    if rng.random() < structures.get("private_address_prob", 0):
        dst = rng.choice(structures["private_destinations"])
    else:
        dst = rng.choice(structures["destinations"])
    d0 = date.fromisoformat(structures["ship_date"]["from"])
    d1 = date.fromisoformat(structures["ship_date"]["to"])
    ship_date = (d0 + timedelta(days=rng.randint(0, (d1 - d0).days))).isoformat()
    lo, hi = structures["modes"][mode]["deadline_days"]
    return Shipment("PENDING", mode, [Carton(ncart, dims[0], dims[1], dims[2], kg)],
                    [Goods(com["desc"], com["hts"], uv, ncart * upc)],
                    Destination(dst["fba_code"], dst["zip"], dst["addr_type"], dst["remote"]),
                    rng.randint(lo, hi), ship_date)


def _ddp_option(value_sets, oid) -> DDP:
    o = next(x for x in value_sets["ddp"] if x["id"] == oid)
    return DDP.from_dict({k: v for k, v in o.items() if k != "id"})


def _quote_lists(ddp: Optional[DDP]):
    """includes / excludes lists; the taxes follow ddp.scope, and no tax appears when ddp is unknown."""
    inc, exc = ["头程运费", "目的港清关"], ["查验费", "仓储费"]
    if ddp is not None:
        taxes = SCOPE_TAXES[ddp.scope] if ddp.is_ddp else set()
        inc += [t for t in TAXES if t in taxes]
        exc += [t for t in TAXES if t not in taxes]
    return inc, exc


def _build_terms(rng, ship, arch, structures, value_sets, snapshot, ddp_wanted) -> QuoteTerms:
    mode = ship.mode
    prices, floor_p = [], None
    for t in arch["tiers"]:
        p = _draw(rng, t["price"][0], t["price"][1], "0.5")
        prices.append(p if floor_p is None else min(p, floor_p))
        floor_p = prices[-1]
    mins = [t["min_kg"] for t in arch["tiers"]]

    def tiers(ps):
        return [RateTier(m, (mins[i + 1] - 1) if i + 1 < len(mins) else None, p, "CNY")
                for i, (m, p) in enumerate(zip(mins, ps))]

    surcharges = []
    for s in arch["surcharges"]:
        if rng.random() < s.get("prob", 1.0):
            amt = _draw(rng, s["amount"][0], s["amount"][1], "0.5")
            surcharges.append(Surcharge(s["code"], s["basis"], amt, s["currency"], s.get("condition", "always"),
                                        s.get("threshold"), s.get("contingent", False)))
    liab = rng.choice(structures["liability_options"])
    td = rng.choice(arch["transit_days"])
    ddp_id = "none"
    if ddp_wanted:
        ddp_id = rng.choice(["all", "all", "duty_mpf", "duty"])
    includes, excludes = _quote_lists(_ddp_option(value_sets, ddp_id))
    kw = dict(channel=arch["name"], mode=mode, rate_tiers=tiers(prices),
              min_kg_shipment=D(str(rng.choice(arch["min_kg_shipment"]))),
              min_kg_carton=D(str(rng.choice(arch["min_kg_carton"]))),
              dim_divisor=D(str(rng.choice(arch["dim_divisor"]))), rounding_kg=D(str(arch["rounding_kg"])),
              surcharges=surcharges, fuel_pct=D(str(rng.choice(arch["fuel_pct"]))),
              includes=includes, excludes=excludes, ddp=_ddp_option(value_sets, "none"),
              broker_fee_cny=D(str(rng.choice(arch["broker_fee_cny"]))),
              isf_fee_cny=D(str(rng.choice(arch["isf_fee_cny"]))),
              transit_days=list(td), validity=["2026-10-01", "2026-10-31"], liability=liab, claims=[])
    terms = QuoteTerms.from_plain(**kw)
    if ddp_wanted:
        bd = landed_cost(ship, terms, snapshot)
        floor = (bd.duty_floor_cny + hmf_cny(ship, snapshot)) / bd.chargeable_kg
        ratio = D(str(rng.choice(structures["ddp_floor_ratio"])))
        f_lo, f_hi = structures["ddp_markup_factor"]
        factor = _draw(rng, f_lo, f_hi, "0.05")
        terms.rate_tiers.value = tiers([_q(ratio * floor + factor * p) for p in prices])
        terms.ddp.value = _ddp_option(value_sets, ddp_id)
    return terms


def model_registry_snapshot(registry: dict, pool: str) -> dict:
    """Model-facing registry snapshot of one pool: registered records only, no generator helper lists."""
    r = registry[pool]
    return {"snapshot_id": f"{registry['snapshot_id']}-{pool}", "is_synthetic": True,
            "fmc": copy.deepcopy(r["fmc"]), "cn": copy.deepcopy(r["cn"])}


def _validate_registry(registry: dict) -> None:
    names = {}
    for pool in ("dev", "test"):
        recs = registry[pool]["fmc"] + registry[pool]["cn"]
        aux = [{"name": n} for n in registry["generator_aux"][pool]["unregistered_entities"]]
        for e in recs + aux:
            if not e["name"].startswith("SYN-"):
                raise ValueError(f"registry name without SYN- prefix: {e['name']}")
            if e["name"] in names and names[e["name"]] != pool:
                raise ValueError(f"entity {e['name']!r} appears in both dev and test")
            names[e["name"]] = pool
        for r in recs:
            if r.get("is_synthetic") is not True:
                raise ValueError("registry records must be marked is_synthetic")
    reg_names = {pool: {r["name"] for r in registry[pool]["fmc"] + registry[pool]["cn"]} for pool in ("dev", "test")}
    for pool in ("dev", "test"):
        if reg_names[pool] & set(registry["generator_aux"][pool]["unregistered_entities"]):
            raise ValueError("an unregistered entity also has a registry record")


def _draw_claim(rng, mode, structures, reg, unreg):
    """Returns (claims list, quote_entity). reg is the model-facing snapshot of the pool."""
    fmc_act = [r["name"] for r in reg["fmc"] if r["oti_type"] == "NVOCC" and r["status"] == "active"]
    fmc_rev = [r["name"] for r in reg["fmc"] if r["oti_type"] == "NVOCC" and r["status"] == "revoked"]
    fmc_off = [r["name"] for r in reg["fmc"] if r["oti_type"] != "NVOCC"]
    cn_act = [r["name"] for r in reg["cn"] if r["cert_type"] == "mot_nvocc_filing" and r["status"] == "active"]
    cn_rev = [r["name"] for r in reg["cn"] if r["cert_type"] == "mot_nvocc_filing" and r["status"] == "revoked"]
    cn_aeo = [r["name"] for r in reg["cn"] if r["cert_type"] == "aeo_advanced"]
    unreg = list(unreg)
    if rng.random() >= structures["claims"]["prob_by_mode"][mode]:
        return [], rng.choice(unreg + fmc_act)
    sc = _weighted(rng, structures["claims"]["scenario_weights"])
    quote_ent = None
    ctype, juris, reg_no = None, None, None
    if sc == "registered":
        if rng.random() < 0.3:
            ent, ctype, juris = rng.choice(cn_act), "MOT_NVOCC", "CN"
        else:
            ent = rng.choice(fmc_act)
            ctype = rng.choice(structures["claims"]["fmc_types"])
            juris = "US" if ctype == "FMC_OTI" else None
            if rng.random() < 0.5:
                reg_no = next(r["license_no"] for r in reg["fmc"] if r["name"] == ent)
    elif sc == "unregistered":
        ent, ctype = rng.choice(unreg), rng.choice(structures["claims"]["fmc_types"])
        juris = "US" if ctype == "FMC_OTI" else None
    elif sc == "unregistered_mot":
        ent, ctype, juris = rng.choice(unreg), "MOT_NVOCC", "CN"
    elif sc == "revoked":
        if rng.random() < 0.3:
            ent, ctype, juris = rng.choice(cn_rev), "MOT_NVOCC", "CN"
        else:
            ent, ctype, juris = rng.choice(fmc_rev), "FMC_OTI", "US"
    elif sc == "type_mismatch":
        ent, ctype, juris = rng.choice(fmc_off), "NVOCC", None
    elif sc == "aeo":
        ent, ctype = rng.choice(cn_aeo), "AEO_CN"
    else:   # entity_mismatch
        ent, ctype = rng.choice(fmc_act), "FMC_OTI"
        juris = "US"
        quote_ent = rng.choice(unreg)
    return [Claim(ctype, ent, reg_no, juris)], quote_ent or ent


def _draw_candidates(rng, ship, structures, value_sets, snapshot, reg, unreg) -> list:
    mode = ship.mode
    n = rng.randint(*structures["n_candidates"])
    flags = [True, False] + [rng.random() < structures["ddp_prob"] for _ in range(n - 2)]
    rng.shuffle(flags)
    aliases = rng.sample(range(1, 100), n)
    out, used = [], set()
    for i in range(n):
        arch = rng.choice(structures["modes"][mode]["channels"])
        terms = _build_terms(rng, ship, arch, structures, value_sets, snapshot, flags[i])
        for _ in range(50):         # the quoting entity is unique within a task
            claims, ent = _draw_claim(rng, mode, structures, reg, unreg)
            if ent not in used:
                break
        used.add(ent)
        terms.claims = Field(claims, "stated", [])
        cid = f"C{i + 1}"
        c = _Cand(cid, f"FWD-{aliases[i]:02d}", ent, arch["id"], list(arch.get("source_seed_ids", [])), terms)
        c.fee_location = {f: "body" for f in FEE_FIELDS}
        out.append(c)
    return out


# ---------------------------------------------------------------- views and gaps
def _view(ship, cands, snapshot, freight_floor, value_sets, shown=True) -> TaskView:
    cv = []
    for c in cands:
        t = (c.shown if shown and c.shown is not None else c.terms)
        cv.append(CandView(c.cid, t, c.conflicts if shown else {}))
    return TaskView(ship, cv, snapshot, freight_floor, value_sets)


def _variable_codes(c: _Cand, value_sets) -> list:
    amounts = value_sets["surcharge_amounts"]
    return [s.code for s in c.terms.plain("surcharges") or []
            if s.code in amounts and not s.contingent]


def _eligible(c: _Cand, mode, value_sets, kind, allow_ddp=True) -> list:
    key = "conflict_fields" if kind == "conflicting" else "eligible_fields"
    out = []
    for f in value_sets[key][mode]:
        if f == "surcharges" and (kind == "missing" or not _variable_codes(c, value_sets)):
            continue
        if f == "ddp" and not allow_ddp:
            continue
        out.append(f)
    return out


def _apply_gap(rng, c: _Cand, name, kind, value_sets):
    """Mark one term of the candidate as missing or conflicting; record the true value."""
    if c.shown is None:
        c.shown = copy.deepcopy(c.terms)
    if name == "surcharges":
        code = rng.choice(_variable_codes(c, value_sets))
        amt = value_sets["surcharge_amounts"][code]
        tables = []
        for lv in ("low", "high"):
            tbl = copy.deepcopy(c.terms.plain("surcharges"))
            for s in tbl:
                if s.code == code:
                    s.amount = D(str(amt[lv]))
            tables.append(tbl)
        if rng.random() < 0.5:
            tables.reverse()
        vals = tables
    else:
        grid = value_set(value_sets, name)
        if name == "ddp":
            # the quote was priced as DDP or not; the hidden true value keeps that nature
            orig = c.terms.plain("ddp").is_ddp
            match = [g for g in grid if g.is_ddp == orig]
        else:
            match = grid
        if kind == "conflicting":
            first = rng.choice(match)
            second = rng.choice([g for g in grid if g is not first])
            vals = [first, second]
            if rng.random() < 0.5:
                vals.reverse()
            pool = [first]
        else:
            vals, pool = grid, match
    if name == "surcharges":
        pool = vals
    true = rng.choice(pool)
    c.true_values[name] = true
    if kind == "conflicting":
        c.conflicts[name] = vals
        setattr(c.shown, name, Field(list(vals), "conflicting", []))   # value holds the two candidates
    else:
        setattr(c.shown, name, Field(None, "missing", []))
    c.terms = copy.deepcopy(c.terms)
    setattr(c.terms, name, Field(true, "stated", []))
    if name == "ddp":
        # with ddp unknown the included / excluded lists carry no tax item; the true terms follow the true ddp
        inc, exc = _quote_lists(None)
        c.shown.includes, c.shown.excludes = Field(inc, "stated", []), Field(exc, "stated", [])
        inc, exc = _quote_lists(true)
        c.terms.includes, c.terms.excludes = Field(inc, "stated", []), Field(exc, "stated", [])


def _drop_fee(terms: QuoteTerms, name) -> QuoteTerms:
    t = copy.deepcopy(terms)
    zero = {"surcharges": [], "fuel_pct": D(0), "broker_fee_cny": D(0), "isf_fee_cny": D(0)}[name]
    setattr(t, name, Field(zero, "stated", []))
    return t


# ---------------------------------------------------------------- one task attempt
@dataclass
class _Raw:
    layer: str
    task_seed: int
    attempt: int
    shipment: Shipment
    cands: list
    decision: object
    view: TaskView
    snapshot: Snapshot
    freight_floor: dict
    registry: dict


def _margin_ok(d) -> bool:
    return d.min_margin is not None and d.min_margin >= MIN_MARGIN and d.min_feasible >= 2


def _positive_fee_fields(view_true: TaskView, c: _Cand) -> list:
    """Fee fields of the candidate whose amount on this shipment is positive."""
    bd, _, _ = evaluate(view_true, c.terms)
    out = []
    if sum((a for k, a in bd.lines if k not in NON_COST_CODES), D(0)) > 0:
        out.append("surcharges")
    for f, code in (("fuel_pct", "fuel"), ("broker_fee_cny", "broker"), ("isf_fee_cny", "isf")):
        if bd.line(code) > 0:
            out.append(f)
    return out


def _attempt(rng, layer, policy, ctx):
    structures, value_sets, snapshot, freight_floor = (ctx["structures"], ctx["value_sets"], ctx["snapshot"],
                                                       ctx["freight_floor"])
    want_decisive, ddp_policy = policy
    mode = _weighted(rng, structures["mode_weights"])
    ship = _draw_shipment(rng, structures, mode)
    cands = _draw_candidates(rng, ship, structures, value_sets, snapshot, ctx["reg"], ctx["unreg"])

    def mkview(shown=True):
        return _view(ship, cands, snapshot, freight_floor, value_sets, shown)

    def raw_of(d, view):
        return _Raw(layer, 0, 0, ship, cands, d, view, snapshot, freight_floor, ctx["reg"])

    if layer in ("explicit", "implicit"):
        vt = mkview(False)
        d0 = decide(vt)
        if d0.type != "choose" or not _margin_ok(d0):
            return None
        if layer == "implicit":
            pool = [(c, f) for c in cands for f in _positive_fee_fields(vt, c)]
            if not pool:
                return None
            sel = rng.sample(pool, min(rng.choice([1, 2]), len(pool)))
            naive = copy.deepcopy(cands)
            for c, f in sel:
                nc = next(x for x in naive if x.cid == c.cid)
                nc.terms = _drop_fee(nc.terms, f)
                c.fee_location[f] = rng.choice(["footnote", "annex"])
            dn = decide(_view(ship, naive, snapshot, freight_floor, value_sets, False))
            if dn.type != "choose" or dn.choice == d0.choice:
                return None
        return raw_of(d0, mkview(False))

    # conflict / insufficient
    kind = "conflicting" if layer == "conflict" else "missing"
    allow_ddp = ddp_policy != "forbid"
    n_gc = min(len(cands), rng.choice([1, 1, 2]))
    chosen = rng.sample(cands, n_gc)
    if ddp_policy == "require":
        c0 = chosen[0]
        _apply_gap(rng, c0, "ddp", kind, value_sets)
        todo = [(c0, [])] if kind == "conflicting" else [(c0, rng.sample(
            [f for f in _eligible(c0, mode, value_sets, kind, False)], rng.choice([0, 1])))]
        todo += [(c, None) for c in chosen[1:]]
    else:
        todo = [(c, None) for c in chosen]
    for c, fixed in todo:
        if fixed is None:
            elig = _eligible(c, mode, value_sets, kind, allow_ddp)
            nf = 1 if kind == "conflicting" else rng.choice([1, 2])
            fixed = rng.sample(elig, min(nf, len(elig)))
            if ddp_policy == "require":
                fixed = [f for f in fixed if f != "ddp"]
        for f in fixed:
            _apply_gap(rng, c, f, kind, value_sets)
    view = mkview(True)
    d = decide(view)
    if want_decisive and (d.type != "ask" or not d.ask_fields):
        return None
    if want_decisive and ddp_policy == "require" and not any(f.endswith(".ddp") for f in d.ask_fields):
        return None
    if not want_decisive and d.type != "choose":
        return None
    if not _margin_ok(d):
        return None
    return raw_of(d, view)


def _present(raw: _Raw, rng, split, structures, template_pool) -> None:
    """Draw the presentation spec of every candidate (stored on the candidate)."""
    wp = structures["wording_pools"][split]
    for c in raw.cands:
        modality = _weighted(rng, structures["modality_weights"])
        tpls = [t for t in template_pool[split] if f"-{modality}-" in t]
        shown = c.shown if c.shown is not None else c.terms
        c.presentation = {
            "cand_id": c.cid, "modality": modality, "template_id": rng.choice(tpls), "pool": split,
            "lang_mix": rng.random() < structures["lang_mix_prob"],
            "fee_location": {k: v for k, v in c.fee_location.items() if getattr(shown, k).status != "missing"},
            "conflicts": {k: _enc(v) for k, v in c.conflicts.items()},
            "ddp_wording_id": rng.choice(wp["ddp"]), "claim_wording_id": rng.choice(wp["claim"]),
            "forwarder_alias": c.alias, "quote_entity": c.entity, "terms": shown.to_dict(),
        }


def render_check(raw: _Raw) -> bool:
    """Render every gap candidate: conflicting terms need two sources, missing terms none; no tax item may
    appear in includes / excludes while ddp is a gap."""
    with tempfile.TemporaryDirectory() as td:
        for c in raw.cands:
            shown = c.shown if c.shown is not None else c.terms
            gaps = {n: getattr(shown, n).status for n in shown.to_dict()
                    if getattr(shown, n).status in ("missing", "conflicting")}
            if not gaps:
                continue
            if "ddp" in gaps:
                text = " ".join(map(str, (shown.includes.value or []) + (shown.excludes.value or [])))
                if any(t in text for t in TAXES):
                    return False
            res = render(RenderSpec.from_dict(c.presentation), Path(td) / c.cid)
            for n, st in gaps.items():
                spans = res.span_map.get(n) or []
                if st == "conflicting" and len(spans) < 2:
                    return False
                if st == "missing" and spans:
                    return False
    return True


def _task_seed(seed, split, layer, k) -> int:
    return int(hashlib.sha256(f"{seed}|{split}|{layer}|{k}".encode()).hexdigest()[:8], 16)


def _make_raw(layer, policy, task_seed, ctx, split, template_pool, stats):
    st = stats.setdefault(layer, {"tasks": 0, "attempts": 0, "swallowed_exceptions": 0,
                                  "render_check_failures": 0})
    st["tasks"] += 1
    for attempt in range(MAX_ATTEMPTS):
        st["attempts"] += 1
        rng = random.Random(f"{task_seed}:{attempt}")
        try:
            raw = _attempt(rng, layer, policy, ctx)
        except ValueError:
            st["swallowed_exceptions"] += 1
            raw = None
        if raw is None:
            continue
        _present(raw, rng, split, ctx["structures"], template_pool)
        if not render_check(raw):
            st["render_check_failures"] += 1
            continue
        raw.task_seed, raw.attempt, raw.rng = task_seed, attempt, rng
        return raw
    raise RuntimeError(f"no valid {layer} task after {MAX_ATTEMPTS} attempts (seed {task_seed}, policy {policy})")


def layer_plan(n: int) -> list:
    """[(want_decisive, ddp_policy)] for one gap layer: decisive first; within each group ddp-decided first."""
    n_dec = n_decisive(n)
    n_non = n - n_dec
    k_d = int(n_dec * DDP_SHARE)
    k_n = int(Decimal(k_d) * n_non / n_dec + Decimal("0.5")) if n_dec else 0
    return ([(True, "require")] * k_d + [(True, "forbid")] * (n_dec - k_d)
            + [(False, "require")] * k_n + [(False, "forbid")] * (n_non - k_n))


def n_decisive(n_per_layer: int) -> int:
    return n_per_layer * 2 // 3 if n_per_layer >= 3 else (n_per_layer + 1) // 2


# ---------------------------------------------------------------- finalize: Task and Gold
def _m2(x) -> str:
    return str(Decimal(str(x)).quantize(D("0.01"), rounding=ROUND_HALF_UP))


def _finalize(raw: _Raw, task_id, split, structures):
    ship = raw.shipment
    ship.shipment_id = task_id
    cands_out, per_cand = [], {}
    cv_of = {cv.cand_id: cv for cv in raw.view.candidates}
    for c in raw.cands:
        cands_out.append(Candidate(c.cid, task_id, f"Q-{task_id}-{c.cid}", c.presentation, [], True,
                                   list(c.seed_ids)))
        bd, dd, feas = evaluate(raw.view, c.terms)
        # grid over the candidate's own gaps: cost range, level set, feasibility set
        _, rows = cand_rows(raw.view, cv_of[c.cid])
        totals = [r["total"] for r in rows.values()]
        levels = sorted({r["level"] for r in rows.values()})
        feas_set = sorted({r["feasible"] for r in rows.values()})
        determined = len(set(totals)) == 1 and len(levels) == 1 and len(feas_set) == 1
        claims = []
        for i, cl in enumerate(c.terms.plain("claims") or []):
            st, det = claim_check(cl, ship.mode, raw.registry, c.entity)
            ev = Evidence(f"{c.cid}-claim{i + 1}", c.cid, cl,
                          {"registry_snapshot_id": raw.registry["snapshot_id"], "is_synthetic": True,
                           "quote_entity": c.entity, **det}, st)
            ev.validate()
            claims.append(ev.to_dict())
        per_cand[c.cid] = {
            "total_cny": str(bd.total_cny), "per_unit_cny": str(bd.per_unit_cny),
            "chargeable_kg": str(bd.chargeable_kg),
            "lines": [[k, str(v)] for k, v in bd.lines], "contingent": [[k, str(v)] for k, v in bd.contingent],
            "feasible": feas, "ddp_level": dd.level,
            "determined": determined, "cost_range_cny": [_m2(min(totals)), _m2(max(totals))],
            "ddp_levels": levels, "feasible_values": feas_set, "grid_points": len(rows),
            "ddp_floor_cny_per_kg": None if dd.floor_cny_per_kg is None else str(dd.floor_cny_per_kg),
            "ddp_quote_cny_per_kg": None if dd.quote_cny_per_kg is None else str(dd.quote_cny_per_kg),
            "ddp_floor_source": dd.floor_source, "docs_to_request": dd.docs_to_request,
            "hidden_fee_ids": [f"{c.cid}.{k}" for k, v in sorted(c.fee_location.items()) if v != "body"],
            "conflict_ids": [f"{c.cid}.{k}" for k in sorted(c.conflicts)],
            "missing_ids": [f"{c.cid}.{k}" for k in sorted(c.true_values) if k not in c.conflicts],
            "true_values": {k: _enc(v) for k, v in sorted(c.true_values.items())},
            "claims": claims, "claim_statuses": [e["gold_status"] for e in claims],
        }
    d = raw.decision
    feas_true = sorted((Decimal(per_cand[c.cid]["total_cny"]), c.cid) for c in raw.cands
                       if per_cand[c.cid]["feasible"])
    oracle = feas_true[0] if feas_true else None
    if d.type == "choose":
        if oracle is None or oracle[1] != d.choice:
            raise AssertionError(f"{task_id}: true optimum differs from the grid choice")
        opt = per_cand[d.choice]["total_cny"]
    else:
        opt = None
    decision = {"type": d.type, "choice": d.choice, "opt_cost_cny": opt, "ask_fields": list(d.ask_fields),
                "oracle_choice": oracle[1] if oracle else None,
                "oracle_cost_cny": str(oracle[0]) if oracle else None,
                "verification": {"grid_size": d.grid_size, "min_margin": str(d.min_margin),
                                 "min_feasible": d.min_feasible, "attempt": raw.attempt, "grid": d.grid_log}}
    task = Task(task_id, split, raw.layer, ship, cands_out, DECISION_RULE_ID, raw.snapshot.snapshot_id,
                GENERATOR_VERSION, raw.task_seed)
    gold = Gold(task_id, per_cand, decision, list(d.ask_fields), {})
    task.validate()
    gold.validate()
    return task, gold


def validate_value_sets(value_sets: dict) -> None:
    """The missing-term lists must equal the specification; conflict lists may only name known terms."""
    for mode, want in MISSING_FIELDS_SPEC.items():
        if set(value_sets["eligible_fields"][mode]) != want:
            raise ValueError(f"eligible_fields[{mode}] differs from the specification: {sorted(want)}")
        for f in value_sets["conflict_fields"][mode]:
            if f not in GAP_FIELDS:
                raise ValueError(f"conflict_fields[{mode}] names {f!r}")
    for f in ("dim_divisor", "min_kg_shipment", "ddp", "fuel_pct", "isf_fee_cny", "broker_fee_cny"):
        if f not in value_sets:
            raise ValueError(f"value_sets has no {f}")


def generate(n_per_layer, split, seed, snapshot, structures, value_sets, registry, template_pool,
             freight_floor, stats=None):
    """-> (tasks, golds). Raises PendingCalibrationError for split 'test' with uncalibrated inputs.

    registry is the full synthetic registry file (dev and test pools plus generator_aux).
    stats, when given, is filled with per-layer attempts, swallowed exceptions and render-check failures.
    """
    if split not in ("dev", "test"):
        raise ValueError(f"split {split!r}")
    if set(template_pool["dev"]) & set(template_pool["test"]):
        raise ValueError("dev and test template pools overlap")
    for pool in ("dev", "test"):
        for m in MODALITIES:
            if len([t for t in template_pool[pool] if f"-{m}-" in t]) < 3:
                raise ValueError(f"template pool {pool} needs at least 3 {m} templates")
    validate_structures(structures, freight_floor)
    validate_value_sets(value_sets)
    _validate_registry(registry)
    wp = structures["wording_pools"]
    for k in ("ddp", "claim"):
        if set(wp["dev"][k]) & set(wp["test"][k]):
            raise ValueError(f"dev and test {k} wording pools overlap")
    if split == "test":
        pend = pending_items(structures, freight_floor)
        if pend:
            raise PendingCalibrationError(
                f"{len(pend)} inputs are still {PENDING} (first: {pend[0]}); test set generation refused")
    stats = {} if stats is None else stats
    ctx = {"structures": structures, "value_sets": value_sets, "snapshot": snapshot,
           "freight_floor": freight_floor, "reg": model_registry_snapshot(registry, split),
           "unreg": registry["generator_aux"][split]["unregistered_entities"]}
    raws = []
    gap_plan = layer_plan(n_per_layer)
    for layer in LAYER_ORDER:
        for k in range(n_per_layer):
            policy = gap_plan[k] if layer in ("conflict", "insufficient") else (False, None)
            raws.append(_make_raw(layer, policy, _task_seed(seed, split, layer, k), ctx, split, template_pool, stats))
    order = list(range(len(raws)))
    random.Random(f"{seed}|{split}|order").shuffle(order)
    tasks, golds = [], []
    for pos, idx in enumerate(order, 1):
        t, g = _finalize(raws[idx], f"T-{split}-{pos:03d}", split, structures)
        tasks.append(t)
        golds.append(g)
    return tasks, golds


# ---------------------------------------------------------------- verification with the second calculator
def true_terms(task: Task, gold: Gold, cand_id: str) -> QuoteTerms:
    cand = next(c for c in task.candidates if c.cand_id == cand_id)
    terms = QuoteTerms.from_dict(cand.presentation["terms"])
    for name, raw in gold.per_cand[cand_id]["true_values"].items():
        setattr(terms, name, Field(decode_term(name, raw), "stated", []))
    return terms


def verify_with_xlsx(tasks, golds, workdir, snapshot: Optional[Snapshot] = None, tol=D("0.01")):
    """Recompute every candidate with calc_xlsx; write gold.calc_hashes; raise on any disagreement."""
    snapshot = snapshot or load_snapshot(DEFAULT_SNAPSHOT_PATH)
    gmap = {g.task_id: g for g in golds}
    for task in tasks:
        gold = gmap[task.task_id]
        if task.param_snapshot_id != snapshot.snapshot_id:
            raise VerificationError(f"{task.task_id}: snapshot id mismatch")
        for c in task.candidates:
            terms = true_terms(task, gold, c.cand_id)
            a = landed_cost(task.shipment, terms, snapshot)
            b = landed_cost_xlsx(task.shipment, terms, snapshot, workdir)
            bad = abs(a.total_cny - b["total_cny"]) > tol or abs(a.total_cny - D(gold.per_cand[c.cand_id]["total_cny"])) > tol
            bad = bad or [k for k, _ in a.lines] != [k for k, _ in b["lines"]]
            bad = bad or any(abs(x - y) > tol for (_, x), (_, y) in zip(a.lines, b["lines"]))
            if bad:
                raise VerificationError(f"{task.task_id}/{c.cand_id}: py {a.total_cny} vs xlsx {b['total_cny']}")
            gold.calc_hashes[c.cand_id] = {
                "py_total_cny": _m2(a.total_cny), "xlsx_total_cny": _m2(b["total_cny"]),
                "sha256": sha256_text(canonical_json({"lines": [[k, str(v)] for k, v in a.lines],
                                                      "total": str(a.total_cny)}))}
    return golds


# ---------------------------------------------------------------- output
def default_gold_dir(outdir: Path) -> Path:
    outdir = Path(outdir)
    if outdir.parent.name == "tasks":
        return outdir.parent.parent / "gold" / outdir.name
    return outdir.parent / (outdir.name + "_gold")


def write_split(tasks, golds, outdir, meta: Optional[dict] = None, gold_dir=None) -> dict:
    """Tasks go to <outdir>/tasks/, answers to a separate directory; manifest.json in outdir."""
    outdir = Path(outdir)
    gold_dir = Path(gold_dir) if gold_dir else default_gold_dir(outdir)
    tdir = outdir / "tasks"
    if gold_dir.resolve() == outdir.resolve() or outdir.resolve() in gold_dir.resolve().parents:
        raise ValueError("gold directory must lie outside the task directory")
    files = {"tasks": {}, "gold": {}}
    for t in tasks:
        p = tdir / f"{t.task_id}.json"
        atomic_json(p, t.to_dict())
        files["tasks"][p.name] = sha256_file(p)
    for g in golds:
        p = gold_dir / f"{g.task_id}.json"
        atomic_json(p, g.to_dict())
        files["gold"][p.name] = sha256_file(p)
    manifest = {"generator_version": GENERATOR_VERSION, "generator_code_sha256": generator_code_sha256(),
                "generator_files_sha256": code_file_hashes(), "n_tasks": len(tasks), "files": files,
                "gold_dir": os.path.relpath(gold_dir, outdir), "environment": environment_info()}
    manifest.update(meta or {})
    atomic_json(outdir / "manifest.json", manifest)
    return manifest


def generate_split(split, seed, n_per_layer=None, data_dir=DEFAULT_DATA_DIR, snapshot_path=DEFAULT_SNAPSHOT_PATH,
                   template_pool=None):
    """Load inputs, generate, and return (tasks, golds, meta)."""
    inp = load_inputs(data_dir)
    snapshot = load_snapshot(snapshot_path)
    n = n_per_layer if n_per_layer is not None else (12 if split == "test" else 6)
    pool = template_pool or DEFAULT_TEMPLATE_POOL
    stats = {}
    tasks, golds = generate(n, split, seed, snapshot, inp["structures"], inp["value_sets"], inp["registry"],
                            pool, inp["freight_floor"], stats)
    pend = pending_items(inp["structures"], inp["freight_floor"])
    meta = {"split": split, "seed": seed, "n_per_layer": n, "yaml_sha256": inp["yaml_sha256"],
            "snapshot_id": snapshot.snapshot_id, "snapshot_version": snapshot.raw.get("version"),
            "snapshot_sha256": sha256_file(snapshot_path),
            "template_pool_sha256": sha256_text(canonical_json(pool)),
            "pending_calibration_count": len(pend), "pending_calibration_examples": pend[:5],
            "max_attempts_per_task": MAX_ATTEMPTS, "layer_stats": stats,
            "model_registry_snapshot_sha256": sha256_text(canonical_json(model_registry_snapshot(inp["registry"], split)))}
    return tasks, golds, meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quoteguard.taskgen")
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    ap.add_argument("--seed", required=True, type=int)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-per-layer", type=int, default=None)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--gold-dir", default=None)
    ap.add_argument("--verify-xlsx", default=None, help="workdir for the LibreOffice cross-check")
    a = ap.parse_args(argv)
    try:
        tasks, golds, meta = generate_split(a.split, a.seed, a.n_per_layer, a.data_dir)
    except PendingCalibrationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.verify_xlsx:
        verify_with_xlsx(tasks, golds, a.verify_xlsx)
        meta["xlsx_verified"] = True
    write_split(tasks, golds, a.out, meta, a.gold_dir)
    print(f"wrote {len(tasks)} tasks to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
