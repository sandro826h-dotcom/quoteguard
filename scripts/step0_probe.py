"""Step 0 interface probe: build the synthetic price-table image and the frozen probe plan.

Writes runs/step0_assets/probe_table.png and runs/step0_plan.json (kind "dev"). It makes no
network call. Run the plan with the executor (mock first, then live).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quoteguard.executor import make_run_key, prompt_hash  # noqa: E402
from quoteguard.util import atomic_json, sha256_file  # noqa: E402

SEED = 20261010
FONT_FILE = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
TEXT_PROMPT = "只回复数字：17+25=？"
VISION_PROMPT = "逐行转写图中表格的全部文字"
RATES = {
    "ds_pro": ("USD", 2.0, 4.0), "ds_flash": ("USD", 0.5, 1.0), "ds_flash_think": ("USD", 0.5, 1.0),
    "qwen_max": ("CNY", 4.0, 40.0), "qwen_plus": ("CNY", 4.0, 40.0), "qwen_vl": ("CNY", 4.0, 40.0),
    "glm": ("CNY", 8.0, 28.0), "glm_v": ("CNY", 4.0, 12.0), "kimi": ("CNY", 6.5, 27.0),
}
BUDGET = {"USD": 0.5, "CNY": 5}


def make_table_png(path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    if Path(FONT_FILE).is_file():
        fm.fontManager.addfont(FONT_FILE)
    plt.rcParams["font.family"] = ["Noto Sans CJK SC", "sans-serif"]
    fig = plt.figure(figsize=(12, 8), dpi=100, facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1200)
    ax.set_ylim(800, 0)
    ax.axis("off")
    ax.text(600, 70, "美国专线 运价表", ha="center", va="center", fontsize=30)
    rows = [("重量档", "单价（元/公斤）"), ("1-45 公斤", "28.50"), ("46-100 公斤", "24.00"),
            ("101 公斤以上", "21.50")]
    top, h = 140, 80
    for i, (a, b) in enumerate(rows):
        y = top + i * h
        ax.plot([150, 1050], [y, y], color="black", lw=1.5)
        ax.text(250, y + h / 2, a, va="center", fontsize=22, fontweight="bold" if i == 0 else "normal")
        ax.text(800, y + h / 2, b, va="center", fontsize=22, fontweight="bold" if i == 0 else "normal")
    ax.plot([150, 1050], [top + 4 * h] * 2, color="black", lw=1.5)
    ax.text(150, 530, "附加费", fontsize=24, fontweight="bold", va="center")
    ax.text(150, 600, "燃油附加费：运费的 12%", fontsize=22, va="center")
    ax.text(150, 660, "偏远地区附加费：150 元/票", fontsize=22, va="center")
    ax.text(1170, 775, "合成示例", ha="right", va="bottom", fontsize=18, color="gray")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", metadata={"Software": None})
    plt.close(fig)


def build_plan(image: dict) -> dict:
    reqs = []

    def add(method, config_id, task, text, repeat, images, max_tokens):
        shas = [i["sha256"] for i in images]
        reqs.append({
            "run_key": make_run_key("step0", method, config_id, task, "probe", "c0", repeat),
            "experiment": "step0", "method": method, "config_id": config_id, "task_id": task,
            "stage": "probe", "cand_id": "c0", "repeat": repeat, "system": None, "text": text,
            "images": images, "seed": SEED, "max_tokens": max_tokens,
            "prompt_sha256": prompt_hash(None, text, shas, max_tokens)})
    for cid in ("ds_pro", "ds_flash", "qwen_max", "qwen_plus", "glm", "kimi", "ds_flash_think"):
        add("think", cid, "arith", TEXT_PROMPT, 1, [], 512 if cid == "ds_flash_think" else 64)
    for r in range(2, 6):
        add("think", "kimi", "arith", TEXT_PROMPT, r, [], 64)
    for cid in ("ds_flash", "qwen_plus"):
        add("think", cid, "arith", TEXT_PROMPT, 2, [], 64)
    for cid in ("qwen_vl", "glm_v", "kimi", "ds_flash"):
        add("vision", cid, "table", VISION_PROMPT, 1, [image], 600)
    return {"plan_id": "step0", "kind": "dev", "budget": BUDGET,
            "rates": {k: {"currency": c, "input": i, "output": o} for k, (c, i, o) in RATES.items()},
            "requests": reqs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    a = ap.parse_args(argv)
    runs = Path(a.runs)
    png = runs / "step0_assets" / "probe_table.png"
    make_table_png(png)
    image = {"path": "step0_assets/probe_table.png", "mime": "image/png", "sha256": sha256_file(png)}
    plan = build_plan(image)
    atomic_json(runs / "step0_plan.json", plan)
    print(json.dumps({"requests": len(plan["requests"]), "png_sha256": image["sha256"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
