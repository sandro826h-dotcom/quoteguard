"""生成方案图（灰度）：系统架构、方法阶梯、评测集构建流程、真实报价集标注流程。

用法：python docs/figures/src/make_figures.py
输出：docs/figures/ 下同名的 .png（300 dpi）、.pdf、.svg。
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

OUT = Path(__file__).resolve().parents[1]
FS = 1.4  # 字号放大系数：图按 10 英寸宽绘制，缩到报告版心约 16 cm 时正文约 6 pt

plt.rcParams.update(
    {
        "font.family": "Noto Serif CJK SC",
        "font.size": 8,
        "pdf.fonttype": 42,
        "svg.fonttype": "path",
        "axes.unicode_minus": False,
        "svg.hashsalt": "quoteguard",
    }
)

# 灰度样式
LLM = "#D0D0D0"  # 大模型调用
PROG = "#FFFFFF"  # 确定性程序
SNAP = "#EFEFEF"  # 带日期的数据快照（虚线框）
HUMAN = "#A8A8A8"  # 人工步骤
INK = "#000000"
LINE = "#333333"
MUTED = "#555555"


def canvas(w, h):
    fig = plt.figure(figsize=(w, h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title, body=None, fill=PROG, dashed=False, bold_edge=False, size=8 * FS, body_size=6.8 * FS):
    """以左下角 (x, y) 画圆角框；title 加粗，body 为较小字号的说明。"""
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0,rounding_size=0.06",
        linewidth=1.4 if bold_edge else 0.8,
        edgecolor=INK,
        facecolor=fill,
        linestyle=(0, (3, 2)) if dashed else "solid",
    )
    ax.add_patch(patch)
    cx = x + w / 2
    if body:
        ax.text(cx, y + h - 0.13, title, ha="center", va="top", fontsize=size, fontweight="bold", color=INK)
        ax.text(cx, y + h - 0.13 - size / 72 * 1.55, body, ha="center", va="top", fontsize=body_size,
                color=INK, linespacing=1.35)
    else:
        ax.text(cx, y + h / 2, title, ha="center", va="center", fontsize=size, fontweight="bold", color=INK,
                linespacing=1.3)
    return (x, y, w, h)


def anchor(b, side, frac=0.5):
    x, y, w, h = b
    return {
        "l": (x, y + h * frac),
        "r": (x + w, y + h * frac),
        "t": (x + w * frac, y + h),
        "b": (x + w * frac, y),
    }[side]


def arrow(ax, p, q, dashed=False, rad=0.0, label=None, label_off=(0, 0.08), lw=0.9):
    a = FancyArrowPatch(
        p,
        q,
        arrowstyle="-|>",
        mutation_scale=8,
        linewidth=lw,
        color=LINE,
        linestyle=(0, (3, 2)) if dashed else "solid",
        connectionstyle=f"arc3,rad={rad}",
        shrinkA=0,
        shrinkB=0,
    )
    ax.add_patch(a)
    if label:
        mx, my = (p[0] + q[0]) / 2 + label_off[0], (p[1] + q[1]) / 2 + label_off[1]
        ax.text(mx, my, label, ha="center", va="bottom", fontsize=6.3 * FS, color=MUTED)


def poly(ax, pts, dashed=False):
    """折线箭头：pts 依次连接，最后一段带箭头。"""
    for a, b in zip(pts[:-2], pts[1:-1]):
        ax.plot([a[0], b[0]], [a[1], b[1]], color=LINE, lw=0.9, linestyle=(0, (3, 2)) if dashed else "solid",
                solid_capstyle="butt")
    arrow(ax, pts[-2], pts[-1], dashed=dashed)


def legend(ax, x, y, items, step=1.55):
    for i, (label, fill, dashed) in enumerate(items):
        xi = x + i * step
        ax.add_patch(
            FancyBboxPatch((xi, y), 0.32, 0.18, boxstyle="round,pad=0,rounding_size=0.03", facecolor=fill,
                           edgecolor=INK, linewidth=0.7, linestyle=(0, (3, 2)) if dashed else "solid")
        )
        ax.text(xi + 0.4, y + 0.09, label, va="center", fontsize=6.8 * FS)


def save(fig, name):
    for ext in ("png", "pdf", "svg"):
        kw = {"dpi": 300} if ext == "png" else {}
        if ext == "pdf":
            kw["metadata"] = {"Title": name, "Author": None, "Creator": None, "Producer": None,
                              "CreationDate": None}
        elif ext == "svg":
            kw["metadata"] = {"Date": None, "Creator": None}
        fig.savefig(OUT / f"{name}.{ext}", facecolor="white", **kw)
    plt.close(fig)


# ---------------------------------------------------------------- 图 1 系统架构
def fig_architecture():
    W, H = 10.0, 6.2
    fig, ax = canvas(W, H)
    cols = [0.25, 2.25, 4.25, 6.25, 8.15]
    cw = 1.7
    heads = ["输入", "大模型理解", "程序核对", "核价与门控", "决策与输出"]
    for x, t in zip(cols, heads):
        ax.text(x + cw / 2, H - 0.25, t, ha="center", va="center", fontsize=8.5 * FS, fontweight="bold")
        ax.plot([x, x + cw], [H - 0.42, H - 0.42], color=INK, lw=0.6)

    # 输入
    quote = box(ax, cols[0], 3.55, cw, 1.75, "报价原件",
                "微信文字\nExcel、CSV\n价格表图片、PDF\n每次审计 4–6 份", fill=PROG)
    cargo = box(ax, cols[0], 1.75, cw, 1.4, "货物参数",
                "件数、箱规、重量\n货值、品名与 HTS\n目的仓、时效要求", fill=PROG)

    # 大模型理解
    ocr = box(ax, cols[1], 4.45, cw, 0.9, "视觉转写", "只用于图片报价\n文字与表格直接抽取", fill=LLM)
    ext = box(ax, cols[1], 2.75, cw, 1.4, "字段抽取",
              "计费、附加费、包税、\n资质等 18 个条款\n每个条款附原文片段", fill=LLM)
    rev = box(ax, cols[1], 1.2, cw, 1.1, "跨模型复核", "另一家模型独立抽取\n同一份报价", fill=LLM)

    # 程序核对
    loc = box(ax, cols[2], 3.6, cw, 1.1, "出处核对", "片段须为原文子串\n核对不上的条款降为缺失")
    cg = box(ax, cols[2], 1.75, cw, 1.4, "冲突与缺口",
             "两次抽取取值不同\n或原文前后矛盾 → 冲突\n必需条款未写 → 缺口")

    # 核价与门控
    calc = box(ax, cols[3], 3.82, cw, 1.2, "确定性核价",
               "计费重、阶梯单价、附加费\n关税、MPF、HMF、报关费\n或然费用单列为风险敞口", body_size=6.2 * FS)
    ddp = box(ax, cols[3], 2.7, cw, 0.95, "包税合理性", "依据税费下限与参照下限\n高 / 需核实 / 不标")
    cred = box(ax, cols[3], 1.45, cw, 0.95, "资质证据",
               "有证据 / 证据不足\n与宣称矛盾 / 不相关")

    # 决策与输出
    dec = box(ax, cols[4], 3.55, cw, 1.8, "决策",
              "可行集：满足时效、\n可送达、包税风险非高\n取确定成本最低者；\n缺失或冲突条款会改变\n最优解时改为追问")
    pas = box(ax, cols[4], 2.2, cw, 0.95, "决策护照", "成本、隐性费用、风险、\n证据状态、推荐理由",
              bold_edge=True)
    ask = box(ax, cols[4], 1.0, cw, 0.85, "追问清单", "可直接发给货代\n附应索要的单证", bold_edge=True)

    # 快照与记录
    sy, sh = 0.1, 0.8
    s1 = box(ax, cols[3], 5.12, cw, 0.42, "参数快照：税率与规则", fill=SNAP, dashed=True, size=6.6 * FS)
    s2 = box(ax, cols[3], sy, cw, sh, "登记库快照", "FMC OTI、海关企业信用", fill=SNAP, dashed=True, size=7.2 * FS,
             body_size=6.0 * FS)
    s3 = box(ax, cols[4], sy, cw, sh, "运行记录", "模型、参数、提示词\n哈希、种子、原始响应", fill=SNAP,
             dashed=True, size=7.2 * FS, body_size=5.8 * FS)

    # 连线
    arrow(ax, anchor(quote, "r", 0.78), anchor(ocr, "l", 0.5))
    arrow(ax, anchor(quote, "r", 0.35), anchor(ext, "l", 0.75))
    arrow(ax, anchor(ocr, "b"), anchor(ext, "t"))
    arrow(ax, anchor(quote, "r", 0.06), anchor(rev, "l", 0.75))
    arrow(ax, anchor(ext, "r", 0.75), anchor(loc, "l", 0.5))
    arrow(ax, anchor(ext, "r", 0.3), anchor(cg, "l", 0.75))
    arrow(ax, anchor(rev, "r", 0.6), anchor(cg, "l", 0.2))
    arrow(ax, anchor(loc, "b"), anchor(cg, "t"))
    arrow(ax, anchor(loc, "r", 0.75), anchor(calc, "l", 0.35))
    poly(ax, [anchor(cargo, "r", 0.3), (cols[1] - 0.25, anchor(cargo, "r", 0.3)[1]),
              (cols[1] - 0.25, 0.98), (cols[3] - 0.2, 0.98), (cols[3] - 0.2, anchor(calc, "l", 0.12)[1]),
              anchor(calc, "l", 0.12)])
    arrow(ax, anchor(calc, "b"), anchor(ddp, "t"))
    arrow(ax, anchor(loc, "r", 0.2), anchor(cred, "l", 0.8))
    arrow(ax, anchor(calc, "r", 0.5), anchor(dec, "l", 0.85))
    arrow(ax, anchor(ddp, "r", 0.5), anchor(dec, "l", 0.35))
    arrow(ax, anchor(cred, "r", 0.6), anchor(dec, "l", 0.08))
    arrow(ax, anchor(cg, "r", 0.5), anchor(ddp, "l", 0.2))
    arrow(ax, anchor(dec, "b", 0.5), anchor(pas, "t", 0.5))
    poly(ax, [anchor(dec, "r", 0.3), (cols[4] + cw + 0.13, anchor(dec, "r", 0.3)[1]),
              (cols[4] + cw + 0.13, anchor(ask, "r", 0.5)[1]), anchor(ask, "r", 0.5)])
    arrow(ax, anchor(s1, "b"), anchor(calc, "t"), dashed=True)
    arrow(ax, anchor(s2, "t"), anchor(cred, "b"), dashed=True)

    legend(ax, 0.25, 0.42, [("大模型调用", LLM, False), ("确定性程序", PROG, False),
                            ("带日期的快照与记录", SNAP, True)], step=1.15)
    save(fig, "fig1_architecture")


# ---------------------------------------------------------------- 图 2 方法阶梯
def fig_ladder():
    W, H = 10.0, 5.6
    fig, ax = canvas(W, H)
    methods = [
        ("M0", "卖家直接询问", "把报价贴给通用大模型\n问哪家划算、有什么坑"),
        ("M1", "强提示词", "检查清单、规则与\n参数快照写进提示词"),
        ("M2", "抽取 + 核价", "大模型抽取字段\n程序计算到仓成本"),
        ("M3", "加风险门控", "包税合理性与\n资质证据由程序判定"),
        ("M4", "完整系统", "出处核对、冲突与缺口\n跨模型复核、追问"),
    ]
    rows = [
        "单次调用直接给出选择",
        "规则与参数快照写进提示词",
        "逐份报价结构化抽取",
        "到仓成本计算",
        "包税合理性判定",
        "资质证据判定",
        "冲突与缺口识别",
        "出处核对",
        "跨模型复核",
        "追问（取值网格判定）",
    ]
    # 2 = 方法包含该组件，1 = 只在提示词中要求模型自行完成，0 = 无
    marks = [
        [2, 2, 0, 0, 0],
        [0, 2, 0, 0, 0],
        [0, 1, 2, 2, 2],
        [0, 1, 2, 2, 2],
        [0, 1, 0, 2, 2],
        [0, 1, 0, 2, 2],
        [0, 1, 0, 0, 2],
        [0, 0, 0, 0, 2],
        [0, 0, 0, 0, 2],
        [0, 1, 0, 0, 2],
    ]

    # 上半部：阶梯
    x0, cw, base = 2.55, 1.42, 3.05
    for i, (code, name, desc) in enumerate(methods):
        x = x0 + i * cw
        h = 0.78 + i * 0.28
        ax.add_patch(Rectangle((x + 0.04, base), cw - 0.08, h, facecolor=["#F4F4F4", "#E4E4E4", "#D0D0D0",
                                                                       "#B8B8B8", "#9C9C9C"][i],
                               edgecolor=INK, linewidth=0.8))
        ax.text(x + cw / 2, base + h - 0.16, code, ha="center", va="top", fontsize=10 * FS, fontweight="bold")
        ax.text(x + cw / 2, base + h - 0.46, name, ha="center", va="top", fontsize=7.5 * FS, fontweight="bold")
        ax.text(x + cw / 2, base + h + 0.08, desc, ha="center", va="bottom", fontsize=6.5 * FS, linespacing=1.3)
    ax.text(0.3, base + 0.2, "方法阶梯\nM0、M1 为单次调用对照\nM2 起每一级在上一级\n基础上加一类组件\n逐级比较说明每层的作用",
            ha="left", va="bottom", fontsize=7.2 * FS, linespacing=1.45)

    # 下半部：组件矩阵
    top = 2.85
    rh = 0.245
    ax.plot([0.3, x0 + 5 * cw], [top, top], color=INK, lw=0.8)
    for r, label in enumerate(rows):
        y = top - (r + 0.5) * rh
        ax.text(0.3, y, label, ha="left", va="center", fontsize=6.9 * FS)
        for c in range(5):
            cx = x0 + c * cw + cw / 2
            m = marks[r][c]
            if m == 2:
                ax.plot(cx, y, marker="o", ms=5.2, color=INK)
            elif m == 1:
                ax.plot(cx, y, marker="o", ms=5.2, markerfacecolor="white", markeredgecolor=INK, mew=0.9)
    bottom = top - len(rows) * rh
    ax.plot([0.3, x0 + 5 * cw], [bottom, bottom], color=INK, lw=0.8)
    for c in range(1, 5):
        ax.plot([x0 + c * cw] * 2, [bottom, top], color="#BBBBBB", lw=0.5)
    ax.plot(0.4, bottom - 0.22, marker="o", ms=5, color=INK)
    ax.text(0.55, bottom - 0.22, "方法包含该组件", va="center", fontsize=6.8 * FS)
    ax.plot(1.65, bottom - 0.22, marker="o", ms=5, markerfacecolor="white", markeredgecolor=INK, mew=0.9)
    ax.text(1.8, bottom - 0.22, "只在提示词中要求模型自行完成", va="center", fontsize=6.8 * FS)
    ax.text(W - 0.25, bottom - 0.22,
            "6 个模型配置各跑 M0–M4；M1 与 M4 拿到相同的规则、参数快照与调试轮数",
            ha="right", va="center", fontsize=6.8 * FS, color=MUTED)
    save(fig, "fig2_method_ladder")


# ---------------------------------------------------------------- 图 3 评测集构建流程
def fig_data():
    W, H = 10.0, 5.0
    fig, ax = canvas(W, H)
    bw = 1.62

    # 泳道
    for y0, h, t in [(3.3, 1.55, "真实报价集"), (0.2, 2.95, "反事实任务集")]:
        ax.add_patch(Rectangle((0.2, y0), W - 0.4, h, facecolor="none", edgecolor="#888888", lw=0.6))
        ax.text(0.33, y0 + h - 0.1, t, ha="left", va="top", fontsize=8.5 * FS, fontweight="bold")

    # 真实报价集
    y, bh = 3.42, 1.08
    r1 = box(ax, 0.4, y, bw, bh, "公开来源", "货代价格页与承运商 PDF\n10 份原件，7 个主体",
             fill=HUMAN)
    r2 = box(ax, 2.3, y, bw, bh, "人工保存与匿名化", "原件与对照表存仓库外\n货代统一为 F 编号",
             fill=HUMAN)
    r3 = box(ax, 4.2, y, bw, bh, "拆分报价单元", "28 个单元\n货代 22，承运商 6\n网页 19，PDF 9")
    r4 = box(ax, 6.1, y, bw, bh, "标注与裁决", "盲标子集 15，其余 13\n流程见图 4", fill=HUMAN)
    r5 = box(ax, 8.0, y, bw, bh, "真实集评测", "6 个配置各抽取一次\n字段与出处准确率", bold_edge=True)
    for a, b in [(r1, r2), (r2, r3), (r3, r4), (r4, r5)]:
        arrow(ax, anchor(a, "r"), anchor(b, "l"))

    # 反事实任务集
    ya, yb, rh = 1.6, 0.4, 0.95
    f1 = box(ax, 0.4, ya, bw, rh, "费用结构库", "数值范围取自真实单元\n与承运商官方规则")
    f2 = box(ax, 0.4, yb, bw, rh, "参数快照", "HTS 税率、MPF、HMF\n汇率、取值集合\n合成登记库",
             fill=SNAP, dashed=True)
    g = box(ax, 2.3, yb, bw, ya + rh - yb, "任务生成器",
            "锁定商业事实\n生成 4–6 份候选报价\n显式、隐含、冲突、\n信息不足四层")
    gold = box(ax, 4.2, ya, bw, rh, "标准答案", "计算器 A：Python\n计算器 B：Excel 公式\n差额 ≤ 0.01 元")
    pool = box(ax, 4.2, yb, bw, rh, "呈现模板池", "开发池与测试池\n模板互不重叠", fill=SNAP, dashed=True)
    vf = box(ax, 6.1, ya, bw, rh, "取值网格验证", "缺失或冲突条款逐值代入\n最优解会变 → 追问\n最优与次优差 ≥ 3%")
    rd = box(ax, 6.1, yb, bw, rh, "渲染", "文字、表格、图片\n中英混写、脚注、附表")
    dev = box(ax, 8.0, ya, bw, rh, "开发集 24 个", "只用于调试提示词")
    test = box(ax, 8.0, yb, bw, rh, "测试集 48 个", "四层各 12 个\n种子 20261011\n提示词冻结后生成",
               bold_edge=True)

    arrow(ax, anchor(f1, "r"), anchor(g, "l", 0.75))
    arrow(ax, anchor(f2, "r"), anchor(g, "l", 0.25))
    arrow(ax, anchor(g, "r", 0.75), anchor(gold, "l"))
    arrow(ax, anchor(gold, "r"), anchor(vf, "l"))
    arrow(ax, anchor(vf, "b"), anchor(rd, "t"))
    arrow(ax, anchor(pool, "r"), anchor(rd, "l"))
    arrow(ax, anchor(rd, "r", 0.7), anchor(dev, "l", 0.3))
    arrow(ax, anchor(rd, "r", 0.4), anchor(test, "l", 0.4))
    # 真实单元 → 费用结构库
    poly(ax, [anchor(r3, "b", 0.3), (4.2 + bw * 0.3, 3.225), (0.4 + bw * 0.85, 3.225), anchor(f1, "t", 0.85)])
    ax.text(3.0, 3.08, "提炼费用结构与不包税参照下限", ha="center", va="top", fontsize=6.3 * FS, color=MUTED)

    legend(ax, 6.3, 2.82, [("人工步骤", HUMAN, False), ("程序", PROG, False), ("快照", SNAP, True)], step=1.0)
    save(fig, "fig3_dataflow")


# ---------------------------------------------------------------- 图 4 真实报价集标注流程
def fig_annotation():
    W, H = 10.0, 4.6
    fig, ax = canvas(W, H)
    bw = 1.75

    src = box(ax, 0.25, 1.55, 1.55, 1.5, "28 个报价单元", "按固定种子 20261010\n分层抽取盲标子集")

    # 盲标子集
    ax.text(2.2, H - 0.2, "盲标子集 15 个（承运商 PDF 4，货代 PDF 2，货代网页 9）", fontsize=7.5 * FS,
            fontweight="bold", va="top")
    a1 = box(ax, 2.2, 3.15, bw, 0.95, "模型初标 1", "OpenAI Codex\n隔离上下文 1", fill=LLM)
    a2 = box(ax, 2.2, 2.05, bw, 0.95, "模型初标 2", "OpenAI Codex\n隔离上下文 2", fill=LLM)
    adj = box(ax, 4.4, 2.05, bw, 2.05, "人工全量审定",
              "裁决人对照原件\n逐字段审定 270 个字段\n分歧按规范第七节\n歧义规则裁决\n逐条记录理由", fill=HUMAN)

    # 其余单元
    ax.text(2.2, 1.6, "其余 13 个单元", fontsize=7.5 * FS, fontweight="bold", va="top")
    c1 = box(ax, 2.2, 0.25, bw, 1.1, "Claude 初标", "不属于 6 个实验配置\n出处经子串核对", fill=LLM)
    c2 = box(ax, 4.4, 0.25, bw, 1.1, "标注人 A 复核", "逐字段确认或改正\n写明原文依据", fill=HUMAN)

    gold = box(ax, 6.6, 1.2, 1.4, 1.6, "金标签", "导入仓库\n记录哈希", bold_edge=True)
    run = box(ax, 8.35, 1.2, 1.4, 1.6, "模型抽取", "标签哈希记录后\n才运行 6 个配置")

    arrow(ax, anchor(src, "r", 0.8), anchor(a1, "l"))
    arrow(ax, anchor(src, "r", 0.55), anchor(a2, "l"))
    arrow(ax, anchor(src, "r", 0.2), anchor(c1, "l", 0.6))
    arrow(ax, anchor(a1, "r"), anchor(adj, "l", 0.77))
    arrow(ax, anchor(a2, "r"), anchor(adj, "l", 0.23))
    arrow(ax, anchor(c1, "r"), anchor(c2, "l"))
    arrow(ax, anchor(adj, "r", 0.4), anchor(gold, "l", 0.75))
    arrow(ax, anchor(c2, "r", 0.6), anchor(gold, "l", 0.2))
    arrow(ax, anchor(gold, "r"), anchor(run, "l"))

    ax.text(6.6, 3.45, "独立性约束\n· 标注与裁决不看系统输出与计算器结果\n· 真实单元原文不进入提示词与词表\n"
            "· 初标之间的一致率只作内部记录", fontsize=6.6 * FS, va="bottom", linespacing=1.45)
    ax.text(6.6, 0.35, "原规范为两人从头人工标注，实际改为\n模型初标加人工全量审定（偏离记录第 1 条）",
            fontsize=6.4 * FS, color=MUTED, va="bottom", linespacing=1.4)
    legend(ax, 0.25, 0.62, [("大模型", LLM, False)])
    legend(ax, 0.25, 0.32, [("人工", HUMAN, False)])
    save(fig, "fig4_annotation")


if __name__ == "__main__":
    fig_architecture()
    fig_ladder()
    fig_data()
    fig_annotation()
