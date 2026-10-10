import json
from pathlib import Path

import pytest

from quoteguard import executor as ex
from quoteguard import orchestrate as orch
from quoteguard import taskgen as tg
from quoteguard.llm import load_models
from quoteguard.params import load_snapshot
from quoteguard.schema import Task

RATES = {c: {"currency": "USD" if c.startswith("ds") else "CNY", "input": 1.0, "output": 2.0}
         for c in ("ds_pro", "ds_flash", "qwen_plus", "qwen_max", "qwen_vl")}
BUDGET = {"USD": 5, "CNY": 50}


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    """Two dev tasks, one of them with an image quote, written like a generated split."""
    inp = tg.load_inputs()
    snap = load_snapshot(tg.DEFAULT_SNAPSHOT_PATH)
    img = other = None
    for seed in range(1, 8):
        tasks, golds = tg.generate(1, "dev", seed, snap, inp["structures"], inp["value_sets"], inp["registry"],
                                   tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])
        for t, g in zip(tasks, golds):
            has_img = any(c.presentation["modality"] == "image" for c in t.candidates)
            if has_img and img is None:
                img = (t, g)
            elif not has_img and other is None:
                other = (t, g)
        if img and other:
            break
    assert img and other
    root = tmp_path_factory.mktemp("split")
    chosen = [img, other]
    tg.write_split([t for t, _ in chosen], [g for _, g in chosen], root / "dev",
                   {"split": "dev"}, gold_dir=root / "gold")
    return {"tasks": root / "dev" / "tasks", "gold": root / "gold", "image_task": img[0].task_id,
            "ids": sorted(t.task_id for t, _ in chosen)}


def plans(split, tmp_path, configs, methods, tb=None):
    return orch.build_stage_plans(split["tasks"], configs, methods, "t1", "dev", BUDGET, RATES, tmp_path,
                                  transcription_batches=tb)


def run(plan, tmp_path, name):
    return orch.run_stage(plan, "mock", tmp_path / "runs", batch_id=name)


def plan_requests(p):
    return json.loads(Path(p).read_text())["requests"]


def test_stage_a_plan_dedupes_shared_vision_and_freezes_inputs(split, tmp_path):
    p = plans(split, tmp_path, ["ds_pro", "ds_flash"], orch.METHODS[:4])
    reqs = plan_requests(p["A"])
    keys = [r["run_key"] for r in reqs]
    assert p["B"] is None and len(keys) == len(set(keys)) and len(keys) >= 1
    assert {r["config_id"] for r in reqs} == {"ds_flash"}      # ds_pro and ds_flash share one vision config
    assert all(r["stage"] == "transcribe" and r["task_id"] == split["image_task"] for r in reqs)
    assert all(r["images"] and Path(r["images"][0]["path"]).is_absolute() for r in reqs)
    for tid in split["ids"]:
        assert (tmp_path / "inputs" / "tasks" / f"{tid}.json").exists()
    assert (tmp_path / "inputs" / "registry_text.txt").exists()
    # the same call again reproduces the same frozen plan; a different one is refused
    assert plans(split, tmp_path, ["ds_pro", "ds_flash"], orch.METHODS[:4])["A"] == p["A"]
    with pytest.raises(FileExistsError):
        orch.build_stage_plans(split["tasks"], ["qwen_plus"], ["M0"], "t1", "dev", BUDGET, RATES, tmp_path)


def test_stage_b_plan_contents(split, tmp_path):
    a = plans(split, tmp_path, ["ds_flash", "qwen_plus"], orch.METHODS)["A"]
    batch = run(a, tmp_path, "a")
    b = plans(split, tmp_path, ["ds_flash", "qwen_plus"], orch.METHODS, [batch])["B"]
    reqs = plan_requests(b)
    keys = [r["run_key"] for r in reqs]
    assert len(keys) == len(set(keys))
    n_cand = {t: len(json.loads((split["tasks"] / f"{t}.json").read_text())["candidates"]) for t in split["ids"]}
    by = lambda m, c: [r for r in reqs if r["method"] == m and r["config_id"] == c]
    for c in ("ds_flash", "qwen_plus"):
        assert len(by("M0", c)) == 2 and len(by("M1", c)) == 2
        assert len(by("extract", c)) == sum(n_cand.values())   # shared by M2 to M4, requested once
    assert ex.load_plan(b, load_models())["kind"] == "dev"


def test_m4_needs_review_partner(split, tmp_path):
    with pytest.raises(orch.OrchestrationError, match="partner"):
        plans(split, tmp_path, ["ds_flash"], ["M4"])
    with pytest.raises(orch.OrchestrationError, match="unknown method"):
        plans(split, tmp_path, ["ds_flash"], ["M9"])


def full_flow(split, tmp_path, configs, methods):
    a = plans(split, tmp_path, configs, methods)["A"]
    ba = run(a, tmp_path, "a")
    b = plans(split, tmp_path, configs, methods, [ba])["B"]
    bb = run(b, tmp_path, "b")
    res = orch.assemble(split["tasks"], split["gold"], [ba, bb], configs, tmp_path, methods=methods,
                        experiment="t1")
    return ba, bb, res


def obs_of(*batches):
    return orch.load_observations(batches)


def total(keys, obs):
    t = {}
    for k in keys:
        for cur, v in orch._obs_cost(obs[k]).items():
            t[cur] = t.get(cur, 0.0) + v
    return t


def test_end_to_end_outputs_costs_and_scores(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    ba, bb, res = full_flow(split, tmp_path, configs, orch.METHODS)
    assert json.loads((ba / "manifest.json").read_text())["status"] == "completed"
    assert json.loads((bb / "manifest.json").read_text())["status"] == "completed"
    assert res["n_outputs"] == 5 * 2 * 2 and res["missing_observations"] == []
    obs = obs_of(ba, bb)
    for m in orch.METHODS:
        for c in configs:
            for tid in split["ids"]:
                f = tmp_path / "outputs" / m / c / f"{tid}.json"
                assert f.exists(), f
                o = json.loads(f.read_text())
                assert o["method"] == m and o["config_id"] == c and o["task_id"] == tid
                cb = o["cost_breakdown"]
                assert set(o["calls"]) == set(cb["transcription_calls"] + cb["direct_calls"] + cb["review_calls"])
                assert all(k in obs for k in o["calls"])
                assert cb["transcription_booking"] == "full"
                exp = total(cb["transcription_calls"] + cb["direct_calls"] + cb["review_calls"], obs)
                for cur in ("USD", "CNY"):
                    assert o["cost"][cur] == pytest.approx(exp.get(cur, 0.0))
                if tid != split["image_task"]:
                    assert cb["transcription_calls"] == []
    # M2 to M4 of one configuration book the same extraction
    c = "ds_flash"
    tid = split["image_task"]
    d = lambda m: json.loads((tmp_path / "outputs" / m / c / f"{tid}.json").read_text())
    assert d("M2")["cost_breakdown"]["direct_calls"] == d("M3")["cost_breakdown"]["direct_calls"] \
        == d("M4")["cost_breakdown"]["direct_calls"]
    assert d("M2")["cost"] == d("M3")["cost"]
    # review extraction of M4 comes from the partner's M2 extraction, so its calls are the partner's
    for k in d("M4")["cost_breakdown"]["review_calls"]:
        assert obs[k]["config_id"] == "qwen_plus" and obs[k]["method"] == "extract"
    # M0 and M1: one call each
    assert len(d("M0")["cost_breakdown"]["direct_calls"]) == 1
    # scoring over the output files
    sc = orch.score_all(tmp_path / "outputs", split["gold"], split["tasks"], tmp_path / "scores", B=20)
    assert len(sc["rows"]) == 20 and all(r["layer"] for r in sc["rows"])
    assert {(a["method"], a["config_id"]) for a in sc["aggregate_overall"]} == {(m, c) for m in orch.METHODS for c in configs}
    for name in ("rows.csv", "rows.json", "aggregate.csv", "aggregate.json", "aggregate_overall.csv"):
        assert (tmp_path / "scores" / name).exists()
    assert all("cost_cny" in r and "n_calls" in r for r in sc["rows"])


def test_transcription_cost_booked_in_full_for_configs_with_same_vision(split, tmp_path):
    configs = ["ds_pro", "ds_flash"]            # both use ds_flash as the vision config
    methods = ["M0", "M2", "M3"]
    ba, bb, res = full_flow(split, tmp_path, configs, methods)
    obs = obs_of(ba, bb)
    tid = split["image_task"]
    tkeys = [k for k, o in obs.items() if o["stage"] == "transcribe" and o["task_id"] == tid]
    assert tkeys
    tt = total(tkeys, obs)
    for c in configs:
        for m in methods:
            o = json.loads((tmp_path / "outputs" / m / c / f"{tid}.json").read_text())
            cb = o["cost_breakdown"]
            assert cb["transcription_shared_by"] == 2 and set(cb["transcription_calls"]) == set(tkeys)
            for cur, v in tt.items():
                assert cb["transcription_share"][cur] == pytest.approx(v)   # full cost, not split
            own = total(cb["direct_calls"], obs)
            for cur in ("USD", "CNY"):
                assert o["cost"][cur] == pytest.approx(own.get(cur, 0.0) + tt.get(cur, 0.0))
    # actual spend counts every observation once, so the shared transcription is not doubled
    every = total(list(obs), obs)
    for cur in ("USD", "CNY"):
        assert res["actual_spend"][cur] == pytest.approx(every.get(cur, 0.0))
    assert res["n_observations"] == len(obs)
    booked = sum(json.loads((tmp_path / "outputs" / "M0" / c / f"{tid}.json").read_text())
                 ["cost_breakdown"]["transcription_share"]["USD"] for c in configs)
    assert booked == pytest.approx(2 * tt.get("USD", 0.0))   # booked per configuration, paid once

def test_missing_and_changed_observations(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    a = plans(split, tmp_path, configs, ["M0"])["A"]
    ba = run(a, tmp_path, "a")
    b = plans(split, tmp_path, configs, ["M0"], [ba])["B"]
    bb = run(b, tmp_path, "b")
    victim = sorted((bb / "observations").glob("*.json"))[0]
    key = json.loads(victim.read_text())["run_key"]
    victim.unlink()
    res = orch.assemble(split["tasks"], split["gold"], [ba, bb], configs, tmp_path, methods=["M0"], experiment="t1")
    assert res["missing_observations"] == [key]
    out = json.loads(next((tmp_path / "outputs" / "M0").rglob("*.json")).read_text())
    assert "parse_ok" in out
    with pytest.raises(orch.OrchestrationError, match="more than one"):
        orch.load_observations([ba, ba])


def test_run_stage_live_requires_matching_confirm(split, tmp_path):
    a = plans(split, tmp_path, ["ds_flash"], ["M0"])["A"]
    with pytest.raises(orch.OrchestrationError, match="confirm"):
        orch.run_stage(a, "live", tmp_path / "runs")
    with pytest.raises(orch.OrchestrationError, match="match"):
        orch.run_stage(a, "live", tmp_path / "runs", confirm_budget="USD=1,CNY=1")


def test_cli_plan_run_assemble_score(split, tmp_path, capsys):
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(RATES))
    out = tmp_path / "work"
    base = ["--tasks", str(split["tasks"]), "--configs", "ds_flash,qwen_plus", "--methods", "M0,M2",
            "--experiment", "c1"]
    assert orch.main(["plan", *base, "--kind", "dev", "--budget", "USD=5,CNY=50", "--rates", str(rates),
                      "--out", str(out)]) == 0
    pa = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["A"]
    assert orch.main(["run", "--plan", pa, "--mode", "mock", "--out", str(tmp_path / "runs")]) == 0
    ba = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["batch_dir"]
    assert orch.main(["plan", *base, "--kind", "dev", "--budget", "USD=5,CNY=50", "--rates", str(rates),
                      "--out", str(out), "--transcriptions", ba]) == 0
    pb = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["B"]
    assert orch.main(["run", "--plan", pb, "--mode", "mock", "--out", str(tmp_path / "runs")]) == 0
    bb = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["batch_dir"]
    assert orch.main(["assemble", "--tasks", str(split["tasks"]), "--gold", str(split["gold"]), "--batches",
                      f"{ba},{bb}", "--configs", "ds_flash,qwen_plus", "--methods", "M0,M2",
                      "--experiment", "c1", "--out", str(out)]) == 0
    assert orch.main(["score", "--outputs", str(out / "outputs"), "--gold", str(split["gold"]),
                      "--tasks", str(split["tasks"]), "--B", "10"]) == 0
    assert (out / "scores" / "rows.csv").exists()
    assert orch.main(["run", "--plan", pa, "--mode", "live", "--out", str(tmp_path / "runs")]) == 2


class ExtractMock(ex.MockClient):
    """Mock whose extraction answers parse (one stated clause), so that M4 can merge the partner's extraction."""

    def complete(self, config, messages, seed=None):
        r = super().complete(config, messages, seed)
        if "请从下面这份货代报价中抽取条款" in json.dumps(messages, ensure_ascii=False):
            r.text = json.dumps({"mode": {"status": "stated", "value": "express", "quotes": []}})
        return r


def full_flow_parsing(split, tmp_path, configs, methods):
    a = plans(split, tmp_path, configs, methods)["A"]
    ba = run(a, tmp_path, "a")
    b = plans(split, tmp_path, configs, methods, [ba])["B"]
    bb = orch.run_stage(b, "mock", tmp_path / "runs", batch_id="b", client_factory=lambda p: ExtractMock())
    res = orch.assemble(split["tasks"], split["gold"], [ba, bb], configs, tmp_path, methods=methods,
                        experiment="t1")
    return ba, bb, res


def test_m4_reviews_all_candidates_and_m4_trig_only_triggered(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    ba, bb, res = full_flow_parsing(split, tmp_path, configs, orch.ASSEMBLE_METHODS)
    assert res["n_outputs"] == 6 * 2 * 2 and res["missing_observations"] == []
    obs = obs_of(ba, bb)
    tot = 0
    for c, partner in (("ds_flash", "qwen_plus"), ("qwen_plus", "ds_flash")):
        for tid in split["ids"]:
            rd = lambda m: json.loads((tmp_path / "outputs" / m / c / f"{tid}.json").read_text())
            m4, tr = rd("M4"), rd("M4_trig")
            assert tr["method"] == "M4_trig" and m4["method"] == "M4"
            cands = list(m4["per_cand"])
            # M4 books the partner extraction of every candidate, M4_trig only of the triggered ones
            assert len(m4["cost_breakdown"]["review_calls"]) == len(cands)
            assert m4["diagnostics"]["review"]["reviewed"] == cands
            trig = {k: v for k, v in m4["review_triggers"].items() if v}
            assert tr["review_triggers"] == m4["review_triggers"]
            assert tr["diagnostics"]["review"]["reviewed"] == [k for k in cands if k in trig]
            assert len(tr["cost_breakdown"]["review_calls"]) == len(trig)
            assert set(tr["cost_breakdown"]["review_calls"]) <= set(m4["cost_breakdown"]["review_calls"])
            assert tr["cost_breakdown"]["direct_calls"] == m4["cost_breakdown"]["direct_calls"]
            assert tr["cost"]["USD"] <= m4["cost"]["USD"] + 1e-12 and tr["cost"]["CNY"] <= m4["cost"]["CNY"] + 1e-12
            for k in m4["cost_breakdown"]["review_calls"]:
                assert obs[k]["config_id"] == partner
            tot += len(cands)
    assert tot > 0


def test_full_review_adds_no_requests_to_the_plan(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    a = plans(split, tmp_path, configs, orch.METHODS)["A"]
    ba = run(a, tmp_path, "a")
    reqs = {}
    for name, methods in (("full", orch.ASSEMBLE_METHODS), ("pipeline_no_m4", ("M0", "M1", "M2", "M3"))):
        sub = tmp_path / name
        b = orch.build_stage_plans(split["tasks"], configs, methods, "t1", "dev", BUDGET, RATES, sub,
                                   transcription_batches=[ba])["B"]
        reqs[name] = sorted(r["run_key"] for r in plan_requests(b))
    # M4 (with or without the variant) needs exactly the requests M2 and M3 already need
    assert reqs["full"] == reqs["pipeline_no_m4"]


def test_primary_endpoints_ignore_m4_trig_outputs(split, tmp_path):
    from quoteguard import metrics
    configs = ["ds_flash", "qwen_plus"]
    full_flow_parsing(split, tmp_path, configs, orch.ASSEMBLE_METHODS)
    sc = orch.score_all(tmp_path / "outputs", split["gold"], split["tasks"], tmp_path / "scores", B=20)
    rows = sc["rows"]
    trig = [r for r in rows if r["method"] == "M4_TRIG"]
    assert len(trig) == 4 and all(r["decision_correct"] is not None for r in trig)
    base = [r for r in rows if r["method"] != "M4_TRIG"]
    for fn in (metrics.endpoint1, metrics.endpoint2):
        a = fn(rows, configs=tuple(configs), sims=200)
        b = fn(base, configs=tuple(configs), sims=200)
        assert a == b
    pairs = (("ds_flash", "ds_flash"), ("qwen_plus", "qwen_plus"))
    assert metrics.endpoint3(rows, pairs=pairs, B=50) == metrics.endpoint3(base, pairs=pairs, B=50)
    agg = {(a["method"], a["config_id"]) for a in sc["aggregate_overall"]}
    assert ("M4_TRIG", "ds_flash") in agg


def test_reviewer_transcription_cost_is_booked_for_reviewed_image_quotes(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    ba, bb, res = full_flow_parsing(split, tmp_path, configs, orch.ASSEMBLE_METHODS)
    obs = obs_of(ba, bb)
    tid = split["image_task"]
    models = load_models()
    for c, partner in (("ds_flash", "qwen_plus"), ("qwen_plus", "ds_flash")):
        pvis = models[partner].vision_config_id
        assert pvis != models[c].vision_config_id
        rd = lambda m: json.loads((tmp_path / "outputs" / m / c / f"{tid}.json").read_text())
        m4, tr = rd("M4"), rd("M4_trig")
        cb = m4["cost_breakdown"]
        assert cb["review_transcription_calls"]
        assert all(obs[k]["config_id"] == pvis and obs[k]["stage"] == "transcribe" for k in cb["review_transcription_calls"])
        assert set(cb["review_transcription_calls"]).isdisjoint(cb["transcription_calls"])
        exp = total(cb["transcription_calls"] + cb["direct_calls"] + cb["review_calls"]
                    + cb["review_transcription_calls"], obs)
        for cur in ("USD", "CNY"):
            assert m4["cost"][cur] == pytest.approx(exp.get(cur, 0.0))
        assert set(tr["cost_breakdown"]["review_transcription_calls"]) <= set(cb["review_transcription_calls"])
        assert set(m4["calls"]) >= set(cb["review_transcription_calls"])
        # tasks without image quotes have no reviewer transcription
        other = [t for t in split["ids"] if t != tid][0]
        o = json.loads((tmp_path / "outputs" / "M4" / c / f"{other}.json").read_text())
        assert o["cost_breakdown"]["review_transcription_calls"] == []


def test_variant_names_are_recognised_by_metrics_and_stay_out_of_primary_endpoints(split, tmp_path):
    from quoteguard import metrics
    assert orch.VARIANTS == metrics.VARIANT_METHODS
    configs = ["ds_flash", "qwen_plus"]
    full_flow_parsing(split, tmp_path, configs, orch.ASSEMBLE_METHODS)
    for v in metrics.VARIANT_METHODS:
        assert v.upper() in metrics.PIPELINE_METHODS
        assert (tmp_path / "outputs" / v).is_dir()
    sc = orch.score_all(tmp_path / "outputs", split["gold"], split["tasks"], tmp_path / "scores", B=20)
    names = {r["method"] for r in sc["rows"]}
    assert {v.upper() for v in metrics.VARIANT_METHODS} <= names
    assert all(metrics._index(sc["rows"], v.upper()) for v in metrics.VARIANT_METHODS)
    assert set(metrics._index(sc["rows"], "M4")) .isdisjoint({}) and all(
        r["method"] == "M4" for r in metrics._index(sc["rows"], "M4").values())


# ---------------------------------------------------------------- final call failures
def _tamper(batch, pred, how):
    """Break the observations of `batch` matching pred: missing (file removed), invalid, or empty text."""
    hit = []
    for f in sorted((Path(batch) / "observations").glob("*.json")):
        o = json.loads(f.read_text())
        if not pred(o):
            continue
        if how == "missing":
            f.unlink()
        else:
            if how == "invalid":
                o["valid"] = False
            else:
                o["text"] = {"empty": "  ", "prose": "无法抽取条款。", "fields": '{"surcharges": 5, "ddp": "?"}'}[how]
            f.write_text(json.dumps(o))
        hit.append(o["run_key"])
    assert hit
    return hit


def fail_flow(split, tmp_path, break_a=None, break_b=None, configs=("ds_flash", "qwen_plus")):
    configs = list(configs)
    a = plans(split, tmp_path, configs, orch.ASSEMBLE_METHODS)["A"]
    ba = run(a, tmp_path, "a")
    hit_a = _tamper(ba, *break_a) if break_a else []
    b = plans(split, tmp_path, configs, orch.ASSEMBLE_METHODS, [ba])["B"]
    bb = orch.run_stage(b, "mock", tmp_path / "runs", batch_id="b", client_factory=lambda p: ExtractMock())
    hit_b = _tamper(bb, *break_b) if break_b else []
    orch.assemble(split["tasks"], split["gold"], [ba, bb], configs, tmp_path, methods=orch.ASSEMBLE_METHODS,
                  experiment="t1")
    return hit_a, hit_b


def out_of(tmp_path, m, c, tid):
    return json.loads((tmp_path / "outputs" / m / c / f"{tid}.json").read_text())


def failed_set(tmp_path, tid, c, methods=orch.ASSEMBLE_METHODS):
    return {m: out_of(tmp_path, m, c, tid)["failed_calls"] for m in methods}


@pytest.mark.parametrize("how", ["missing", "invalid", "empty"])
def test_m0_m1_own_call_failure(split, tmp_path, how):
    tid = split["ids"][0]
    hit, = fail_flow(split, tmp_path, break_b=(lambda o: o["method"] == "M1" and o["config_id"] == "ds_flash"
                                               and o["task_id"] == tid, how))[1:]
    f = failed_set(tmp_path, tid, "ds_flash")
    assert f["M1"] == hit
    assert all(f[m] == [] for m in f if m != "M1")
    assert failed_set(tmp_path, tid, "qwen_plus")["M1"] == []
    other = [t for t in split["ids"] if t != tid][0]
    assert failed_set(tmp_path, other, "ds_flash")["M1"] == []


@pytest.mark.parametrize("how", ["missing", "invalid", "empty"])
def test_own_extraction_failure_hits_pipeline_and_the_partner_m4(split, tmp_path, how):
    tid = split["ids"][1]
    hit = fail_flow(split, tmp_path, break_b=(lambda o: o["stage"] == "extract" and o["config_id"] == "ds_flash"
                                              and o["task_id"] == tid and o["cand_id"] == "C1", how))[1]
    key, = hit
    f = failed_set(tmp_path, tid, "ds_flash")
    assert f["M0"] == [] and f["M1"] == []
    for m in ("M2", "M3", "M4"):
        assert f[m] == [key]
    # qwen_plus reviews with ds_flash's extraction: M4 needs all candidates, M2 / M3 / M0 do not
    g = failed_set(tmp_path, tid, "qwen_plus")
    assert g["M4"] == [key] and g["M2"] == [] and g["M3"] == [] and g["M0"] == []
    # the triggered variant needs the partner extraction only for triggered candidates
    trig = out_of(tmp_path, "M4_trig", "qwen_plus", tid)["diagnostics"]["triggers"]
    assert g["M4_trig"] == ([key] if "C1" in trig else [])


def test_partner_extraction_failure_only_affects_m4_of_the_reviewed_config(split, tmp_path):
    tid = split["ids"][1]
    key, = fail_flow(split, tmp_path, break_b=(lambda o: o["stage"] == "extract" and o["config_id"] == "qwen_plus"
                                               and o["task_id"] == tid and o["cand_id"] == "C2", "missing"))[1]
    # ds_flash M2-M4 do not use qwen_plus's own M2 extraction except M4 / M4_trig through review
    f = failed_set(tmp_path, tid, "ds_flash")
    assert f["M2"] == [] and f["M3"] == [] and f["M4"] == [key]
    trig = out_of(tmp_path, "M4_trig", "ds_flash", tid)["diagnostics"]["triggers"]
    assert f["M4_trig"] == ([key] if "C2" in trig else [])
    assert failed_set(tmp_path, tid, "qwen_plus")["M2"] == [key]


def image_cands(split, tid):
    t = Task.from_dict(json.loads((split["tasks"] / f"{tid}.json").read_text()))
    return [c.cand_id for c in t.candidates if c.presentation["modality"] == "image"]


@pytest.mark.parametrize("how", ["missing", "invalid", "empty"])
def test_own_transcription_failure_hits_every_method(split, tmp_path, how):
    tid = split["image_task"]
    vis = load_models()["ds_flash"].vision_config_id
    hit = fail_flow(split, tmp_path, break_a=(lambda o: o["stage"] == "transcribe" and o["config_id"] == vis
                                              and o["task_id"] == tid, how))[0]
    f = failed_set(tmp_path, tid, "ds_flash")
    for m, calls in f.items():
        assert set(hit) <= set(calls), m
    other = [t for t in split["ids"] if t != tid][0]
    assert all(v == [] for v in failed_set(tmp_path, other, "ds_flash").values())
    # qwen_plus transcribes with its own vision model; only its M4 / M4_trig lean on the partner's transcript
    g = failed_set(tmp_path, tid, "qwen_plus")
    assert g["M0"] == [] and g["M2"] == [] and set(hit) <= set(g["M4"])


def test_partner_transcription_failure_hits_m4_only(split, tmp_path):
    tid = split["image_task"]
    vis = load_models()["qwen_plus"].vision_config_id
    assert vis != load_models()["ds_flash"].vision_config_id
    hit = fail_flow(split, tmp_path, break_a=(lambda o: o["stage"] == "transcribe" and o["config_id"] == vis
                                              and o["task_id"] == tid, "missing"))[0]
    f = failed_set(tmp_path, tid, "ds_flash")
    assert f["M0"] == [] and f["M1"] == [] and f["M2"] == [] and f["M3"] == []
    assert set(hit) <= set(f["M4"])
    trig = out_of(tmp_path, "M4_trig", "ds_flash", tid)["diagnostics"]["triggers"]
    needed = set(image_cands(split, tid)) & set(trig)
    assert bool(set(hit) & set(f["M4_trig"])) == bool(needed)
    assert all(v == [] for v in failed_set(tmp_path, tid, "ds_flash", ("M0", "M2")).values())
    assert failed_set(tmp_path, tid, "qwen_plus")["M0"] != []   # its own transcript


def test_failed_outputs_are_scored_alike_for_m0_and_m4(split, tmp_path):
    tid = split["image_task"]
    vis = load_models()["ds_flash"].vision_config_id
    fail_flow(split, tmp_path, break_a=(lambda o: o["stage"] == "transcribe" and o["config_id"] == vis
                                        and o["task_id"] == tid, "missing"))
    res = orch.score_all(tmp_path / "outputs", split["gold"], split["tasks"], tmp_path / "scores", B=10)
    rows = {(r["method"], r["config_id"], r["task_id"]): r for r in res["rows"]}
    skip = {"method", "cost_usd", "cost_cny", "n_calls"}
    ref = {k: v for k, v in rows[("M0", "ds_flash", tid)].items() if k not in skip}
    assert ref["call_failed"] == 1 and ref["decision"] is None and ref["decision_correct"] == 0 and ref["tp"] == 0
    for m in ("M1", "M2", "M3", "M4", "M4_TRIG"):
        assert {k: v for k, v in rows[(m, "ds_flash", tid)].items() if k not in skip} == ref
    assert rows[("M0", "qwen_plus", tid)]["call_failed"] == 0
    agg = {(a["method"], a["config_id"]): a for a in res["aggregate_overall"]}
    assert agg[("M0", "ds_flash")]["call_failed_n"] == 1 and agg[("M0", "ds_flash")]["call_failed_rate"] == 0.5


def test_score_cli_passes_seed_sims_and_b(split, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(orch, "score_all", lambda *a, **k: seen.update(k) or {"rows": []})
    base = ["score", "--outputs", "o", "--gold", "g", "--tasks", "t"]
    assert orch.main(base) == 0
    assert seen["seed"] == 0 and seen["sims"] == 99999 and "B" not in seen and "endpoint_B" not in seen
    assert orch.main(base + ["--seed", "7", "--sims", "123", "--B", "55"]) == 0
    assert (seen["seed"], seen["sims"], seen["B"], seen["endpoint_B"]) == (7, 123, 55, 55)


@pytest.mark.parametrize("how,failed", [("prose", True), ("fields", False)])
def test_unparseable_extraction_response_is_a_call_failure_but_bad_fields_are_not(split, tmp_path, how, failed):
    tid = split["ids"][1]
    key, = fail_flow(split, tmp_path, break_b=(lambda o: o["stage"] == "extract" and o["config_id"] == "ds_flash"
                                               and o["task_id"] == tid and o["cand_id"] == "C1", how))[1]
    f, g = failed_set(tmp_path, tid, "ds_flash"), failed_set(tmp_path, tid, "qwen_plus")
    assert f["M0"] == [] and f["M1"] == []
    for m in ("M2", "M3", "M4"):
        assert f[m] == ([key] if failed else [])
    assert g["M4"] == ([key] if failed else []) and g["M2"] == []   # review partner's extraction too


# ---------------------------------------------------------------- one plan per provider
PROVIDER_RATES = dict(RATES, ds_flash_think={"currency": "USD", "input": 1.0, "output": 2.0})


def provider_plan(split, work, configs, methods, tag, tb=None):
    return orch.build_stage_plans(split["tasks"], configs, methods, "t1", "dev", {"USD": 5} if configs[0].startswith("ds")
                                  else {"CNY": 50}, PROVIDER_RATES, work, transcription_batches=tb, tag=tag)


def test_per_provider_plans_equal_single_plan_and_assemble_across_batches(split, tmp_path):
    methods4 = ["M0", "M1", "M2", "M3"]
    single = tmp_path / "single"
    sa = orch.build_stage_plans(split["tasks"], ["ds_flash", "qwen_plus"], list(orch.METHODS), "t1", "dev", BUDGET,
                                RATES, single)
    sba = orch.run_stage(sa["A"], "mock", single / "runs", batch_id="a")
    sb = orch.build_stage_plans(split["tasks"], ["ds_flash", "qwen_plus"], list(orch.METHODS), "t1", "dev", BUDGET,
                                RATES, single, transcription_batches=[sba])["B"]
    sbb = orch.run_stage(sb, "mock", single / "runs", batch_id="b", client_factory=lambda p: ExtractMock())
    keys_single = set(obs_of(sba, sbb))

    work = tmp_path / "work"
    batches = []
    for tag, cfg in (("deepseek", "ds_flash"), ("dashscope", "qwen_plus")):
        pa = provider_plan(split, work, [cfg], methods4, tag)["A"]
        ba = orch.run_stage(pa, "mock", work / "runs", batch_id=f"{tag}-A")
        pb = provider_plan(split, work, [cfg], methods4, tag, [ba])["B"]
        bb = orch.run_stage(pb, "mock", work / "runs", batch_id=f"{tag}-B", client_factory=lambda p: ExtractMock())
        batches += [ba, bb]
        assert json.loads(Path(pa).read_text())["plan_id"] == f"t1-{tag}-A"
    keys_split = set(obs_of(*batches))
    assert keys_split == keys_single                       # same run_keys, no M4 requests of their own
    res = orch.assemble(split["tasks"], split["gold"], batches, ["ds_flash", "qwen_plus"], work,
                        methods=orch.ASSEMBLE_METHODS, experiment="t1")
    assert res["missing_observations"] == [] and res["n_outputs"] == 6 * 2 * 2
    ref = orch.assemble(split["tasks"], split["gold"], [sba, sbb], ["ds_flash", "qwen_plus"], single,
                        methods=orch.ASSEMBLE_METHODS, experiment="t1")
    for m in orch.ASSEMBLE_METHODS:
        for c in ("ds_flash", "qwen_plus"):
            for tid in split["ids"]:
                a, b = (json.loads((r / "outputs" / m / c / f"{tid}.json").read_text()) for r in (work, single))
                assert a["failed_calls"] == [] and a["decision"] == b["decision"] and a["choice"] == b["choice"]
                assert a["calls"] == b["calls"]

    # ds_flash_think runs M1 only: its own plan, transcripts taken from the deepseek stage A batch
    th = provider_plan(split, work, ["ds_flash_think"], ["M1"], "think", [batches[0]])["B"]
    reqs = plan_requests(th)
    assert {(r["method"], r["config_id"]) for r in reqs} == {("M1", "ds_flash_think")}
    bt = orch.run_stage(th, "mock", work / "runs", batch_id="think-B")
    allb = batches + [bt]
    res = orch.assemble(split["tasks"], split["gold"], allb, ["ds_flash_think"], work, methods=["M1"],
                        experiment="t1")
    assert res["missing_observations"] == []
    for tid in split["ids"]:
        assert out_of(work, "M1", "ds_flash_think", tid)["failed_calls"] == []
    assert len(obs_of(*allb)) == len(keys_single) + len(split["ids"])


# ---------------------------------------------------------------- retries and observation selection
def _copy_obs(src_batch, dst_batch, key, **changes):
    f = next(f for f in (Path(src_batch) / "observations").glob("*.json") if json.loads(f.read_text())["run_key"] == key)
    o = json.loads(f.read_text())
    o.update(changes)
    d = Path(dst_batch) / "observations"
    d.mkdir(parents=True, exist_ok=True)
    (d / f.name).write_text(json.dumps(o))


def test_load_observations_takes_earliest_usable_and_rejects_a_batch_listed_twice(split, tmp_path):
    a = plans(split, tmp_path, ["ds_flash"], ["M0"])["A"]
    ba = run(a, tmp_path, "a")
    key = next(iter(orch.load_observations([ba])))
    r1, r2, r3 = tmp_path / "r1", tmp_path / "r2", tmp_path / "r3"
    base = json.loads(next((ba / "observations").glob("*.json")).read_text())
    _copy_obs(ba, r1, key, finished_at="2026-01-02T00:00:00Z", text="late")
    _copy_obs(ba, r2, key, finished_at="2026-01-01T00:00:00Z", text="early")
    _copy_obs(ba, r3, key, finished_at="2025-01-01T00:00:00Z", valid=False)
    pick = lambda *b: orch.load_observations(b)[key]["text"]
    assert pick(r1, r2) == "early" and pick(r2, r1) == "early"
    assert pick(r3, r1) == "late"                      # an earlier invalid observation does not win
    assert pick(r3) is not None and orch.load_observations([r3])[key]["valid"] is False
    f = json.loads(next((r3 / "observations").glob("*.json")).read_text())
    _copy_obs(ba, tmp_path / "r4", key, finished_at="2025-01-01T00:00:00Z", valid=False, text="x")
    _copy_obs(ba, tmp_path / "r5", key, finished_at="2024-01-01T00:00:00Z", valid=False, text="y")
    assert orch.load_observations([tmp_path / "r4", tmp_path / "r5"])[key]["text"] == "y"   # none usable: earliest
    _copy_obs(ba, tmp_path / "r6", key, finished_at="2026-01-01T00:00:00Z", text="same")
    _copy_obs(ba, tmp_path / "r7", key, finished_at="2026-01-01T00:00:00Z", text="same2")
    assert pick(tmp_path / "r7", tmp_path / "r6") == "same2"                                  # tie: batch listed first
    with pytest.raises(orch.OrchestrationError, match="more than one"):
        orch.load_observations([ba, ba])
    assert base["run_key"] == key
