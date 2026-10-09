// EchoGuard defense deck v15: v12 reordered for bank judges (business numbers first, positioning on slide 3, two slides to backup). Based on v12. Same content as v5, restyled to match the
// review console: warm neutral background, one accent, serif type (Noto Serif CJK SC), rules
// instead of filled cards, grayscale-friendly charts. Neutral third-person wording on slides.
// Usage: node build_deck_v7.js out.pptx   (images from DECK_ASSETS or ./assets)
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.PPTX_SKILL_THEME || "/mnt/skills/public/pptx/scripts/apply_theme.js");

const ASSETS = process.env.DECK_ASSETS || path.join(__dirname, "assets");
const img = (f) => path.join(ASSETS, f);
const aspect = (f) => { const b = fs.readFileSync(img(f)); return b.readUInt32BE(16) / b.readUInt32BE(20); };

const SERIF = "Noto Serif CJK SC";
const SANS = "Microsoft YaHei";
const THEME = {
  name: "EchoGuard Ivory",
  headFontFace: SERIF,
  bodyFontFace: SERIF,
  colors: {
    dk1: "23211D", lt1: "FFFFFF", dk2: "4B4740", lt2: "F7F5F0",
    accent1: "2E2B26", accent2: "8E3B23", accent3: "B9B3A6", accent4: "868176",
    accent5: "DAD5CA", accent6: "4B4740", hlink: "8E3B23", folHlink: "4B4740",
  },
};
const HEX = THEME.colors;
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: SERIF, bodyFontFace: SERIF };
pres.title = "同模共振 EchoGuard 答辩";
pres.author = "";
pres.subject = "同模共振 EchoGuard：财富管理 AI 模型上线前行为测试与复核工具";
const C = pres.SchemeColor;

pres.defineSlideMaster({
  title: "COVER",
  background: { color: C.background2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 2.05, w: 6.2, h: 1.6, fontSize: 40, bold: true, color: C.text1, valign: "top", align: "left" }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.7, y: 3.7, w: 6.0, h: 0.9, fontSize: 19, color: C.text2, valign: "top" }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background2 },
  margin: [0.4, 0.6, 0.6, 0.6],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.52, w: 12.1, h: 0.78, fontSize: 26, bold: true, color: C.text1, valign: "middle", align: "left" }, text: "" } },
    { line: { x: 0.6, y: 6.95, w: 12.1, h: 0, line: { color: HEX.accent5, width: 0.75 } } },
    { text: { text: "同模共振 EchoGuard · 第十七届“工行杯”", options: { x: 0.6, y: 7.02, w: 6, h: 0.3, fontSize: 9.5, color: C.accent4, fontFace: SANS } } },
  ],
  slideNumber: { x: 12.2, y: 7.02, w: 0.5, h: 0.3, fontSize: 9.5, color: C.accent4, align: "right", fontFace: SANS },
});

let n = 0;
function content(title, section) {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  s.addText(section, { isTextBox: true, x: 0.6, y: 0.22, w: 6, h: 0.3, fontSize: 11, color: C.accent4, fontFace: SANS, charSpacing: 2, margin: 0 });
  s.addText(title, { placeholder: "title" });
  n++;
  return s;
}
function txt(s, text, o) {
  s.addText(text, Object.assign({ isTextBox: true, fontSize: 15, color: C.text1, valign: "top", margin: 0.03 }, o));
}
function rule(s, x, y, w, color, width) {
  s.addShape(pres.shapes.LINE, { x, y, w, h: 0, line: { color: color || HEX.dk1, width: width || 1.25 }, objectName: "rule" });
}
// A block: rule on top, small sans label, then content placed by the caller.
function block(s, x, y, w, label, opts = {}) {
  rule(s, x, y, w, opts.accent ? HEX.accent2 : HEX.dk1, opts.width || 1.25);
  if (label) txt(s, label, { x, y: y + 0.08, w, h: 0.32, fontSize: 11.5, bold: true, color: opts.accent ? C.accent2 : C.text2, fontFace: SANS, margin: 0 });
}
function idx(s, x, y, label, accent) {
  txt(s, label, { x, y, w: 0.6, h: 0.42, fontSize: 18, bold: true, color: accent ? C.accent2 : C.accent4, margin: 0, valign: "middle" });
}
function shot(s, file, x, y, w, h, name) {
  s.addShape(pres.shapes.RECTANGLE, { x: x - 0.04, y: y - 0.04, w: w + 0.08, h: h + 0.08, fill: { color: "FFFFFF" }, line: { color: HEX.accent5, width: 0.75 }, objectName: name + "-frame" });
  s.addImage({ path: img(file), x, y, w, h, altText: name, objectName: name });
}
function source(s, text) {
  txt(s, text, { x: 0.6, y: 6.55, w: 12.1, h: 0.35, fontSize: 9.5, color: C.accent4, fontFace: SANS, margin: 0 });
}
const HAIR = { type: "solid", pt: 0.5, color: HEX.accent5 };
const INK = { type: "solid", pt: 1, color: HEX.dk1 };
const NONE = { type: "none" };
function table(s, head, rows, o) {
  const H = head.map((t) => ({ text: t, options: { bold: true, fontFace: SANS, fontSize: (o.fontSize || 12.5) - 1.5, color: C.text2, border: [NONE, NONE, INK, NONE] } }));
  const B = rows.map((r, i) => r.map((t, j) => ({ text: t, options: Object.assign({ color: C.text1, border: [NONE, NONE, i === rows.length - 1 ? INK : HAIR, NONE] }, j === 0 ? { bold: true } : {}, (o.align && o.align[j]) ? { align: o.align[j] } : {}) })));
  s.addTable([H, ...B], Object.assign({ fontSize: 12.5, valign: "middle", margin: [0.04, 0.06, 0.04, 0.0] }, o));
}
const chartText = { catAxisLabelColor: HEX.dk2, valAxisLabelColor: HEX.accent4, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
  dataLabelFontFace: "+mn-lt", legendFontFace: "+mn-lt", titleFontFace: "+mn-lt", catAxisLabelFontSize: 12, valAxisLabelFontSize: 10.5,
  dataLabelFontSize: 10.5, dataLabelColor: HEX.dk1, valGridLine: { color: "E6E2D9", size: 0.5 }, catGridLine: { style: "none" },
  catAxisLineColor: HEX.accent3, valAxisLineShow: false, titleColor: HEX.dk2, titleFontSize: 12.5, legendColor: HEX.dk2 };
const INKC = "2E2B26", GRAYC = "B9B3A6", RUST = "8E3B23";

// 1 Cover ---------------------------------------------------------------------------
pres.addSection({ title: "开场" });
{
  const s = pres.addSlide({ masterName: "COVER", sectionTitle: "开场" });
  txt(s, "第十七届“工行杯”全国大学生金融科技创新大赛　金融安全服务", { x: 0.7, y: 1.45, w: 6.4, h: 0.35, fontSize: 11.5, color: C.accent4, fontFace: SANS, margin: 0 });
  s.addText("同模共振\nEchoGuard", { placeholder: "title" });
  s.addText("财富管理 AI 模型上线前行为测试与复核工具", { placeholder: "body" });
  rule(s, 0.7, 5.2, 5.6, HEX.dk1, 1);
  txt(s, "成员甲　成员乙", { x: 0.7, y: 5.32, w: 6, h: 0.4, fontSize: 15, bold: true, color: C.text1, margin: 0 });
  txt(s, "（学校信息不写入）", { x: 0.7, y: 5.75, w: 6.2, h: 0.4, fontSize: 11.5, color: C.text2, fontFace: SANS, margin: 0 });
  const w = 5.6, h = w / aspect("console_full.png");
  shot(s, "console_full.png", 7.15, 1.55, w, h, "cover-console");
  txt(s, "测试台：复测批次的风险卡", { x: 7.15, y: 1.6 + h + 0.12, w, h: 0.3, fontSize: 10, color: C.accent4, align: "right", fontFace: SANS, margin: 0 });
  s.addNotes("各位评委好，我们是（学校信息不写入）");
  n++;
}

// 2 Example + numbers ------------------------------------------------------------------
{
  const s = content("一个例子：AI 按列表顺序和编号推荐，提示词声明没有显著效果", "开场");
  block(s, 0.6, 1.5, 12.1, "例子");
  txt(s, "同一位高风险客户，分别交给 DeepSeek、千问、Kimi 配置基金。四只沪深 300 ETF 联接基金经济上接近，9 次推荐中有 8 次包含这一组里排在最前的那只；货架顺序一换，被选中的基金公司也跟着换。展示顺序成了 AI 实际的推荐依据。",
    { x: 0.6, y: 1.9, w: 12.1, h: 1.0, fontSize: 17, color: C.text1 });
  const stats = [
    ["约八成", "选排在最前的那只", "同类 4 只选 1 只时：首批 78%、复测 83%，顺序无关时为 25%", true],
    ["约 2,700 万元", "每 1 亿元推荐资金", "去向随排序改变：只接一家模型时，三只首位产品的推荐权重为统一排序 46.4%、逐次随机 19.6%（三家合计，等额换算）", true],
    ["43% 对 16%", "编号两端管理人的单选占比", "逐次随机排序、沿用按管理人顺序的编号时；均分参照为 25%", false],
    ["83%→80%", "提示词声明没有显著效果", "说明“顺序不代表优先级”后首位比例无显著变化（p=0.50）", false],
  ];
  stats.forEach(([big, label, sub, acc], i) => {
    const x = 0.6 + i * 3.1;
    block(s, x, 3.35, 2.85, null);
    txt(s, big, { x, y: 3.5, w: 2.85, h: 0.95, fontSize: 28, bold: true, color: acc ? C.accent2 : C.text1, valign: "middle", margin: 0 });
    txt(s, label, { x, y: 4.5, w: 2.85, h: 0.42, fontSize: 15.5, bold: true, color: C.text1, margin: 0 });
    txt(s, sub, { x, y: 4.95, w: 2.8, h: 1.2, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  source(s, "DeepSeek、千问、Kimi 共 2,841 次真实调用（首批 81、标签对照 72、第二阶段 468、第三阶段 324、第四阶段 1,896）；第二至四阶段的假设与检验方法在调用前确定，主要结论在第二套货架与受控实验中复现。");
  s.addNotes("先看一个例子：同一位高风险客户交给三家模型配置基金，9 次推荐有 8 次包含沪深 300 组里排在最前的那只，顺序一换，选中的基金公司也跟着换。四个数字：同类产品只选一只时，约八成选排在最前的；只接一家模型、所有请求同一顺序时，按客户全部采纳、等额资金换算，每 1 亿元推荐资金约 2,700 万元的去向随排序改变；只做随机排序、编号按管理人顺序编排时，编号两端的管理人单选占比是 43% 对 16%；在提示词里声明顺序无关没有显著效果。");
}

// 5b Positioning ---------------------------------------------------------------------------
{
  const s = content("定位：测试结论通过两条路径影响客户看到的推荐", "开场");
  const w = 10.8, h = w / aspect("fig_position.png");
  s.addImage({ path: img("fig_position.png"), x: (13.333 - w) / 2, y: 1.42, w, h,
    altText: "EchoGuard 与客户侧 AI 应用的关系：上方为面向客户的推荐链路，下方为测试链路；准入审批决定应用能否上线，处置规则写入产品列表接口", objectName: "position" });
  rule(s, 0.6, 6.05, 12.1, HEX.accent2, 1);
  txt(s, "处置都在银行系统一侧实施，不修改外部模型。每次变更后需要重新判断是否处置、处置是否有效，EchoGuard 把这一判断做成可重复执行的测试。",
    { x: 0.6, y: 6.1, w: 12.1, h: 0.42, fontSize: 13, bold: true, color: C.text1, valign: "middle", margin: 0 });
  source(s, "这类应用已在规模化使用：工商银行个人客户经理智能助手服务次数超过 2,200 万次（金融时报《六大行人工智能部署实现规模化落地》）。");
  s.addNotes("EchoGuard 不直接面向客户，结论通过两条路径到达客户：一是准入，测试和复核通过后，理财经理助手、智能客服才上线或变更；二是展示处置，规则写进应用读取产品列表的接口。处置都在银行系统一侧，不改外部模型。每次换模型、改提示词或改列表逻辑后，都要重新判断要不要处置、处置是否有效，EchoGuard 把这一判断做成每次变更都能重复执行的测试。");
}

// 3 Background + requirements -----------------------------------------------------------
pres.addSection({ title: "问题" });
{
  const s = content("背景：银行 AI 应用规模化，监管与标准提出测评要求", "问题");
  block(s, 0.6, 1.5, 3.3, "工商银行");
  txt(s, "600 余个", { x: 0.6, y: 1.9, w: 3.3, h: 0.85, fontSize: 36, bold: true, color: C.text1, margin: 0 });
  txt(s, "AI 落地场景", { x: 0.6, y: 2.75, w: 3.3, h: 0.4, fontSize: 15, bold: true, color: C.text1, margin: 0 });
  txt(s, "“工银智涌”大模型在多个领域落地，并引入了 DeepSeek 系列开源模型。不同渠道接入不同供应商的模型、模型更换期间新旧版本并行时，多家模型会同时服务同类业务。", { x: 0.6, y: 3.25, w: 3.2, h: 2.0, fontSize: 13, color: C.text2, margin: 0 });
  table(s, ["来源", "要求（摘录）", "EchoGuard 中的对应"], [
    ["指导意见（金发〔2026〕8 号）", "建设测试工具链、测评指标和测试用例集", "冻结的测试用例、条件基准与首位比例指标、可复跑执行器"],
    ["金发〔2026〕8 号", "对引入的外部模型建立严格的内部评估框架", "多家外部模型同条件横向测试，风险卡进入准入材料"],
    ["金发〔2026〕8 号", "高风险应用关键环节建立人工监督和干预机制", "证据闸门转人工复核，复核记录留痕"],
    ["金发〔2026〕8 号", "应对投资策略趋同、放大市场波动风险", "条件基准区分客户约束带来的一致与超额趋同；统计三家选中同一只"],
    ["代理销售办法（金规〔2025〕6 号）第三十五条", "同类产品一致的展示规则；不得简单依据业绩高低排序", "检验模型是否把展示位置当作推荐依据"],
    ["JR/T 0221-2021", "模型版本升级留有审计记录，版本更新可追溯", "记录模型 ID、参数、响应 ID；版本回归"],
  ], { x: 4.3, y: 1.5, w: 8.4, colW: [2.2, 3.05, 3.15], fontSize: 12, rowH: [0.38, 0.72, 0.72, 0.72, 0.72, 0.72, 0.72] });
  source(s, "来源：中国新闻网；新华网；金发〔2026〕8 号、金规〔2025〕6 号、JR/T 0221-2021 原文。");
  s.addNotes("工行的大模型已落地 600 余个场景，并引入了外部模型。金融监管总局的指导意见要求建设测试工具链、对外部模型做内部评估、应对投资策略趋同；代销管理办法要求同类产品用一致的展示规则。右边一列是 EchoGuard 中对应的功能。");
}

// 5 Product ------------------------------------------------------------------------------
pres.addSection({ title: "方案" });
{
  const s = content("方案：供模型风险人员使用的测试与复核工具", "方案");
  table(s, ["", ""], [
    ["直接使用者", "银行模型风险管理人员：发起测试、阅读风险卡、组织复核"],
    ["协同复核", "财富业务人员判断经济理由与适当性；科技人员核查提示词、解析与参数"],
    ["使用节点", "上线前评估；模型版本、提示词或展示方式变化后的回归测试；定期复测"],
    ["输入", "合成客户画像、产品货架字段、候选模型接口与参数"],
    ["输出", "风险卡、复核记录、回归报告、原始记录索引"],
    ["最终决定", "银行既有模型准入审批流程"],
  ], { x: 0.6, y: 1.45, w: 7.6, colW: [1.7, 5.9], fontSize: 14, rowH: [0.1, 0.72, 0.72, 0.72, 0.72, 0.72, 0.72] });
  block(s, 8.6, 1.55, 4.1, "接入方式");
  txt(s, [
    { text: "接在模型准入与变更流程中，与生产推荐链路分开", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "数据只有合成画像和产品字段", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "经模型网关调用候选模型", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "重要性下限与方向规则由银行按本行政策配置", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "结论和原始响应一起进入审批", options: { bullet: { indent: 14 } } },
  ], { x: 8.6, y: 2.05, w: 4.1, h: 3.6, fontSize: 14.5, color: C.text1, paraSpaceAfter: 9 });
  s.addNotes("使用者是模型风险管理人员，业务和科技人员协同复核。工具用在三个节点：上线前评估、配置变化后的回归测试和定期复测。数据只有合成画像和产品字段，最终决定由既有准入审批作出。");
}

// 6 Workflow -----------------------------------------------------------------------------
{
  const s = content("流程：自动测试找证据，人下结论；配置变化后做回归", "方案");
  const steps = [
    ["冻结测试条件", "画像、货架、提示词\n模型参数、输入哈希"], ["多模型重复调用", "各家独立接口\n保存原始响应"],
    ["校验与覆盖", "解析、结构、适当性\n推荐 / 弃答 / 失败"], ["行为分解", "条件基准、展示顺序\n画像方向规则"],
    ["证据闸门", "暂缓、证据不足\n复核、观察、通过"], ["人工复核", "测试台、原始记录\n复核记录、重测"],
  ];
  const w = 1.85, gap = 0.2, y = 1.55;
  txt(s, "上线前测试", { x: 0.6, y: 1.35, w: 3, h: 0.3, fontSize: 11, bold: true, color: C.accent4, fontFace: SANS, margin: 0 });
  steps.forEach(([h, b], i) => {
    const x = 0.6 + i * (w + gap), human = i === 5;
    s.addShape(pres.shapes.RECTANGLE, { x, y: y + 0.2, w, h: 1.7, fill: { color: human ? "F3E9E3" : "FFFFFF" }, line: { color: human ? HEX.accent2 : HEX.accent3, width: human ? 1.25 : 0.75 }, objectName: `step-${i}` });
    txt(s, h, { x: x + 0.08, y: y + 0.35, w: w - 0.16, h: 0.5, fontSize: 14.5, bold: true, color: C.text1, align: "center" });
    txt(s, b, { x: x + 0.08, y: y + 0.9, w: w - 0.16, h: 0.9, fontSize: 12, color: C.text2, align: "center" });
    if (i < 5) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + w + 0.03, y: y + 0.97, w: 0.14, h: 0.18, fill: { color: HEX.accent4 }, line: { color: HEX.accent4 }, objectName: "arrow" });
  });
  txt(s, "版本回归", { x: 0.6, y: 3.75, w: 3, h: 0.3, fontSize: 11, bold: true, color: C.accent4, fontFace: SANS, margin: 0 });
  const reg = [["现行配置", "最近一次通过\n复核的批次"], ["候选配置", "同一冻结条件\n只改一个因素"], ["逐条配对", "相同展示顺序下\n比较行为与覆盖"], ["噪声参照", "现行配置复测\n给出正常波动"], ["复核与审批", "超出波动的变化\n写入复核记录"]];
  const w2 = 2.25, gap2 = 0.21, y2 = 4.1;
  reg.forEach(([h, b], i) => {
    const x = 0.6 + i * (w2 + gap2), human = i === 4;
    s.addShape(pres.shapes.RECTANGLE, { x, y: y2, w: w2, h: 1.5, fill: { color: human ? "F3E9E3" : "FFFFFF" }, line: { color: human ? HEX.accent2 : HEX.accent3, width: human ? 1.25 : 0.75 }, objectName: `reg-${i}` });
    txt(s, h, { x: x + 0.08, y: y2 + 0.14, w: w2 - 0.16, h: 0.5, fontSize: 14.5, bold: true, color: C.text1, align: "center" });
    txt(s, b, { x: x + 0.08, y: y2 + 0.65, w: w2 - 0.16, h: 0.8, fontSize: 12, color: C.text2, align: "center" });
    if (i < 4) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + w2 + 0.035, y: y2 + 0.66, w: 0.14, h: 0.18, fill: { color: HEX.accent4 }, line: { color: HEX.accent4 }, objectName: "arrow" });
  });
  source(s, "证据闸门优先级：暂缓 > 证据不足 > 人工复核 > 观察 > 通过；较低级别的信号作为次要信号留在风险卡上。框线加深者为人工步骤。");
  s.addNotes("流程是先固定测试条件，多模型重复调用，检查证据是否完整，再做行为分解，证据不够就不给结论。配置变化后在相同顺序下做版本回归，以复测的正常波动作参照。");
}

// 7 Console -----------------------------------------------------------------------------
{
  const s = content("测试台：在一页里看结论、调下限、查原始响应", "方案");
  const w = 7.1, h = w / aspect("console_full.png");
  shot(s, "console_full.png", 0.64, 1.5, w, h, "console-full");
  const pts = [
    ["选批次、分组与下限", "九个真实批次；调整重要性下限，风险卡即时重算"],
    ["风险卡", "结论、触发规则、次要信号、覆盖统计与对客户结果的影响"],
    ["查看原始响应", "每次推荐的产品、权重、组内位次、模型 ID 与响应 ID"],
    ["导出复核记录", "按风险卡预填证据，复核人填写归类与处理建议"],
    ["在线访问", "sandro826h-dotcom.github.io/echoguard"],
  ];
  pts.forEach(([hh, b], i) => {
    const y = 1.5 + i * 1.0, last = i === 4;
    rule(s, 8.1, y, 4.6, i === 0 ? HEX.dk1 : HEX.accent5, i === 0 ? 1.25 : 0.75);
    idx(s, 8.1, y + 0.1, `0${i + 1}`, i === 1);
    txt(s, hh, { x: 8.75, y: y + 0.12, w: last ? 2.9 : 3.95, h: 0.38, fontSize: 15, bold: true, color: C.text1, margin: 0 });
    txt(s, last ? [{ text: "sandro826h-dotcom.github.io", options: { breakLine: true, hyperlink: { url: "https://sandro826h-dotcom.github.io/echoguard/", tooltip: "在线测试台" } } }, { text: "/echoguard/", options: { hyperlink: { url: "https://sandro826h-dotcom.github.io/echoguard/", tooltip: "在线测试台" } } }] : b,
      { x: 8.75, y: y + 0.5, w: last ? 2.9 : 3.95, h: last ? 0.6 : 0.45, fontSize: last ? 11.5 : 12, color: C.text2, margin: 0 });
  });
  s.addImage({ path: img("qr_console.png"), x: 11.78, y: 5.6, w: 0.9, h: 0.9, altText: "在线测试台二维码：https://sandro826h-dotcom.github.io/echoguard/", objectName: "qr-console" });
  source(s, "静态网页，数据嵌入页面，可离线打开，不调用模型接口。");
  s.addNotes("这是测试台，已经在线发布，扫右下角二维码可以打开。可以选择批次、调整下限，风险卡即时重算；点开任一画像能看到三家模型的原始响应；复核完成后导出复核记录。");
}

// 8 Technique -----------------------------------------------------------------------------
{
  const s = content("技术：四个模块把“相似”拆成可以解释的部分", "方案");
  const blocks = [
    ["条件基准与持续置换", "在经济属性相近的产品组内交换产品身份，算出只按经济条件选择时三家应有多相似；同一模型各次重复使用同一映射，只检验模型之间是否共享偏好；BY 校正。"],
    ["展示顺序分析", "货架顺序随机生成，被选产品的组内展示名次本应均匀分布；按原设计重抽顺序，得到顺序无关时首位命中次数的分布。"],
    ["画像方向规则", "客户条件变化时，组合应朝合理方向变化，如风险承受下调后平均风险分和权益占比不应上升。银行替换为本行适当性政策。"],
    ["带噪声参照的版本回归", "两版配置看到相同顺序时逐条配对；现行配置复测的相似度作为正常波动，低于它的部分记为漂移。"],
  ];
  blocks.forEach(([h, b], i) => {
    const x = 0.6 + (i % 2) * 6.15, y = 1.55 + Math.floor(i / 2) * 2.5;
    block(s, x, y, 5.9, null);
    idx(s, x, y + 0.15, `0${i + 1}`, i === 1);
    txt(s, h, { x: x + 0.7, y: y + 0.15, w: 5.2, h: 0.45, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "middle" });
    txt(s, b, { x, y: y + 0.75, w: 5.85, h: 1.45, fontSize: 13.5, color: C.text2, margin: 0 });
  });
  source(s, "相似度：weighted Jaccard（加权 Jaccard）。BY：Benjamini & Yekutieli (2001) 的多重检验校正，校正后的 p 值记为 q。");
  s.addNotes("核心方法是条件基准：先算出只按经济条件选择时几家模型应该有多相似，超出的部分才需要解释。另外三个模块是展示顺序分析、画像方向规则和带噪声参照的版本回归。");
}

// 9 Experiments -----------------------------------------------------------------------------
pres.addSection({ title: "实证" });
{
  const s = content("实测：四个阶段共 2,841 次真实调用，各批次分开分析", "实证");
  table(s, ["", "首批货架测试", "标签对照", "第二阶段对照实验", "第三阶段复现与处置", "第四阶段受控货架"], [
    ["规模", "81 次：9 画像 × 3 家 × 3 次", "72 次：3 画像 × 3 家 × 4 次 × 2 条件", "468 次：复测、独立顺序、提示词修正、固定顺序各 81，标签复验 144", "324 次：新货架共享与独立顺序、统一排序与逐次随机各 81", "1,896 次：名称、编号、示例、顺序四因素交叉，另加 120 个顺序区组与 24 次校验调用"],
    ["货架", "货架 A：12 只公开基金", "4 只等价虚构产品", "货架 A；标签复验用虚构产品", "货架 B（另外四家管理人 12 只）与货架 A", "两个四选一货币基金货架，经济字段相同"],
    ["用途", "发现问题", "检查品牌影响", "确认机制、检验处置与版本回归", "换条件复现；单模型展示处置", "区分个别产品偏好来源；检验三家同选"],
    ["调用成本", "USD 0.076 + CNY 0.41", "USD 0.040 + CNY 0.21", "USD 0.385 + CNY 2.68", "USD 0.310 + CNY 1.71", "USD 0.507 + CNY 7.48"],
  ], { x: 0.6, y: 1.5, w: 12.1, colW: [1.05, 1.95, 2.0, 2.45, 2.4, 2.25], fontSize: 12, rowH: [0.42, 1.15, 0.8, 0.8, 0.65] });
  txt(s, "返回模型 ID：deepseek-v4-pro、qwen-plus、kimi-k2.6，各阶段一致。均为非思考模式，输出上限 800 token（第四阶段 400）；DeepSeek 与千问温度 0.3，Kimi 使用服务端默认温度 0.6。第二至四阶段的假设、检验方法与缺失数据规则在任何调用前冻结；25,000 组合成数据另用于检验统计方法。", { x: 0.6, y: 5.5, w: 12.1, h: 0.9, fontSize: 13, color: C.text2, margin: 0 });
  source(s, "成本按官方单价与实际 token 用量计算。");
  s.addNotes("我们一共做了 2,841 次真实调用，分四个阶段：第一阶段的首批测试发现问题，标签对照检查产品名称的影响；第二阶段确认机制、检验处置；第三阶段换一套货架复现，并检验单模型处置；第四阶段在受控货架上区分编号、名称和顺序。全部调用约 1.3 美元加 12.5 元。");
}

// 10 Three outcomes -------------------------------------------------------------------------
{
  const s = content("三种结论：在基准内不报警，超出时转复核，不足时不下结论", "实证");
  const order = ["极保守边界", "基准画像", "低风险承受", "高风险承受", "短期资金", "无投资经验", "高风险且无经验", "高风险边界"];
  s.addChart(pres.charts.BAR, [
    { name: "条件基准均值", labels: order, values: [0.782, 0.286, 0.316, 0.163, 0.243, 0.280, 0.295, 0.155] },
    { name: "观测相似度", labels: order, values: [0.782, 0.326, 0.425, 0.295, 0.390, 0.511, 0.535, 0.414] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 7.9, h: 5.0, barDir: "bar", barGrouping: "clustered", barGapWidthPct: 60,
    chartColors: [GRAYC, INKC], showValue: true, dataLabelFormatCode: "0.00", dataLabelPosition: "outEnd",
    valAxisMinVal: 0, valAxisMaxVal: 1, valAxisLabelFormatCode: "0.0", showLegend: true, legendPos: "b", legendFontSize: 11.5,
    showTitle: true, title: "三模型平均相似度：观测值与条件基准（首批，8 个可分析画像）" }, chartText));
  const calls = [
    ["未检出超额趋同", "极保守边界：观测 0.782 = 基准 0.782。可选产品少，一致来自客户约束，不报警。", false],
    ["转人工复核", "高风险边界：0.414 对 0.155，q=0.0015；连同另 2 个画像达到复核线。", true],
    ["证据不足", "标签对照：Kimi 弃答 11 次，3 个画像都无法完成主检验，不下结论。", false],
  ];
  calls.forEach(([h, b, acc], i) => {
    const y = 1.55 + i * 1.6;
    block(s, 8.75, y, 3.95, null, { accent: acc });
    txt(s, h, { x: 8.75, y: y + 0.12, w: 3.9, h: 0.45, fontSize: 16, bold: true, color: acc ? C.accent2 : C.text1, margin: 0 });
    txt(s, b, { x: 8.75, y: y + 0.6, w: 3.9, h: 0.9, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  source(s, "持续置换基准 + BY 校正；复核线：q<0.05 且超出基准 0.15 以上。下限取 0 至 0.23、q 取 0.01、0.05 或 0.10，九个批次判断不变。只按相似度排序时，极保守边界排第一，高风险边界排第五。");
  s.addNotes("三种结论：极保守画像三家很一致，但基准同样高，工具不报警；高风险边界画像超出基准，转人工复核；证据不足时不下结论。只看相似度高低会把前两种情形排反。");
}

// 11 Position finding ----------------------------------------------------------------------
{
  const s = content("复核发现：模型倾向于选同类产品中展示在最前的那只", "实证");
  s.addChart(pres.charts.BAR, [{ name: "次数", labels: ["组内第1个", "组内第2个", "组内第3个", "组内第4个"], values: [54, 5, 3, 7] }],
    Object.assign({ x: 0.45, y: 1.45, w: 5.4, h: 4.1, barDir: "col", chartColors: [INKC], showValue: true, dataLabelPosition: "outEnd",
      valAxisMinVal: 0, valAxisMaxVal: 60, showLegend: false, showTitle: true, title: "首批：被选产品的组内展示名次（69 次单选）" }, chartText));
  s.addChart(pres.charts.BAR, [
    { name: "DeepSeek", labels: ["首批", "复测"], values: [55, 68] },
    { name: "千问", labels: ["首批", "复测"], values: [96, 100] },
    { name: "Kimi", labels: ["首批", "复测"], values: [82, 84] },
  ], Object.assign({ x: 5.95, y: 1.45, w: 4.0, h: 4.1, barDir: "col", barGrouping: "clustered", chartColors: [INKC, RUST, GRAYC],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\"%\"", dataLabelFontSize: 10, valAxisMinVal: 0, valAxisMaxVal: 110,
    showLegend: true, legendPos: "b", legendFontSize: 11, showTitle: true, title: "各模型首位比例（顺序无关为 25%）" }, chartText));
  block(s, 10.2, 1.55, 2.5, null, { accent: true });
  txt(s, [
    { text: "11 至 15 倍", options: { bold: true, color: C.accent2, fontSize: 26, breakLine: true } },
    { text: "排在组内第一位的产品获得的单选次数，相对其余位置的平均值（首批、复测、提示词修正）。", options: { breakLine: true } },
    { text: "货架顺序影响客户最终持有哪家基金公司的产品。", options: { bold: true, color: C.text1 } },
  ], { x: 10.2, y: 1.7, w: 2.5, h: 3.8, fontSize: 12.5, color: C.text2, paraSpaceAfter: 8, margin: 0 });
  txt(s, "重抽货架顺序 99,999 次：首批最多 42 次（实际 54），复测最多 35 次（实际 49），p 均不大于 1×10⁻⁵。合计首位比例：首批 78%（54/69），复测 83%（49/59）。", { x: 0.6, y: 5.7, w: 12.1, h: 0.75, fontSize: 12.5, color: C.text1, margin: 0 });
  source(s, "首批 81 次与第二阶段复测 81 次。");
  s.addNotes("复核高风险边界画像时发现，模型从同类产品中只选一只时，约八成选排在最前的那只，顺序无关时只有四分之一。三家都有这个倾向，千问最强。");
}

// 12 Mechanism ------------------------------------------------------------------------------
{
  const s = content("机制确认：每家模型独立排序后，超出基准的画像由 5 个（复测）降为 0", "实证");
  const labels = ["高风险边界", "高风险承受", "无投资经验", "高风险且无经验", "低风险承受", "短期资金", "极保守边界", "基准画像"];
  s.addChart(pres.charts.BAR, [
    { name: "复测：三家共享顺序", labels, values: [0.494, 0.382, 0.501, 0.479, 0.477, 0.342, 0.674, 0.318] },
    { name: "独立顺序：各家各自随机", labels, values: [0.118, 0.137, 0.280, 0.325, 0.367, 0.293, 0.661, 0.313] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 7.6, h: 5.0, barDir: "bar", barGrouping: "clustered", barGapWidthPct: 55,
    chartColors: [GRAYC, RUST], showValue: true, dataLabelFormatCode: "0.00", dataLabelPosition: "outEnd", dataLabelFontSize: 10,
    valAxisMinVal: 0, valAxisMaxVal: 0.8, valAxisLabelFormatCode: "0.0", showLegend: true, legendPos: "b", legendFontSize: 11.5,
    showTitle: true, title: "跨模型平均相似度（其余条件完全相同）" }, chartText));
  const pts = [
    ["8/8 画像下降", "平均下降 0.147，精确符号翻转检验 p=0.0039", true],
    ["超出基准 5 → 0", "观测值与条件基准基本重合；债券细分为三组时 1 → 0", false],
    ["单模型偏好仍在", "各家仍有 60% 至 94% 选自己看到的组内首位（精确检验 p<0.001）；闸门判为“观察”", false],
  ];
  pts.forEach(([h, b, acc], i) => {
    const y = 1.55 + i * 1.6;
    block(s, 8.45, y, 4.25, null, { accent: acc });
    txt(s, h, { x: 8.45, y: y + 0.12, w: 4.2, h: 0.45, fontSize: 16, bold: true, color: acc ? C.accent2 : C.text1, margin: 0 });
    txt(s, b, { x: 8.45, y: y + 0.6, w: 4.2, h: 0.9, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  source(s, "复测与独立顺序实验各 81 次。");
  s.addNotes("让每家模型看各自的随机顺序，其余条件不变，8 个画像的跨模型相似度全部下降，复测中超出基准的 5 个画像降到 0 个，债券细分为三组时由 1 个降到 0。每家模型仍偏好自己看到的第一个，只是不再被同一个顺序带到同一只产品上。");
}

// 13b Replication + single-model remedy (stage 3) -------------------------------------------------
{
  const s = content("复现与单模型处置：换一套货架仍成立；单模型用逐次随机排序", "实证");
  const labels = ["新画像 2（C5长期）", "新画像 5（C3长期）", "新画像 6（C2长期）", "新画像 1（C4长期）", "新画像 8（C3短期）", "新画像 7（C4短期）", "新画像 3（C4长期）", "新画像 4（C5长期）", "新画像 9（C5短期）"];
  s.addChart(pres.charts.BAR, [
    { name: "三家共享顺序", labels, values: [0.408, 0.522, 0.484, 0.303, 0.96, 0.415, 0.279, 0.336, 0.353] },
    { name: "各家独立顺序", labels, values: [0.149, 0.301, 0.284, 0.136, 0.817, 0.304, 0.189, 0.27, 0.311] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 6.4, h: 4.45, barDir: "bar", barGrouping: "clustered", barGapWidthPct: 55,
    chartColors: [GRAYC, RUST], showValue: true, dataLabelFormatCode: "0.00", dataLabelPosition: "outEnd", dataLabelFontSize: 9,
    valAxisMinVal: 0, valAxisMaxVal: 1, valAxisLabelFormatCode: "0.0", showLegend: true, legendPos: "b", legendFontSize: 11,
    showTitle: true, title: "新货架与 9 个新画像：跨模型平均相似度" }, chartText));
  const cats = ["DeepSeek", "千问", "Kimi", "三家合计"];
  s.addChart(pres.charts.BAR, [
    { name: "统一排序（全部请求同一顺序）", labels: cats, values: [58.3, 100.0, 37.5, 66.7] },
    { name: "逐次随机（每次请求随机）", labels: cats, values: [10.0, 20.0, 17.4, 15.9] },
  ], Object.assign({ x: 7.05, y: 1.45, w: 5.75, h: 4.45, barDir: "col", barGrouping: "clustered", barGapWidthPct: 60,
    chartColors: [INKC, GRAYC], showValue: true, dataLabelFormatCode: "0", dataLabelPosition: "outEnd", dataLabelFontSize: 10,
    valAxisMinVal: 0, valAxisMaxVal: 110, valAxisLabelFormatCode: "0", showLegend: true, legendPos: "b", legendFontSize: 11,
    showTitle: true, title: "单模型：单选落在锚定产品上的比例（%）" }, chartText));
  rule(s, 0.6, 6.05, 12.1, HEX.accent2, 1);
  txt(s, "新货架：9/9 画像下降（p=0.002），超出基准 7 → 0。单模型：锚定产品 50/75（67%）→ 10/63（16%），p=0.008。", { x: 0.6, y: 6.1, w: 12.1, h: 0.42, fontSize: 13, bold: true, color: C.text1, valign: "middle", margin: 0 });
  source(s, "第三阶段 324 次。新货架为嘉实、博时、南方、天弘的 12 只基金。锚定产品为统一排序中各组排在最前的三只产品，分属三家管理人。逐次随机后，货币组和债券组中编号最小的华夏产品仍被选得最多，华夏基金单选占比 43%、工银瑞信 16%，展示编号也需随机生成（下一页）。");
  s.addNotes("换一套 12 只基金和 9 个新画像，结论复现。只接一家模型时，所有请求用同一个顺序，排在最前的三只产品拿到 67% 的单选；改成每次请求随机排序后，同样三只降到 16%。三家差别很大，接入时要对实际使用的模型逐一测试。");
}

// 13c Controlled shelves: ID labels (stage 4) ----------------------------------------------------
{
  const s = content("个别产品偏好与编号有关：编号最小的产品被优先选择", "实证");
  const cats = ["DeepSeek", "千问", "Kimi", "三家合计"];
  s.addChart(pres.charts.BAR, [
    { name: "原编号（P01 至 P04）", labels: cats, values: [96.9, 77.1, 99.0, 91.0] },
    { name: "随机编号（如 K7F9）", labels: cats, values: [33.3, 33.3, 30.2, 32.3] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 7.4, h: 4.45, barDir: "col", barGrouping: "clustered", barGapWidthPct: 60,
    chartColors: [RUST, GRAYC], showValue: true, dataLabelFormatCode: "0", dataLabelPosition: "outEnd", dataLabelFontSize: 10,
    valAxisMinVal: 0, valAxisMaxVal: 110, valAxisLabelFormatCode: "0", showLegend: true, legendPos: "b", legendFontSize: 11,
    showTitle: true, title: "受控货架一：选中华夏现金增利（编号 P01）的比例（%）" }, chartText));
  const pts = [
    ["编号  91% → 32%", "原编号 / 随机编号；不在首位的 196 次中被选 170 次对 3 次。Holm 校正后 p=0.002", true],
    ["名称与管理人  58% / 65%", "显示 / 隐藏；显示名称后比例下降（p=0.008）", false],
    ["格式示例  27% / 23%", "示例含 / 不含该候选编号；校正后 p=0.0503，未达显著", false],
  ];
  pts.forEach(([h, b, acc], i) => {
    const y = 1.55 + i * 1.5;
    block(s, 8.2, y, 4.5, null, { accent: acc });
    txt(s, h, { x: 8.2, y: y + 0.12, w: 4.5, h: 0.45, fontSize: 16, bold: true, color: acc ? C.accent2 : C.text1, margin: 0 });
    txt(s, b, { x: 8.2, y: y + 0.6, w: 4.5, h: 0.8, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  rule(s, 0.6, 6.05, 12.1, HEX.accent2, 1);
  txt(s, "处置建议：展示给模型的产品编号按请求随机生成，在系统内映射回产品；格式示例不使用货架编号。", { x: 0.6, y: 6.1, w: 12.1, h: 0.42, fontSize: 13, bold: true, color: C.text1, valign: "middle", margin: 0 });
  source(s, "第四阶段 1,896 次（含 24 次校验调用）：两个四选一货币基金货架，候选经济字段相同；名称、编号、示例、顺序四因素交叉。受控货架二中编号最小的产品同样被优先选择（65% 对 27%）。");
  s.addNotes("随机排序之后，编号最小的产品仍被优先选择。受控实验把候选的经济条件设为相同，原编号下华夏现金增利拿到 91% 的选择，换成随机编号后是 32%。处置是给模型看随机生成的编号，系统内再映射回产品。");
}

// 13 Customer impact ------------------------------------------------------------------------
{
  const s = content("对客户与资金的影响：展示方式影响客户最终持有哪只产品", "实证");
  s.addChart(pres.charts.BAR, [
    { name: "三家共享顺序", labels: ["公开基金货架（23/39 对 0/8）", "受控货架（144/144 对 16/144）"], values: [59.0, 100.0] },
    { name: "各家独立随机顺序", labels: ["公开基金货架（23/39 对 0/8）", "受控货架（144/144 对 16/144）"], values: [0.0, 11.1] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 7.5, h: 4.5, barDir: "col", barGrouping: "clustered", barGapWidthPct: 70, chartColors: [RUST, GRAYC],
      showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0\"%\"", valAxisMinVal: 0, valAxisMaxVal: 110, valAxisLabelFormatCode: "0",
      showLegend: true, legendPos: "b", legendFontSize: 11.5, showTitle: true, title: "三家都从同一组只选一只时，选中同一只产品的比例" }, chartText));
  block(s, 8.3, 1.55, 4.4, null, { accent: true });
  txt(s, [{ text: "约 2,700 万元", options: { bold: true, color: C.accent2, fontSize: 34, breakLine: true } }, { text: "每 1 亿元推荐资金的去向随排序改变", options: { color: C.text1, bold: true, fontSize: 14, breakLine: true } }, { text: "单模型统一排序时三只锚定产品占推荐权重 46.4%，逐次随机时同样三只为 19.6%", options: { color: C.text2, fontSize: 12.5 } }], { x: 8.3, y: 1.7, w: 4.4, h: 1.9, margin: 0 });
  block(s, 8.3, 3.75, 4.4, null);
  txt(s, [{ text: "约 2,900 名", options: { bold: true, color: C.text1, fontSize: 34, breakLine: true } }, { text: "每 1 万名获得推荐的客户中多持有锚定产品的人数", options: { color: C.text1, bold: true, fontSize: 14, breakLine: true } }, { text: "推荐含锚定产品之一：统一排序 93.8%，逐次随机 65.0%", options: { color: C.text2, fontSize: 12.5 } }], { x: 8.3, y: 3.9, w: 4.4, h: 1.9, margin: 0 });
  rule(s, 0.6, 6.05, 12.1, HEX.accent2, 1);
  txt(s, "处置建议：多家 AI 助手并行时为每家模型独立随机排列产品列表；只接一家时按请求随机排列，并随机生成展示编号。", { x: 0.6, y: 6.1, w: 12.1, h: 0.42, fontSize: 13, bold: true, color: C.text1, valign: "middle", margin: 0 });
  source(s, "左图：公开基金货架为首批与第二阶段共五个批次，受控货架为第四阶段 144 组对照测试（24 + 120 个区组，Holm 校正后 p<0.001）。右侧：货架 A 统一排序 81 条、逐次随机 80 条推荐，三家合计，按客户全部采纳推荐、每条等额计；锚定产品为统一排序中各组排在最前的三只产品。");
  s.addNotes("换算成资金和客户：只接一家模型、所有请求用同一个顺序时，三只排在最前的产品占推荐权重 46.4%，逐次随机排序时同样三只为 19.6%，按客户全部采纳、等额资金换算，每 1 亿元推荐资金约 2,700 万元的去向随排序改变。多家模型共享顺序时，受控货架上 144 组对照全部选中同一只。");
}

// 14 Prompt fix regression ------------------------------------------------------------------
{
  const s = content("版本回归：修改提示词改变了行为，没有消除顺序依赖", "实证");
  table(s, ["指标", "复测（现行）", "提示词修正（候选）"], [
    ["组内首位比例（目标指标）", "83%", "80%（p=0.50）"],
    ["推荐 / 有效 / 适当性违规", "81 / 81 / 0", "81 / 81 / 0"],
    ["逐条配对相似度", "0.737（噪声参照）", "0.656（比参照低 0.08）"],
    ["管理人集中度变化", "基准", "−0.003"],
    ["方向问题", "2", "0"],
    ["闸门", "人工复核", "人工复核"],
  ], { x: 0.6, y: 1.5, w: 7.4, colW: [2.9, 2.25, 2.25], fontSize: 13, rowH: [0.42, 0.62, 0.62, 0.62, 0.62, 0.62, 0.62], align: [null, "center", "center"] });
  txt(s, "提示词修正实验加入“货架中产品的排列顺序为随机生成，不代表推荐优先级”，与复测看到完全相同的顺序。回归结论：不采用这一提示词，处置转向展示一侧。", { x: 0.6, y: 5.95, w: 7.4, h: 0.55, fontSize: 12.5, color: C.text2, margin: 0 });
  block(s, 8.4, 1.55, 4.3, "固定顺序实验");
  txt(s, "同一模型三次重复的相似度 0.401 → 0.771（p≈5×10⁻⁶），首位比例 94%。重复间的“不稳定”大部分来自顺序变化，所以回归在相同顺序下配对。固定顺序实验中 65 次单选有 61 次落在首位。", { x: 8.4, y: 1.98, w: 4.3, h: 2.0, fontSize: 12.5, color: C.text1, margin: 0 });
  block(s, 8.4, 4.25, 4.3, "处置", { accent: true });
  txt(s, "多家 AI 助手并行服务时，为每家模型独立随机排序；展示逻辑变化后按独立顺序实验的设计重测。", { x: 8.4, y: 4.68, w: 4.3, h: 1.5, fontSize: 13, color: C.text1, margin: 0 });
  source(s, "测试台“配置对比”页可查看同一组指标。");
  s.addNotes("在提示词里声明顺序随机，首位比例只从 83% 降到 80%，没有显著变化；回归报告显示它改变了模型行为，但没有解决顺序依赖，所以不采用。");
}

// 18 Review case ------------------------------------------------------------------------------------
pres.addSection({ title: "落地" });
{
  const s = content("一次完整的复核：从风险卡提示到处置建议", "落地");
  const steps = [
    ["风险卡提示", "高风险边界：观测 0.414，基准 0.155，转人工复核"],
    ["打开原始响应", "9 条推荐中 8 条包含沪深 300 组排在最前的那只"],
    ["预设对照实验", "复测重现；独立顺序后降到 0.118，低于基准"],
    ["回归检验处置", "提示词修正：首位比例无显著变化（p=0.50），不采用"],
    ["复核记录", "归类“展示顺序依赖”；独立排序，重测后交审批"],
  ];
  steps.forEach(([h, b], i) => {
    const x = 0.6 + i * 2.46, last = i === 4;
    block(s, x, 1.5, 2.25, null, { accent: last });
    idx(s, x, 1.62, `0${i + 1}`, last);
    txt(s, h, { x: x + 0.55, y: 1.62, w: 1.7, h: 0.42, fontSize: 14, bold: true, color: last ? C.accent2 : C.text1, valign: "middle", margin: 0 });
    txt(s, b, { x, y: 2.15, w: 2.2, h: 1.2, fontSize: 12, color: C.text2, margin: 0 });
  });
  const w = 8.3, h = w / aspect("console_dialog_crop.png");
  shot(s, "console_dialog_crop.png", 0.64, 3.6, w, h, "console-dialog");
  block(s, 9.3, 3.6, 3.4, "测试台中的原始响应");
  txt(s, [
    { text: "复测批次 DeepSeek 第 1 次推荐：产品按所属组排列，选中 4 只，其中 3 只为所在组的首位产品。", options: { breakLine: true } },
    { text: "复核记录写明证据、归类、处理建议与重测条件，审批字段由银行填写。", options: {} },
  ], { x: 9.3, y: 4.02, w: 3.4, h: 2.4, fontSize: 12, color: C.text1, paraSpaceAfter: 8, margin: 0 });
  source(s, "复核记录可在测试台导出。");
  s.addNotes("一次完整的复核：风险卡提示，查原始响应，用对照实验确认原因，用版本回归检验处置，最后复核记录给出处理建议和重测条件，决定由银行审批作出。");
}

// 19 Deployment ---------------------------------------------------------------------------------------
{
  const s = content("部署与落地：接在模型准入和变更流程中", "落地");
  const w = 8.05, h = w / aspect("fig_deploy.png");
  s.addImage({ path: img("fig_deploy.png"), x: 0.55, y: 1.45, w, h, altText: "部署架构", objectName: "deploy" });
  const stages = [["离线原型", "已完成"], ["机制与回归", "已完成"], ["复现与处置", "已完成"], ["受控实验", "已完成"], ["使用者试用", "计划"], ["银行环境测试", "计划"], ["纳入流程", "计划"]];
  stages.forEach(([t, st], i) => {
    const x = 0.6 + i * 1.18, done = i <= 3;
    rule(s, x, 5.35, 1.1, done ? HEX.dk1 : HEX.accent3, done ? 2 : 1);
    txt(s, `0${i + 1}`, { x, y: 5.42, w: 1.1, h: 0.3, fontSize: 11, color: C.accent4, fontFace: SANS, margin: 0 });
    txt(s, t, { x, y: 5.7, w: 1.16, h: 0.35, fontSize: 11.5, bold: true, color: done ? C.text1 : C.text2, margin: 0 });
    txt(s, st, { x, y: 6.05, w: 1.16, h: 0.3, fontSize: 11, color: done ? C.text1 : C.accent4, margin: 0 });
  });
  table(s, ["单次测试", ""], [
    ["规模", "原型 81 次（9 画像 × 3 家 × 3 次）"],
    ["API 成本", "约 USD 0.08 + CNY 0.4（实测）"],
    ["时间", "约 16 分钟（受接口限速）"],
    ["人员", "模型风险、业务、科技各 1 人"],
    ["银行提供", "测试接口、产品字段、适当性规则"],
  ], { x: 8.95, y: 1.5, w: 3.75, colW: [1.05, 2.7], fontSize: 12, rowH: [0.38, 0.68, 0.6, 0.6, 0.6, 0.68] });
  source(s, "成本按官方单价与实际 token 用量计算。");
  s.addNotes("部署在银行测试环境，经模型网关调用候选模型，结果进入测试台和审计存档。一次测试 81 次调用，API 费用约 1 元，需要模型风险、业务、科技各一人。下一步是请银行使用者试用。");
}

// 20 Closing ------------------------------------------------------------------------------------------
{
  const s = content("局限与团队", "落地");
  table(s, ["局限", "对应做法"], [
    ["合成画像，两套货架共 24 只公开基金", "银行环境测试时接入本行货架字段与适当性规则"],
    ["只测了三家国产模型", "接入适配器后按同一冻结条件重跑"],
    ["模型 ID 是服务别名，供应商可能更新", "每次记录返回 ID，变化即停止；定期回归"],
    ["受控货架候选的经济条件相同", "用本行货架测量编号与顺序的影响程度"],
    ["持续置换检出能力较低", "风险卡同时报告观测值、基准值与超出幅度"],
    ["业务换算按客户全部采纳计", "按本行 AI 推荐覆盖量与采纳率计算实际金额"],
  ], { x: 0.6, y: 1.5, w: 7.7, colW: [3.5, 4.2], fontSize: 12.5, rowH: [0.42, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5] });
  block(s, 8.8, 1.55, 3.9, "团队");
  txt(s, [
    { text: "成员甲　成员乙", options: { bold: true, color: C.text1, fontSize: 18, breakLine: true } },
    { text: "（学校信息不写入）", options: { color: C.text2, breakLine: true } },
    { text: "（学院信息不写入）", options: { color: C.text2, breakLine: true } },
    { text: "信息管理与信息系统专业", options: { color: C.text2, breakLine: true } },
    { text: "成员甲：分工说明", options: { color: C.text1, breakLine: true } },
    { text: "成员乙：分工说明", options: { color: C.text1 } },
  ], { x: 8.8, y: 2.0, w: 3.9, h: 3.4, fontSize: 13.5, paraSpaceAfter: 8, margin: 0 });
  rule(s, 0.6, 5.55, 12.1, HEX.dk1, 1.25);
  txt(s, "EchoGuard 在上线前以每次约 1 元 API 费用的测试查出展示顺序带来的推荐偏向，用对照实验区分顺序与编号的作用，给出可执行的展示改造，并在每次变更后重复测试。", { x: 0.6, y: 5.65, w: 12.1, h: 0.75, fontSize: 17, bold: true, color: C.text1, margin: 0 });
  source(s, "在线测试台：https://sandro826h-dotcom.github.io/echoguard/");
  s.addNotes("局限是画像为合成数据、只测了三家模型，表中列出了对应做法。总结一句：EchoGuard 在上线前以每次约 1 元 API 费用的测试查出展示顺序带来的推荐偏向，用对照实验区分顺序与编号的作用，给出可执行的展示改造，并在每次变更后重复测试。谢谢各位评委。");
}

pres.addSection({ title: "备用" });
// 4 Three questions --------------------------------------------------------------------
{
  const s = content("备用：单模型测评回答不了的三个横向问题", "备用");
  const items = [
    ["一致是否合理", "客户约束很强时，几家模型推荐相同产品是正常的。只看相似度会误报，也会漏掉超出合理范围的趋同。"],
    ["趋同从哪里来", "共同的经济理由、产品展示顺序、品牌标签、模型共有的偏好，都会让推荐变得相似，处置方法各不相同。"],
    ["证据是否足够", "模型弃答、接口失败、覆盖不完整时，“没有问题”的结论缺少依据，需要先补证据。"],
  ];
  items.forEach(([h, b], i) => {
    const x = 0.6 + i * 4.1;
    block(s, x, 1.6, 3.8, null);
    idx(s, x, 1.75, `0${i + 1}`, i === 1);
    txt(s, h, { x, y: 2.3, w: 3.8, h: 0.55, fontSize: 21, bold: true, color: C.text1, margin: 0 });
    txt(s, b, { x, y: 3.0, w: 3.7, h: 2.6, fontSize: 15.5, color: C.text2, margin: 0 });
  });
  source(s, "JR/T 0221-2021 从安全性、可解释性、精准性、性能评价单个算法；EchoGuard 补充多模型横向行为的测试。");
  s.addNotes("单个模型的测评回答这个模型答得对不对。多家模型同时上线后，模型风险人员还要回答三个问题：一致是否合理、趋同从哪里来、证据够不够。");
}

// 15 Labels -------------------------------------------------------------------------------------
{
  const s = content("备用：证据不足与复验，Kimi 的弃答受产品标签影响", "备用");
  const stats = [["72/72", "标签对照调用成功，格式全部有效", false], ["11", "次弃答，全部来自 Kimi；0/3 画像满足主检验", false], ["18 对 5", "标签复验：Kimi 显示标签时推荐 18/24，隐藏时 5/24", true], ["p=0.0005", "精确 McNemar 检验，不一致对 14:1", true]];
  stats.forEach(([b, l, acc], i) => {
    const x = 0.6 + i * 3.1;
    block(s, x, 1.55, 2.85, null, { accent: acc });
    txt(s, b, { x, y: 1.7, w: 2.85, h: 0.85, fontSize: 30, bold: true, color: acc ? C.accent2 : C.text1, margin: 0, valign: "middle" });
    txt(s, l, { x, y: 2.6, w: 2.8, h: 0.9, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  block(s, 0.6, 3.8, 12.1, null);
  txt(s, [
    { text: "设计　", options: { bold: true, color: C.text1 } },
    { text: "4 只虚构产品经济属性完全相同，只改变是否显示产品名与管理人；位置用拉丁方轮换，主检验与弃答规则在调用前冻结。", options: { breakLine: true } },
    { text: "处理　", options: { bold: true, color: C.text1 } },
    { text: "标签对照中任一画像出现弃答即判证据不足，保留全部弃答。Kimi 显示与隐藏标签时的推荐率差异，作为标签复验的预设假设在 144 次新调用上检验，结果支持这一假设。", options: { breakLine: true } },
    { text: "附带发现　", options: { bold: true, color: C.text1 } },
    { text: "标签复验的 118 条推荐全部把 0.6 的最高权重放在展示在首位的产品上。DeepSeek 48/48 推荐，千问 47/48。", options: {} },
  ], { x: 0.6, y: 3.95, w: 12.1, h: 2.4, fontSize: 13.5, color: C.text2, paraSpaceAfter: 8, margin: 0 });
  source(s, "风险卡同时列出弃答率与标签条件。");
  s.addNotes("第三种结论来自标签对照。72 次调用全部成功，但 Kimi 弃答 11 次，按事前规则输出证据不足。数据全部保留，并把 Kimi 显示标签时更愿意推荐作为预设假设，在 144 次新调用上检验：显示标签时推荐 18 次，隐藏时 5 次，p 约万分之五，结果支持这一假设。另外 118 条推荐全部把最高权重放在首位产品上。");
}

// 16 Regrouping + direction rules -----------------------------------------------------------------
{
  const s = content("备用：货架分组与方向规则", "备用");
  const labels = ["高风险边界", "高风险且无经验", "无投资经验", "短期资金", "高风险承受", "低风险承受"];
  s.addChart(pres.charts.BAR, [
    { name: "基准（债券 4 只一组）", labels, values: [0.155, 0.295, 0.280, 0.243, 0.163, 0.316] },
    { name: "基准（纯债 / 中短债 / 含权债）", labels, values: [0.196, 0.432, 0.392, 0.295, 0.209, 0.381] },
    { name: "观测相似度", labels, values: [0.414, 0.535, 0.511, 0.390, 0.295, 0.425] },
  ], Object.assign({ x: 0.45, y: 1.45, w: 7.4, h: 5.0, barDir: "bar", barGrouping: "clustered", barGapWidthPct: 50,
    chartColors: ["DAD5CA", "868176", INKC], showValue: true, dataLabelFormatCode: "0.00", dataLabelPosition: "outEnd", dataLabelFontSize: 9.5,
    valAxisMinVal: 0, valAxisMaxVal: 0.7, valAxisLabelFormatCode: "0.0", showLegend: true, legendPos: "b", legendFontSize: 10.5,
    showTitle: true, title: "首批债券一组分组下超出基准的 6 个画像" }, chartText));
  block(s, 8.2, 1.55, 4.5, "债券组拆分");
  txt(s, "按中报持仓：110007 含股票 19.79%、转债 3.93%；006591 为中短债。拆组后超出基准的画像：首批 6 → 2，复测 5 → 1；高风险边界在两种分组下都达到复核线。", { x: 8.2, y: 1.98, w: 4.5, h: 2.0, fontSize: 13, color: C.text1, margin: 0 });
  block(s, 8.2, 4.15, 4.5, "画像方向规则", { accent: true });
  txt(s, "客户风险承受下调后，千问组合平均风险分反而上升（首批 1.733 → 1.900，复测 1.767 → 2.000），复测中 Kimi 也出现。没有违反适当性硬约束，转人工复核。", { x: 8.2, y: 4.58, w: 4.5, h: 1.9, fontSize: 13, color: C.text1, margin: 0 });
  source(s, "同一批 81 次的再分析，每画像 99,999 次置换；第二阶段同时报告两种分组。");
  s.addNotes("四只债券基金的中期报告显示，它们在持仓上差别不小，因此按纯债、中短债、含权债拆组。拆组后基准升高，超出基准的画像变少，但高风险边界画像依然超出。画像方向规则发现千问在客户风险承受下调后组合风险反而上升，复测中再次出现。");
}

// 17 Method self-check ----------------------------------------------------------------------------
{
  const s = content("备用：先用已知答案的数据检验统计方法", "备用");
  s.addChart(pres.charts.BAR, [{ name: "至少一次误报的批次比例", labels: ["各次独立·逐次置换", "各次独立·持续置换", "偏好稳定·逐次置换", "偏好稳定·持续置换"], values: [4.4, 3.0, 48.0, 1.2] }],
    Object.assign({ x: 0.45, y: 1.45, w: 7.4, h: 4.9, barDir: "col", chartColors: [INKC], showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.0\"%\"",
      valAxisMinVal: 0, valAxisMaxVal: 60, showLegend: false, showTitle: true, title: "没有共享偏好时，九画像批次至少误报一次的比例（每格 500 组）" }, chartText));
  const pts = [["逐次置换的误报", "模型自身偏好稳定时，逐次置换基准 48% 的批次会误报。", true], ["持续置换 + BY", "主校准两种情形下批次误报为 1.2% 与 3.0%；另 10 个扩展零偏差场景为 0% 至 2.6%。", false], ["检出能力", "共享偏好强度 0.6 时检出 25% 至 55% 的受影响画像；只有一个四产品组受共享偏好影响、模型自身偏好完全持续时只有 2.1%，所以风险卡同时报告观测值、基准值和超出幅度。", false]];
  pts.forEach(([h, b, acc], i) => {
    const y = 1.55 + i * 1.6;
    block(s, 8.2, y, 4.5, null, { accent: acc });
    txt(s, h, { x: 8.2, y: y + 0.12, w: 4.5, h: 0.4, fontSize: 15.5, bold: true, color: acc ? C.accent2 : C.text1, margin: 0 });
    txt(s, b, { x: 8.2, y: y + 0.55, w: 4.5, h: 0.95, fontSize: 12.5, color: C.text2, margin: 0 });
  });
  source(s, "合成数据的生成机制已知，用于检验方法在零偏差、注入偏差和违反假设时的表现。");
  s.addNotes("先用已知答案的合成数据检验方法。逐次置换基准在模型自身偏好稳定时会让 48% 的批次误报；持续置换把它降到 1.2%，各次重复相互独立时为 3.0%，代价是检出能力较低。");
}

(async () => {
  const out = process.argv[2] || "EchoGuard_答辩_v11.pptx";
  await pres.writeFile({ fileName: out });
  await applyTheme(out, THEME);
  console.log("wrote", out, "slides", n);
})();
