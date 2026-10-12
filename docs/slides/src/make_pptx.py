"""按 docs/slides/PPT大纲.md 生成评审 PPT：docs/slides/QuoteGuard_评审.pptx。

用法：python docs/slides/src/make_pptx.py
结果页只放版式与图位，数字统一用“〔待正式结果〕”占位；讲稿要点写入每页备注。
"""

from pathlib import Path

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[3]
FIG = ROOT / "docs" / "figures"
SHOT = ROOT / "demo" / "screenshots"
OUT = ROOT / "docs" / "slides" / "QuoteGuard_评审.pptx"

FONT = "Noto Serif CJK SC"
INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x5F, 0x5F, 0x5F)
RULE = RGBColor(0xBF, 0xBF, 0xBF)
PANEL = RGBColor(0xF3, 0xF3, 0xF3)
ACCENT = RGBColor(0x8C, 0x1D, 0x18)  # 与演示系统的强调色一致，只用于关键数字与结论标记
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TBD = "〔待正式结果〕"

W, H = 13.333, 7.5
LM = 0.6  # 左右边距
CW = W - 2 * LM

prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
BLANK = prs.slide_layouts[6]
core = prs.core_properties
core.title = "运价明镜：跨境头程报价智能审计"
core.author = ""
core.last_modified_by = ""
core.comments = ""
core.keywords = ""
core.subject = ""


# ---------------------------------------------------------------- 文字工具
def _font(run, size, bold=False, color=INK, italic=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.rgb = color
    f.name = FONT
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
        el.set("typeface", FONT)


def _bullet(p, level=0):
    ppr = p._p.get_or_add_pPr()
    indent = 0.22 + 0.25 * level
    ppr.set("marL", str(Emu(Inches(indent))))
    ppr.set("indent", str(-Emu(Inches(0.22))))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in ppr.findall(qn(tag)):
            ppr.remove(el)
    bu = etree.SubElement(ppr, qn("a:buChar"))
    bu.set("char", "•" if level == 0 else "–")


def text(slide, x, y, w, h, items, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         bullets=False, space=6, line=1.15, margin=0.05):
    """items：字符串，或列表；列表元素可为字符串、(字符串, 层级) 或 [(片段, 样式字典), ...]。"""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(margin))
    if isinstance(items, str):
        items = [items]
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line
        p.space_after = Pt(space)
        level = 0
        if isinstance(item, tuple):
            item, level = item
        if bullets:
            _bullet(p, level)
        runs = item if isinstance(item, list) else [(item, {})]
        for seg, style in runs:
            r = p.add_run()
            r.text = seg
            _font(r, style.get("size", size - 1 * level), style.get("bold", bold), style.get("color", color),
                  style.get("italic", False))
    return tb


def title(slide, s, sub=None):
    text(slide, LM, 0.38, CW, 0.8, s, size=28, bold=True, anchor=MSO_ANCHOR.TOP, margin=0)
    if sub:
        text(slide, LM, 1.12, CW, 0.45, sub, size=15, color=MUTED, margin=0)


def rect(slide, x, y, w, h, fill=PANEL, line=None, dash=False, shape=MSO_SHAPE.RECTANGLE):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.shadow.inherit = False
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
        if dash:
            s.line.dash_style = 7  # MSO_LINE.DASH
    return s


def card(slide, x, y, w, h, head, body, size=15, head_size=17, fill=PANEL, bullets=True):
    rect(slide, x, y, w, h, fill=fill)
    text(slide, x + 0.25, y + 0.2, w - 0.5, 0.45, head, size=head_size, bold=True, margin=0)
    text(slide, x + 0.25, y + 0.72, w - 0.5, h - 0.9, body, size=size, bullets=bullets, margin=0)


def placeholder(slide, x, y, w, h, what, size=14):
    """结果图表位：虚线框，中间为占位符与该位置将放的内容。"""
    rect(slide, x, y, w, h, fill=WHITE, line=RULE, dash=True)
    text(slide, x + 0.3, y + 0.25, w - 0.6, h - 0.5,
         [[(TBD, {"size": 18, "bold": True, "color": ACCENT})], what],
         size=size, color=MUTED, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, margin=0)


def image(slide, path, x, y, w, h, border=False):
    """在 (x, y, w, h) 内按比例居中放图。"""
    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    px, py = x + (w - pw) / 2, y + (h - ph) / 2
    pic = slide.shapes.add_picture(str(path), Inches(px), Inches(py), Inches(pw), Inches(ph))
    if border:
        pic.line.color.rgb = RULE
        pic.line.width = Pt(0.75)
    return pic


def table(slide, x, y, w, rows, col_w, size=14, head_fill=PANEL, row_h=0.42):
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w),
                                   Inches(row_h * len(rows)))
    tbl = shape.table
    tblpr = tbl._tbl.tblPr
    style = tblpr.find(qn("a:tableStyleId"))
    if style is None:
        style = etree.SubElement(tblpr, qn("a:tableStyleId"))
    style.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # No Style, Table Grid
    tblpr.set("firstRow", "1")
    tblpr.set("bandRow", "0")
    for j, cw in enumerate(col_w):
        tbl.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Inches(row_h)
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            if i == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = head_fill
            else:
                cell.fill.background()
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            color = ACCENT if val == TBD else INK
            r = p.add_run()
            r.text = val
            _font(r, size, bold=(i == 0), color=color)
            _cell_border(cell)
    return shape


def _cell_border(cell):
    tcpr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        ln = etree.SubElement(tcpr, qn(tag))
        ln.set("w", str(Emu(Pt(0.75))))
        fill = etree.SubElement(ln, qn("a:solidFill"))
        clr = etree.SubElement(fill, qn("a:srgbClr"))
        clr.set("val", "BFBFBF")


def footer(slide, n, tag=None):
    text(slide, LM, H - 0.45, 6, 0.3, "运价明镜 QuoteGuard", size=10, color=MUTED, margin=0)
    right = f"{tag}　{n}" if tag else str(n)
    text(slide, W - LM - 6, H - 0.45, 6, 0.3, right, size=10, color=MUTED, align=PP_ALIGN.RIGHT, margin=0)


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


def new(n, t, sub=None, tag=None, note=""):
    s = prs.slides.add_slide(BLANK)
    title(s, t, sub)
    footer(s, n, tag)
    notes(s, note)
    return s


# ================================================================ 第 1 页 封面
s = prs.slides.add_slide(BLANK)
text(s, LM, 2.1, CW, 1.0, "运价明镜", size=54, bold=True, margin=0)
text(s, LM, 3.15, CW, 0.6, "跨境头程报价智能审计　QuoteGuard", size=26, margin=0)
text(s, LM, 4.1, CW, 0.9,
     "把美国头程报价折算成可比的到仓成本，找出隐性费用与包税风险，信息不足时生成追问清单",
     size=18, color=MUTED, margin=0)
text(s, LM, 5.75, CW, 0.9, ["第八届全球校园人工智能算法精英大赛　算法主题赛“AI+跨境电商”",
                             "参赛团队编号：〔报名后填写〕　　2026 年 10 月"], size=14, color=MUTED, margin=0)
notes(s, "一句话介绍：帮中国跨境卖家审计美国头程物流报价，把报价折算成可比的到仓成本，找出隐性费用和包税风险，"
         "信息不足时生成追问清单。")

# ================================================================ 第 2 页 一页看懂
s = new(2, "一页看懂：问题、方案、效果、应用", tag=None,
        note="先给结论数字，后面各页依次给出依据。效果格的措辞按第 16、18 页最终选定的写法同步。")
gx, gy, gw, gh, gap = LM, 1.45, (CW - 0.3) / 2, 2.6, 0.3
card(s, gx, gy, gw, gh, "问题",
     ["头程报价的计泡口径、不含项目、包税范围散落在正文、脚注和图片里，卖家难以比较",
      "宁波海事法院 400 件跨境电商物流案件中，代理运费争议占 79.3%"], size=16)
card(s, gx + gw + gap, gy, gw, gh, "方案",
     ["大模型逐份抽取条款并标出原文出处",
      "程序核价，判定包税合理性与资质证据",
      "信息不足时不推荐，生成追问清单"], size=16)
rect(s, gx, gy + gh + gap, gw, gh, fill=PANEL)
text(s, gx + 0.25, gy + gh + gap + 0.2, gw - 0.5, 0.45, "效果（对比卖家现在的做法 M0）", size=17, bold=True, margin=0)
text(s, gx + 0.25, gy + gh + gap + 0.75, gw - 0.5, gh - 0.9,
     [[("决策正确率提升　", {}), (TBD, {"color": ACCENT})],
      [("风险检出 micro-F1 差值　", {}), (TBD, {"color": ACCENT})],
      [("每次审计调用成本　", {}), (TBD, {"color": ACCENT})]], size=16, bullets=True, margin=0)
card(s, gx + gw + gap, gy + gh + gap, gw, gh, "应用",
     ["独立审计工具", "卖家 ERP 插件", "货代比价平台的审核模块"], size=16)

# ================================================================ 第 3 页 问题示例
s = new(3, "同一票货，几份报价没法直接比", tag="合成示例",
        note="卖家现在的做法是把这些报价贴给通用大模型问“哪家划算”，这正是本项目的对照组 M0。"
             "左图为演示系统的报价输入页，报价为开发集合成示例。")
image(s, SHOT / "01-input.png", LM, 1.45, 7.2, 4.5, border=True)
text(s, LM, 6.05, 7.2, 0.35, "演示系统报价输入页（开发集合成示例）", size=11, color=MUTED, margin=0)
rx = LM + 7.6
rw = CW - 7.6
items = [("计泡口径不一", "货代空派、海派按 ÷6000，快递按 ÷5000 或 139 in³/lb"),
         ("不含项目藏在脚注", "查验费、滞港费、私人住宅派送费、高货值加价"),
         ("包税口径模糊", "正文写“双清包税”，备注写“关税实报实销”")]
for i, (h_, b_) in enumerate(items):
    y = 1.45 + i * 1.55
    text(s, rx, y, 0.6, 0.6, str(i + 1), size=30, bold=True, color=ACCENT, margin=0)
    text(s, rx + 0.6, y + 0.05, rw - 0.6, 0.45, h_, size=18, bold=True, margin=0)
    text(s, rx + 0.6, y + 0.55, rw - 0.6, 0.9, b_, size=15, margin=0)

# ================================================================ 第 4 页 纠纷与执法
s = new(4, "纠纷集中在运费，执法在收紧",
        note="纠纷的主要对象是运费本身，目的地以美国为主，所以选美国头程报价作为切入点。"
             "来源：宁波海事法院白皮书（2024-11）；CBP 征税数据（supplychain247 报道）；雨果网卖家案例。")
cols = [("79.3%", "代理运费争议占比",
         "宁波海事法院 2021 年至 2024 年 6 月结案的 400 件跨境电商物流案件（标的额 1.58 亿元）中，"
         "代理运费是最主要的争议焦点；目的港为美国的占 74.5%"),
        ("52.2%", "此类案件占比上升",
         "跨境电商物流案件在货代与多式联运案件中的占比，从 9.8% 升到 52.2%"),
        ("2.46 亿件", "原免税小包已被征税",
         "截至 2025-12，CBP 已对 2.46 亿多件原免税小包征税超过 10 亿美元；2026 年卖家社区报道 5H、9H 扣货与低申报补单证案例")]
cw3 = (CW - 0.6) / 3
for i, (big, lab, body) in enumerate(cols):
    x = LM + i * (cw3 + 0.3)
    rect(s, x, 1.55, cw3, 4.6, fill=PANEL)
    text(s, x + 0.3, 1.8, cw3 - 0.6, 1.0, big, size=36, bold=True, color=ACCENT, margin=0)
    text(s, x + 0.3, 2.85, cw3 - 0.6, 0.5, lab, size=17, bold=True, margin=0)
    text(s, x + 0.3, 3.45, cw3 - 0.6, 2.6, body, size=15, margin=0)
text(s, LM, 6.35, CW, 0.4, "来源：宁波海事法院跨境电商物流纠纷白皮书（2024-11）；CBP 公布数据；雨果网卖家案例报道",
     size=11, color=MUTED, margin=0)

# ================================================================ 第 5 页 时间线
s = new(5, "2025–2026 年美国进口规则多次变化，报价口径随之混乱",
        note="税率每次变化，包税报价的成本底线随之变化。系统把税率做成带日期的参数快照，按报价日期选用；"
             "这也是第 9 页包税合理性判定的依据。税率与日期以 USITC HTS 2026 Rev 20 与 Federal Register 原文为准。")
nodes = [("2025-08-29", "暂停所有国家的小额豁免"),
         ("2026-02-20", "最高法院判定 IEEPA 关税无效，02-24 起停征"),
         ("2026-02-24", "第 122 条临时关税 10%，至 07-23 到期"),
         ("2026-07-24", "新 301 关税对华加征 12.5%，与原 301 叠加"),
         ("2026-10-08", "CBP 发布 Low-Value Shipments 拟议规则"),
         ("2027-07-01", "小额豁免在法律上永久废除")]
ly = 3.9
ln = s.shapes.add_connector(1, Inches(LM + 0.3), Inches(ly), Inches(W - LM - 0.3), Inches(ly))
ln.line.color.rgb = INK
ln.line.width = Pt(1.5)
step = (CW - 0.6) / (len(nodes) - 1)
for i, (d, t) in enumerate(nodes):
    cx = LM + 0.3 + i * step
    dot = rect(s, cx - 0.09, ly - 0.09, 0.18, 0.18, fill=ACCENT if i == 3 else INK, shape=MSO_SHAPE.OVAL)
    up = i % 2 == 0
    bx = min(max(cx - 1.15, LM), W - LM - 2.3)
    if up:
        text(s, bx, 1.75, 2.3, 0.4, d, size=15, bold=True, align=PP_ALIGN.CENTER, margin=0)
        text(s, bx, 2.15, 2.3, 1.4, t, size=14, align=PP_ALIGN.CENTER, margin=0)
    else:
        text(s, bx, 4.25, 2.3, 0.4, d, size=15, bold=True, align=PP_ALIGN.CENTER, margin=0)
        text(s, bx, 4.65, 2.3, 1.4, t, size=14, align=PP_ALIGN.CENTER, margin=0)
text(s, LM, 6.35, CW, 0.4, "来源：USITC HTS 2026 Rev 20；91 FR 9339、91 FR 47318、91 FR 64532；白宫 2026-02 总统令",
     size=11, color=MUTED, margin=0)

# ================================================================ 第 6 页 工具对照
s = new(6, "现有工具没有覆盖中文非结构化报价的审计",
        note="结构化运价平台把包税条款留在自由文本里；CostClaw 面向英文海运进口商；通用大模型能读中文报价，"
             "但给不出可核对的计算和出处。需求证据来自司法统计、海关数据、政策原文、真实报价与工具对照五类，没有用问卷和访谈。"
             "表中只写已核实的定位，来源见方案“来源”一节。")
rows = [["工具", "已核实的定位", "与本项目的差别"],
        ["Freightos、亚马逊 AGL、5688 运价 API", "提供结构化报价或订舱", "包税条款只写在自由文本里，不做条款审计"],
        ["Flexport MCP", "大模型助手查询自家运价", "只覆盖自家运价"],
        ["CostClaw", "报价归一与发票审核", "面向英文海运进口商"],
        ["通用大模型", "能读中文报价并回答", "给不出可复算的成本、原文出处与证据状态（即对照组 M0）"],
        ["运价明镜", "中文多模态报价审计", "核价、包税合理性、资质证据、出处核对、拒答与追问"]]
table(s, LM, 1.6, CW, rows, [3.6, 3.4, CW - 7.0], size=15, row_h=0.62)

# ================================================================ 第 7 页 架构
s = new(7, "系统架构：大模型负责理解，程序负责计算与判定",
        note="灰底的三个模块调用大模型：视觉转写、字段抽取、跨模型复核；其余全部是确定性程序。"
             "这样分工的原因：成本和合规判定要能复算，交给程序；条款理解交给大模型，并用出处核对和复核约束它。"
             "税率、汇率、承运商规则、登记库都是带日期的快照，可以替换。"
             "数据合规：报价不含个人信息；模型接口可换成国内服务商或本地部署模型；评测数据中货代名称匿名化。")
image(s, FIG / "fig1_architecture.png", LM, 1.3, CW, 5.6)

# ================================================================ 第 8 页 抽取与复核
s = new(8, "关键技术一：抽取有出处，复核找冲突", tag="合成示例",
        note="出处让卖家和评委都能逐条核对；复核把“模型漏看”转成可检出的冲突。复核复用对方配置已有的抽取，不另发请求。")
image(s, SHOT / "02-fields.png", LM, 1.45, 6.0, 3.75, border=True)
image(s, SHOT / "03-conflicts.png", LM + 6.3, 1.45, 6.0, 3.75, border=True)
text(s, LM, 5.25, 6.0, 0.3, "字段抽取与原文出处", size=11, color=MUTED, margin=0)
text(s, LM + 6.3, 5.25, 6.0, 0.3, "冲突与缺口", size=11, color=MUTED, margin=0)
text(s, LM, 5.65, CW, 1.3,
     ["每份报价抽取 18 个条款，每个条款附原文片段；片段不是原文子串的条款降为缺失",
      "另一家模型独立抽取全部报价，两次取值不同记为冲突。开发集预试验中，含风险的 14 个报价有 11 个因首次抽取漏抽而未触发风险条件"],
     size=15, bullets=True, margin=0, space=4)

# ================================================================ 第 9 页 核价与门控
s = new(9, "关键技术二：确定性核价与风险门控",
        note="系统只标出不一致之处和应索要的单证，不判定货代违法。核价由 Python 引擎与 Excel 公式（LibreOffice 重算）两套独立实现交叉核对。")
cw3 = (CW - 0.6) / 3
card(s, LM, 1.45, cw3, 5.3, "确定性核价",
     ["计费重：每箱取实重、体积重、单箱最低计费重的最大值，按步长进位后求和，再与整票最低计费重取大",
      "成本 = 运费 + 附加费 + 关税 + MPF（海运加 HMF）+ 报关费",
      "查验费等或然费用单列为风险敞口",
      "Python 与 Excel 两套实现交叉核对"], size=15)
card(s, LM + cw3 + 0.3, 1.45, cw3, 5.3, "包税合理性",
     ["税费下限 =（关税 + MPF，海运加 HMF）÷ 整票计费重",
      "包税单价低于税费下限：高",
      "低于税费下限加不包税运价参照下限：需核实",
      "标出风险时列出应索要的单证：IOR 名称、CBP 7501、ISF 申报确认、申报价值发票"], size=15)
card(s, LM + 2 * (cw3 + 0.3), 1.45, cw3, 5.3, "资质证据",
     ["宣称与登记库查询快照比对：FMC OTI 名单、海关企业信用公示平台",
      "四种状态：有证据、证据不足、与宣称矛盾、与本次运输不相关",
      "空运援引海运执照、认证主体与报价主体不一致，记为不相关"], size=15)

# ================================================================ 第 10 页 追问
s = new(10, "关键技术三：信息不足时拒答并追问", tag="合成示例",
        note="通用大模型面对信息不足的报价通常仍会给出一个选择；拒答质量和误拒率在第 16 页单独报告。"
             "右图为演示系统案例 A 的决策护照：报价 4 的整票最低计费重前后不一致，取值会改变最优选择，系统输出追问。")
steps = [("1", "可行集", "满足时效、可送达、包税风险不为高"),
         ("2", "最低确定成本", "在可行集内取确定到仓成本最低者"),
         ("3", "取值网格检验", "缺失或冲突条款逐值代入，例如计泡系数取 5000 或 6000"),
         ("4", "推荐或追问", "最优解会改变时不推荐，生成可直接发给货代的追问清单")]
for i, (n_, h_, b_) in enumerate(steps):
    y = 1.5 + i * 1.3
    rect(s, LM, y, 0.55, 0.55, fill=ACCENT if i == 3 else INK, shape=MSO_SHAPE.OVAL)
    text(s, LM, y, 0.55, 0.55, n_, size=16, bold=True, color=WHITE, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE, margin=0)
    text(s, LM + 0.75, y - 0.02, 4.3, 0.4, h_, size=17, bold=True, margin=0)
    text(s, LM + 0.75, y + 0.4, 4.3, 0.8, b_, size=14, margin=0)
image(s, SHOT / "06-passport.png", LM + 5.3, 1.45, CW - 5.3, 5.3, border=True)

# ================================================================ 第 11 页 评测集
s = new(11, "两套评测集：真实报价集与反事实任务集",
        note="决策类成效来自合成任务，在结果页标为“合成任务估算”；真实报价集补上真实场景下的抽取证据。"
             "测试集在 M0–M4 提示词与冻结协议定稿后以种子 20261011 生成并记录哈希，生成后不查看金标签调整任何方法。")
image(s, FIG / "fig3_dataflow.png", LM, 1.3, 8.6, 5.4)
text(s, LM + 8.9, 1.5, CW - 8.9, 5.2,
     [[("真实报价集", {"bold": True})], "10 份公开原件、7 个发布主体，28 个报价单元（货代 22，承运商 6）",
      [("反事实任务集", {"bold": True})], "72 个任务，四层难度；开发集 24 个只用于调试",
      "测试集 48 个，用独立模板，提示词冻结后生成并记录哈希",
      "标准答案由两套独立计算器给出，差额不超过 0.01 元"], size=14, margin=0, space=6)

# ================================================================ 第 12 页 标注
s = new(12, "真实报价集的标签经人工全量审定",
        note="原规范要求两名标注人从头人工标注，实际改为模型初标加人工全量审定，见偏离记录第 1 条（docs/protocol/偏离记录.md）；"
             "金标签以人工全量审定为准。两份初标之间的一致率不上页面，它反映的是模型初标之间的一致性，与人工标注一致性含义不同。"
             "全部标签记录哈希之后才运行模型。")
image(s, FIG / "fig4_annotation.png", LM, 1.3, CW, 4.1)
rect(s, LM, 5.6, CW, 1.2, fill=PANEL)
text(s, LM + 0.3, 5.72, CW - 0.6, 1.0,
     [[("盲标子集：裁决人对两份初标各自的逐字段改正率（改正字段数 / 270）　", {}), (TBD, {"color": ACCENT})],
      [("其余 13 个单元：标注人 A 复核改正字段数 / 字段总数　", {}), (TBD, {"color": ACCENT})]],
     size=15, margin=0, space=4)

# ================================================================ 第 13 页 方法阶梯
s = new(13, "方法阶梯：逐级加组件，看每一层的作用",
        note="M1 与 M4 拿到同样的检查清单、参数快照和同样轮数的提示词调试，两者只差架构，用来回答“只改提示词够不够”。"
             "M0–M4 的提示词在测试集生成前冻结。选型依据：都是国内服务商接口；强、低两档按厂商配对，用于主终点 3；"
             "GLM-5.3 与 Kimi-K2.6 扩大强模型档覆盖面并互为复核方。GLM-5.3 不能关闭思考，用最小思考预算并单独注明。")
image(s, FIG / "fig2_method_ladder.png", LM, 1.3, 7.0, 5.5)
rows = [["档位", "模型（服务商）"],
        ["强模型", "DeepSeek-V4-Pro（DeepSeek 官方）"],
        ["", "qwen3.8-max-0902（阿里云百炼）"],
        ["", "GLM-5.3（硅基流动）"],
        ["", "Kimi-K2.6（硅基流动）"],
        ["低成本", "deepseek-flash（DeepSeek 官方）"],
        ["", "qwen-plus（阿里云百炼）"]]
table(s, LM + 7.3, 1.5, CW - 7.3, rows, [0.95, CW - 7.3 - 0.95], size=14, row_h=0.5)
text(s, LM + 7.3, 5.3, CW - 7.3, 1.2, "另设 deepseek-flash 开启思考的 M1 组，对应卖家在网页端开启深度思考的用法",
     size=14, color=MUTED, margin=0)

# ================================================================ 第 14 页 实验设计
s = new(14, "实验在冻结协议下执行，主终点事前指定",
        note="结果出来后按事前决策规则收窄或调整主张，不临时改主线；规则表在备用页 B3。"
             "每次调用记录模型、参数、提示词哈希、随机种子与原始响应；调用失败一律按错误计，不从分母中剔除。"
             "冻结后的全部偏离逐条列在 docs/protocol/偏离记录.md（共 9 条）；其中 DeepSeek-Flash 思考组全部失败（第 8 条）"
             "与硅基流动停批续跑（第 9 条）直接影响结果页的分母说明。")
rows = [["实验", "问题", "主要指标"],
        ["E1 报价理解与核价", "能否算对到仓成本并作出正确决策", "决策正确率、字段与出处准确率、核价误差、决策遗憾"],
        ["E2 风险与证据", "能否识别隐性费用、包税风险和无依据宣称", "风险项检出 micro-F1、包税风险精确率与召回率、证据状态准确率"],
        ["E3 复核机制", "选择性复核值不值得", "复核比例、纠错数量、决策正确率与成本"]]
table(s, LM, 1.5, 7.3, rows, [1.9, 2.55, 2.85], size=13, row_h=0.95)
rect(s, LM + 7.6, 1.5, CW - 7.6, 3.8, fill=PANEL)
text(s, LM + 7.85, 1.65, CW - 8.1, 3.6,
     [[("三个主终点（Holm 族，α = 0.05）", {"bold": True, "size": 16})],
      "① 决策正确率：M4 对 M0，非显式 36 个任务，置换检验，双侧",
      "② 风险检出 micro-F1：M4 对 M0，48 个任务，置换检验，双侧",
      "③ 低成本档 M4 对同厂商强模型档 M0：非劣效界值 −5 个百分点，整簇 bootstrap，单侧",
      "其余比较只作描述，给 95% 置信区间"], size=14, margin=0, space=6)
text(s, LM, 5.6, CW, 1.2,
     ["冻结协议 sha256：29a08030…37ac8（docs/protocol/冻结记录.md），冻结于 2026-10-11",
      "冻结后的偏离逐条列在 docs/protocol/偏离记录.md，共 9 条"], size=13, color=MUTED, margin=0, space=3)

# ================================================================ 第 15 页 项目实施
s = new(15, "项目实施：排期、分工与资源",
        note="每个工作块都有提交记录与日志，正式实验按冻结协议的命令序列执行，原始记录不手工改动。排期为北京时间，"
             "定稿前按 PROJECT_LOG 与提交记录核对。")
sched = [("10-09", "方案定稿、数据结构、参数快照与两套计算器"),
         ("10-10", "真实报价单元整理、任务生成与渲染、M0–M4 实现、预试验"),
         ("10-11", "冻结协议并生成测试集"),
         ("10-12", "启动正式实验；真实报价集审定与复核"),
         ("其后", "评分、报告、演示与视频；完成日期" + TBD)]
for i, (d, t) in enumerate(sched):
    y = 1.55 + i * 0.98
    text(s, LM, y, 1.1, 0.45, d, size=17, bold=True, color=ACCENT if i == 4 else INK, margin=0)
    text(s, LM + 1.2, y, 5.6, 0.9, t, size=15, margin=0)
card(s, LM + 7.1, 1.45, CW - 7.1, 2.9, "分工",
     ["项目负责人：方案、实验协议与结果解读",
      "新队友（标注人 A）：13 个单元逐字段复核",
      "整理原件的队友：真实报价采集、匿名化、盲标子集全量审定",
      "编码、测试与实验执行在 Claude Code 中完成，提交前独立审查"], size=14)
rect(s, LM + 7.1, 4.6, CW - 7.1, 2.15, fill=PANEL)
text(s, LM + 7.35, 4.8, CW - 7.6, 0.45, "资源", size=17, bold=True, margin=0)
text(s, LM + 7.35, 5.3, CW - 7.6, 1.4,
     ["3 家国内服务商、6 个模型配置",
      [("预算上限 300 元，实际支出　", {}), (TBD, {"color": ACCENT})],
      [("代码测试　", {}), (TBD, {"color": ACCENT})]], size=14, bullets=True, margin=0, space=3)

# ================================================================ 第 16–20 页 结果
s = new(16, "E1 决策正确率", sub="标题定稿时按事前决策规则选用结论句", tag="合成任务估算",
        note="标题写法：主终点 1 显著时写“完整系统把决策正确率从（M0）提高到（M4）（非显式三层，6 个配置合并）”；"
             "不显著时写“决策正确率差异未达显著，系统增量集中在风险识别与追问”，并照实报告各层正确率。"
             "若 M0 在显式层正确率 ≥ 90%：本页主图只放隐含、冲突、信息不足三层，显式层移到备用页；"
             "第 19 页真实报价集结果提前到本页之后。强调信息不足层的正确拒答率与非决定性任务上的误拒率同时报告。")
placeholder(s, LM, 1.7, 8.0, 5.0, "分层柱状图：四层 × M0–M4，误差线为 95% CI（灰度）")
placeholder(s, LM + 8.3, 1.7, CW - 8.3, 2.3, "主终点 1：M4 − M0 差值与 Holm 校正后 p 值")
placeholder(s, LM + 8.3, 4.3, CW - 8.3, 2.4, "拒答率与误拒率；决策遗憾（元/票与占最优成本比例）")

s = new(17, "E2 风险与证据", sub="标题定稿时按事前决策规则选用结论句", tag="合成任务估算",
        note="标题写法：主终点 2 显著时写“完整系统的风险检出 micro-F1 为（M4），M0 为（M0）”；"
             "不显著时写“风险检出差异未达显著”，做错误类型分析，主张收窄为核价与决策。"
             "若 M4 风险标记精确率 < 0.7：加一个精确率与召回率权衡的小图，说明阈值选择依据。"
             "讲稿：从测试集原始记录中选一例资质宣称的判定，对比 M0 与 M4 的输出，注明任务编号与配置。")
placeholder(s, LM, 1.7, 6.0, 5.0, "风险项检出 micro-F1：M0–M4，6 个配置合并")
placeholder(s, LM + 6.3, 1.7, CW - 6.3, 5.0,
            "隐性费用召回率、冲突与缺口 F1、包税风险精确率与召回率、资质证据状态准确率、无依据宣称采纳率")

s = new(18, "成本：低成本模型加系统对比强模型直接使用", sub="标题定稿时按事前决策规则选用结论句", tag="合成任务估算",
        note="标题写法：达到非劣效时写“低成本模型加完整系统，决策正确率不低于强模型直接使用，每次审计成本为（元）”；"
             "未达到时写“按每个正确决策的调用成本比较”，照实报告两档的正确率与成本。"
             "每百票多付金额 =（M0 平均决策遗憾 − M4 平均决策遗憾）× 100，遗憾均值只计正确答案为推荐、且方法选中可行集内报价的任务，"
             "页面注明任务数，可行集外选择比例另列。千问按官方标价折算，单独标注。")
placeholder(s, LM, 1.7, 7.0, 5.0, "散点图：横轴每次审计调用成本（元），纵轴决策正确率；6 个配置 × M0、M4")
placeholder(s, LM + 7.3, 1.7, CW - 7.3, 2.3, "主终点 3：两对差值均值、单侧 95% 下界、界值 −5 个百分点")
placeholder(s, LM + 7.3, 4.3, CW - 7.3, 2.4, "每次审计 token、费用、时延；每百票多付金额及其口径")

s = new(19, "真实报价集：抽取与缺口识别", sub="标题定稿时按事前决策规则选用结论句", tag="真实数据",
        note="标题写法：字段准确率 ≥ 0.8 时写“真实报价上的字段准确率为（值），出处准确率为（值）”；"
             "< 0.8 时写“真实报价的字段准确率为（值），主要错误集中在（条款）”，并把错误类型作为改进方向。"
             "真实单元的原文不进入任何提示词或词表；这一页是对合成任务结果的外部检验。")
placeholder(s, LM, 1.7, 7.0, 5.0, "按配置的字段准确率与出处准确率，按原件整簇 bootstrap 的 95% CI；承运商公布价单列")
placeholder(s, LM + 7.3, 1.7, CW - 7.3, 5.0, "按条款的错误分布；用于提炼费用结构的单元与未用过的单元分开报告")

s = new(20, "E3 与消融：每一层带来了什么", sub="逐级差值为次要结果，只作描述，给 95% 置信区间", tag="合成任务估算",
        note="M1 对 M0：只改提示词的作用；M2 对 M1：程序核价替代模型自算（同时改为逐份抽取，提示词中不再给规则）；"
             "M3 对 M2：门控的作用；M4 对 M3：出处、复核与追问的作用。若 M4 对 M2 的核价绝对误差差值的 95% CI 包含 0，"
             "写“核价改进有限，系统增量来自风险识别与追问”。若 deepseek-flash 开启思考的 M1 点估计 ≥ 同模型 M4，照实报告，"
             "主张改为 M4 在成本、时延与可核对出处上的优势。回答“这不就是运费计算器吗”：计算器只是 M2。")
placeholder(s, LM, 1.7, 7.0, 5.0, "消融阶梯：M0 → M4 每一步的决策正确率与 micro-F1 增量")
placeholder(s, LM + 7.3, 1.7, CW - 7.3, 5.0, "E3：全部复核与只复核触发报价的复核比例、纠错数量、决策正确率与成本")

# ================================================================ 第 21 页 总结
s = new(21, "总结、局限与展望", note="结束语回到第 2 页的一句话结论。")
cw3 = (CW - 0.6) / 3
card(s, LM, 1.45, cw3, 5.3, "成果",
     ["可运行的审计系统（演示见视频）", "可复算的评测集与生成器", "冻结协议与全部原始记录"], size=15)
card(s, LM + cw3 + 0.3, 1.45, cw3, 5.3, "局限",
     ["决策类成效来自合成任务",
      "真实报价集 28 个单元，原件为网页正文与 PDF，没有图片原件",
      "盲标子集初标由 OpenAI Codex 生成、人工全量审定（偏离记录第 1 条）",
      "计费重只支持单一进位步长",
      "资质证据依赖人工查询的登记库快照",
      "税率为 2026-10-09 快照"], size=15)
card(s, LM + 2 * (cw3 + 0.3), 1.45, cw3, 5.3, "展望",
     ["接入卖家 ERP 与货代比价平台作审核模块", "税率与承运商规则按周更新快照", "扩展到欧盟头程、海外仓尾程与更多渠道",
      "收集卖家试用记录检验实际节省"], size=15)

# ================================================================ 备用页
s = new("B1", "备用 B1　评委追问与回答", note="答辩追问时调出。")
rows = [["追问", "回答要点", "见"],
        ["这不就是运费计算器吗？", "计算器只是 M2；M3、M4 相对 M2 的增量是理解条款和识别风险的价值", "第 20 页"],
        ["数据是自己编的吧？", "真实报价集来自公开价格页；反事实集费用结构取自真实报价和官方规则，测试集在提示词冻结后生成", "第 11 页"],
        ["规则由你们定义，M4 又内置同一规则？", "M1 拿到同样的规则、参数快照和调试轮数；测试集模板与开发集分离", "第 13 页"],
        ["能判断货代违法吗？", "不判断，只标出不一致之处并列出应索要的单证", "第 9 页"],
        ["关税变化这么快怎么跟上？", "税率是带日期的参数快照，按报价日期选用，可以替换", "第 5 页"],
        ["模型更新后能复现吗？", "记录模型名、服务商、日期、参数、提示词哈希和原始响应", "第 14 页"],
        ["数据合规怎么处理？", "报价不含个人信息；可接国内服务商或本地部署模型；货代名称匿名化", "第 7 页"],
        ["有没有用户？", "对照组 M0 就是卖家现在的做法；真实报价集来自市场", "第 3 页"]]
table(s, LM, 1.4, CW, rows, [3.6, CW - 4.7, 1.1], size=13, row_h=0.6)

s = new("B2", "备用 B2　参数快照：四类商品的合计税率",
        note="选旅行袋（4202.92.3131）而不选双肩包：常见尺寸的化纤双肩包（4202.92.3120）在 2026-11-09 前排除原 301 清单 3 关税，"
             "税率随尺寸变化；4202.92.3131 没有现行排除项。")
rows = [["商品", "HTS", "MFN", "原 301", "新 301", "合计"],
        ["棉制 T 恤", "6109.10.00", "16.5%", "清单 4A，7.5%", "12.5%", "36.5%"],
        ["塑料手机壳", "3926.90.99", "5.3%", "清单 4A，7.5%", "12.5%", "25.3%"],
        ["化纤旅行袋", "4202.92.3131", "17.6%", "清单 3，25%", "12.5%", "55.1%"],
        ["玩具", "9503.00.00", "免税", "清单 4B，已暂停", "12.5%", "12.5%"]]
table(s, LM, 1.6, CW, rows, [2.3, 2.3, 1.6, 2.9, 1.6, CW - 10.7], size=15, row_h=0.6)
text(s, LM, 4.9, CW, 0.5, "来源：USITC HTS 2026 Rev 20；选品避开 232 商品", size=12, color=MUTED, margin=0)

s = new("B3", "备用 B3　事前决策规则", note="定稿时标出本次实际触发的行。")
rows = [["情况", "处理"],
        ["主终点 1 经 Holm 校正后不显著", "主张收窄为“风险识别与追问”，决策正确率照实报告"],
        ["主终点 2 经 Holm 校正后不显著", "主张收窄为“核价与决策”，风险检出照实报告并做错误类型分析"],
        ["主终点 3 未达到非劣效", "成本主张改为“每个正确决策的调用成本”对比"],
        ["M0 显式层决策正确率 ≥ 90%", "成效一节以非显式三层和真实报价集为主"],
        ["M4 对 M2 核价绝对误差差值的 95% CI 包含 0", "核价改进照实写，系统增量归于风险识别与追问"],
        ["M4 风险标记精确率 < 0.7", "报告精确率与召回率的权衡，给出阈值选择依据"],
        ["M1 思考组正确率点估计 ≥ M4（deepseek-flash）", "主张改为 M4 在成本、时延和可核对出处上的优势"],
        ["真实报价集字段准确率 < 0.8", "照实报告并分析错误类型，作为改进方向"]]
table(s, LM, 1.4, CW, rows, [5.4, CW - 5.4], size=13, row_h=0.56)

s = new("B4", "备用 B4　统计细节", note="")
rect(s, LM, 1.45, CW, 2.5, fill=PANEL)
text(s, LM + 0.3, 1.7, CW - 0.6, 2.2,
     ["置换检验以任务为单位，同一任务在 6 个配置中的方法标签一起交换",
      "任务数超过 16 时蒙特卡洛 99,999 次；bootstrap 9,999 次；种子 20261011",
      "主终点 3 以按任务整簇 bootstrap 为主检验，Tango 检验为敏感性分析",
      "调用失败按决策错误、风险未检出计，失败数按配置与方法列表"], size=16, bullets=True, margin=0, space=8)

s = new("B5", "备用 B5　实验规模与费用", note="千问使用免费额度，费用按官方标价折算，单独列示。数据取自 runs/spend_ledger.jsonl。")
rows = [["服务商", "配置", "请求数", "token", "实际支出（千问为折算）"],
        ["DeepSeek 官方", "ds_pro、ds_flash、ds_flash_think", TBD, TBD, TBD],
        ["阿里云百炼", "qwen_max、qwen_plus、qwen_vl", TBD, TBD, TBD],
        ["硅基流动", "glm、glm_v、kimi", TBD, TBD, TBD],
        ["合计", "预算上限 300 元", TBD, TBD, TBD]]
table(s, LM, 1.6, CW, rows, [2.1, 3.9, 1.7, 1.7, CW - 9.4], size=14, row_h=0.62)

s = new("B6", "备用 B6　演示截图", tag="合成示例", note="六张截图依次为报价输入、抽取与出处、冲突与缺口、包税风险与资质证据、到仓成本对比、决策护照与追问清单。")
shots = ["01-input", "02-fields", "03-conflicts", "04-gate", "05-cost", "06-passport"]
gw3, gh3 = (CW - 0.4) / 3, 2.5
for i, name in enumerate(shots):
    x = LM + (i % 3) * (gw3 + 0.2)
    y = 1.4 + (i // 3) * (gh3 + 0.3)
    image(s, SHOT / f"{name}.png", x, y, gw3, gh3, border=True)

prs.save(OUT)
print(OUT)
