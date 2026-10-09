"""Print the stage 4 numbers needed for the write-up (markdown), from saved outputs only.

Reads runs/stage4-live-20261006-v12/analysis/results.json (frozen analysis), the descriptives written
by stage4_describe_v12.py, and cost_report.json. No API calls; no new inference.
  python scripts/stage4_report_v12.py [--root runs/stage4-live-20261006-v12] [--desc reports/stage4_analysis_v12/descriptives.json]
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PN = {'deepseek': 'DeepSeek', 'qwen': '千问', 'kimi': 'Kimi', 'all': '三家合计'}
NAMES = {'example_copying': '示例照抄（有示例 − 无示例，选中示例所指候选）',
         'identity_focal_A': '名称与管理人（显示 − 隐藏，货架 A 选中华夏现金增利）',
         'id_focal_A': '编号（原编号 − 随机代号，货架 A 选中华夏现金增利）',
         'shared_vs_independent_all3': '共享顺序（共享 − 各自，三家选中同一只）'}


def r(x):
    return 'n/a' if x is None else f"{x['k']}/{x['n']}（{x['share']*100:.0f}%）" if x['n'] else '0/0'


def main(root, desc):
    res = json.loads((root / 'analysis/results.json').read_text(encoding='utf-8'))
    d = json.loads(desc.read_text(encoding='utf-8'))
    cost = root / 'cost_report.json'
    out = [f"# 第四阶段数字（{'MOCK' if res['is_mock'] else '真实调用'}）", '',
           f"矩阵完整：{res['matrix']['complete']}；工程校验 {res['guardrails']['correct']}/{res['guardrails']['planned']}", '',
           '| 模型 | 收到 | 无效 | 弃答 |', '|---|---|---|---|']
    for p, c in d['coverage'].items():
        out.append(f"| {PN[p]} | {c['received']} | {c['invalid']} | {c['abstain']} |")
    out += ['', '## 主要终点（双侧符号翻转，Holm 4 项）', '', '| 终点 | 区组 | 差 | 95% 区间（描述） | p | Holm p |', '|---|---|---|---|---|---|']
    for k, v in res.get('primary', {}).items():
        lo, hi = v['bootstrap95']
        out.append(f"| {NAMES[k]} | {v['blocks']} | {v['difference']:+.3f} | {lo:+.3f} 至 {hi:+.3f} | {v['p']:.5f} | {v['holm_p']:.5f} |")
    if 'all3_scheduled' in res:
        a = res['all3_scheduled']
        out.append(f"\n三家同选（中性条件）：共享 {a['0']['same']}/{a['0']['scheduled_units']}，各自 {a['1']['same']}/{a['1']['scheduled_units']}")
    out += ['', '## 分条件比例（描述）', '']
    for p in ['all', 'deepseek', 'qwen', 'kimi']:
        f = d['focal_A_by_identity_and_id'][p]; b = d['focal_B_by_identity'][p]; e = d['example_copying'][p]; fs = d['first_shown_neutral'][p]
        out.append(f"**{PN[p]}**：货架 A 选中华夏现金增利 — 显示名称+原编号 {r(f.get('named1_origid1'))}，显示名称+随机代号 {r(f.get('named1_origid0'))}，"
                   f"隐藏名称+原编号 {r(f.get('named0_origid1'))}，隐藏名称+随机代号 {r(f.get('named0_origid0'))}；"
                   f"货架 B 选中第一只（嘉实）显示 {r(b.get('named1'))}、隐藏 {r(b.get('named0'))}；"
                   f"选中示例所指候选 有示例 {r(e.get('example1'))}、无示例 {r(e.get('example0'))}；"
                   f"中性条件选中自己看到的第一位 共享 {r(fs.get('independent0'))}、各自 {r(fs.get('independent1'))}。")
        out.append('')
    oe = d['order_endpoint']
    out.append(f"两两同选：共享 {r(oe['shared']['pairwise_same'])}，各自 {r(oe['independent']['pairwise_same'])}；"
               f"三家同选：共享 {r(oe['shared']['all_three_same'])}，各自 {r(oe['independent']['all_three_same'])}")
    if cost.exists():
        c = json.loads(cost.read_text(encoding='utf-8'))
        out += ['', '费用（按返回用量估算）：' + json.dumps({k: c[k].get('charged_usage_or_reserved') for k in ('smoke', 'formal') if k in c}, ensure_ascii=False)]
    print('\n'.join(out))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=ROOT / 'runs/stage4-live-20261006-v12')
    ap.add_argument('--desc', type=Path, default=ROOT / 'reports/stage4_analysis_v12/descriptives.json')
    a = ap.parse_args()
    main(a.root, a.desc)
