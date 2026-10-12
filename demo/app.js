/* QuoteGuard demo: static page that replays recorded pilot outputs (data/cases.json). No network, no framework. */
(function () {
  "use strict";

  var STEPS = [
    "报价输入", "字段抽取与原文出处", "冲突与缺口", "包税风险与资质证据", "到仓成本对比", "决策护照与追问清单"
  ];
  var PROVIDER_CN = { deepseek: "DeepSeek", dashscope: "阿里云百炼", siliconflow: "硅基流动" };
  var LEVEL_CN = { none: "不标", verify: "需核实", high: "高" };
  var DATA = null;
  var state = { case: "A", cfg: null, step: 1, q: null };
  var sel = { cand: null, key: null };

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  function money(s) {
    if (s === null || s === undefined) return "—";
    return Number(s).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function caseObj() { return DATA.cases.find(function (c) { return c.id === state.case; }); }
  function cfgObj() { return caseObj().configs[state.cfg]; }
  function quoteMeta(cid) { return caseObj().quotes.find(function (q) { return q.cand_id === cid; }); }
  function modelName(cfg) {
    var m = caseObj().configs[cfg].model;
    return (m.requested[0] || cfg);
  }
  function qLabel(q, withAlias) {
    return q.label + (withAlias ? " · " + quoteMeta(q.cand_id).alias : "");
  }

  // ---------------------------------------------------------------- routing
  function parseHash() {
    var p = new URLSearchParams(location.hash.replace(/^#/, ""));
    var c = p.get("case");
    if (c && DATA.cases.some(function (x) { return x.id === c; })) state.case = c;
    var cs = caseObj();
    var cfg = p.get("cfg");
    state.cfg = cfg && cs.configs[cfg] ? cfg : (cs.configs[state.cfg] ? state.cfg : Object.keys(cs.configs)[0]);
    var s = parseInt(p.get("step"), 10);
    state.step = s >= 1 && s <= 6 ? s : 1;
    state.q = p.get("q");
  }
  function hashFor(o) {
    var s = Object.assign({ case: state.case, cfg: state.cfg, step: state.step, q: null }, o || {});
    var parts = ["case=" + s.case, "cfg=" + s.cfg, "step=" + s.step];
    if (s.q) parts.push("q=" + s.q);
    return "#" + parts.join("&");
  }
  function go(o) { location.hash = hashFor(o); }

  // ---------------------------------------------------------------- chrome
  function renderChrome() {
    var ctl = document.getElementById("case-ctl");
    ctl.textContent = "";
    DATA.cases.forEach(function (c) {
      var b = h("button", {
        type: "button", role: "tab", "aria-selected": String(c.id === state.case),
        onclick: function () { go({ case: c.id, cfg: Object.keys(c.configs)[0], q: null }); }
      }, "案例 " + c.id, h("small", { text: c.layer_cn }));
      ctl.appendChild(b);
    });
    var cs = caseObj();
    var sel_ = document.getElementById("cfg-sel");
    sel_.textContent = "";
    Object.keys(cs.configs).forEach(function (k) {
      var m = cs.configs[k].model;
      sel_.appendChild(h("option", { value: k, selected: k === state.cfg, text: m.requested[0] + " · " + PROVIDER_CN[m.provider] }));
    });
    sel_.onchange = function () { go({ cfg: sel_.value }); };
    var sc = document.getElementById("scenario");
    sc.textContent = "";
    sc.appendChild(h("span", null, h("b", { text: "案例 " + cs.id + "（" + cs.layer_cn + "）" }), "　" + cs.scenario));
    var nav = document.getElementById("sidenav");
    nav.textContent = "";
    STEPS.forEach(function (name, i) {
      nav.appendChild(h("a", { href: hashFor({ step: i + 1, q: null }), class: state.step === i + 1 ? "on" : "" },
        h("span", { class: "n", text: String(i + 1) }), h("span", { text: name })));
    });
    document.getElementById("footer").textContent =
      "dev 集合成任务（合成示例）· 预试验记录离线回放 · 配置 " + modelName(state.cfg) +
      "（复核方 " + modelName(cfgObj().model.review_config) + "）· 参数快照 " + DATA.meta.param_snapshot;
  }

  function notice(msg) {
    var n = document.getElementById("notice");
    if (!msg) { n.hidden = true; n.textContent = ""; return; }
    n.hidden = false;
    n.textContent = msg;
  }

  // ---------------------------------------------------------------- upload
  function sha256Fallback(bytes) {
    var K = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
      0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
      0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
      0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
    var H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    var l = bytes.length, bl = l * 8;
    var n = ((l + 9 + 63) >> 6) << 6;
    var m = new Uint8Array(n);
    m.set(bytes);
    m[l] = 0x80;
    var dv = new DataView(m.buffer);
    dv.setUint32(n - 8, Math.floor(bl / 4294967296));
    dv.setUint32(n - 4, bl >>> 0);
    var w = new Array(64);
    function rr(x, k) { return (x >>> k) | (x << (32 - k)); }
    for (var o = 0; o < n; o += 64) {
      for (var i = 0; i < 16; i++) w[i] = dv.getUint32(o + i * 4);
      for (i = 16; i < 64; i++) {
        var s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        var s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], hh = H[7];
      for (i = 0; i < 64; i++) {
        var S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25), ch = (e & f) ^ (~e & g);
        var t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
        var S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22), mj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + mj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H = [(H[0] + a) | 0, (H[1] + b) | 0, (H[2] + c) | 0, (H[3] + d) | 0, (H[4] + e) | 0, (H[5] + f) | 0, (H[6] + g) | 0, (H[7] + hh) | 0];
    }
    return H.map(function (x) { return ("00000000" + (x >>> 0).toString(16)).slice(-8); }).join("");
  }
  function sha256(buf) {
    if (window.crypto && window.crypto.subtle) {
      return window.crypto.subtle.digest("SHA-256", buf).then(function (d) {
        return Array.prototype.map.call(new Uint8Array(d), function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
      });
    }
    return Promise.resolve(sha256Fallback(new Uint8Array(buf)));
  }
  function onUpload(file) {
    if (!file) return;
    file.arrayBuffer().then(sha256).then(function (hex) {
      var hit = null;
      DATA.cases.forEach(function (c) {
        c.quotes.forEach(function (q) { if (q.file.sha256 === hex) hit = { c: c, q: q }; });
      });
      if (hit) {
        notice("");
        go({ case: hit.c.id, cfg: hit.c.configs[state.cfg] ? state.cfg : Object.keys(hit.c.configs)[0], step: 1, q: hit.q.cand_id });
      } else {
        notice("离线演示版回放已记录的模型输出，只识别内置样例；新报价需联网调用模型。（文件 " + file.name + "，SHA-256 " + hex.slice(0, 12) + "…）");
      }
    });
  }

  // ---------------------------------------------------------------- shared pieces
  function sheetTable(sheet) {
    var rows = sheet.rows;
    var width = rows.reduce(function (m, r) { return Math.max(m, r.length); }, 0);
    var head = h("tr", null, h("th", { text: "" }));
    for (var j = 0; j < width; j++) head.appendChild(h("th", { text: String.fromCharCode(65 + j) }));
    var t = h("table", { class: "sheet" }, h("thead", null, head));
    var body = h("tbody");
    rows.forEach(function (r, i) {
      var tr = h("tr", null, h("th", { text: String(i + 1) }));
      for (var k = 0; k < width; k++) tr.appendChild(h("td", { text: r[k] || "" }));
      body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
  }
  function originalView(q) {
    var o = q.original;
    if (o.kind === "image") return h("img", { src: o.src, alt: q.label + " 原图" });
    if (o.kind === "table") return h("div", null, o.sheets.map(function (s) { return sheetTable(s); }));
    return h("pre", { class: "orig", text: o.text });
  }
  function tabsFor(cur, onPick, flagFn) {
    var cf = cfgObj();
    return h("div", { class: "tabs", role: "tablist" }, cf.quotes.map(function (q) {
      var flag = flagFn ? flagFn(q) : false;
      return h("button", {
        type: "button", role: "tab", "aria-selected": String(q.cand_id === cur),
        onclick: function () { onPick(q.cand_id); }
      }, qLabel(q, true), flag ? h("span", { class: "mark", text: "●" }) : null);
    }));
  }
  function highlight(container, text, quote) {
    container.textContent = "";
    var re = null;
    if (quote) {
      var chars = Array.from(quote.replace(/\s+/g, ""));
      if (chars.length) {
        try {
          re = new RegExp(chars.map(function (c) { return c.replace(/[.*+?^${}()|[\]\\\/-]/g, "\\$&"); }).join("\\s*"));
        } catch (e) { re = null; }
      }
    }
    var m = re ? re.exec(text) : null;
    if (!m) { container.appendChild(document.createTextNode(text)); return false; }
    container.appendChild(document.createTextNode(text.slice(0, m.index)));
    var mk = h("mark", { text: m[0] });
    container.appendChild(mk);
    container.appendChild(document.createTextNode(text.slice(m.index + m[0].length)));
    if (mk.scrollIntoView) mk.scrollIntoView({ block: "nearest" });
    return true;
  }

  // ---------------------------------------------------------------- step 1
  function stepInput() {
    var cs = caseObj(), sh = cs.shipment;
    var g = sh.goods[0];
    var cartons = sh.cartons.map(function (c) {
      return c.n + " 箱 × " + c.length_cm + "×" + c.width_cm + "×" + c.height_cm + " cm，" + c.kg + " kg/箱";
    }).join("；");
    var facts = [
      ["商品", g.desc + "，" + g.qty + " 件"],
      ["HTS 编码", g.hts],
      ["货值", sh.total_value_usd + " 美元（单价 " + g.unit_value_usd + " 美元）"],
      ["运输方式", sh.mode_cn],
      ["箱规与箱数", cartons + "；共 " + sh.n_cartons + " 箱，毛重 " + sh.gross_kg + " kg"],
      ["目的仓与地址类型", sh.dest.code + "，邮编 " + sh.dest.zip + "，" + sh.dest.addr_type_cn + "，" + sh.dest.remote_cn],
      ["发运日期", sh.ship_date],
      ["时效要求", "发运后 " + sh.deadline_days + " 天内到仓"]
    ];
    var shipCard = h("section", { class: "card" },
      h("header", null, h("h3", { text: "货物与任务" }), h("span", { class: "sub", text: cs.task_id + "（合成任务）" })),
      h("div", { class: "facts" }, facts.map(function (f) {
        return h("div", { class: "fact" }, h("div", { class: "k", text: f[0] }), h("div", { class: "v", text: f[1] }));
      })));
    var cards = cs.quotes.map(function (q) {
      return h("section", { class: "card qcard" + (state.q === q.cand_id ? " hl" : ""), id: "qc-" + q.cand_id },
        h("header", null, h("h3", { text: q.label + " · " + q.alias }), h("span", { class: "tag", text: q.modality_cn }),
          h("span", { class: "sub", text: "报价主体 " + q.entity })),
        h("div", { class: "body" }, originalView(q)));
    });
    return h("div", null,
      h("div", { class: "step-head" }, h("h2", { text: "1 报价输入" }),
        h("span", { class: "hint", text: "4 份报价，形态包含文字、表格（Excel）和图片；以下为原件。" })),
      shipCard, h("div", { style: "height:12px" }), h("div", { class: "grid2" }, cards));
  }

  // ---------------------------------------------------------------- step 2
  function verifyCell(f) {
    if (!f.quotes.length) return h("span", { class: "muted", text: "—" });
    var allOk = f.quotes.every(function (q) { return q.verified; });
    return h("span", { class: allOk ? "ok" : "no", title: allOk ? "出处在原文中" : "出处不在原文中" });
  }
  function stepFields() {
    var cf = cfgObj();
    var cand = sel.cand && cf.quotes.some(function (q) { return q.cand_id === sel.cand; }) ? sel.cand : (state.q || cf.quotes[0].cand_id);
    sel.cand = cand;
    var q = cf.quotes.find(function (x) { return x.cand_id === cand; });
    var meta = quoteMeta(cand);
    var srcText = q.model_text;
    var srcBox = h("pre", { class: "orig", id: "src-text" });
    var trBox = h("pre", { class: "orig", id: "tr-text" });
    var panelTitle = meta.modality === "image" ? "原图与模型转写" : (meta.modality === "table" ? "模型读取的文本（表格转为 Markdown）" : "报价原文");
    var rows = q.fields.map(function (f) {
      var tr = h("tr", { "data-key": f.key, class: sel.key === f.key ? "sel" : "", onclick: function () { pick(f); } },
        h("td", null, h("b", { text: f.label })),
        h("td", null,
          f.value ? h("span", { text: f.value }) : h("span", { class: "muted", text: "未抽取到" }),
          " ", f.status === "conflicting" ? h("span", { class: "tag warn", text: "前后矛盾" }) : (f.status === "implied" ? h("span", { class: "tag", text: "隐含" }) : null),
          f.downgraded ? h("span", { class: "tag warn", text: "出处无法核验，按缺失处理" }) : null),
        h("td", null, f.quotes.length ? f.quotes.map(function (x) {
          return h("span", { class: "q" + (x.verified ? "" : " bad"), text: x.text });
        }) : h("span", { class: "muted", text: "—" })),
        h("td", { class: "chk" }, verifyCell(f)));
      return tr;
    });
    function pick(f) {
      sel.key = f.key;
      Array.prototype.forEach.call(document.querySelectorAll(".ftable tr[data-key]"), function (r) {
        r.classList.toggle("sel", r.getAttribute("data-key") === f.key);
      });
      var target = f.quotes.find(function (x) { return x.verified; }) || f.quotes[0];
      var qtxt = target ? target.text : null;
      if (meta.modality === "image") {
        highlight(trBox, q.transcript, qtxt);
      } else {
        highlight(srcBox, srcText, qtxt);
      }
    }
    var table = h("table", { class: "ftable" },
      h("thead", null, h("tr", null, h("th", { text: "字段" }), h("th", { text: "抽取值" }), h("th", { text: "原文出处" }), h("th", { text: "核验", class: "chk" }))),
      h("tbody", null, rows));
    var right;
    if (meta.modality === "image") {
      trBox.textContent = q.transcript;
      right = h("div", { class: "src-two" },
        h("div", null, h("h4", { text: "原图" }), h("img", { src: meta.original.src, alt: "原图" })),
        h("div", null, h("h4", { text: "转写文字（" + modelName(state.cfg) + " 视觉）" }), trBox));
    } else {
      srcBox.textContent = srcText;
      right = srcBox;
    }
    var nBad = q.fields.filter(function (f) { return f.downgraded || f.quotes.some(function (x) { return !x.verified; }); }).length;
    var note = h("p", { class: "small muted", style: "margin:6px 0 0" },
      "核验：出处片段须出现在模型读到的文本中；全部片段找不到的字段按缺失处理。本报价 " + nBad + " 个字段未通过核验。点击字段行，在右侧原文中高亮出处。");
    var out = h("div", null,
      h("div", { class: "step-head" }, h("h2", { text: "2 字段抽取与原文出处" }),
        h("span", { class: "hint", text: "抽取模型 " + modelName(state.cfg) + "；每个字段附原文片段并回查原文。" })),
      tabsFor(cand, function (c) { sel.cand = c; sel.key = null; render(); }, function (x) { return x.conflicts.length > 0; }),
      h("div", { class: "split" },
        h("section", { class: "card" }, h("div", { class: "scroll-panel" }, table)),
        h("section", { class: "card src-panel" },
          h("header", null, h("h3", { text: panelTitle })), h("div", { class: "body" }, right))),
      note);
    return out;
  }

  // ---------------------------------------------------------------- step 3
  function stepConflicts() {
    var cf = cfgObj();
    var partner = cf.model.review_config;
    var nDiff = cf.quotes.reduce(function (n, q) { return n + q.fields.filter(function (f) { return f.review_differs; }).length; }, 0);
    var blocks = cf.quotes.map(function (q) {
      var has = q.conflicts.length || q.gaps.length || q.defaulted.length || q.hidden_fees.length;
      if (!has && !q.triggers.length) {
        return h("div", { class: "clean" }, h("b", { text: qLabel(q, true) }), "　未发现冲突、缺口或需另计的费用。");
      }
      var secs = [];
      if (q.conflicts.length) {
        secs.push(h("div", { class: "sec" }, h("h4", { text: "冲突字段（两个取值都有出处，不由系统擅自取舍）" }),
          q.conflicts.map(function (c) {
            return h("div", { style: "margin-bottom:6px" },
              h("div", { class: "small" }, h("b", { text: c.label }), "　", h("span", { class: "tag warn", text: c.origin_cn }),
                " ", c.in_cost ? h("span", { class: "tag", text: "影响成本" }) : h("span", { class: "tag", text: "不影响成本" })),
              h("div", { class: "vs" }, c.values.map(function (v, i) {
                var used = c.resolved_idx === i;
                return h("div", { class: used ? "used" : "" }, v.value, used ? h("span", { class: "muted", text: "　← 成本计算采用" }) : null,
                  v.quotes.length ? h("span", { class: "q", text: "出处：" + v.quotes.join(" ／ ") }) : null);
              })));
          })));
      }
      if (q.gaps.length) {
        secs.push(h("div", { class: "sec" }, h("h4", { text: "缺口字段（必需条款未写明）" }),
          h("div", { class: "chips" }, q.gaps.map(function (g) { return h("span", { class: "tag warn", text: g.label }); }))));
      }
      if (q.defaulted.length) {
        secs.push(h("div", { class: "sec" }, h("h4", { text: "按默认值处理的字段" }),
          h("div", { class: "chips" }, q.defaulted.map(function (g) { return h("span", { class: "tag", text: g.label + "（缺失，按未包税 / 不收取处理）" }); }))));
      }
      if (q.hidden_fees.length) {
        secs.push(h("div", { class: "sec" }, h("h4", { text: "运费之外另计的费用项（已计入到仓成本）" }),
          h("div", { class: "chips" }, q.hidden_fees.map(function (g) { return h("span", { class: "tag", text: g.label }); }))));
      }
      if (q.triggers.length) {
        secs.push(h("div", { class: "sec" }, h("h4", { text: "触发复核的条件" }),
          h("div", { class: "small" }, q.triggers.join("；"))));
      }
      return h("section", { class: "card qissue" },
        h("header", null, h("h3", { text: qLabel(q, true) }), h("span", { class: "tag", text: quoteMeta(q.cand_id).modality_cn }),
          q.conflicts.length ? h("span", { class: "tag warn", text: q.conflicts.length + " 项冲突" }) : null),
        h("div", { class: "body" }, secs));
    });
    return h("div", null,
      h("div", { class: "step-head" }, h("h2", { text: "3 冲突与缺口" }),
        h("span", { class: "hint", text: "跨模型复核：" + modelName(partner) + " 独立抽取同一批报价，与 " + modelName(state.cfg) + " 逐字段对照，取值不同的字段共 " + nDiff + " 项。成本按冲突字段中成本较低的取值计算，追问后再确定。" })),
      h("div", { class: "grid2" }, blocks));
  }

  // ---------------------------------------------------------------- step 4
  function stepGate() {
    var cf = cfgObj();
    var docsSeen = {};
    var rows = cf.quotes.map(function (q) {
      var d = q.ddp;
      var gate = [h("div", null, h("span", { class: "level " + d.level, text: LEVEL_CN[d.level] }), " ",
        d.ddp_missing ? h("span", { class: "tag warn", text: "包税条款未写明" }) : null,
        d.ddp_conflicting ? h("span", { class: "tag warn", text: "包税条款有两种取值" }) : null)];
      if (d.ddp_text) gate.push(h("div", { class: "small", style: "margin-top:3px", text: "报价写法：" + d.ddp_text }));
      var kv = [["计费重", d.chargeable_kg + " kg"], ["税费下限（关税 + MPF）", d.tax_floor_cny_per_kg + " 元/kg"]];
      if (d.is_ddp) {
        kv.push(["报价包税单价", d.quote_cny_per_kg + " 元/kg"]);
        kv.push(["运价参照下限", d.freight_ref_cny_per_kg === null ? "无" : d.freight_ref_cny_per_kg + " 元/kg（" + (d.floor_source === "zone" ? "按目的地分区" : d.floor_source === "national" ? "全国" : "无") + "）"]);
        if (d.freight_ref_cny_per_kg !== null) {
          var thr = (Number(d.tax_floor_cny_per_kg) + Number(d.freight_ref_cny_per_kg)).toFixed(2);
          kv.push(["比较", d.quote_cny_per_kg + (Number(d.quote_cny_per_kg) < Number(d.tax_floor_cny_per_kg) ? " < 税费下限 " + d.tax_floor_cny_per_kg : (Number(d.quote_cny_per_kg) < Number(thr) ? " < 下限加参照 " + thr : " ≥ 下限加参照 " + thr)) + " → " + LEVEL_CN[d.level]]);
        }
      }
      gate.push(h("div", { class: "kv" }, kv.map(function (x) { return [h("span", { class: "k", text: x[0] }), h("span", { class: "num", text: x[1] })]; })));
      if (!d.is_ddp && !d.ddp_missing) gate.push(h("div", { class: "small muted", style: "margin-top:3px", text: "报价不含包税，税费由买家另付，未触发包税判定。" }));
      if (d.docs.length) {
        gate.push(h("div", { class: "small", style: "margin-top:3px" }, h("b", { text: "应索要单证：" }), d.docs.join("、")));
      }
      var claims;
      if (!q.claims.length) {
        claims = h("div", { class: "muted small", text: "报价中无资质宣称。" });
      } else {
        claims = q.claims.map(function (c) {
          var bad = c.status === "insufficient" || c.status === "contradicted";
          return h("div", { class: "claimbox" },
            h("div", null, h("span", { class: "tag " + (bad ? "warn" : ""), text: c.status_cn }), " ", h("b", { text: c.type_cn }), "　主体 " + c.entity + (c.reg_no ? "，登记号 " + c.reg_no : "")),
            c.quotes.length ? h("div", { class: "muted", text: "宣称原文：" + c.quotes.map(function (x) { return x.text; }).join(" ／ ") }) : null,
            c.reason ? h("div", { text: "说明：" + c.reason }) : null,
            c.records.length ? h("div", { text: "登记库记录：" + c.records.map(function (r) { return r.name + "（" + r.no + "，" + r.status + "）"; }).join("；") })
              : (c.status === "insufficient" ? h("div", { text: "登记库（" + c.databases.join("、") + "）中没有该主体的记录。" }) : null),
            bad ? h("div", { class: "warn-text", text: "应索要：该主体的登记证明与登记号。" }) : null);
        });
      }
      return h("div", { class: "gate-row" },
        h("div", null, h("b", { text: q.label }), h("div", { class: "small muted", text: quoteMeta(q.cand_id).alias })),
        h("div", null, gate), h("div", null, claims));
    });
    var c0 = caseObj();
    var ddpRule = h("p", { class: "rule" },
      "判定规则：报价包税单价低于税费下限时为“高”；低于税费下限加运价参照下限时为“需核实”；其余为“不标”。“高”的报价不进入可行集。");
    return h("div", null,
      h("div", { class: "step-head" }, h("h2", { text: "4 包税风险与资质证据" }),
        h("span", { class: "hint", text: "登记库为合成数据，快照 " + c0.registry.snapshot_id + "；资质状态分四类：有证据、证据不足、证据与宣称矛盾、证据与本次运输不相关。" })),
      ddpRule,
      h("section", { class: "card" },
        h("div", { class: "gate-row gate-head" }, h("div", { text: "报价" }), h("div", { text: "包税风险" }), h("div", { text: "资质宣称与登记库比对" })),
        rows));
  }

  // ---------------------------------------------------------------- step 5
  function quoteVerdict(q, dec) {
    if (dec.type === "choose" && dec.choice === q.cand_id) return "recommended";
    if (!q.cost.feasible) return "infeasible";
    if (dec.type === "ask" && dec.ask_fields.some(function (f) { return f.split(".")[0] === q.cand_id; })) return "ask";
    return "feasible";
  }
  function stepCost() {
    var cf = cfgObj(), dec = cf.decision;
    var maxV = 0;
    cf.quotes.forEach(function (q) {
      maxV = Math.max(maxV, Number(q.cost.range ? q.cost.range[1] : q.cost.total), Number(q.cost.total));
    });
    var axis = maxV * 1.04;
    var bars = cf.quotes.map(function (q) {
      var v = quoteVerdict(q, dec);
      var best = v === "recommended", off = v === "infeasible";
      var base = Number(q.cost.total), hi = q.cost.range ? Number(q.cost.range[1]) : base;
      var track = h("div", { class: "track" },
        hi > base ? h("div", { class: "fillbar ext", style: "width:" + (hi / axis * 100) + "%" }) : null,
        h("div", { class: "fillbar " + (best ? "best" : off ? "off" : ""), style: "width:" + (base / axis * 100) + "%" }));
      var val = h("div", { class: "val num" }, money(q.cost.total) + " 元",
        q.cost.range ? h("div", { class: "small muted", text: "区间 " + money(q.cost.range[0]) + " – " + money(q.cost.range[1]) }) : null);
      return h("div", { class: "bar-row" + (off ? " off" : "") },
        h("div", { class: "lab" + (best ? " best" : "") }, qLabel(q, true), best ? h("div", { class: "tag solid", text: "推荐" }) : null,
          off ? h("div", { class: "tag", text: "不可行" }) : null, v === "ask" ? h("div", { class: "tag warn", text: "待追问" }) : null),
        track, val);
    });
    // line-item table
    var codes = [];
    cf.quotes.forEach(function (q) {
      q.cost.lines.forEach(function (l) { if (codes.indexOf(l.code) < 0) codes.push(l.code); });
    });
    var labelOf = {};
    cf.quotes.forEach(function (q) { q.cost.lines.forEach(function (l) { labelOf[l.code] = l.label; }); });
    function lineAmt(q, code) {
      var l = q.cost.lines.find(function (x) { return x.code === code; });
      return l ? l.amount : null;
    }
    var head = h("tr", null, h("th", { text: "费用项（元）" }), cf.quotes.map(function (q) {
      var v = quoteVerdict(q, dec);
      return h("th", { class: "num" + (v === "recommended" ? " best" : v === "infeasible" ? " off" : ""), text: q.label });
    }));
    var body = [];
    body.push(h("tr", null, h("td", { text: "计费重（kg）" }), cf.quotes.map(function (q) { return h("td", { class: "num", text: q.cost.chargeable_kg }); })));
    var zeroLines = [];
    codes.forEach(function (code) {
      var all0 = cf.quotes.every(function (q) { var a = lineAmt(q, code); return a === null || Number(a) === 0; });
      if (all0) { zeroLines.push(labelOf[code]); return; }
      body.push(h("tr", null, h("td", { text: labelOf[code] }), cf.quotes.map(function (q) {
        var v = quoteVerdict(q, dec), a = lineAmt(q, code);
        return h("td", { class: "num" + (v === "recommended" ? " best" : v === "infeasible" ? " off" : ""), text: a === null ? "—" : money(a) });
      })));
    });
    body.push(h("tr", { class: "total" }, h("td", { text: "确定成本合计" }), cf.quotes.map(function (q) {
      var v = quoteVerdict(q, dec);
      return h("td", { class: "num" + (v === "recommended" ? " best" : v === "infeasible" ? " off" : ""), text: money(q.cost.total) });
    })));
    body.push(h("tr", null, h("td", { text: "每件成本" }), cf.quotes.map(function (q) { return h("td", { class: "num", text: money(q.cost.per_unit) }); })));
    body.push(h("tr", null, h("td", { text: "不确定字段导致的区间" }), cf.quotes.map(function (q) {
      return h("td", { class: "num", text: q.cost.range ? money(q.cost.range[0]) + "–" + money(q.cost.range[1]) : "无" });
    })));
    body.push(h("tr", null, h("td", { text: "时效 / 要求" }), cf.quotes.map(function (q) {
      var t = q.cost.transit;
      var over = t && t[1] > q.cost.deadline_days;
      return h("td", { class: "num" + (over ? " warn-text" : ""), text: t ? (t[0] === t[1] ? t[0] : t[0] + "–" + t[1]) + " 天 / " + q.cost.deadline_days + " 天" : "未写明" });
    })));
    body.push(h("tr", null, h("td", { text: "是否可行" }), cf.quotes.map(function (q) {
      return h("td", { class: "r" + (q.cost.feasible ? "" : " warn-text") }, q.cost.feasible ? "可行" : q.cost.infeasible_reasons.join("；"));
    })));
    var table = h("table", { class: "costtbl" }, h("thead", null, head), h("tbody", null, body));
    return h("div", null,
      h("div", { class: "step-head" }, h("h2", { text: "5 到仓成本对比" }),
        h("span", { class: "hint", text: "条形为确定成本（冲突字段取较低成本取值），虚线段为另一取值下的增加额；灰显为不可行。" })),
      h("section", { class: "card" }, h("div", { class: "body bars" }, bars)),
      h("div", { style: "height:12px" }),
      h("section", { class: "card" }, h("div", { class: "body" }, table)),
      h("p", { class: "small muted", text: "各报价均为 0 的费项：" + (zeroLines.join("、") || "无") + "。成本含运费、燃油、附加费、关税、MPF、HMF、报关费与 ISF，由确定性计算器按参数快照 " + DATA.meta.param_snapshot + "（汇率日 " + DATA.meta.fx_date + "，1 美元 = " + DATA.meta.fx_cny_per_usd + " 元）重算，为计算值。" }));
  }

  // ---------------------------------------------------------------- step 6
  function copyText(text, btn) {
    function done() { var o = btn.textContent; btn.textContent = "已复制"; setTimeout(function () { btn.textContent = o; }, 1500); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else fallback();
    function fallback() {
      var ta = h("textarea", { style: "position:fixed;left:-9999px" });
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { btn.textContent = "请手动复制"; }
      document.body.removeChild(ta);
    }
  }
  function goldLine(c) {
    var g = c.gold;
    return g.decision === "choose" ? "选择 " + c.quotes.find(function (q) { return q.cand_id === g.choice; }).label : "追问：" + g.ask_fields.map(function (f) {
      var p = f.split("."); return c.quotes.find(function (q) { return q.cand_id === p[0]; }).label + " " + p[1];
    }).join("、");
  }
  function stepPassport() {
    var c = caseObj(), cf = cfgObj(), dec = cf.decision;
    var verdict = dec.type === "choose"
      ? "结论：选择 " + dec.choice_label + "（" + quoteMeta(dec.choice).alias + "）"
      : "结论：暂不选择，先向货代追问";
    var risks = dec.risks.length ? dec.risks.map(function (r) {
      return h("li", null, h("span", { class: "kind", text: "[" + r.kind + "]" }), r.text);
    }) : [h("li", { text: "未发现风险项。" })];
    var ev = h("table", { class: "evtable" },
      h("thead", null, h("tr", null, ["报价", "状态", "时效", "包税风险", "资质证据", "确定成本（元）"].map(function (t, i) { return h("th", { text: t, class: i === 5 ? "num" : "" }); }))),
      h("tbody", null, cf.quotes.map(function (q) {
        var v = quoteVerdict(q, dec);
        var vt = { recommended: "推荐", feasible: "可行", infeasible: "不可行", ask: "待追问" }[v];
        var tt = q.cost.transit;
        var cl = q.claims.length ? q.claims.map(function (x) { return x.status_cn; }).join("、") : "无宣称";
        return h("tr", null,
          h("td", null, h("b", { text: q.label }), " " + quoteMeta(q.cand_id).alias),
          h("td", null, h("span", { class: "tag " + (v === "recommended" ? "solid" : v === "infeasible" || v === "ask" ? "warn" : ""), text: vt })),
          h("td", { class: tt && tt[1] > q.cost.deadline_days ? "warn-text" : "", text: tt ? (tt[0] === tt[1] ? tt[0] : tt[0] + "–" + tt[1]) + " 天（要求 ≤" + q.cost.deadline_days + "）" : "未写明" }),
          h("td", { class: q.ddp.level === "none" ? "" : "warn-text", text: LEVEL_CN[q.ddp.level] }),
          h("td", { class: q.claims.some(function (x) { return x.status === "insufficient" || x.status === "contradicted"; }) ? "warn-text" : "", text: cl }),
          h("td", { class: "num", text: money(q.cost.total) + (q.cost.range ? "（至 " + money(q.cost.range[1]) + "）" : "") }));
      })));
    var qs = dec.questions;
    var qBlock = qs.length
      ? [h("div", { class: "qline" }, h("h3", { style: "border:0;margin:0", text: "追问清单（发给货代）" }),
        h("button", { type: "button", class: "btn btn-sm no-print", onclick: function (e) { copyText(qs.map(function (x, i) { return (i + 1) + ". " + x; }).join("\n"), e.target); } }, "复制")),
        h("ol", null, qs.map(function (x) { return h("li", { text: x }); }))]
      : [h("h3", { text: "追问清单" }), h("p", { class: "muted small", text: "无需追问：所有可行报价的成本相关字段均已确定。" })];
    var passport = h("section", { class: "passport", id: "passport" },
      h("div", { class: "ptitle" }, h("h2", { text: "决策护照" }),
        h("span", { class: "pmeta", text: "案例 " + c.id + " · " + c.task_id + "（合成任务）· 配置 " + modelName(state.cfg) })),
      h("div", { class: "verdict" + (dec.type === "ask" ? " ask" : ""), text: verdict }),
      h("p", { style: "margin:2px 0", text: dec.reason }),
      h("h3", { text: "风险项" }), h("ul", null, risks),
      h("h3", { text: "证据状态" }), ev,
      qBlock,
      h("div", { class: "print-note", text: "dev 集合成任务（合成示例）· 预试验记录离线回放 · 配置 " + modelName(state.cfg) }));
    // folds
    var m0 = cf.m0;
    var m0Body = m0.decision === null
      ? h("p", { class: "small", text: "该次输出未能解析为有效结论（预试验单次输出）。" })
      : h("div", null,
        h("p", { class: "small" }, "同一模型直接读报价并作答（无抽取、计算与核验）。结论：",
          h("b", { text: m0.decision === "choose" ? "选择 " + m0.choice_label : "追问" }), "；与标准答案" + (m0.match_gold ? "一致" : "不一致") + "。"),
        m0.questions.length ? [h("h4", { text: "其追问" }), h("ol", { class: "small" }, m0.questions.map(function (x) { return h("li", { text: x }); }))] : null,
        m0.risks.length ? [h("h4", { text: "其风险提示" }), h("ul", { class: "small" }, m0.risks.map(function (x) { return h("li", { text: x }); }))] : null);
    var goldRows = c.quotes.map(function (q) {
      var g = c.gold.per_cand[q.cand_id];
      var sys = cf.quotes.find(function (x) { return x.cand_id === q.cand_id; });
      var diff = Number(g.total_cny) !== Number(sys.cost.total);
      return h("tr", null, h("td", { text: q.label }), h("td", { class: "num", text: money(g.total_cny) }),
        h("td", { class: "num", text: money(sys.cost.total) }), h("td", { text: g.ddp_level === "none" ? "不标" : LEVEL_CN[g.ddp_level] }),
        h("td", { text: g.feasible ? "可行" : "不可行" }), h("td", { class: diff ? "warn-text" : "", text: diff ? "有差异（系统取冲突较低值）" : "一致" }));
    });
    var goldBody = h("div", null,
      h("p", { class: "small" }, "标准答案：", h("b", { text: goldLine(c) }), "；系统结论" + (dec.match_gold ? "与之一致。" : "与之不一致。")),
      h("table", { class: "evtable" }, h("thead", null, h("tr", null, ["报价", "标准成本（元）", "系统确定成本（元）", "标准包税档", "标准可行性", "对比"].map(function (t, i) { return h("th", { text: t, class: i === 1 || i === 2 ? "num" : "" }); }))),
        h("tbody", null, goldRows)),
      h("p", { class: "small muted", text: "标准答案由数据生成器给出（合成任务，答案已知）。冲突字段的标准成本按其真实取值计算，系统在尚未追问时取较低成本取值，二者可能不同。" }));
    var mdl = cf.model;
    var meta = h("div", { class: "small" },
      h("div", { text: "抽取模型：" + mdl.requested.join("、") + "（返回 " + mdl.returned.join("、") + "），服务商 " + PROVIDER_CN[mdl.provider] }),
      h("div", { text: "复核模型：" + mdl.review_requested.join("、") + "（" + PROVIDER_CN[mdl.review_provider] + "）" }),
      mdl.vision_requested.length ? h("div", { text: "图片转写模型：" + mdl.vision_requested.join("、") }) : null,
      h("div", { text: "本次 M4 输出涉及的模型调用 " + cf.n_calls + " 次（预试验记录），记账成本 " + (cf.cost.CNY ? cf.cost.CNY.toFixed(3) + " 元" : "") + (cf.cost.USD ? (cf.cost.CNY ? " + " : "") + cf.cost.USD.toFixed(4) + " 美元" : "") + "；记录时间 " + (mdl.recorded_at || "").slice(0, 10) }),
      h("div", { text: "数据性质：dev 集合成任务；模型输出为预试验记录，演示中不发起新调用；成本、包税档、资质状态由确定性代码离线重算；登记库快照 " + c.registry.snapshot_id + " 为合成数据。" }));
    return h("div", null,
      h("div", { class: "step-head no-print" }, h("h2", { text: "6 决策护照与追问清单" }),
        h("span", { class: "hint", text: "可打印为一页。" }), h("div", { class: "spacer" }),
        h("button", { type: "button", class: "btn btn-sm", onclick: function () { window.print(); } }, "打印护照")),
      passport,
      h("details", { class: "fold" }, h("summary", { text: "对照：同模型直接提问（M0）" }), h("div", { class: "body" }, m0Body)),
      h("details", { class: "fold" }, h("summary", { text: "对照：标准答案" }), h("div", { class: "body" }, goldBody)),
      h("details", { class: "fold" }, h("summary", { text: "运行信息" }), h("div", { class: "body" }, meta)));
  }

  // ---------------------------------------------------------------- render
  function render() {
    renderChrome();
    var main = document.getElementById("main");
    main.textContent = "";
    var fn = [stepInput, stepFields, stepConflicts, stepGate, stepCost, stepPassport][state.step - 1];
    main.appendChild(fn());
    main.scrollTop = 0;
    if (state.step === 1 && state.q) {
      var el = document.getElementById("qc-" + state.q);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    }
  }

  function onHash() {
    var prev = state.case + "/" + state.cfg;
    parseHash();
    if (prev !== state.case + "/" + state.cfg) { sel.cand = null; sel.key = null; }
    if (state.q && state.step === 2) sel.cand = state.q;
    render();
  }

  function init() {
    document.getElementById("upload-btn").addEventListener("click", function () { document.getElementById("upload-input").click(); });
    document.getElementById("upload-input").addEventListener("change", function (e) {
      onUpload(e.target.files[0]); e.target.value = "";
    });
    window.addEventListener("hashchange", onHash);
    fetch("data/cases.json").then(function (r) { return r.json(); }).then(function (d) {
      DATA = d;
      if (!location.hash) { parseHash(); history.replaceState(null, "", hashFor()); }
      onHash();
    }).catch(function (e) {
      document.getElementById("main").textContent = "无法读取 data/cases.json：请先运行 build_data.py，并用 python3 -m http.server 8000 -d demo 打开页面。（" + e + "）";
    });
  }
  init();
})();
