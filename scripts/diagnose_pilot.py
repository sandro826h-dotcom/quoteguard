"""Offline diagnosis of the pilot extractions (M2 to M4).

Reads recorded observations only; no model call. For every (config, task, candidate) it normalises the
recorded extraction, compares it with the expected terms, and traces each difference through the
pipeline stages:

  A  normalize          parsed response -> QuoteTerms
  B  locate             source check against the quote text the extractor saw
  C  merge (M4)         first extraction merged with the partner's extraction for triggered quotes

Expected terms are the terms as shown in the quote (conflicting and missing statuses kept) and the true
terms of the gold answer (cost comparison).

Error classes of a field difference
  parse_failure      the response has no readable JSON
  normalize          the response holds a value that normalize could not read
  model_missed       the quote states the term, the extraction reports it missing
  model_spurious     the quote lacks the term, the extraction states a value
  model_conflict     the extraction reports a conflict the quote does not hold (or misses one that it does)
  model_value        a different value
  currency_unit      value correct up to currency or a factor of 100
  surcharge_cond     surcharge amount right, condition, threshold or contingency different
  surcharge_name     surcharge name not mapped to an internal code
  locate_downgrade   correct after normalize, downgraded to missing by the source check
  review_disagree    first extraction correct, merge made the term conflicting or missing

Usage:  python scripts/diagnose_pilot.py [--batches DIR ...] [--configs ds_flash qwen_plus]
            [--tasks-dir DIR] [--gold-dir DIR] [--outdir DIR] [--json FILE] [--verbose]
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard import locate  # noqa: E402
from quoteguard import methods_llm as ml  # noqa: E402
from quoteguard import methods_pipeline as mp  # noqa: E402
from quoteguard.orchestrate import Context, load_observations  # noqa: E402
from quoteguard.schema import _TERM_CODECS, Field, Gold, QuoteTerms  # noqa: E402
from quoteguard.taskgen import true_terms  # noqa: E402

D = Decimal
EXPERIMENT = "pilota"
SEED = 20261010
FIELDS = [n for n in _TERM_CODECS if n != "channel"]
COST_FIELDS = ("rate_tiers", "min_kg_shipment", "min_kg_carton", "dim_divisor", "rounding_kg", "surcharges",
               "fuel_pct", "ddp", "broker_fee_cny", "isf_fee_cny", "transit_days")


# ---------------------------------------------------------------- comparison helpers
def canon(name, v):
    """Comparable form of a plain term value."""
    if v is None:
        return None
    if name in ("includes", "excludes"):
        return tuple(sorted(v))
    if name == "claims":
        return tuple(sorted((c.type, c.entity, c.reg_no or "", c.jurisdiction or "") for c in v))
    if name in COST_FIELDS or isinstance(v, Decimal):
        return mp._canon_value(name, v)
    if name == "validity":
        return tuple(v)
    if name == "liability":
        return "".join(str(v).split())
    return v


def eff(name, f, mode):
    """Field as the pipeline reads it: an absent optional fee is zero."""
    if name == "claims" and f.status == "missing":
        return Field([], "stated", [])      # a quote without qualification claims
    return mp._effective(name, f, mode) if name in COST_FIELDS else f


def sig(name, f, mode):
    f = eff(name, f, mode)
    if f.status == "conflicting":
        return ("conflicting", tuple(canon(name, x) for x in (f.value or [])))
    if f.status == "missing":
        return ("missing", None)
    return ("value", canon(name, f.value))


def show(name, f):
    if f.status == "missing":
        return "missing"
    if f.status == "conflicting":
        return "conflicting" + str([_plain(name, x) for x in f.value])
    return f"{f.status}:{_plain(name, f.value)}"


def _plain(name, v):
    if isinstance(v, list) and v and hasattr(v[0], "to_dict"):
        return [{k: x for k, x in i.to_dict().items() if x not in (None, "per_kg", False)} if name != "claims" else
                i.to_dict() for i in v]
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return str(v) if isinstance(v, Decimal) else v


def surcharge_parts(v):
    """Per surcharge: (basis, amount, currency) and (condition, threshold, contingent)."""
    a, b = [], []
    for s in v or []:
        a.append((s.basis, mp._canon_value("x", s.amount), s.currency or ""))
        b.append((s.condition, mp._canon_value("x", s.threshold), s.contingent))
    return sorted(a), sorted(b)


def classify(name, got, exp, raw_entry, mode):
    """Error class of one differing field (stage A)."""
    g, e = eff(name, got, mode), eff(name, exp, mode)
    if exp.status == "conflicting" or got.status == "conflicting":
        return "model_conflict"
    if e.status == "missing" and g.status != "missing":
        return "model_spurious"
    if g.status == "missing":
        _, rv, _ = mp._entry(raw_entry)
        if raw_entry is not None and rv is not None and (not isinstance(raw_entry, dict)
                                                         or raw_entry.get("status") != "missing"):
            return "normalize"
        return "model_missed"
    gv, ev = got.value, exp.value
    if name == "surcharges":
        ga, gb = surcharge_parts(gv)
        ea, eb = surcharge_parts(ev)
        if any(not mp.surcharge_code(s.code) or s.code not in mp.CODE_DEFAULTS for s in gv):
            return "surcharge_name"
        if ga == ea and gb != eb:
            return "surcharge_cond"
        if [(x[0], x[1]) for x in ga] == [(x[0], x[1]) for x in ea]:
            return "currency_unit"
        return "model_value"
    if isinstance(gv, Decimal) and isinstance(ev, Decimal) and ev != 0:
        if gv * 100 == ev or gv == ev * 100:
            return "currency_unit"
    if name == "rate_tiers":
        if [t.min_kg for t in gv] == [t.min_kg for t in ev] and [t.price for t in gv] == [t.price for t in ev]:
            return "currency_unit"
    return "model_value"


# ---------------------------------------------------------------- main diagnosis
def run(args):
    obs = load_observations(args.batches)
    obs_list = list(obs.values())
    ctx = Context(args.tasks_dir, args.outdir)
    task_ids = sorted({o["task_id"] for o in obs_list if o.get("stage") == "extract"})
    tasks = [t for t in ctx.tasks if t.task_id in task_ids]
    golds = {t.task_id: Gold.from_dict(json.loads((Path(args.gold_dir) / f"{t.task_id}.json").read_text("utf-8")))
             for t in tasks}
    rows, fps, cost_rows, decisions = [], [], [], []

    def ext_text(cfg, task, cid):
        key = f"{EXPERIMENT}|extract|{cfg}|{task.task_id}|extract|{cid}|r{mp_repeat}"
        o = obs.get(key)
        return None if o is None else o["text"]

    mp_repeat = 1
    for t in tasks:
        gold = golds[t.task_id]
        _, ti0 = ctx.prepare(t)
        reg = ctx.registry_snapshot(t)
        mode = t.shipment.mode
        shown = {c.cand_id: QuoteTerms.from_dict(c.presentation["terms"]) for c in t.candidates}
        true = {c.cand_id: true_terms(t, gold, c.cand_id) for c in t.candidates}
        tis = {c: ml.apply_transcriptions(ti0, obs_list, ctx.models[c].vision_config_id) for c in args.configs}
        for cfg in args.configs:
            partner = mp.review_partner(cfg)
            ti, ti_p = tis[cfg], tis[partner]
            ext = {}
            for q in ti.quotes:
                txt = ext_text(cfg, t, q.cand_id)
                if txt is not None:
                    ext[q.cand_id] = txt
            pext = {}
            for q in ti_p.quotes:
                txt = ext_text(partner, t, q.cand_id)
                if txt is not None:
                    pext[q.cand_id] = txt
            collected, terms_b, down, info, trig = mp._stage_one(t, ti, ext, ctx.snapshot, ctx.freight_floor, reg)
            m4 = mp.run_m4(t, ti, ext, ctx.snapshot, None, ctx.freight_floor, reg, pext, ctx.value_sets,
                           config_id=cfg, review_config_id=partner,
                           review_texts={q.cand_id: ti_p.quote_text(q) for q in ti_p.quotes})
            m2 = mp.run_m2(t, ti, ext, ctx.snapshot)
            gd = gold.decision
            decisions.append({"config": cfg, "task": t.task_id, "gold": gd["type"] + ":" + str(gd["choice"] or gold.followup_gap_ids),
                              "M2": m2["choice"], "M4": m4["decision"] + ":" + str(m4["choice"] or m4["ask_fields"]),
                              "M4_correct": (m4["decision"] == gd["type"] and (m4["choice"] == gd["choice"] if gd["type"] == "choose"
                                             else bool(set(m4["ask_fields"]) & set(gold.followup_gap_ids))))})
            pcol = {cid: mp._as_terms(pext[cid], mode) if cid in pext else (None, "no extraction") for cid in terms_b}
            for q in ti.quotes:
                cid = q.cand_id
                t1, err1 = collected[cid]
                raw1 = _raw(ext.get(cid))
                t1b = terms_b[cid]
                t2, err2 = pcol[cid]
                t2b = None
                if t2 is not None:
                    t2b, _ = locate.verify_terms(t2, ti_p.quote_text(q))
                merged = None
                if cid in trig and t2b is not None:
                    merged, _ = mp.merge_extractions(t1b, t2b, mode)
                final = merged or t1b
                raw2 = _raw(pext.get(cid))
                gold_pc = gold.per_cand[cid]
                for name in FIELDS:
                    e = getattr(shown[cid], name)
                    a = getattr(t1, name)
                    b = getattr(t1b, name)
                    c = getattr(final, name)
                    ok_a = sig(name, a, mode) == sig(name, e, mode)
                    ok_b = sig(name, b, mode) == sig(name, e, mode)
                    ok_c = sig(name, c, mode) == sig(name, e, mode)
                    if ok_a and ok_b and ok_c:
                        continue
                    if err1:
                        cls, stage = "parse_failure", "A"
                    elif not ok_a:
                        cls, stage = classify(name, a, e, (raw1 or {}).get(name), mode), "A"
                    elif not ok_b:
                        cls, stage = "locate_downgrade", "B"
                    else:
                        cls, stage = "review_disagree", "C"
                    rows.append({"config": cfg, "task": t.task_id, "cand": cid, "field": name, "class": cls,
                                 "stage": stage, "cost_relevant": name in COST_FIELDS,
                                 "expected": show(name, e), "extracted": show(name, a), "after_locate": show(name, b),
                                 "final": show(name, c),
                                 "review_extracted": show(name, getattr(t2, name)) if t2 is not None else None,
                                 "review_ok": (sig(name, getattr(t2b, name), mode) == sig(name, e, mode))
                                 if t2b is not None else None})
                # false positive triples of M4 (conflict / gap lists against the gold lists)
                out = m4["per_cand"][cid]
                for kind, got_list, gold_ids in (("conflict", out["conflicts"], gold_pc["conflict_ids"]),
                                                 ("gap", out["gaps"], gold_pc["missing_ids"])):
                    for name in got_list:
                        if f"{cid}.{name}" in gold_ids:
                            continue
                        fps.append({"config": cfg, "task": t.task_id, "cand": cid, "kind": kind, "field": name,
                                    "source": fp_source(name, kind, t1, t1b, t2b, merged, shown[cid], raw1, mode,
                                                        cid in trig),
                                    "first": show(name, getattr(t1, name)),
                                    "after_locate": show(name, getattr(t1b, name)),
                                    "review": show(name, getattr(t2b, name)) if t2b is not None else None,
                                    "expected": show(name, getattr(shown[cid], name))})
                # cost error and the impact of each wrong field
                p = m2["per_cand"][cid]
                tr = true[cid]
                gold_total = D(gold_pc["total_cny"])
                cost = p.get("cost_cny")
                imp = {}
                if cost is not None:
                    for name in COST_FIELDS:
                        if sig(name, getattr(t1, name), mode) != sig(name, getattr(tr, name), mode):
                            fixed = copy.deepcopy(t1)
                            setattr(fixed, name, copy.deepcopy(getattr(tr, name)))
                            r = mp.price_terms(t.shipment, fixed, ctx.snapshot)
                            if "bd" in r:
                                imp[name] = str(r["bd"].total_cny - cost)
                cost_rows.append({"config": cfg, "task": t.task_id, "cand": cid, "gold": str(gold_total),
                                  "m2_cost": None if cost is None else str(cost),
                                  "abs_err": None if cost is None else str(abs(cost - gold_total)),
                                  "impact_of_fixing_field": imp, "defaulted": p.get("defaulted"),
                                  "determined": gold_pc["determined"]})
    return rows, fps, cost_rows, decisions


def _raw(text):
    if text is None:
        return None
    try:
        return mp.parse_extraction(text)
    except ValueError:
        return None


def fp_source(name, kind, t1, t1b, t2b, merged, exp, raw1, mode, triggered):
    """Where a false conflict or gap came from."""
    a, b = getattr(t1, name), getattr(t1b, name)
    if kind == "conflict":
        if a.status == "conflicting":
            return "model_reported_conflict"
        if merged is not None and getattr(merged, name).status == "conflicting":
            c1 = sig(name, getattr(t1b, name), mode) == sig(name, getattr(exp, name), mode)
            c2 = t2b is not None and sig(name, getattr(t2b, name), mode) == sig(name, getattr(exp, name), mode)
            return ("review_disagree: first correct, reviewer wrong" if c1 and not c2 else
                    "review_disagree: reviewer correct, first wrong" if c2 and not c1 else
                    "review_disagree: both differ from expected" if not c1 and not c2 else
                    "review_disagree: both equal to expected (comparison artefact)")
        return "other"
    if a.status == "missing":
        _, rv, _ = mp._entry((raw1 or {}).get(name))
        if raw1 is not None and rv is not None and (raw1.get(name) or {}).get("status") != "missing":
            return "normalize_unreadable"
        return "model_missing"
    if b.status == "missing":
        return "locate_downgrade"
    if merged is not None and getattr(merged, name).status == "missing":
        return "review_one_side_missing"
    return "other"


# ---------------------------------------------------------------- report
def table(rows, keys, title):
    cnt = collections.Counter(tuple(r[k] for k in keys) for r in rows)
    print(f"\n== {title}")
    for k, n in cnt.most_common():
        print(f"  {n:3d}  " + " | ".join(str(x) for x in k))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", nargs="+", default=[str(ROOT / "runs/pilota-A-live"), str(ROOT / "runs/pilota-B-live")])
    ap.add_argument("--configs", nargs="+", default=["ds_flash", "qwen_plus"])
    ap.add_argument("--tasks-dir", default=str(ROOT / "data/tasks/dev/tasks"))
    ap.add_argument("--gold-dir", default=str(ROOT / "data/gold/dev"))
    ap.add_argument("--outdir", default=str(ROOT / "runs/derived/pilota"))
    ap.add_argument("--json", default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    rows, fps, cost_rows, decisions = run(a)
    print(f"field differences: {len(rows)}  (cost-relevant {sum(r['cost_relevant'] for r in rows)})")
    table(rows, ("field",), "most frequent wrong fields")
    table(rows, ("class",), "error classes")
    table(rows, ("class", "field"), "class x field")
    table([r for r in rows if r["cost_relevant"]], ("class", "field"), "cost-relevant class x field")
    print(f"\nM4 false-positive conflict/gap triples: {len(fps)}")
    table(fps, ("kind", "source"), "false positives by source")
    table(fps, ("kind", "field", "source"), "false positives by field and source")
    print("\n== M2 cost errors (abs yuan), by candidate")
    for r in cost_rows:
        print(f"  {r['config']:9s} {r['task']} {r['cand']} gold={r['gold']:>10s} m2={r['m2_cost']} err={r['abs_err']} "
              f"fix-impact={r['impact_of_fixing_field']}")
    print("\n== decisions")
    for d in decisions:
        print("  " + json.dumps(d, ensure_ascii=False))
    if a.verbose:
        print("\n== details")
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        for r in fps:
            print(json.dumps(r, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps({"differences": rows, "false_positives": fps, "cost": cost_rows, "decisions": decisions},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
