"use strict";

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
const fmtTokens = (v) => (typeof v === "number" ? (v / 1000).toFixed(1) + "k" : "—");
const fmtCost = (v) => (typeof v === "number" && v > 0 ? "$" + v.toFixed(4) : "—");

// ── 改词试评分 ─────────────────────────────────────────────────
async function rescore() {
  const btn = document.getElementById("btn-rescore");
  const stat = document.getElementById("rs-stat");
  const box = document.getElementById("rs-results");
  const kind = document.getElementById("rs-report").value;
  btn.disabled = true;
  stat.textContent = "评分中…";
  stat.className = "run-status";
  try {
    const d = await fetch("/api/rescore", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ report: kind }),
    }).then((r) => r.json());
    if (!d.ok) { stat.textContent = d.error || "失败"; stat.className = "run-status err"; box.replaceChildren(); return; }

    const included = d.kind === "business"
      ? d.items.filter((it) => it.score >= d.threshold).length
      : Math.min(d.top_n, d.items.length);
    const crit = d.kind === "business" ? `收录阈值 ≥ ${d.threshold}` : `Top-${d.top_n} 精读`;
    stat.textContent = `✓ ${d.count} 条 · 按当前词库 ${crit} 将命中 ${included} 条`;
    stat.className = "run-status ok";

    box.replaceChildren();
    d.items.forEach((it, idx) => {
      const inN = d.kind === "business" ? (it.score >= d.threshold) : (idx < d.top_n);
      const row = el("div", { class: "rs-row" + (inN ? " in" : "") });
      row.appendChild(el("span", { class: "rs-score" + (inN ? " in" : "") }, String(it.score)));
      const main = el("div", { class: "rs-main" });
      main.appendChild(el("div", { class: "rs-title" }, [
        el("a", { href: it.url, target: "_blank", rel: "noopener" }, it.title || "（无标题）"),
      ]));
      const sub = el("div", { class: "rs-sub" }, `${it.source || "—"} · ${it.label || ""}`);
      main.appendChild(sub);
      const hitCats = Object.entries(it.hits || {});
      if (hitCats.length) {
        const chips = el("div", { class: "rs-hits" });
        for (const [cat, words] of hitCats) {
          chips.appendChild(el("span", { class: "rs-hit" }, `${cat}: ${words.join("、")}`));
        }
        main.appendChild(chips);
      } else {
        main.appendChild(el("div", { class: "rs-nohit" }, "无关键词命中（兜底低分）"));
      }
      row.appendChild(main);
      box.appendChild(row);
    });
  } catch (e) {
    stat.textContent = "请求失败：" + e.message;
    stat.className = "run-status err";
  } finally {
    btn.disabled = false;
  }
}

// ── 信息源健康度 ───────────────────────────────────────────────
async function loadHealth() {
  const list = document.getElementById("sh-list");
  const warns = document.getElementById("sh-warns");
  let d;
  try { d = await fetch("/api/source_health").then((r) => r.json()); }
  catch (e) { list.textContent = "加载失败：" + e.message; return; }

  list.replaceChildren();
  for (const s of d.sources) {
    const row = el("div", { class: "sh-row sh-" + s.status });
    const dot = { ok: "●", warn: "▲", disabled: "○" }[s.status] || "●";
    const txt = { ok: "正常", warn: "启用但 0 产出", disabled: "已停用" }[s.status] || "";
    row.append(
      el("span", { class: "sh-dot" }, dot),
      el("span", { class: "sh-name" }, s.name),
      el("span", { class: "badge badge-tier-" + s.tier }, TIER_LABELS[s.tier] || "行业媒体"),
      el("span", { class: "sh-kind" }, s.kind.toUpperCase()),
      el("span", { class: "sh-status" }, `${txt} · 累计 ${s.recent_count} 篇`),
    );
    list.appendChild(row);
  }

  warns.replaceChildren();
  if (!d.warnings.length) {
    warns.appendChild(el("div", { class: "sh-nowarn" }, "近期日志无 WARNING / ERROR。"));
  } else {
    for (const w of d.warnings) {
      warns.appendChild(el("div", { class: "sh-warn" }, [
        el("span", { class: "sh-warn-log" }, w.log), " " + w.line,
      ]));
    }
  }
}

// ── 运行历史 ───────────────────────────────────────────────────
async function loadHistory() {
  const wrap = document.getElementById("hist-wrap");
  let d;
  try { d = await fetch("/api/history").then((r) => r.json()); }
  catch (e) { wrap.textContent = "加载失败：" + e.message; return; }
  const reports = d.reports || [];
  if (!reports.length) { wrap.textContent = "暂无报告。"; return; }

  const head = el("div", { class: "hist-row hist-head" }, [
    el("span", {}, "日期"), el("span", {}, "类型"), el("span", {}, "收录"),
    el("span", {}, "候选"), el("span", {}, "执行器"), el("span", {}, "调用"),
    el("span", {}, "Token"), el("span", {}, "费用"),
  ]);
  wrap.replaceChildren(head);
  for (const r of reports) {
    wrap.appendChild(el("div", { class: "hist-row" }, [
      el("a", { href: "/dashboard?file=" + encodeURIComponent(r.file), title: "在看板打开" }, r.date),
      el("span", { class: "badge " + (r.kind === "business" ? "badge-tag" : "badge-score") },
        r.kind === "business" ? "商业" : "技术"),
      el("span", {}, String(r.count)),
      el("span", {}, String(r.candidates ?? r.candidates_count ?? "—")),
      el("span", { class: "hist-exec" }, r.ai_executor || "—"),
      el("span", {}, r.calls != null ? String(r.calls) : "—"),
      el("span", {}, fmtTokens(r.total_tokens)),
      el("span", {}, fmtCost(r.cost_usd)),
    ]));
  }
}

// ── 已处理列表（seen）编辑 ─────────────────────────────────────
let seenItems = [];

function toast(msg, kind) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + (kind || "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 2600);
}

async function loadSeen() {
  const slug = document.getElementById("seen-slug").value;
  const list = document.getElementById("seen-list");
  const stat = document.getElementById("seen-stat");
  list.textContent = "加载中…";
  let d;
  try { d = await fetch("/api/seen?slug=" + slug).then((r) => r.json()); }
  catch (e) { list.textContent = "加载失败：" + e.message; return; }
  if (!d.ok) { list.textContent = d.error || "加载失败"; return; }
  seenItems = d.items;
  stat.textContent = `共 ${d.count} 条已处理`;
  stat.className = "run-status";
  renderSeen();
}

function renderSeen() {
  const list = document.getElementById("seen-list");
  const q = document.getElementById("seen-search").value.trim().toLowerCase();
  list.replaceChildren();
  const shown = seenItems.filter((it) => !q ||
    (it.title + " " + it.source + " " + it.url).toLowerCase().includes(q));
  if (!shown.length) {
    list.appendChild(el("div", { class: "exp-empty" }, seenItems.length ? "无匹配条目。" : "该列表为空。"));
    return;
  }
  for (const it of shown) {
    const row = el("label", { class: "seen-row" });
    row.appendChild(el("input", { type: "checkbox", "data-url": it.url }));
    const main = el("div", { class: "seen-main" });
    main.appendChild(el("div", { class: "seen-title" }, it.title || "（不在现有报告中，仅 URL）"));
    main.appendChild(el("div", { class: "seen-sub" },
      [it.source, it.date].filter(Boolean).join(" · ") + (it.url ? "  " + it.url : "")));
    row.appendChild(main);
    list.appendChild(row);
  }
}

function seenChecked() {
  return [...document.querySelectorAll("#seen-list input[data-url]:checked")].map((c) => c.dataset.url);
}

async function deleteSeen() {
  const urls = seenChecked();
  if (!urls.length) { toast("未选择条目", "err"); return; }
  if (!confirm(`确定从已处理列表移除 ${urls.length} 条？下次运行会重新处理（重新评分/生成）这些新闻。`)) return;
  const slug = document.getElementById("seen-slug").value;
  try {
    const d = await fetch("/api/seen/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, urls }),
    }).then((r) => r.json());
    if (!d.ok) { toast(d.error || "删除失败", "err"); return; }
    toast(`✓ 已移除 ${d.removed} 条，剩余 ${d.remaining}`, "ok");
    loadSeen();
  } catch (e) { toast("删除失败：" + e.message, "err"); }
}

function seenSetAll(checked) {
  document.querySelectorAll("#seen-list input[data-url]").forEach((c) => { c.checked = checked; });
}

document.getElementById("btn-rescore").addEventListener("click", rescore);
document.getElementById("seen-slug").addEventListener("change", loadSeen);
document.getElementById("seen-search").addEventListener("input", renderSeen);
document.getElementById("seen-all").addEventListener("click", () => seenSetAll(true));
document.getElementById("seen-none").addEventListener("click", () => seenSetAll(false));
document.getElementById("btn-seen-del").addEventListener("click", deleteSeen);

loadHealth();
loadHistory();
loadSeen();
