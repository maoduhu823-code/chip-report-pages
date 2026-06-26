"use strict";

// ── 小工具（与 control.js 同款 el helper，文件作用域隔离）──────────
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

const TIER_LABELS = {
  official: "官方公告", regulatory: "监管披露", industry: "行业组织",
  academic: "会议论文", media: "行业媒体", aggregator: "聚合转载",
};
const FLAGS = ["分享", "追问", "忽略"];

const board = document.getElementById("board");
const emptyEl = document.getElementById("board-empty");
const selectEl = document.getElementById("report-select");
const sortEl = document.getElementById("sort-select");
const searchEl = document.getElementById("search-box");
const statEl = document.getElementById("board-stat");
const refreshBtn = document.getElementById("btn-refresh");
const tokenSummaryEl = document.getElementById("token-summary");

let STATE = { file: "", kind: "", date: "", articles: [], ratings: {}, nodes: new Map(), metadata: {} };
let curFilter = "all";

// ── 评分读取 ───────────────────────────────────────────────────
function ratingOf(url) { return STATE.ratings[url] || {}; }
function aiScore(a) { return typeof a.score === "number" ? a.score : null; }
function humanScore(url) {
  const v = ratingOf(url).human_score;
  return typeof v === "number" ? v : null;
}
function isDiverge(a) {
  const ai = aiScore(a), h = humanScore(a.url);
  return ai != null && h != null && Math.abs(ai - h) >= 3;
}

function googleSearchUrl(query) {
  return "https://www.google.com/search?q=" + encodeURIComponent(String(query || "").trim());
}

function splitTerms(value) {
  if (!value) return [];
  return String(value)
    .replaceAll("，", ",")
    .replaceAll("、", ",")
    .replaceAll("；", ",")
    .replaceAll(";", ",")
    .split(",")
    .map((term) => term.trim())
    .filter(Boolean);
}

// ── 数据加载 ───────────────────────────────────────────────────
async function latestRunReport() {
  try {
    const status = await fetch("/api/run/status").then((r) => r.json());
    if (status && status.status === "done" && status.report) return status.report;
  } catch (_) { /* 网络异常下继续显示空态 */ }
  return "";
}

async function loadReports(preselect) {
  if (!preselect) {
    preselect = await latestRunReport();
  }

  let reports = [];
  try {
    const data = await fetch("/api/reports").then((r) => r.json());
    reports = data.reports || [];
  } catch (_) { /* 网络异常下保持空 */ }

  selectEl.replaceChildren();
  if (!reports.length) {
    board.replaceChildren();
    emptyEl.hidden = false;
    if (tokenSummaryEl) tokenSummaryEl.hidden = true;
    statEl.textContent = "";
    return;
  }
  emptyEl.hidden = true;
  for (const r of reports) {
    const label = (r.kind === "business" ? "商业日报" : "技术周报") +
      " · " + r.date + " · " + r.count + " 条";
    selectEl.appendChild(el("option", { value: r.file }, label));
  }
  const target = (preselect && reports.some((r) => r.file === preselect)) ? preselect : reports[0].file;
  if (!new URLSearchParams(location.search).get("file")) {
    history.replaceState(null, "", "/dashboard?file=" + encodeURIComponent(target));
  }
  selectEl.value = target;
  await loadReport(target);
}

async function loadReport(file) {
  board.replaceChildren();
  statEl.textContent = "加载中…";
  let rep, ratings;
  try {
    [rep, ratings] = await Promise.all([
      fetch("/api/report?file=" + encodeURIComponent(file)).then((r) => r.json()),
      fetch("/api/ratings").then((r) => r.json()),
    ]);
  } catch (e) {
    statEl.textContent = "加载失败：" + e.message;
    return;
  }
  if (!rep || !rep.ok) {
    statEl.textContent = "报告加载失败";
    return;
  }
  STATE = {
    file: rep.file, kind: rep.kind, date: rep.date,
    articles: rep.articles || [], ratings: ratings || {}, nodes: new Map(),
    metadata: rep.metadata || {},
  };
  renderTokenSummary();
  renderBoard();
  applyView();
}

function fmtTokens(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未捕获";
  if (value >= 1000) return (value / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return String(value);
}

function fmtCost(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "—";
  return "$" + value.toFixed(value < 0.01 ? 4 : 3);
}

function renderTokenSummary() {
  if (!tokenSummaryEl) return;
  const m = STATE.metadata || {};
  const modelText = m.models && Object.keys(m.models).length
    ? Object.entries(m.models).map(([name, count]) => `${name} × ${count}`).join("，")
    : "—";
  tokenSummaryEl.replaceChildren(
    el("div", { class: "token-title" }, "Token 统计"),
    el("div", { class: "token-grid" }, [
      tokenItem("执行器", m.ai_executor || m.provider || "—"),
      tokenItem("调用次数", typeof m.calls === "number" ? String(m.calls) : "—"),
      tokenItem("输入", fmtTokens(m.input_tokens)),
      tokenItem("输出", fmtTokens(m.output_tokens)),
      tokenItem("合计", fmtTokens(m.total_tokens)),
      tokenItem("费用", fmtCost(m.cost_usd)),
      tokenItem("模型", modelText, true),
    ]),
  );
  tokenSummaryEl.hidden = false;
}

function tokenItem(label, value, wide = false) {
  return el("div", { class: "token-item" + (wide ? " wide" : "") }, [
    el("span", { class: "token-label" }, label),
    el("span", { class: "token-value" }, value),
  ]);
}

// ── 卡片渲染 ───────────────────────────────────────────────────
function renderBoard() {
  board.replaceChildren();
  STATE.nodes = new Map();
  for (const a of STATE.articles) {
    const node = cardNode(a);
    STATE.nodes.set(a.url, node);
    board.appendChild(node);
  }
}

function metaRow(a) {
  const row = el("div", { class: "meta" });
  if (aiScore(a) != null) row.appendChild(el("span", { class: "badge badge-score" }, "AI " + a.score + "/10"));
  if (a.source) row.appendChild(el("span", { class: "badge badge-source" }, a.source));
  const tier = a.tier || "media";
  row.appendChild(el("span", { class: "badge badge-tier-" + tier }, TIER_LABELS[tier] || "行业媒体"));
  const lang = a.language === "en" ? "en" : "zh";
  row.appendChild(el("span", { class: "badge badge-lang-" + lang }, lang === "en" ? "EN" : "中文"));
  if (a.pub_date) row.appendChild(el("span", { class: "badge badge-date" }, a.pub_date));
  (String(a.tags || "").replace(/,/g, "，").split("，")).forEach((t) => {
    t = t.trim();
    if (t) row.appendChild(el("span", { class: "badge badge-tag" }, t));
  });
  const terms = splitTerms(a.keywords);
  if (terms.length) {
    const kw = el("span", { class: "kw" }, "关键词：");
    terms.forEach((term) => {
      kw.appendChild(el("a", {
        href: googleSearchUrl(term),
        target: "_blank",
        rel: "noopener",
        title: "Google 搜索：" + term,
      }, term));
    });
    row.appendChild(kw);
  }
  return row;
}

function cardNode(a) {
  const r = ratingOf(a.url);
  const card = el("div", { class: "rcard", "data-url": a.url });
  card.appendChild(el("div", { class: "rank " + ((a.rank ?? 99) <= 3 ? "top3" : "") }, String(a.rank ?? "")));
  card.appendChild(el("img", {
    class: "thumb", loading: "lazy", alt: "", src: a.img, referrerpolicy: "no-referrer", decoding: "async",
  }));

  const body = el("div", { class: "rbody" });
  body.appendChild(el("h3", {}, [el("a", { href: a.url, target: "_blank", rel: "noopener" }, a.title || "（无标题）")]));
  if (a.summary) body.appendChild(el("p", { class: "rsum" }, a.summary));
  body.appendChild(metaRow(a));
  if (a.followup) body.appendChild(el("div", { class: "rfollowup" }, "🤔 分析师追问：" + a.followup));
  body.appendChild(rateRow(a, card));
  body.appendChild(flagRow(a, card));

  const note = el("textarea", { class: "note", placeholder: "备注…", rows: "1", spellcheck: "false" }, r.note || "");
  note.addEventListener("input", () => { autosize(note); scheduleSave(a); });
  body.appendChild(note);

  card.appendChild(body);
  // 初始化评分 / 分歧 UI
  setTimeout(() => { autosize(note); }, 0);
  refreshRateUI(a, card);
  return card;
}

function rateRow(a, card) {
  const row = el("div", { class: "rate-row" });
  row.appendChild(el("span", { class: "rate-label" }, "我的评分"));
  const pips = el("div", { class: "pips" });
  for (let k = 1; k <= 10; k++) {
    const pip = el("button", { class: "pip", type: "button", "data-k": k, title: k + " 分" }, String(k));
    pip.addEventListener("click", () => {
      const cur = humanScore(a.url);
      setHuman(a, cur === k ? null : k); // 再点同一格 = 取消
      refreshRateUI(a, card);
      scheduleSave(a);
    });
    pips.appendChild(pip);
  }
  row.appendChild(pips);
  row.appendChild(el("span", { class: "myscore" }, "—"));
  row.appendChild(el("span", { class: "diverge-badge" }, "分歧"));
  row.appendChild(el("a", {
    class: "clear-rate", href: "#",
    onclick: (e) => { e.preventDefault(); setHuman(a, null); refreshRateUI(a, card); scheduleSave(a); },
  }, "清除"));
  return row;
}

function flagRow(a, card) {
  const row = el("div", { class: "flags" });
  const flags = new Set((ratingOf(a.url).flags) || []);
  for (const f of FLAGS) {
    const chip = el("button", { class: "flag-chip" + (flags.has(f) ? " on" : ""), type: "button" }, f);
    chip.addEventListener("click", () => {
      const r = STATE.ratings[a.url] || {};
      const set = new Set(r.flags || []);
      if (set.has(f)) set.delete(f); else set.add(f);
      r.flags = [...set];
      STATE.ratings[a.url] = r;
      chip.classList.toggle("on", set.has(f));
      scheduleSave(a);
      updateStat();
    });
    row.appendChild(chip);
  }
  // 对生成效果不满意 → 移出已处理列表，下次运行重新处理/生成
  const regen = el("button", {
    class: "regen-btn", type: "button",
    title: "从已处理列表移除，下次运行将重新评分/生成这条",
  }, "♻️ 重新生成");
  regen.addEventListener("click", () => regenArticle(a, regen));
  row.appendChild(regen);
  return row;
}

async function regenArticle(a, btn) {
  const kindLabel = STATE.kind === "business" ? "商业日报" : "技术周报";
  if (!confirm(`把这条移出「已处理列表」？下次运行${kindLabel}会重新处理并重新生成它。`)) return;
  btn.disabled = true;
  try {
    const d = await fetch("/api/seen/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: STATE.kind, urls: [a.url] }),
    }).then((r) => r.json());
    if (d.ok && d.removed) { btn.textContent = "✓ 已移出，待重跑"; btn.classList.add("done"); }
    else if (d.ok) { btn.textContent = "不在已处理列表"; }
    else { btn.disabled = false; }
  } catch (_) { btn.disabled = false; }
}

function setHuman(a, val) {
  const r = STATE.ratings[a.url] || {};
  r.human_score = val;
  STATE.ratings[a.url] = r;
}

function refreshRateUI(a, card) {
  const h = humanScore(a.url);
  card.querySelectorAll(".pip").forEach((p) => {
    const k = Number(p.dataset.k);
    p.classList.toggle("on", h != null && k <= h);
    p.classList.toggle("exact", h != null && k === h);
  });
  card.querySelector(".myscore").textContent = h != null ? (h + "/10") : "—";
  card.classList.toggle("diverge", isDiverge(a));
  updateStat();
}

// ── 自动保存（按 URL 合并 + 串行）─────────────────────────────
// 同一条卡快速连点 flag/分数会触发多次保存：用「150ms 防抖合并 + 单条 URL 同时
// 只允许一个请求在途」消除乱序与丢更新。STATE 始终是用户意图的最新来源，不用
// 响应回灌评分字段（避免迟到的旧响应覆盖刚改的值）。
const saveState = {}; // url -> { timer, inflight, dirty }

function _st(url) {
  return saveState[url] || (saveState[url] = { timer: null, inflight: false, dirty: false });
}

function scheduleSave(a) {
  const st = _st(a.url);
  clearTimeout(st.timer);
  st.timer = setTimeout(() => flushSave(a), 150);
}

async function flushSave(a) {
  const st = _st(a.url);
  if (st.inflight) { st.dirty = true; return; } // 有在途保存：标脏，待其返回后再存最新值
  st.inflight = true;
  st.dirty = false;
  const card = STATE.nodes.get(a.url);
  const note = card ? card.querySelector(".note").value : "";
  const r = ratingOf(a.url);
  const payload = {
    file: STATE.file, url: a.url,
    human_score: (typeof r.human_score === "number" ? r.human_score : null),
    flags: r.flags || [], note,
    title: a.title, source: a.source, ai_score: a.score, category: a.category,
  };
  try {
    await fetch("/api/ratings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) { st.dirty = true; } // 失败留脏，稍后重试
  st.inflight = false;
  if (st.dirty) flushSave(a);
}

// ── 筛选 / 排序 / 搜索 ─────────────────────────────────────────
function applyView() {
  const q = searchEl.value.trim().toLowerCase();
  const sort = sortEl.value;
  const arts = STATE.articles.slice();

  const cmp = {
    ai: (x, y) => (aiScore(y) ?? -1) - (aiScore(x) ?? -1),
    human: (x, y) => (humanScore(y.url) ?? -1) - (humanScore(x.url) ?? -1),
    rank: (x, y) => (x.rank ?? 1e9) - (y.rank ?? 1e9),
    source: (x, y) => String(x.source || "").localeCompare(String(y.source || ""), "zh"),
  }[sort] || (() => 0);
  arts.sort(cmp);

  function visible(a) {
    const r = ratingOf(a.url);
    if (curFilter === "rated" && typeof r.human_score !== "number") return false;
    if (curFilter === "unrated" && typeof r.human_score === "number") return false;
    if (curFilter === "share" && !(r.flags || []).includes("分享")) return false;
    if (curFilter === "diverge" && !isDiverge(a)) return false;
    if (q) {
      const hay = ((a.title || "") + " " + (a.source || "") + " " + (a.keywords || "") + " " + (a.tags || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  for (const a of arts) {
    const node = STATE.nodes.get(a.url);
    if (!node) continue;
    const vis = visible(a);
    node.hidden = !vis;
    if (vis) board.appendChild(node); // 按排序顺序重排可见卡片
  }
  updateStat();
}

function updateStat() {
  const arts = STATE.articles;
  let rated = 0, shared = 0, div = 0;
  for (const a of arts) {
    const r = ratingOf(a.url);
    if (typeof r.human_score === "number") rated++;
    if ((r.flags || []).includes("分享")) shared++;
    if (isDiverge(a)) div++;
  }
  statEl.textContent = `共 ${arts.length} 条 · 已评 ${rated} · 标分享 ${shared} · 分歧 ${div}`;
}

// ── 杂项 ───────────────────────────────────────────────────────
function autosize(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ── 事件绑定 ───────────────────────────────────────────────────
selectEl.addEventListener("change", () => loadReport(selectEl.value));
refreshBtn.addEventListener("click", () => loadReports(selectEl.value));
sortEl.addEventListener("change", applyView);
searchEl.addEventListener("input", debounce(applyView, 200));
document.querySelectorAll("#filter-group .chip-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    curFilter = btn.dataset.filter;
    document.querySelectorAll("#filter-group .chip-btn").forEach((b) => b.classList.toggle("active", b === btn));
    applyView();
  });
});

// ── 初始化（支持 ?file= 预选，配合控制台「运行完成 → 看板」跳转）──
const preselect = new URLSearchParams(location.search).get("file") || "";
loadReports(preselect);
