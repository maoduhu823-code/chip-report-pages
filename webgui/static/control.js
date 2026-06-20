"use strict";

// ── 元数据：变量与 Prompt 的中文标签 ───────────────────────────
const VAR_META = [
  { key: "business_min_score", label: "商业日报收录阈值", desc: "评分≥该值即收录，不限条目", step: 1, min: 0, max: 10 },
  { key: "tech_top_n", label: "技术周报精读条数", desc: "Top N 精读，其余进附录", step: 1, min: 1, max: 100 },
  { key: "business_age_days", label: "商业日报时效窗口(天)", desc: "只看近 N 天的 RSS 文章", step: 1, min: 1, max: 30 },
  { key: "tech_age_days", label: "技术周报时效窗口(天)", desc: "只看近 N 天的 RSS 文章", step: 1, min: 1, max: 60 },
  { key: "scoring_batch_size", label: "批量评分每批条数", desc: "调大省调用次数，调小更稳", step: 1, min: 1, max: 200 },
  { key: "dedup_similarity_threshold", label: "去重相似度阈值", desc: "标题相似度超过即视为重复", step: 0.01, min: 0, max: 1 },
];

const PROMPT_META = [
  { key: "TECH_RELEVANCE_PROMPT", label: "技术版 · 相关度评分" },
  { key: "BUSINESS_RELEVANCE_PROMPT", label: "商业版 · 相关度评分" },
  { key: "TECH_SUMMARY_PROMPT", label: "技术版 · 精读摘要" },
  { key: "BUSINESS_SUMMARY_PROMPT", label: "商业版 · 结构化摘要" },
  { key: "TECH_EXECUTIVE_SUMMARY_PROMPT", label: "技术版 · 趋势概览" },
  { key: "BUSINESS_EXECUTIVE_SUMMARY_PROMPT", label: "商业版 · 动态概览" },
  { key: "TECH_WEEKLY_DIGEST_PROMPT", label: "技术版 · 本周讯息总结" },
  { key: "TRANSLATION_PROMPT", label: "英文翻译" },
  { key: "ARTICLE_FOLLOWUP_PROMPT", label: "每条新闻 · 引导性追问人设" },
];

let S = JSON.parse(document.getElementById("settings-data").textContent);
let activeTab = "tech";

// ── 小工具 ─────────────────────────────────────────────────────
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function toast(msg, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 2600);
}

// ── 渲染：变量 ─────────────────────────────────────────────────
function renderVars() {
  const grid = document.getElementById("vars-grid");
  grid.replaceChildren();
  for (const m of VAR_META) {
    grid.appendChild(el("div", { class: "var-item" }, [
      el("label", { for: "var-" + m.key }, m.label),
      el("input", { type: "number", id: "var-" + m.key, "data-key": m.key,
        step: m.step, min: m.min, max: m.max, value: S[m.key] ?? "" }),
      el("span", { class: "var-desc" }, m.desc),
    ]));
  }
}

// ── 渲染：信息源 ───────────────────────────────────────────────
function srcRow(src) {
  const tier = src.tier || "media";
  const row = el("div", { class: "src-row" + (src.enabled ? "" : " off"), "data-name": src.name });
  const cb = el("input", { type: "checkbox", "data-role": "enabled" });
  cb.checked = !!src.enabled;
  cb.addEventListener("change", () => row.classList.toggle("off", !cb.checked));
  row.append(
    cb,
    el("span", { class: "src-name" }, src.name),
    el("span", { class: "badge badge-tier-" + tier }, tierLabel(tier)),
    el("span", { class: "badge badge-lang" }, (src.language || "").toUpperCase() || "—"),
    el("span", { class: "src-url", title: src.url || "" }, src.url || ""),
    el("span", { class: "src-weight" }, [
      "权重",
      el("input", { type: "number", step: 0.1, min: 0, "data-role": "weight", value: src.weight ?? 1.0 }),
    ]),
  );
  return row;
}

function tierLabel(t) {
  return { official: "官方公告", regulatory: "监管披露", industry: "行业组织",
    academic: "会议论文", media: "行业媒体", aggregator: "聚合转载" }[t] || "行业媒体";
}

function renderSources() {
  const rss = document.getElementById("rss-list");
  const html = document.getElementById("html-list");
  rss.replaceChildren(...(S.rss_sources || []).map(srcRow));
  html.replaceChildren(...(S.html_sources || []).map(srcRow));
  updateSrcSummary();
}

function updateSrcSummary() {
  const boxes = document.querySelectorAll('.src-row input[data-role="enabled"]');
  const on = [...boxes].filter(b => b.checked).length;
  document.getElementById("src-summary").textContent = `（已启用 ${on} / ${boxes.length}）`;
}

// ── 渲染：关键词词库 ───────────────────────────────────────────
function renderBuckets() {
  for (const sub of ["tech", "business"]) {
    const pane = document.getElementById("kb-" + sub);
    pane.hidden = sub !== activeTab;
    pane.replaceChildren();
    const buckets = (S.keyword_buckets && S.keyword_buckets[sub]) || {};
    for (const [cat, words] of Object.entries(buckets)) {
      const chips = el("div", { class: "chips" });
      words.forEach((w, i) => chips.appendChild(chipNode(sub, cat, w, i)));
      const adder = el("input", { class: "chip-add", placeholder: "+ 加词，回车确认" });
      adder.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const v = adder.value.trim();
        if (!v) return;
        if (!S.keyword_buckets[sub][cat].includes(v)) S.keyword_buckets[sub][cat].push(v);
        renderBuckets();
      });
      chips.appendChild(adder);
      pane.appendChild(el("div", { class: "kb-cat" }, [
        el("div", { class: "kb-cat-name" }, `${cat}　·　${words.length} 词`),
        chips,
      ]));
    }
  }
}

function chipNode(sub, cat, word, idx) {
  return el("span", { class: "chip" }, [
    word,
    el("span", { class: "x", title: "删除", onclick: () => {
      S.keyword_buckets[sub][cat].splice(idx, 1);
      renderBuckets();
    } }, "×"),
  ]);
}

// ── 渲染：Prompt ───────────────────────────────────────────────
function renderPrompts() {
  const box = document.getElementById("prompts-list");
  box.replaceChildren();
  const prompts = S.prompts || {};
  for (const m of PROMPT_META) {
    box.appendChild(el("details", { class: "prompt-item" }, [
      el("summary", {}, m.label),
      el("textarea", { "data-key": m.key, spellcheck: "false" }, prompts[m.key] || ""),
    ]));
  }
}

// ── 收集表单 → 设置 dict ───────────────────────────────────────
function collect() {
  const out = { keyword_buckets: S.keyword_buckets };
  document.querySelectorAll("#vars-grid input[data-key]").forEach(inp => {
    if (inp.value !== "") out[inp.dataset.key] = Number(inp.value);
  });
  for (const [listKey, listId] of [["rss_sources", "rss-list"], ["html_sources", "html-list"]]) {
    out[listKey] = [...document.querySelectorAll(`#${listId} .src-row`)].map(row => ({
      name: row.dataset.name,
      enabled: row.querySelector('[data-role="enabled"]').checked,
      weight: Number(row.querySelector('[data-role="weight"]').value),
    }));
  }
  out.prompts = {};
  document.querySelectorAll("#prompts-list textarea[data-key]").forEach(ta => {
    if (ta.value.trim()) out.prompts[ta.dataset.key] = ta.value;
  });
  return out;
}

// ── 保存 / 重置 ────────────────────────────────────────────────
async function save() {
  try {
    const resp = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collect()),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error("保存失败");
    S = data.settings;
    renderAll();
    toast("✓ 已保存，对手动运行/计划任务/控制台运行同时生效", "ok");
  } catch (e) {
    toast("保存失败：" + e.message, "err");
  }
}

async function reset() {
  if (!confirm("确定重置为代码默认值？将删除 data/gui_settings.json。")) return;
  try {
    const resp = await fetch("/api/settings/reset", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error("重置失败");
    S = data.settings;
    renderAll();
    toast("已重置为默认值", "ok");
  } catch (e) {
    toast("重置失败：" + e.message, "err");
  }
}

// ── 事件绑定 ───────────────────────────────────────────────────
function bindStatic() {
  document.getElementById("btn-save").addEventListener("click", save);
  document.getElementById("btn-reset").addEventListener("click", reset);
  document.querySelectorAll("#kb-tabs .tab").forEach(tab => {
    tab.addEventListener("click", () => {
      activeTab = tab.dataset.tab;
      document.querySelectorAll("#kb-tabs .tab").forEach(t => t.classList.toggle("active", t === tab));
      renderBuckets();
    });
  });
  document.querySelectorAll(".chip-btn[data-bulk]").forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.bulk;
      document.querySelectorAll('.src-row input[data-role="enabled"]').forEach(cb => {
        cb.checked = mode === "all" ? true : mode === "none" ? false : !cb.checked;
        cb.dispatchEvent(new Event("change"));
      });
      updateSrcSummary();
    });
  });
  document.addEventListener("change", (e) => {
    if (e.target.matches('.src-row input[data-role="enabled"]')) updateSrcSummary();
  });
}

function renderAll() {
  renderVars();
  renderSources();
  renderBuckets();
  renderPrompts();
}

bindStatic();
renderAll();
