import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('erc', ROOT / 'scripts/export_review_copy.py')
erc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(erc)


def stats():
    return {'dropped': {}, 'raw_created': 0, 'renamed': 0}


def test_drops_time_fields_and_batch_id_only():
    rec = {'batch_id': 's3-real-20261005T154833Z-cf3b72a0', 'run_key': 'E5R__kimi__P__r3', 'started_at_utc': '2026-10-05T15:59:01+00:00',
           'finished_at_utc': '2026-10-05T15:59:22+00:00', 'order_seed': 1791215959, 'parsed': {'decision': 'RECOMMEND'},
           'pricing_checked_date': '2026-10-02'}
    out = erc.clean(rec, stats())
    assert set(out) == {'run_key', 'order_seed', 'parsed'}
    assert out['order_seed'] == 1791215959  # seeds are kept even if they look like epochs


def test_raw_api_body_created_removed_rest_unchanged():
    body = {'id': 'chatcmpl-1', 'object': 'chat.completion', 'created': 1791215959, 'model': 'kimi-k2.6', 'choices': [{'message': {'content': '{"a":1}'}}]}
    rec = {'raw_api_body': json.dumps(body, separators=(',', ':'))}
    st = stats()
    out = erc.clean(rec, st)
    got = json.loads(out['raw_api_body'])
    body.pop('created')
    assert got == body and st['raw_created'] == 1
    last = {'raw_api_body': '{"id":"x","created":1791215959}'}
    assert json.loads(erc.clean(last, stats())['raw_api_body']) == {'id': 'x'}


def test_dated_directory_names_renamed_in_keys_and_values():
    rec = {'runs/live-20261002T160402Z-ddf7f62f/pilot': {'path': 'runs/stage3-live-20261005/batch'}}
    out = erc.clean(rec, stats())
    assert list(out) == ['runs/stage1_pilot/pilot']
    assert out['runs/stage1_pilot/pilot']['path'] == 'runs/stage3_live/batch'
    assert erc.residuals(json.dumps(out)) == []


def test_residual_scan_flags_dates_but_not_hashes():
    assert erc.residuals('x 2026-10-05 y') == ['2026-10-05']
    assert erc.residuals('s-20261005T154833Z') == ['20261005T154833Z']
    assert erc.residuals('6d1c77eb20261005ab') == []
