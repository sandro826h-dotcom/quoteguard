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
