import importlib.util
import json
from pathlib import Path

from quoteguard.executor import Executor, load_plan
from quoteguard.llm import load_models

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_step0_plan_and_mock_summary(tmp_path):
    probe, summ = load_script("step0_probe"), load_script("step0_summary")
    assert probe.main(["--runs", str(tmp_path)]) == 0
    plan = load_plan(tmp_path / "step0_plan.json", load_models())
    reqs = plan["requests"]
    assert len(reqs) == 17 and plan["kind"] == "dev" and all(r["seed"] == 20261010 for r in reqs)
    assert sum(r["config_id"] == "kimi" and r["method"] == "think" for r in reqs) == 5
    assert {r["max_tokens"] for r in reqs if r["config_id"] == "ds_flash_think"} == {512}
    assert all(r["max_tokens"] == 600 for r in reqs if r["method"] == "vision")
    ex = Executor(tmp_path / "step0_plan.json", tmp_path / "out", "mock")
    assert ex.run()["status"] == "completed"
    s = summ.summarise(ex.dir)
    assert len(s["calls"]) == 17 and s["kimi_latency"]["n"] == 5
    assert s["seed_check"]["ds_flash"]["identical"] is True
    assert summ.main([str(ex.dir)]) == 0 and (ex.dir / "step0_summary.md").exists()
    assert json.loads((ex.dir / "step0_summary.json").read_text())["completed"] == 17
