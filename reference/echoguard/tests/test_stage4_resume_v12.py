import importlib.util, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load():
    spec = importlib.util.spec_from_file_location('s4resume', ROOT / 'scripts/stage4_resume_v12.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resume_keeps_frozen_plan_and_ledger_identity(tmp_path):
    r = load()
    m, reqs = r.s4.load_plan()  # source hashes of the frozen modules still match
    base_cfg, _ = r._configurations(m, 1.0)
    new_cfg, _ = r.configurations(m, 1.0)
    assert all(c['transport']['max_retries'] == r.MAX_RETRIES for c in new_cfg.values())
    assert all(base_cfg[k]['transport']['max_retries'] == 2 for k in base_cfg)
    strip = lambda cfg: {k: {kk: vv for kk, vv in v.items() if kk != 'transport'} for k, v in cfg.items()}
    assert strip(base_cfg) == strip(new_cfg)  # payload-relevant configuration unchanged
    a = r.old.Ledger(tmp_path / 'a', reqs[:3], base_cfg, True, m['request_plan_sha256'], label='x')
    b = r.old.Ledger(tmp_path / 'b', reqs[:3], new_cfg, True, m['request_plan_sha256'], label='x')
    assert a.identity_hash == b.identity_hash  # resume identity accepts the amendment


def test_connection_error_becomes_retryable_outcome_with_wait(monkeypatch):
    r = load()
    import requests

    def boom(self, request, config):
        raise requests.ConnectionError('reset')
    monkeypatch.setattr(r._Transport, '__call__', boom)
    t = r.PatientTransport({'deepseek': 'k', 'qwen': 'k', 'kimi': 'k'}, {'deepseek': 0, 'qwen': 0, 'kimi': 0})
    out = t({'provider': 'kimi'}, {})
    assert out['status'] == 'transport_failure' and out['retryable'] and out['retry_after'] == r.TRANSPORT_WAIT_S


def test_fourth_attempt_allowed_only_after_amendment(tmp_path):
    r = load()
    m, reqs = r.s4.load_plan()
    req = next(x for x in reqs if x['provider'] == 'kimi')
    base_cfg, _ = r._configurations(m, 1.0)
    for c in base_cfg.values():
        c['max_tokens'] = 400
    led = r.old.Ledger(tmp_path / 'batch', [req], base_cfg, True, m['request_plan_sha256'], label='x')
    fail = {'status': 'transport_failure', 'retryable': True, 'error_type': 'ConnectionError',
            'possible_remote_completion': True, 'metadata': {}}
    for _ in range(3):
        led.finish(req, led.begin(req), fail)
    with pytest.raises(ValueError, match='Attempt limit'):
        led.begin(req)
    new_cfg, _ = r.configurations(m, 1.0)
    for c in new_cfg.values():
        c['max_tokens'] = 400
    led2 = r.old.Ledger(tmp_path / 'batch', [req], new_cfg, True, m['request_plan_sha256'], label='x')  # resume
    intent = led2.begin(req)
    assert intent['attempt_number'] == 4
