"""Method flow orchestration: stage plans, executor runs, output assembly with cost back-fill, scoring.

Stage A renders the tasks, freezes the model-facing inputs and requests transcriptions of image quotes.
Stage B (needs the stage A observations) requests M0 and M1 answers and per-quote extractions.
assemble() turns observations into MethodOutput files and books the cost of the calls each output used;
score_all() scores the output files against the answer files.

Nothing here calls a model; calls go through executor.Executor.

CLI:  python -m quoteguard.orchestrate plan|run|assemble|score ...
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from . import executor as ex
from . import methods_llm as ml
from . import methods_pipeline as mp
from . import metrics
from .inputs import build_task_input, freeze_inputs, render_task
from .llm import load_models
from .params import load_snapshot
from .schema import Task
from .taskgen import DEFAULT_DATA_DIR, DEFAULT_SNAPSHOT_PATH, load_inputs, model_registry_snapshot
from .util import atomic_json, canonical_json, sha256_file

METHODS = ("M0", "M1", "M2", "M3", "M4")
PIPELINE = ("M2", "M3", "M4")
DEFAULT_SEED = 20261010
EXTRACT_REPEAT = 1
TRANSCRIBE_REPEAT = 0


class OrchestrationError(ValueError):
    """Inconsistent configuration, plan or observations."""


# ---------------------------------------------------------------- context
class Context:
    """Task files plus the parameter inputs the methods read."""

    def __init__(self, tasks_dir, outdir, models: Optional[dict] = None, data_dir=DEFAULT_DATA_DIR,
                 snapshot_path=DEFAULT_SNAPSHOT_PATH):
        self.outdir = Path(outdir)
        self.models = models or load_models()
        self.inp = load_inputs(data_dir)
        self.snapshot = load_snapshot(snapshot_path)
        self.tasks = [Task.from_dict(json.loads(f.read_text(encoding="utf-8")))
                      for f in sorted(Path(tasks_dir).glob("*.json"))]
        if not self.tasks:
            raise OrchestrationError(f"no task files in {tasks_dir}")
        self.renders = {}
        self.inputs = {}

    def prepare(self, task: Task):
        """(render results, untranscribed TaskInput); rendering is deterministic and repeated safely."""
        if task.task_id not in self.inputs:
            rr = render_task(task, self.outdir / "render" / task.task_id)
            self.renders[task.task_id] = rr
            self.inputs[task.task_id] = build_task_input(task, rr)
        return self.renders[task.task_id], self.inputs[task.task_id]

    def registry_snapshot(self, task: Task) -> dict:
        return model_registry_snapshot(self.inp["registry"], task.split)

    @property
    def freight_floor(self): return self.inp["freight_floor"]

    @property
    def value_sets(self): return self.inp["value_sets"]


def _check_methods(configs, methods, models):
    for m in methods:
        if m not in METHODS:
            raise OrchestrationError(f"unknown method {m}")
    for c in configs:
        if c not in models:
            raise OrchestrationError(f"unknown config {c}")
        if models[c].vision_config_id is None:
            raise OrchestrationError(f"config {c} has no vision_config_id")
    if "M4" in methods:
        for c in configs:
            partner = mp.review_partner(c)
            if partner not in configs:
                raise OrchestrationError(f"M4 for {c} needs its review partner {partner} in configs")


# ---------------------------------------------------------------- observations
def load_observations(batch_dirs: Iterable) -> dict:
    """{run_key: observation} from the batch directories; a repeated run_key is an error."""
    out = {}
    for d in batch_dirs:
        for f in sorted((Path(d) / "observations").glob("*.json")):
            o = json.loads(f.read_text(encoding="utf-8"))
            if o["run_key"] in out:
                raise OrchestrationError(f"run_key {o['run_key']} appears in more than one observation")
            out[o["run_key"]] = o
    return out


def _obs_cost(o: dict) -> dict:
    c = {}
    for part in (o.get("cost_estimate"), o.get("possibly_billed_cost")):
        if part:
            c[part["currency"]] = c.get(part["currency"], 0.0) + float(part["amount"])
    return c


def _add(a: dict, b: dict, scale: float = 1.0) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0.0) + v * scale
    return out


def _find(obs: dict, req: dict) -> Optional[dict]:
    o = obs.get(req["run_key"])
    if o is not None and o["prompt_sha256"] != req["prompt_sha256"]:
        raise OrchestrationError(f"observation of {req['run_key']} was made with a different prompt")
    return o


# ---------------------------------------------------------------- plans
def _write_plan(path: Path, plan: dict, models: dict, overwrite: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(json.dumps(plan)):
            raise FileExistsError(f"{path} exists with different content (frozen plans are not overwritten)")
    else:
        atomic_json(path, plan)
    ex.load_plan(path, models)   # fail early on any plan error
    return path


def build_stage_plans(tasks_dir, configs, methods, experiment, kind, budget, rates, outdir, *,
                      transcription_batches=None, models=None, data_dir=DEFAULT_DATA_DIR,
                      snapshot_path=DEFAULT_SNAPSHOT_PATH, seed: Optional[int] = DEFAULT_SEED,
                      protocol_sha256: Optional[str] = None, overwrite: bool = False) -> dict:
    """Write the frozen plan of stage A (always) and stage B (when transcription_batches are given).

    Returns {"A": plan path, "B": plan path or None}. Inputs are frozen under <outdir>/inputs, renders under
    <outdir>/render, plans under <outdir>/plans.
    """
    configs, methods = list(configs), list(methods)
    ctx = Context(tasks_dir, outdir, models, data_dir, snapshot_path)
    _check_methods(configs, methods, ctx.models)
    out = Path(outdir)

    def plan_doc(stage, reqs):
        doc = {"plan_id": f"{experiment}-{stage}", "kind": kind, "budget": budget, "rates": rates,
               "requests": ml.dedupe_requests(reqs)}
        if protocol_sha256:
            doc["protocol_sha256"] = protocol_sha256
        return doc

    frozen = {}
    reqs_a = []
    for t in ctx.tasks:
        rr, ti = ctx.prepare(t)
        frozen.update({f"{t.task_id}/{k}": v for k, v in freeze_inputs(
            t, rr, out / "inputs", registry_snapshot=ctx.registry_snapshot(t), snapshot=ctx.snapshot,
            freight_floor=ctx.freight_floor, value_sets=ctx.value_sets).items()})
        for c in configs:
            reqs_a += ml.transcription_requests(ti, c, ctx.models, experiment=experiment,
                                                repeat=TRANSCRIBE_REPEAT, seed=seed)
    atomic_json(out / "inputs" / "manifest.json", frozen)
    paths = {"A": _write_plan(out / "plans" / f"{experiment}-A.json", plan_doc("A", reqs_a), ctx.models,
                              overwrite), "B": None}
    if transcription_batches:
        obs = list(load_observations(transcription_batches).values())
        reqs_b = []
        for t in ctx.tasks:
            _, ti0 = ctx.prepare(t)
            for c in configs:
                ti = ml.apply_transcriptions(ti0, obs, ctx.models[c].vision_config_id)
                if "M0" in methods:
                    reqs_b.append(ml.m0_request(ti, c, experiment, seed=seed))
                if "M1" in methods:
                    reqs_b.append(ml.m1_request(
                        ti, c, experiment, snapshot=ctx.snapshot, freight_floor=ctx.freight_floor,
                        hts=t.shipment.goods[0].hts, registry_snapshot=ctx.registry_snapshot(t),
                        value_sets=ctx.value_sets, seed=seed))
                if any(m in methods for m in PIPELINE):
                    reqs_b += mp.extraction_requests(ti, c, experiment, repeat=EXTRACT_REPEAT, seed=seed)
        paths["B"] = _write_plan(out / "plans" / f"{experiment}-B.json", plan_doc("B", reqs_b), ctx.models,
                                 overwrite)
    return paths


def _confirm_live(plan_path, confirm_budget: Optional[str]) -> None:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if confirm_budget is None:
        raise OrchestrationError("live mode requires confirm_budget matching the plan budget")
    if ex.parse_budget(confirm_budget) != {k: float(v) for k, v in plan["budget"].items()}:
        raise OrchestrationError("confirm_budget does not match the plan budget")


def run_stage(plan, mode, out, *, confirm_budget: Optional[str] = None, **executor_kwargs) -> Path:
    """Run a frozen plan through the executor; returns the batch directory (see its manifest.json status).

    Live mode needs confirm_budget ("USD=x,CNY=y") equal to the plan budget. executor_kwargs go to Executor
    (models_path, client_factory, min_interval_s, batch_id, protocol_sha256, ...).
    """
    if mode == "live":
        _confirm_live(plan, confirm_budget)
    e = ex.Executor(plan, out, mode, **executor_kwargs)
    e.run()
    return e.dir


# ---------------------------------------------------------------- assembly
def _jsonable(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(_jsonable(v) for v in x)
    if hasattr(x, "to_dict"):
        return _jsonable(x.to_dict())
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    return str(x)


def _cost_dict(c: dict) -> dict:
    out = {"USD": 0.0, "CNY": 0.0}
    out.update(c)
    return out


def assemble(tasks_dir, gold_dir, batch_dirs, configs, out, *, methods=METHODS, experiment: str,
             models=None, data_dir=DEFAULT_DATA_DIR, snapshot_path=DEFAULT_SNAPSHOT_PATH,
             seed: Optional[int] = DEFAULT_SEED) -> dict:
    """Write <out>/outputs/<method>/<config>/<task>.json for every (method, config, task).

    gold_dir is accepted for a uniform CLI and is not read: outputs are built from observations only.
    Cost of an output = its own calls + the full cost of the image transcription of its task + for M4 the
    partner extractions it used. Configurations sharing a vision config each book the full transcription,
    and M2 to M4 of one configuration each book the same extraction; the breakdown is in
    output["cost_breakdown"]. assemble_manifest.json holds the actual total spend of the loaded
    observations, in which a shared call counts once.
    Returns {"dir", "n_outputs", "missing_observations", "actual_spend"}.
    """
    configs, methods = list(configs), list(methods)
    out = Path(out)
    ctx = Context(tasks_dir, out, models, data_dir, snapshot_path)
    _check_methods(configs, methods, ctx.models)
    obs = load_observations(batch_dirs)
    obs_list = list(obs.values())
    sharers: dict = {}
    for c in configs:
        v = ctx.models[c].vision_config_id
        sharers[v] = sharers.get(v, 0) + 1
    missing, n_out = [], 0

    def finish(o, method, config, task, ti_calls, direct, review, breakdown_extra=None):
        trans = breakdown_extra["trans"]
        share = dict(trans["total"])   # every configuration books the full transcription cost
        total = _add(_add(_add({}, direct["cost"]), review["cost"]), share)
        o["calls"] = list(trans["calls"]) + list(direct["calls"]) + list(review["calls"])
        o["cost"] = _cost_dict(total)
        o["config_id"] = config
        o["cost_breakdown"] = {
            "direct": _cost_dict(direct["cost"]), "review": _cost_dict(review["cost"]),
            "transcription_total": _cost_dict(trans["total"]), "transcription_shared_by": trans["shared_by"],
            "transcription_share": _cost_dict(share), "transcription_booking": "full",
            "direct_calls": direct["calls"],
            "review_calls": review["calls"], "transcription_calls": trans["calls"]}
        p = out / "outputs" / method / config / f"{task.task_id}.json"
        atomic_json(p, _jsonable(o))

    def collect(reqs):
        cost, calls = {}, []
        for r in reqs:
            o = _find(obs, r)
            if o is None:
                missing.append(r["run_key"])
                continue
            cost = _add(cost, _obs_cost(o))
            calls.append(r["run_key"])
        return {"cost": cost, "calls": calls}

    for t in ctx.tasks:
        _, ti0 = ctx.prepare(t)
        reg = ctx.registry_snapshot(t)
        hts = t.shipment.goods[0].hts
        ti_by_cfg = {c: ml.apply_transcriptions(ti0, obs_list, ctx.models[c].vision_config_id) for c in configs}
        for c in configs:
            ti = ti_by_cfg[c]
            treqs = ml.transcription_requests(ti0, c, ctx.models, experiment=experiment,
                                              repeat=TRANSCRIBE_REPEAT, seed=seed)
            tr = collect(treqs)
            trans = {"calls": tr["calls"], "total": tr["cost"], "shared_by": sharers[ctx.models[c].vision_config_id]}
            none = {"cost": {}, "calls": []}
            extra = {"trans": trans}
            for m in methods:
                if m in ("M0", "M1"):
                    req = (ml.m0_request(ti, c, experiment, seed=seed) if m == "M0" else
                           ml.m1_request(ti, c, experiment, snapshot=ctx.snapshot,
                                         freight_floor=ctx.freight_floor, hts=hts, registry_snapshot=reg,
                                         value_sets=ctx.value_sets, seed=seed))
                    o = _find(obs, req)
                    if o is None:
                        missing.append(req["run_key"])
                        res = ml.to_method_output(None, ti, m, c, [])
                        res["parse_error"] = "missing observation"
                        direct = none
                    else:
                        res = ml.to_method_output(ml.parse_final_json(o["text"], "choice"), ti, m, c,
                                                  [req["run_key"]], o["text"])
                        direct = {"cost": _obs_cost(o), "calls": [req["run_key"]]}
                    finish(res, m, c, t, None, direct, none, extra)
                    n_out += 1
                    continue
                ereqs = mp.extraction_requests(ti, c, experiment, repeat=EXTRACT_REPEAT, seed=seed)
                ext, ekeys = {}, []
                for r in ereqs:
                    o = _find(obs, r)
                    if o is None:
                        missing.append(r["run_key"])
                    else:
                        ext[r["cand_id"]] = o["text"]
                        ekeys.append(r["run_key"])
                direct = {"cost": collect_cost(obs, ekeys), "calls": ekeys}
                review = none
                if m == "M2":
                    res = mp.run_m2(t, ti, ext, ctx.snapshot, None, config_id=c, calls=ekeys)
                elif m == "M3":
                    res = mp.run_m3(t, ti, ext, ctx.snapshot, None, ctx.freight_floor, reg, config_id=c,
                                    calls=ekeys)
                else:
                    partner = mp.review_partner(c)
                    ti_p = ti_by_cfg[partner]
                    pext, pkeys = {}, {}
                    for r in mp.extraction_requests(ti_p, partner, experiment, repeat=EXTRACT_REPEAT, seed=seed):
                        o = _find(obs, r)
                        if o is None:
                            missing.append(r["run_key"])
                        else:
                            pext[r["cand_id"]] = o["text"]
                            pkeys[r["cand_id"]] = r["run_key"]
                    kw = {}
                    kw["review_texts"] = {q.cand_id: ti_p.quote_text(q) for q in ti_p.quotes}
                    res = mp.run_m4(t, ti, ext, ctx.snapshot, None, ctx.freight_floor, reg, pext,
                                    ctx.value_sets, config_id=c, calls=ekeys, review_config_id=partner, **kw)
                    used = [pkeys[cid] for cid in res["diagnostics"]["review"]["reviewed"] if cid in pkeys]
                    review = {"cost": collect_cost(obs, used), "calls": used}
                finish(res, m, c, t, None, direct, review, extra)
                n_out += 1
    missing = sorted(set(missing))
    spend = {}
    for o in obs.values():
        spend = _add(spend, _obs_cost(o))
    summary = {"dir": str(out / "outputs"), "n_outputs": n_out, "missing_observations": missing,
               "actual_spend": _cost_dict(spend), "n_observations": len(obs)}
    atomic_json(out / "outputs" / "assemble_manifest.json", summary)
    return summary


def collect_cost(obs: dict, keys) -> dict:
    cost = {}
    for k in keys:
        cost = _add(cost, _obs_cost(obs[k]))
    return cost


# ---------------------------------------------------------------- scoring
def _csv(path: Path, rows: list) -> None:
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                        for k, v in r.items()})


def score_all(outputs_dir, gold_dir, tasks_dir, out=None, *, endpoints: bool = False, B: int = 2000,
              sims: int = 99999, endpoint_B: int = 9999, seed: int = 0) -> dict:
    """Score every output file; writes rows/aggregate CSV and JSON to `out` (default <outputs_dir>/../scores).

    Returns {"rows", "aggregate", "aggregate_overall", "endpoints"}.
    """
    outputs_dir, gold_dir = Path(outputs_dir), Path(gold_dir)
    out = Path(out) if out else outputs_dir.parent / "scores"
    rows = []
    for f in sorted(outputs_dir.glob("*/*/*.json")):
        o = json.loads(f.read_text(encoding="utf-8"))
        g = json.loads((gold_dir / f"{o['task_id']}.json").read_text(encoding="utf-8"))
        row = metrics.score_task(o, g)
        row.update(cost_usd=o["cost"].get("USD"), cost_cny=o["cost"].get("CNY"), n_calls=len(o.get("calls", [])))
        rows.append(row)
    rows = metrics.attach_layers(rows, tasks_dir)
    agg = metrics.aggregate(rows, B=B, seed=seed)
    overall = metrics.aggregate(rows, by=("method", "config_id"), B=B, seed=seed)
    ep = None
    if endpoints:
        try:
            ep = metrics.primary_endpoints(rows, sims=sims, B=endpoint_B, seed=seed)
        except ValueError as e:
            ep = {"error": str(e)}
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / "rows.json", _jsonable(rows))
    atomic_json(out / "aggregate.json", _jsonable(agg))
    atomic_json(out / "aggregate_overall.json", _jsonable(overall))
    _csv(out / "rows.csv", rows)
    _csv(out / "aggregate.csv", agg)
    _csv(out / "aggregate_overall.csv", overall)
    if ep is not None:
        atomic_json(out / "endpoints.json", _jsonable(ep))
    return {"rows": rows, "aggregate": agg, "aggregate_overall": overall, "endpoints": ep}


# ---------------------------------------------------------------- CLI
def _split(s): return [x for x in s.split(",") if x]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quoteguard.orchestrate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
        p.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
        p.add_argument("--models", default=None)
        p.add_argument("--seed", type=int, default=DEFAULT_SEED)

    p = sub.add_parser("plan")
    p.add_argument("--tasks", required=True)
    p.add_argument("--configs", required=True)
    p.add_argument("--methods", default=",".join(METHODS))
    p.add_argument("--experiment", required=True)
    p.add_argument("--kind", required=True, choices=["dev", "pilot", "formal"])
    p.add_argument("--budget", required=True, help="USD=x,CNY=y")
    p.add_argument("--rates", required=True, help="JSON file {config_id: {currency, input, output}}")
    p.add_argument("--out", required=True)
    p.add_argument("--transcriptions", default=None, help="comma-separated stage A batch directories")
    p.add_argument("--protocol-sha256", default=None)
    common(p)
    r = sub.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--mode", required=True, choices=["mock", "live"])
    r.add_argument("--out", default="runs/")
    r.add_argument("--confirm-budget", default=None)
    r.add_argument("--models", default=None)
    r.add_argument("--batch-id", default=None)
    r.add_argument("--min-interval", type=float, default=0.0)
    r.add_argument("--protocol-sha256", default=None)
    a_ = sub.add_parser("assemble")
    a_.add_argument("--tasks", required=True)
    a_.add_argument("--gold", required=True)
    a_.add_argument("--batches", required=True)
    a_.add_argument("--configs", required=True)
    a_.add_argument("--methods", default=",".join(METHODS))
    a_.add_argument("--experiment", required=True)
    a_.add_argument("--out", required=True)
    common(a_)
    s = sub.add_parser("score")
    s.add_argument("--outputs", required=True)
    s.add_argument("--gold", required=True)
    s.add_argument("--tasks", required=True)
    s.add_argument("--out", default=None)
    s.add_argument("--endpoints", action="store_true")
    s.add_argument("--B", type=int, default=2000)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            rates = json.loads(Path(a.rates).read_text(encoding="utf-8"))
            paths = build_stage_plans(
                a.tasks, _split(a.configs), _split(a.methods), a.experiment, a.kind, ex.parse_budget(a.budget),
                rates, a.out, transcription_batches=_split(a.transcriptions) if a.transcriptions else None,
                models=load_models(a.models), data_dir=a.data_dir, snapshot_path=a.snapshot, seed=a.seed,
                protocol_sha256=a.protocol_sha256)
            print(json.dumps({k: str(v) if v else None for k, v in paths.items()}))
        elif a.cmd == "run":
            d = run_stage(a.plan, a.mode, a.out, confirm_budget=a.confirm_budget, models_path=a.models,
                          batch_id=a.batch_id, min_interval_s=a.min_interval, protocol_sha256=a.protocol_sha256)
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            print(json.dumps({"batch_dir": str(d), "status": m["status"], "completed": m.get("completed"),
                              "total": m.get("total"), "spent": m.get("spent")}, ensure_ascii=False))
            return 0 if m["status"] == "completed" else 3
        elif a.cmd == "assemble":
            res = assemble(a.tasks, a.gold, _split(a.batches), _split(a.configs), a.out,
                           methods=_split(a.methods), experiment=a.experiment, models=load_models(a.models),
                           data_dir=a.data_dir, snapshot_path=a.snapshot, seed=a.seed)
            print(json.dumps({"n_outputs": res["n_outputs"], "missing": len(res["missing_observations"])}))
        else:
            res = score_all(a.outputs, a.gold, a.tasks, a.out, endpoints=a.endpoints, B=a.B)
            print(json.dumps({"rows": len(res["rows"])}))
    except (OrchestrationError, ex.PlanError, FileExistsError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
