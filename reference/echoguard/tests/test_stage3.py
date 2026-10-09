"""Stage 3: frozen plan integrity, shelf B validity and executor plan loading (offline)."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "runs/stage3-plan-20261005"
sys.path.insert(0, str(ROOT / "src"))
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.validation import validate_products  # noqa: E402


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plan():
    manifest = json.loads((PLAN / "design_manifest.json").read_text(encoding="utf-8"))
    reqs = [json.loads(l) for l in (PLAN / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return manifest, reqs


def test_plan_hash_and_counts():
    manifest, reqs = _plan()
    assert manifest["schema_version"] == "stage3-plan-0.1"
    assert _digest(reqs) == manifest["request_plan_sha256"]
    assert manifest["counts"] == {"E5F": 81, "E5R": 81, "R2S": 81, "R2I": 81}
    assert len({r["run_key"] for r in reqs}) == 324
    for r in reqs:
        assert hashlib.sha256(r["prompt"].encode("utf-8")).hexdigest() == r["prompt_sha256"]


def test_frozen_inputs_unchanged():
    manifest, _ = _plan()
    for label, rel in manifest["frozen_input_paths"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == manifest["frozen_inputs"][label]


def test_order_designs():
    manifest, reqs = _plan()
    g = manifest["e5_global_order"]["order"]
    assert all(r["order"] == g for r in reqs if r["experiment"] == "E5F")
    shared = {}
    for r in reqs:
        if r["experiment"] == "R2S":
            shared.setdefault((r["persona_id"], r["repeat"]), set()).add(tuple(r["order"]))
    assert all(len(v) == 1 for v in shared.values())
    indep = {}
    for r in reqs:
        if r["experiment"] == "R2I":
            indep.setdefault((r["persona_id"], r["repeat"]), set()).add(tuple(r["order"]))
    assert sum(len(v) for v in indep.values()) > len(indep)


def test_anchors_from_distinct_managers():
    manifest, _ = _plan()
    products = {p.product_id: p for p in load_products(ROOT / manifest["frozen_input_paths"]["shelf_A"])}
    anchors = manifest["e5_global_order"]["anchors"]
    assert len({products[a].issuer_name for a in anchors.values()}) == len(anchors) == 3


def test_shelf_b_valid_and_disjoint():
    manifest, _ = _plan()
    a = load_products(ROOT / manifest["frozen_input_paths"]["shelf_A"])
    b = load_products(ROOT / manifest["frozen_input_paths"]["shelf_B"])
    assert validate_products(b) == []
    assert not ({p.official_code for p in a} & {p.official_code for p in b})
    assert not ({p.issuer_name for p in a} & {p.issuer_name for p in b})
    assert len(load_personas(ROOT / manifest["frozen_input_paths"]["personas_B"])) == 9


def test_executor_loads_plan():
    spec = importlib.util.spec_from_file_location("stage3_executor", ROOT / "scripts/stage3_executor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    manifest, planned = mod.load_plan(PLAN)
    assert len(planned) == 324 and mod.CAPS == {"USD": 0.6, "CNY": 4.0}
