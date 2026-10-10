"""Offline statistics of the pilot after re-assembly (no model call, reads recorded observations only).

Run after `orchestrate assemble` and `score` have written <root>/{a,b}/outputs and <root>/{a,b}/scores.

Usage:  PYTHONPATH=src python scripts/rescore_pilot.py [--root runs/derived/pilot_rescore_20261010]
"""
from __future__ import annotations

import argparse
import collections
import functools
import json
import re
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import diagnose_pilot as dp  # noqa: E402
from quoteguard import methods_llm as ml  # noqa: E402
from quoteguard import methods_pipeline as mp  # noqa: E402
from quoteguard.orchestrate import Context, load_observations  # noqa: E402
from quoteguard.schema import _TERM_CODECS  # noqa: E402

DATA_DIR = ROOT / "data/taskgen/dev_v3"
GROUPS = {
    "A": {"exp": "pilota", "tasks": ["T-dev-004", "T-dev-010"], "configs": ["ds_flash", "qwen_plus"],
          "batches": ["pilota-A-live", "pilota-B-live", "pilota-B-retry-live"], "dir": "a"},
    "B": {"exp": "pilotb", "tasks": ["T-dev-020"], "configs": ["glm", "kimi"],
          "batches": ["pilotb-A-live", "pilotb-B-live", "pilotb-B-retry-live"], "dir": "b"},
}
M1_LIMIT = 8000


def usage_from_raw(raw: str):
    """Last non-null usage object in a plain JSON body or an SSE stream, and the length of any reasoning text."""
    usage, reasoning_chars = None, 0

    def take(d):
        nonlocal usage, reasoning_chars
        if not isinstance(d, dict):
            return
        if isinstance(d.get("usage"), dict):
            usage = d["usage"]
        for ch in d.get("choices") or []:
            for part in (ch.get("message") or {}, ch.get("delta") or {}):
                rc = part.get("reasoning_content") or part.get("reasoning")
                if isinstance(rc, str):
                    reasoning_chars += len(rc)

    s = raw.lstrip()
    if s.startswith("data:"):
        for line in raw.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                try:
                    take(json.loads(line[5:]))
                except ValueError:
                    pass
    else:
        try:
            take(json.loads(raw))
        except ValueError:
            pass
    return usage, reasoning_chars


def reasoning_tokens(o):
    u, chars = usage_from_raw(o.get("raw_body") or "")
    rt = ((u or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
    return rt, (u or {}).get("completion_tokens"), chars


def med_max(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "median": statistics.median(xs) if xs else None, "max": max(xs) if xs else None}


def call_stats(group, obs_list, outputs_root):
    """Per config and per call kind (M0, M1, extract, transcribe)."""
    g = GROUPS[group]
    rows = collections.defaultdict(list)
    for o in obs_list:
        rows[(o["config_id"], o["method"] if o["stage"] == "answer" else o["stage"])].append(o)
    parse = {}
    for m in ("M0", "M1"):
        for f in (outputs_root / m).glob("*/*.json") if (outputs_root / m).exists() else []:
            d = json.loads(f.read_text("utf-8"))
            parse[(d["config_id"], m, d["task_id"])] = bool(d.get("parse_ok"))
    res = {}
    for (cfg, kind), os_ in sorted(rows.items()):
        out_tok, trunc, reas, ok = [], 0, [], 0
        for o in os_:
            rt, ct, _ = reasoning_tokens(o)
            ct = ct if ct is not None else (o.get("usage") or {}).get("completion_tokens")
            out_tok.append(ct)
            mx = o.get("max_tokens")
            if o.get("finish_reason") == "length" or (ct is not None and mx and ct >= 0.95 * mx):
                trunc += 1
            reas.append(rt)
            if kind in ("M0", "M1"):
                ok += parse.get((cfg, kind, o["task_id"]), False)
            elif kind == "extract":
                try:
                    mp.parse_extraction(o["text"])
                    ok += 1
                except ValueError:
                    pass
            else:
                ok += 1 if (o.get("text") or "").strip() else 0
        r = {"calls": len(os_), "parse_ok": ok, "parse_rate": ok / len(os_), "truncated": trunc,
             "trunc_rate": trunc / len(os_), "max_tokens": sorted({o.get("max_tokens") for o in os_}, key=str),
             "output_tokens": med_max(out_tok), "reasoning_tokens": med_max(reas),
             "finish_reasons": dict(collections.Counter(o.get("finish_reason") for o in os_))}
        if kind == "M1":
            r["m1_vs_8000"] = {"max_over_limit": (max(t for t in out_tok if t is not None) / M1_LIMIT
                                                  if any(t is not None for t in out_tok) else None),
                               "n_ge_95pct_of_8000": sum(1 for t in out_tok if t is not None and t >= 0.95 * M1_LIMIT)}
        res[f"{cfg}|{kind}"] = r
    return res


def pipeline_stats(group, obs, obs_list, ctx):
    g = GROUPS[group]
    tasks = [t for t in ctx.tasks if t.task_id in g["tasks"]]
    gold_dir = ROOT / "data/gold/dev"
    down_tot, stated_tot = collections.Counter(), collections.Counter()
    down_fields = collections.defaultdict(collections.Counter)
    notes_type, unmapped = collections.Counter(), collections.Counter()
    trig_stat = collections.defaultdict(lambda: {"cands": 0, "triggered": 0, "gold_risk": 0, "gold_risk_untriggered": 0,
                                                 "gold_risk_untriggered_ids": []})
    seen_notes = set()
    for t in tasks:
        _, ti0 = ctx.prepare(t)
        reg = ctx.registry_snapshot(t)
        gold = json.loads((gold_dir / f"{t.task_id}.json").read_text("utf-8"))
        for cfg in g["configs"]:
            ti = ml.apply_transcriptions(ti0, list(obs.values()), ctx.models[cfg].vision_config_id)
            ereqs = mp.extraction_requests(ti, cfg, g["exp"], repeat=1, seed=20261010)
            ext = {r["cand_id"]: obs[r["run_key"]]["text"] for r in ereqs if r["run_key"] in obs}
            collected, terms, down, info, trig = mp._stage_one(t, ti, ext, ctx.snapshot, ctx.freight_floor, reg)
            for cid, (t0, _e) in collected.items():
                stated = [n for n in _TERM_CODECS if getattr(t0, n).status != "missing"]
                stated_tot[cfg] += len(stated)
                down_tot[cfg] += len(down[cid])
                for n in down[cid]:
                    down_fields[cfg][n] += 1
                if (cfg, t.task_id, cid) not in seen_notes:
                    seen_notes.add((cfg, t.task_id, cid))
                    for note in getattr(t0, "norm_notes", []):
                        typ = re.sub(r"'[^']*'", "'*'", note)
                        notes_type[(cfg, typ)] += 1
                        if note.startswith(("unmapped", "unknown")):
                            for w in re.findall(r"'([^']*)'", note):
                                unmapped[(cfg, note.split("'")[0].strip(" :"), w)] += 1
                st = trig_stat[cfg]
                st["cands"] += 1
                triggered = cid in trig
                st["triggered"] += triggered
                pc = gold["per_cand"][cid]
                risk = bool(pc["conflict_ids"] or pc["missing_ids"] or pc["hidden_fee_ids"]
                            or (pc["ddp_level"] not in (None, "none")) or pc["claim_statuses"])
                st["gold_risk"] += risk
                if risk and not triggered:
                    st["gold_risk_untriggered"] += 1
                    st["gold_risk_untriggered_ids"].append(f"{t.task_id}.{cid}")
    out = {}
    for cfg in g["configs"]:
        out[cfg] = {
            "locate_downgraded_fields": down_tot[cfg], "stated_fields_before_locate": stated_tot[cfg],
            "locate_downgrade_rate": down_tot[cfg] / stated_tot[cfg] if stated_tot[cfg] else None,
            "locate_downgraded_by_field": dict(down_fields[cfg]),
            "norm_notes_by_type": {typ: n for (c, typ), n in sorted(notes_type.items()) if c == cfg},
            "review": {**trig_stat[cfg], "triggered_rate": trig_stat[cfg]["triggered"] / trig_stat[cfg]["cands"]}}
    top = collections.Counter()
    for (c, kind, w), n in unmapped.items():
        top[(kind, w)] += n
    return out, [{"kind": k, "text": w, "count": n} for (k, w), n in top.most_common(20)]


def diagnose_group_a(root):
    """Field differences, M4 false positives and M2 cost errors of group A under the current pipeline."""
    tmp = tempfile.mkdtemp(prefix="rescore_ctx_")
    ns = argparse.Namespace(
        batches=[str(ROOT / "runs" / b) for b in GROUPS["A"]["batches"]], configs=GROUPS["A"]["configs"],
        tasks_dir=str(ROOT / "data/tasks/dev/tasks"), gold_dir=str(ROOT / "data/gold/dev"), outdir=tmp)
    orig = dp.Context
    dp.Context = functools.partial(Context, data_dir=DATA_DIR)
    try:
        rows, fps, cost_rows, decisions = dp.run(ns)
    finally:
        dp.Context = orig
    wrong = [r for r in cost_rows if r["abs_err"] is not None and float(r["abs_err"]) != 0.0]
    (root / "diagnose_a.json").write_text(json.dumps(
        {"differences": rows, "false_positives": fps, "cost": cost_rows, "decisions": decisions},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return {"field_differences": len(rows), "field_differences_cost_relevant": sum(r["cost_relevant"] for r in rows),
            "by_class": dict(collections.Counter(r["class"] for r in rows)),
            "m4_false_positive_triples": len(fps),
            "m2_candidates_priced": sum(r["m2_cost"] is not None for r in cost_rows),
            "m2_candidates_with_cost_error": len(wrong),
            "m2_candidates_unpriced": sum(r["m2_cost"] is None for r in cost_rows),
            "m4_decisions_correct": sum(d["M4_correct"] for d in decisions), "m4_decisions": len(decisions),
            "source_file": "runs/derived/pilot_rescore_20261010/diagnose_a.json"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "runs/derived/pilot_rescore_20261010"))
    a = ap.parse_args(argv)
    root = Path(a.root)
    summary = {"data_dir": "data/taskgen/dev_v3", "groups": {}}
    for gname, g in GROUPS.items():
        batches = [ROOT / "runs" / b for b in g["batches"]]
        obs = load_observations(batches)
        ctx = Context(ROOT / "data/tasks/dev/tasks", tempfile.mkdtemp(prefix="rescore_ctx_"), data_dir=DATA_DIR)
        gd = root / g["dir"]
        pipe, unmapped = pipeline_stats(gname, obs, list(obs.values()), ctx)
        agg = json.loads((gd / "scores/aggregate_overall.json").read_text("utf-8"))
        keep = ("n_rows", "decision_correct", "cost_n", "cost_missing", "cost_exact_rate", "cost_abs_mean",
                "micro_f1", "precision", "recall", "false_ask_rate", "correct_ask_rate", "regret_abs_mean",
                "claim_accuracy", "extraction_failures", "parse_ok")
        summary["groups"][gname] = {
            "observations": len(obs),
            "batches": g["batches"],
            "calls": call_stats(gname, list(obs.values()), gd / "outputs"),
            "pipeline": pipe, "unmapped_top20": unmapped,
            "score_overall": {f"{r['method']}|{r['config_id']}": {k: r.get(k) for k in keep} for r in agg},
            "score_file": f"runs/derived/pilot_rescore_20261010/{g['dir']}/scores/aggregate_overall.json"}
    summary["groups"]["A"]["diagnose"] = diagnose_group_a(root)
    summary["groups"]["A"]["diagnose_prior"] = {
        "source": "PROJECT_LOG.md (state after 665b5fe)", "field_differences": 18, "m4_false_positive_triples": 3,
        "m2_candidates_with_cost_error": 4,
        "before_665b5fe": {"field_differences": 58, "m4_false_positive_triples": 18, "m2_candidates_with_cost_error": 14}}
    old = root.parent / "pilota/scores/aggregate_overall.json"
    if old.exists():
        summary["groups"]["A"]["score_before_first_fix"] = {
            f"{r['method']}|{r['config_id']}": {k: r.get(k) for k in ("n_rows", "decision_correct", "cost_n", "cost_missing",
                                                                     "cost_abs_mean", "micro_f1")}
            for r in json.loads(old.read_text("utf-8"))}
        summary["groups"]["A"]["score_before_first_fix_source"] = "runs/derived/pilota/scores/aggregate_overall.json"
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(root / "summary.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
