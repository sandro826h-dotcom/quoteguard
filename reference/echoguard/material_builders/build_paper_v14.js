// Paper-format builder for the EchoGuard write-up (v12): IEEE-style numbering and captions in a single-column layout,
// heading styles with outline levels (PDF bookmarks), clickable links written as <https://...>, document properties.
// v13: body bold in the serif face; optional "%% widths: a,b,..." before a table; short tables kept on one page.
// Usage: node build_paper_v13.js source.md out.docx
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, AlignmentType,
  BorderStyle, LevelFormat, Footer, PageNumber, TableLayoutType, SectionType, TabStopType, VerticalAlign,
  ExternalHyperlink, HeadingLevel,
} = require("docx");

const SRC = process.argv[2], OUT = process.argv[3];
const BASE = path.dirname(SRC);
const lines = fs.readFileSync(SRC, "utf8").split("\n");

const SONG = { ascii: "Times New Roman", hAnsi: "Times New Roman", eastAsia: "SimSun", cs: "Times New Roman" };
const HEI = { ascii: "Times New Roman", hAnsi: "Times New Roman", eastAsia: "SimHei", cs: "Times New Roman" };
const KAI = { ascii: "Times New Roman", hAnsi: "Times New Roman", eastAsia: "KaiTi", cs: "Times New Roman" };
const PAGE_W = 11906, PAGE_H = 16838, ML = 1247, MR = 1247, MT = 1191, MB = 1247;
const TEXT_W = PAGE_W - ML - MR;          // 10092
const COL_GAP = 284;                      // ~0.5 cm
const COL_W = Math.floor((TEXT_W - COL_GAP) / 2); // 4904
const PX_PER_DXA = 96 / 1440;
const BODY = 21, SMALL = 18, CAP = 18;    // half-points: 10.5pt, 9pt
const LINE = 300;                         // 1.25

function runs(text, o = {}) {
  const t = text.replace(/`/g, "");
  const parts = t.split(/(<https?:\/\/[^>\s]+>|\*\*[^*]+\*\*|\*[^*]+\*)/g).filter((s) => s.length);
  return parts.map((s) => {
    if (s.startsWith("<http")) {
      const url = s.slice(1, -1);
      return new ExternalHyperlink({ link: url, children: [new TextRun({ text: url, font: o.font || SONG, size: o.size || BODY, color: "1F4E79", underline: {} })] });
    }
    if (s.startsWith("**")) return new TextRun({ text: s.slice(2, -2), bold: true, font: o.boldFont || o.font || SONG, size: o.size || BODY, color: o.color });
    if (s.startsWith("*") && s.length > 2) return new TextRun({ text: s.slice(1, -1), italics: true, font: o.font || SONG, size: o.size || BODY, color: o.color });
    return new TextRun({ text: s, bold: o.bold, italics: o.italics, font: o.font || SONG, size: o.size || BODY, color: o.color });
  });
}

function pngSize(file) { const b = fs.readFileSync(file); return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) }; }

const THICK = { style: BorderStyle.SINGLE, size: 10, color: "000000" };
const THIN = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const NONE = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };

function table(rows, width, o2 = {}) {
  const cells = rows.map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
  const header = cells[0], body = cells.slice(2), n = header.length;
  const dlen = (t) => [...t.replace(/\*\*/g, "")].reduce((a, ch) => a + (/[　-鿿＀-￯]/.test(ch) ? 1 : 0.55), 0);
  const lens = header.map((_, i) => Math.max(...[header, ...body].map((r) => dlen(r[i] || ""))));
  const clamp = (l) => Math.max(5.2, Math.min(l + 1.2, o2.maxCol || 34));
  const total = lens.reduce((a, b) => a + clamp(b), 0);
  const hint = o2.widths && o2.widths.length === n ? o2.widths : null;
  const hsum = hint ? hint.reduce((a, b) => a + b, 0) : 0;
  const widths = hint ? hint.map((h) => Math.round((h / hsum) * width)) : lens.map((l) => Math.round((clamp(l) / total) * width));
  widths[n - 1] += width - widths.reduce((a, b) => a + b, 0);
  const keep = body.length <= 10;
  const mk = (r, ri, isHead, isLast) => new TableRow({
    tableHeader: isHead, cantSplit: true,
    children: r.map((c, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA }, verticalAlign: VerticalAlign.CENTER,
      borders: { top: isHead ? THICK : NONE, bottom: isHead ? THIN : (isLast ? THICK : NONE), left: NONE, right: NONE },
      margins: { top: 30, bottom: 30, left: 60, right: 60 },
      children: [new Paragraph({ keepNext: keep && !isLast, alignment: (i === 0 || lens[i] > 12) ? AlignmentType.LEFT : AlignmentType.CENTER, spacing: { line: 240 },
        children: runs(c, { size: SMALL, bold: isHead, font: isHead ? HEI : SONG, boldFont: HEI }) })],
    })),
  });
  return new Table({ width: { size: width, type: WidthType.DXA }, columnWidths: widths, layout: TableLayoutType.FIXED,
    alignment: AlignmentType.CENTER,
    rows: [mk(header, 0, true, false), ...body.map((r, j) => mk(r, j + 1, false, j === body.length - 1))] });
}

// ---- parse front matter
const meta = {};
let i = 0;
while (i < lines.length && lines[i].startsWith("% ")) { const m = lines[i].slice(2).match(/^(\w+):\s*(.*)$/); if (m) meta[m[1]] = m[2]; i++; }

const sections = [];
let cur = { cols: 1, children: [] };
const titleBlock = cur;
{
  const parts = meta.title.split("：");
  const tr = parts.length > 1
    ? [new TextRun({ text: parts[0] + "：", font: HEI, size: 34 }), new TextRun({ text: parts.slice(1).join("："), font: HEI, size: 34, break: 1 })]
    : [new TextRun({ text: meta.title, font: HEI, size: 34 })];
  titleBlock.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 200, line: 360 }, children: tr }));
}
if (meta.subtitle) titleBlock.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
  children: [new TextRun({ text: meta.subtitle, font: HEI, size: 24 })] }));
titleBlock.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
  children: [new TextRun({ text: meta.authors, font: KAI, size: 24 })] }));
titleBlock.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
  children: [new TextRun({ text: meta.affiliation, font: SONG, size: 18 })] }));
if (meta.note) titleBlock.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
  children: [new TextRun({ text: meta.note, font: SONG, size: 18, color: "404040" })] }));
sections.push(titleBlock);
cur = { cols: 1, children: [] };
sections.push(cur);

let numInst = 0;
let pendingWidths = null;
const width = () => (cur.cols === 2 ? COL_W : TEXT_W);
while (i < lines.length) {
  const line = lines[i];
  if (!line.trim()) { i++; continue; }
  if (line.startsWith(":::")) { i++; continue; }   // single-column layout: wide/narrow markers are no-ops
  if (line.startsWith("@abstract ") || line.startsWith("@keywords ")) {
    const isAbs = line.startsWith("@abstract ");
    const text = line.replace(/^@\w+ /, "");
    const label = isAbs ? "摘要：" : "关键词：";
    cur.children.push(new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { line: 252, after: isAbs ? 80 : 200 },
      children: [new TextRun({ text: label, font: HEI, size: 19 }), ...runs(text, { size: 19, font: SONG })] }));
    i++; continue;
  }
  if (line.startsWith("## ")) {
    cur.children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, outlineLevel: 0, alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 240, after: 120 },
      pageBreakBefore: line.startsWith("## 附录 A"),
      children: [new TextRun({ text: line.slice(3), font: HEI, size: 24, color: "000000" })] }));
    i++; continue;
  }
  if (line.startsWith("### ")) {
    cur.children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, outlineLevel: 1, keepNext: true, spacing: { before: 160, after: 80 },
      children: [new TextRun({ text: line.slice(4), font: HEI, size: BODY, color: "000000" })] }));
    i++; continue;
  }
  if (line.startsWith("Table: ")) {
    cur.children.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 },
      children: [new TextRun({ text: line.slice(7), font: HEI, size: CAP })] }));
    i++; continue;
  }
  if (line.startsWith("%% widths:")) { pendingWidths = line.slice(10).split(",").map((x) => parseFloat(x)); i++; continue; }
  if (line.startsWith("|")) {
    const rows = [];
    while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
    cur.children.push(table(rows, width(), { maxCol: rows[0].includes("| 项目 | 内容 |") ? 60 : 34, widths: pendingWidths }));
    pendingWidths = null;
    cur.children.push(new Paragraph({ spacing: { after: 60 }, children: [] }));
    continue;
  }
  const img = line.match(/^!\[([^\]]*)\]\(([^)]+)\)(\{(\w+)\})?/);
  if (img) {
    const file = path.join(BASE, img[2]);
    const { w, h } = pngSize(file);
    const frac = img[4] === "double" ? 1.0 : (img[4] === "single" ? 0.62 : (img[4] === "w85" ? 0.85 : (img[4] === "small" ? 0.5 : (img[4] === "xs" ? 0.42 : 0.8))));
    const maxW = Math.floor(width() * PX_PER_DXA * frac) - 2;
    const scale = maxW / w;
    cur.children.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 100, after: 40 },
      children: [new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width: maxW, height: Math.round(h * scale) },
        altText: { title: img[1], description: img[1], name: img[1] } })] }));
    const cap = img[1];
    const m = cap.match(/^(图 \d+\.)\s*(.*)$/);
    cur.children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 140, line: 240 },
      children: m ? [new TextRun({ text: m[1] + " ", font: HEI, size: CAP }), new TextRun({ text: m[2], font: SONG, size: CAP })]
                  : [new TextRun({ text: cap, font: SONG, size: CAP })] }));
    i++; continue;
  }
  const eq = line.match(/^\$\$\s*(.*?)\s*\((\d+)\)\s*\$\$$/);
  if (eq) {
    cur.children.push(new Paragraph({ tabStops: [{ type: TabStopType.CENTER, position: Math.floor(width() / 2) }, { type: TabStopType.RIGHT, position: width() }],
      spacing: { before: 60, after: 60 },
      children: [new TextRun({ text: "\t" + eq[1], font: { ascii: "Cambria Math", hAnsi: "Cambria Math", eastAsia: "SimSun" }, size: BODY, italics: false }),
                 new TextRun({ text: "\t(" + eq[2] + ")", font: SONG, size: BODY })] }));
    i++; continue;
  }
  if (line.startsWith("- ")) {
    cur.children.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, alignment: AlignmentType.JUSTIFIED, spacing: { line: LINE, after: 30 },
      children: runs(line.slice(2)) }));
    i++; continue;
  }
  const num = line.match(/^(\d+)\. (.*)$/);
  if (num) {
    if (!(i > 0 && /^\d+\. /.test(lines[i - 1]))) numInst++;
    cur.children.push(new Paragraph({ numbering: { reference: "numbers", level: 0, instance: numInst }, alignment: AlignmentType.JUSTIFIED,
      spacing: { line: LINE, after: 30 }, children: runs(num[2]) }));
    i++; continue;
  }
  const isRef = /^\[\d+\]/.test(line);
  if (isRef) {
    const m = line.match(/^(\[\d+\])\s*(.*)$/);
    cur.children.push(new Paragraph({ alignment: AlignmentType.LEFT, indent: { left: 420, hanging: 420 }, spacing: { line: 240, after: 30 },
      children: [new TextRun({ text: m[1] + "\t", font: SONG, size: SMALL }), ...runs(m[2], { size: SMALL })],
      tabStops: [{ type: TabStopType.LEFT, position: 420 }] }));
    i++; continue;
  }
  if (line.startsWith("> 注")) {
    cur.children.push(new Paragraph({ alignment: AlignmentType.LEFT, spacing: { line: 240, after: 120 }, children: runs(line.slice(2), { size: SMALL }) }));
    i++; continue;
  }
  const noIndent = line.startsWith("> ");
  const text = noIndent ? line.slice(2) : line;
  cur.children.push(new Paragraph({ alignment: noIndent ? AlignmentType.LEFT : AlignmentType.JUSTIFIED, indent: noIndent ? undefined : { firstLine: 400 },
    spacing: { line: LINE, after: 20 }, children: runs(text) }));
  i++;
}

// single-column layout: no trailing balancing section (it made LibreOffice drop a final table)
const footer = new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ children: [PageNumber.CURRENT], size: 16, font: SONG })] })] });
const docSections = sections.filter((s) => s.children.length).map((s, k) => ({
  properties: {
    type: k === 0 ? undefined : SectionType.CONTINUOUS,
    page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MT, bottom: MB, left: ML, right: MR, footer: 567 } },
    column: s.cols === 2 ? { count: 2, space: COL_GAP, equalWidth: true } : { count: 1 },
  },
  footers: { default: footer },
  children: s.children,
}));

const doc = new Document({
  creator: (meta.authors || "").replace(/\s+/g, "、"), title: meta.title, subject: meta.subject || "", keywords: meta.keywords || "",
  description: meta.description || "", lastModifiedBy: (meta.authors || "").replace(/\s+/g, "、"),
  styles: { default: { document: { run: { font: SONG, size: BODY } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEI, size: 24, bold: false, color: "000000" }, paragraph: { outlineLevel: 0, spacing: { before: 240, after: 120 } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEI, size: BODY, bold: false, color: "000000" }, paragraph: { outlineLevel: 1, spacing: { before: 160, after: 80 } } },
    ] },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 300, hanging: 200 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1)", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 360, hanging: 260 } } } }] },
  ] },
  sections: docSections,
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, "sections", docSections.length); });
