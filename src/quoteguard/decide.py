"""Decision rule: feasible set, optimum, follow-up (ask) detection on the value grid, regret.

Feasible set: transit_days upper bound <= the shipment deadline and ddp_check level != high.
Optimum: lowest deterministic landed cost in the feasible set (ties by cand_id).
Ask: for every missing or conflicting term the optimum is recomputed over the value grid
(conflicting: the two stated values; missing: the finite set in value_sets; several gaps: Cartesian
product). If the optimum changes anywhere on the grid, or the feasible set is empty, the answer is ask.
"""
from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from .calc_py import landed_cost
from .gate import ddp_from_breakdown
from .params import Snapshot
from .schema import Field, QuoteTerms, Shipment
from .util import canonical_json

GAP_FIELDS = ("dim_divisor", "fuel_pct", "ddp", "broker_fee_cny", "isf_fee_cny", "min_kg_shipment", "surcharges")
MAX_GRID = 4096


@dataclass
class CandView:
    cand_id: str
    terms: QuoteTerms
    conflicts: dict = field(default_factory=dict)   # field -> [decoded value, decoded value]


@dataclass
class TaskView:
    shipment: Shipment
    candidates: list
    snapshot: Snapshot
    freight_floor: dict
    value_sets: dict


@dataclass
class Decision:
    type: str                       # choose | ask
    choice: Optional[str]
    opt_cost_cny: Optional[Decimal]  # choose: lowest cost of the chosen quote over the grid
    ask_fields: list                # "<cand_id>.<field>" gap ids whose values change the optimum
    cost_range: Optional[tuple] = None
    min_margin: Optional[Decimal] = None     # min over grid points of (second - best) / best
    min_feasible: int = 0                    # min number of feasible quotes over grid points
    grid_size: int = 1
    grid_log: list = field(default_factory=list)


# ---------------------------------------------------------------- value decoding
def decode_term(name: str, raw: Any):
    """Decode a plain/JSON value of a term (Decimal, DDP, list of Surcharge) like QuoteTerms.from_dict."""
    return QuoteTerms.from_dict({name: {"value": raw, "status": "stated"}}).plain(name)


def value_set(value_sets: dict, name: str) -> list:
    if name not in value_sets or name == "surcharges":
        raise ValueError(f"no value set for term {name!r}")
    vals = value_sets[name]
    if name == "ddp":
        vals = [{k: v for k, v in d.items() if k != "id"} for d in vals]
    return [decode_term(name, v) for v in vals]


def _label(v) -> str:
    if hasattr(v, "to_dict"):
        return canonical_json(v.to_dict())
    if isinstance(v, list):
        return canonical_json([x.to_dict() if hasattr(x, "to_dict") else str(x) for x in v])
    return str(v)


def cand_gaps(view: TaskView, cand: CandView) -> list:
    """[(field, kind, values)] for the grid-eligible terms that are missing or conflicting."""
    out = []
    for name in GAP_FIELDS:
        st = getattr(cand.terms, name).status
        if st == "conflicting":
            vals = cand.conflicts.get(name) or getattr(cand.terms, name).value
            if not vals or len(vals) < 2:
                raise ValueError(f"{cand.cand_id}.{name} is conflicting but has no two values")
            out.append((name, "conflicting", list(vals)))
        elif st == "missing" and name in view.value_sets:
            # a missing term without a value set is not a grid dimension: the calculator's default applies
            out.append((name, "missing", value_set(view.value_sets, name)))
    return out


def with_values(terms: QuoteTerms, values: dict) -> QuoteTerms:
    t = copy.deepcopy(terms)
    for name, v in values.items():
        setattr(t, name, Field(v, "stated", []))
    return t


def _transit_ok(terms: QuoteTerms, deadline: int) -> bool:
    td = terms.plain("transit_days")
    if td is None:
        raise ValueError("transit_days missing; the feasible set cannot be determined")
    return td[1] <= deadline


def evaluate(view: TaskView, terms: QuoteTerms):
    """(CostBreakdown, DDPResult, feasible) for fully specified terms."""
    bd = landed_cost(view.shipment, terms, view.snapshot)
    dd = ddp_from_breakdown(view.shipment, terms, view.snapshot, bd, view.freight_floor)
    return bd, dd, (_transit_ok(terms, view.shipment.deadline_days) and dd.level != "high")


# ---------------------------------------------------------------- decide
def cand_rows(view: TaskView, cand: CandView):
    """(own gap slots, {combo: row}) over the candidate's own value grid.

    row = {"total": Decimal, "feasible": bool, "level": ddp level, "assign": {field: label}}.
    """
    own = cand_gaps(view, cand)
    rows = {}
    for combo in itertools.product(*[range(len(v)) for _, _, v in own]):
        t = with_values(cand.terms, {own[j][0]: own[j][2][i] for j, i in enumerate(combo)})
        bd, dd, feas = evaluate(view, t)
        rows[combo] = {"total": bd.total_cny, "feasible": feas, "level": dd.level,
                       "assign": {own[j][0]: _label(own[j][2][i]) for j, i in enumerate(combo)}}
    return own, rows


def decide(view: TaskView) -> Decision:
    slots = []      # (cand index, field, kind, values)
    tables = []     # per candidate: {tuple of value indices of its own slots: (total, feasible)}
    for ci, c in enumerate(view.candidates):
        own, rows = cand_rows(view, c)
        for f, k, v in own:
            slots.append((ci, f, k, v))
        tables.append({combo: (r["total"], r["feasible"]) for combo, r in rows.items()})
    size = 1
    for s in slots:
        size *= len(s[3])
    if size > MAX_GRID:
        raise ValueError(f"value grid too large: {size}")
    slot_of = [[j for j, s in enumerate(slots) if s[0] == ci] for ci in range(len(view.candidates))]

    choices, log = {}, []
    min_margin, min_feas = None, None
    costs = {}      # point -> {cand index: total}
    for point in itertools.product(*[range(len(s[3])) for s in slots]):
        rows = []
        for ci, c in enumerate(view.candidates):
            total, feas = tables[ci][tuple(point[j] for j in slot_of[ci])]
            if feas:
                rows.append((total, c.cand_id, ci))
        rows.sort()
        choice = rows[0][1] if rows else None
        choices[point] = choice
        costs[point] = {ci: tables[ci][tuple(point[j] for j in slot_of[ci])][0]
                        for ci in range(len(view.candidates))}
        margin = None
        if len(rows) >= 2 and rows[0][0] > 0:
            margin = (rows[1][0] - rows[0][0]) / rows[0][0]
            min_margin = margin if min_margin is None else min(min_margin, margin)
        min_feas = len(rows) if min_feas is None else min(min_feas, len(rows))
        log.append({"assign": {f"{view.candidates[s[0]].cand_id}.{s[1]}": _label(s[3][point[j]])
                               for j, s in enumerate(slots)},
                    "choice": choice,
                    "best_cny": str(rows[0][0]) if rows else None,
                    "second_cny": str(rows[1][0]) if len(rows) > 1 else None,
                    "n_feasible": len(rows)})

    distinct = set(choices.values())
    common = dict(min_margin=min_margin, min_feasible=min_feas or 0, grid_size=len(choices), grid_log=log)
    if len(distinct) == 1 and None not in distinct:
        choice = next(iter(distinct))
        ci = next(i for i, c in enumerate(view.candidates) if c.cand_id == choice)
        cs = [costs[p][ci] for p in choices]
        return Decision("choose", choice, min(cs), [], (min(cs), max(cs)), **common)
    if len(distinct) == 1:      # feasible set empty everywhere
        return Decision("ask", None, None, [], None, **common)
    ask = []
    for j, s in enumerate(slots):
        decisive = False
        for p, ch in choices.items():
            for i in range(len(s[3])):
                if i != p[j] and choices[p[:j] + (i,) + p[j + 1:]] != ch:
                    decisive = True
                    break
            if decisive:
                break
        if decisive:
            ask.append(f"{view.candidates[s[0]].cand_id}.{s[1]}")
    return Decision("ask", None, None, ask, None, **common)


# ---------------------------------------------------------------- regret
def regret(choice: Optional[str], gold) -> Any:
    """'out_of_feasible' | None | (yuan per shipment, ratio to the optimum).
    Follow-up (ask) tasks are judged first and return None; then choices outside the feasible set.

    gold: Gold or its dict. per_cand[cid] needs total_cny and feasible; decision needs type and opt_cost_cny.
    A missing choice (the model asked) returns None.
    """
    per = gold["per_cand"] if isinstance(gold, dict) else gold.per_cand
    dec = gold["decision"] if isinstance(gold, dict) else gold.decision
    if choice is None:
        return None
    if choice not in per:
        raise ValueError(f"unknown candidate {choice!r}")
    if dec["type"] == "ask":       # follow-up tasks carry no regret, whatever the choice
        return None
    if not per[choice]["feasible"]:
        return "out_of_feasible"
    opt = Decimal(str(dec["opt_cost_cny"]))
    diff = Decimal(str(per[choice]["total_cny"])) - opt
    return diff, (diff / opt if opt else Decimal(0))


# ---------------------------------------------------------------- views from tasks
def view_from_task(task, snapshot: Snapshot, freight_floor: dict, value_sets: dict) -> TaskView:
    """Rebuild the decision view from a Task alone (what the gold generator saw)."""
    cands = []
    for c in task.candidates:
        p = c.presentation
        terms = QuoteTerms.from_dict(p["terms"])
        conflicts = {k: [decode_term(k, x) for x in v] for k, v in (p.get("conflicts") or {}).items()}
        cands.append(CandView(c.cand_id, terms, conflicts))
    return TaskView(task.shipment, cands, snapshot, freight_floor, value_sets)
