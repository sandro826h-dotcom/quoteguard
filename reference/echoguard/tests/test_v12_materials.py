"""v12: business-impact conversions are recomputable from frozen data; console v12 changes only page text."""
import importlib.util, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bi', ROOT / 'scripts/business_impact_v12.py')
bi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bi)


def test_per_units_arithmetic():
    u = bi.per_units(0.46, 0.20)
    assert abs(u['difference'] - 0.26) < 1e-12
    assert u['per_10000_recommendations'] == 2600 and u['per_100m_yuan_10k_yuan'] == 2600


def test_impact_json_matches_frozen_stage3_data():
    res = json.loads((ROOT / 'reports/business_impact_v12/impact.json').read_text(encoding='utf-8'))
    by, design, _ = bi.s3.load(ROOT / 'runs/stage3-live-20261005/batch', ROOT / 'runs/stage3-plan-20261005')
    g = design['e5_global_order']['order']
    f, r = bi.anchor_money_customers(by['E5F'], g), bi.anchor_money_customers(by['E5R'], g)
    conv = res['conversions']
    assert round(f['anchor_weight_share'], 2) == 0.46 and round(r['anchor_weight_share'], 2) == 0.20
    assert (f['recs_with_anchor'], f['recommendations']) == (76, 81) and (r['recs_with_anchor'], r['recommendations']) == (52, 80)
    assert abs(conv['fixed_order_money']['difference'] - (f['anchor_weight_share'] - r['anchor_weight_share'])) < 1e-12
    assert round(conv['fixed_order_money']['per_100m_yuan_10k_yuan'], -2) == 2700
    assert round(conv['fixed_order_customers']['per_10000_recommendations'], -2) == 2900
    allx = res['exposure']['stage3_all']
    assert (allx['with_single_pick'], allx['recommendations']) == (193, 323)
    assert res['stage3_runs_sha256'] == bi.s3.sha(ROOT / 'runs/stage3-live-20261005/batch/runs.jsonl')


def test_console_v12_changes_only_page_text():
    v11 = (ROOT / 'output/console_v11/index.html').read_text(encoding='utf-8')
    v12 = (ROOT / 'output/console_v12/index.html').read_text(encoding='utf-8')
    data = lambda h: re.search(r'const DATA = (\{.*?\});\n', h, flags=re.S).group(1)
    assert data(v11) == data(v12)
    assert '（${k}）' not in v12 and '（${state.batch}）' not in v12 and '离线运行' not in v12
    assert '<meta name="description"' in v12
