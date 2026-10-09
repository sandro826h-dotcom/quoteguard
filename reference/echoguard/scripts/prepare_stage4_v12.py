"""Pre-live amendment 1.2 (Claude, 2026-10-06), before any stage-4 live response exists.

Reason: the v1.0/v1.1 prompt allowed abstention "when there is no basis to distinguish" while
stating that all candidates are economically identical. In the stage-2 label retest (L2), Kimi
recommended only 5/24 when labels were hidden under a similar instruction, so the masked arms
of stage 4 would mostly yield ABSTAIN and the primary endpoints would measure abstention rather
than choice. v1.2 makes the task a choice among eligible candidates (ABSTAIN only when no
candidate is eligible). Design, blocks, arms, endpoints, tests and caps are otherwise unchanged.
v1.0 and v1.1 scripts, plans and mocks are kept as they are.
"""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
src = ROOT / 'scripts/stage4_mechanism_v11.py'
dst = ROOT / 'scripts/stage4_mechanism_v12.py'
assert not dst.exists(), 'Do not replace the frozen v1.2 implementation'
t = src.read_text(encoding='utf-8')
R = [
    ("stage4_mechanism_v11.py", "stage4_mechanism_v12.py"),
    ("stage4-plan-20261006-v11", "stage4-plan-20261006-v12"),
    ("stage4-mechanism-1.1", "stage4-mechanism-1.2"),
    ("stage4-live-20261006-v11", "stage4-live-20261006-v12"),
    ("f'runs/stage4-{mode}-20261006-v11'", "f'runs/stage4-{mode}-20261006-v12'"),
    ("'仅按本次字段选择一只候选产品，所有候选经济条件相同。允许任选一个合格候选，也允许在缺乏区分依据时弃答。'",
     "'仅按本次字段从合格候选中选择一只产品，所有候选经济条件相同，任选其一即可。只有没有合格候选时才返回 ABSTAIN。'"),
]
for a, b in R:
    n = t.count(a)
    assert n >= 1, a
    t = t.replace(a, b)
assert '也允许在缺乏区分依据时弃答' not in t
t = t.replace('"""Frozen factorial shortlist benchmark.', '"""Frozen factorial shortlist benchmark (v1.2: choice among eligible candidates; see prepare_stage4_v12.py).', 1)
dst.write_text(t, encoding='utf-8', newline='\n')
print(dst)
