"""Build console v12 from v11: batch labels without internal codes, wording for the hosted copy,
and a page description. Data and decision logic are unchanged. Does not overwrite v11.

  python scripts/build_console_v12.py
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "output/console_v11/index.html"
OUT = ROOT / "output/console_v12/index.html"

REPL = [
    ('`<option value="${k}">${bname(k)}（${k}）</option>`', '`<option value="${k}">${bname(k)}</option>`'),
    ('$("#rItem").value = `${bname(state.batch)}（${state.batch}） · ', '$("#rItem").value = `${bname(state.batch)} · '),
    ('第十七届“工行杯”参赛作品<br>离线运行 · 数据来自已冻结的真实调用',
     '第十七届“工行杯”参赛作品<br>数据来自已冻结的真实调用，不调用模型接口'),
    ('<title>EchoGuard 测试台</title>',
     '<title>EchoGuard 测试台</title>\n<meta name="description" content="EchoGuard 测试台：财富管理 AI 模型上线前行为测试与复核。'
     '可查看 9 个测试批次的风险卡、各画像的跨模型相似度与条件基准、DeepSeek、千问和 Kimi 的原始推荐，'
     '比较两种配置并导出复核记录。">'),
]


def main():
    html = SRC.read_text(encoding="utf-8")
    for a, b in REPL:
        assert html.count(a) == 1, a
        html = html.replace(a, b)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists() and OUT.read_text(encoding="utf-8") != html:
        raise SystemExit(f"{OUT} exists with different content")
    OUT.write_text(html, encoding="utf-8")
    print("v12", hashlib.sha256(html.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()
