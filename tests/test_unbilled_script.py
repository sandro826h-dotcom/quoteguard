import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_unbilled_scan(tmp_path):
    spec = importlib.util.spec_from_file_location("unbilled", ROOT / "scripts" / "unbilled_attempts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from quoteguard.executor import upper_cost
    from quoteguard.llm import load_models
    models = load_models()
    req = {"run_key": "k", "config_id": "ds_flash", "system": None, "text": "x" * 100, "images": [], "max_tokens": 50}
    rates = {"ds_flash": {"currency": "USD", "input": 1.0, "output": 2.0}}
    b = tmp_path / "b1"
    (b / "errors").mkdir(parents=True)
    (b / "plan.json").write_text(json.dumps({"requests": [req], "rates": rates}))
    (b / "models.yaml").write_text(open(ROOT / "configs" / "models.yaml", encoding="utf-8").read())
    (b / "manifest.json").write_text("{}")
    att = [{"http_status": 502, "possibly_billed": False}, {"http_status": 504, "possibly_billed": False},
           {"http_status": 502, "possibly_billed": True}, {"http_status": 503, "possibly_billed": False}]
    f = b / "errors" / "e.json"
    f.write_text(json.dumps({"run_key": "k", "attempts": att}))
    before = f.read_bytes()
    r = mod.scan_batch(b)
    _, upper = upper_cost(req, models["ds_flash"], rates["ds_flash"])
    assert r["errors"]["attempts"] == 2 and r["errors"]["cost"]["USD"] == pytest.approx(2 * upper)
    assert f.read_bytes() == before
