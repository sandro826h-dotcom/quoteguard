import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_retry_plan as mrp  # noqa: E402

from quoteguard import executor as ex  # noqa: E402
from quoteguard import orchestrate as orch  # noqa: E402
from tests.test_orchestrate import BUDGET, RATES, plans, run, split  # noqa: E402,F401


def test_retry_plan_covers_failed_and_unsent_requests_and_assembles_with_the_original(split, tmp_path):
    configs = ["ds_flash", "qwen_plus"]
    ba = run(plans(split, tmp_path, configs, ["M0"])["A"], tmp_path, "a")
    pb = plans(split, tmp_path, configs, ["M0", "M2"], [ba])["B"]
    full = run(pb, tmp_path, "full")                       # a complete reference run
    bb = tmp_path / "runs" / "b"                           # the "real" batch: copy, then damage it
    shutil.copytree(full, bb)
    files = sorted((bb / "observations").glob("*.json"))
    keys = [json.loads(f.read_text())["run_key"] for f in files]
    gone, bad = files[0], files[1]
    gone.unlink()                                          # never sent or failed attempts only
    o = json.loads(bad.read_text())
    o["valid"] = False
    bad.write_text(json.dumps(o))                          # returned but unusable
    (bb / "errors").mkdir()
    (bb / "errors" / (gone.stem + ".001.json")).write_text(json.dumps({"run_key": keys[0], "error": "x"}))
    out = tmp_path / "retry_plan.json"
    assert mrp.main([str(bb), "--out", str(out), "--budget", "USD=1,CNY=2"]) == 0
    plan = json.loads(out.read_text())
    orig = {r["run_key"]: r for r in json.loads((bb / "plan.json").read_text())["requests"]}
    assert plan["plan_id"].endswith("-retry") and plan["budget"] == {"USD": 1.0, "CNY": 2.0}
    assert {r["run_key"] for r in plan["requests"]} == {keys[0], keys[1]}
    for r in plan["requests"]:
        assert r == orig[r["run_key"]]
    # the retry runs as its own batch; the original and retry batches are listed together
    rb = orch.run_stage(out, "mock", tmp_path / "runs", batch_id="b-retry")
    obs = orch.load_observations([bb, rb])
    assert set(obs) == set(keys) and all(orch.obs_usable(v) for v in obs.values())
    res = orch.assemble(split["tasks"], split["gold"], [ba, bb, rb], ["ds_flash", "qwen_plus"], tmp_path,
                        methods=["M0", "M2"], experiment="t1")
    assert res["missing_observations"] == []
    # a second pass with the retry batch counted finds nothing to do
    assert mrp.retry_plan(bb, also=[rb])["requests"] == []


def test_retry_plan_empty_when_complete_and_budget_defaults_to_original(split, tmp_path):
    ba = run(plans(split, tmp_path, ["ds_flash"], ["M0"])["A"], tmp_path, "a")
    doc = mrp.retry_plan(ba)
    assert doc["requests"] == [] and doc["budget"] == json.loads((ba / "plan.json").read_text())["budget"]
    assert mrp.main([str(ba), "--out", str(tmp_path / "none.json")]) == 0
    assert not (tmp_path / "none.json").exists()
    (next((ba / "observations").glob("*.json"))).unlink()
    assert len(mrp.retry_plan(ba, plan_id="x")["requests"]) == 1
