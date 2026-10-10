"""Compute the sha256 of the files the frozen protocol depends on and write docs/protocol/冻结记录.md.

Usage: python scripts/freeze_protocol.py [--check]
--check compares the current files with the recorded hashes and exits non-zero on any difference.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "protocol" / "冻结记录.md"
FILES = [
    "docs/protocol/冻结协议.md",
    "docs/plan/AIC_最终方案.md",
    "docs/plan/任务生成规格.md",
    "docs/plan/方法实现规格.md",
    "docs/plan/真实报价集_口径与标注规范.md",
    "src/quoteguard/prompts.py",
    "configs/models.yaml",
    "configs/rates.json",
    "docs/protocol/预算估算.md",
    "data/taskgen/structures.yaml",
    "data/taskgen/value_sets.yaml",
    "data/taskgen/registry_synthetic.yaml",
    "data/taskgen/freight_floor.yaml",
    "data/params/tariff_snapshot_2026-10-09.json",
    "data/seeds/units.json",
    "data/seeds/blind_subset.json",
    "data/seeds/unit_assets.json",
]
ROW = re.compile(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def src_tree_hash() -> str:
    """Hash of all src/quoteguard/*.py, by sorted relative path and content."""
    h = hashlib.sha256()
    for p in sorted((ROOT / "src" / "quoteguard").glob("*.py")):
        h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def current() -> dict:
    out = {f: sha256(ROOT / f) for f in FILES}
    for p in sorted((ROOT / "data" / "seeds" / "anonymous").iterdir()):
        out[p.relative_to(ROOT).as_posix()] = sha256(p)
    out["src/quoteguard/*.py"] = src_tree_hash()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    now = current()
    if a.check:
        recorded = {m.group(1): m.group(2) for line in RECORD.read_text(encoding="utf-8").splitlines()
                    if (m := ROW.match(line))}
        diff = [f for f in now if recorded.get(f) != now[f]]
        for f in diff:
            print(f"changed: {f}")
        return 1 if diff else 0
    if RECORD.exists():
        print(f"{RECORD} exists; the frozen record is never rewritten", file=sys.stderr)
        return 1
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *FILES, "src", "data/seeds"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("uncommitted changes in frozen files:\n" + dirty, file=sys.stderr)
        return 1
    lines = ["# 冻结记录", "", f"冻结时的提交：`{head}`（冻结记录本身在其后一次提交中加入）。", "",
             "| 文件 | sha256 |", "|---|---|"]
    lines += [f"| `{f}` | `{h}` |" for f, h in now.items()]
    lines += ["", "测试集生成后在下方追加 manifest 哈希与生成所用提交号，不改上表。", ""]
    RECORD.write_text("\n".join(lines), encoding="utf-8")
    print(RECORD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
