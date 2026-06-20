"use strict";

// 复用 base.html 的 toast
function toast(msg, kind) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + (kind || "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 2800);
}

const reportSel = document.getElementById("exp-report");
const minScore = document.getElementById("exp-minscore");
const previewEl = document.getElementById("exp-preview");
const resultsEl = document.getElementById("exp-results");

function scope() {
  const r = document.querySelector('#exp-scope input[name="scope"]:checked');
  return r ? r.value : "shared";
}

async function loadReports() {
  let reports = [];
  try { reports = (await fetch("/api/reports").then((r) => r.json())).reports || []; } catch (_) {}
  reportSel.replaceChildren();
  if (!reports.length) {
    reportSel.appendChild(new Option("（暂无报告）", ""));
    previewEl.textContent = "output/ 下还没有报告，先到控制台运行一次。";
    return;
  }
  for (const r of reports) {
    reportSel.appendChild(new Option(
      (r.kind === "business" ? "商业日报" : "技术周报") + " · " + r.date + " · " + r.count + " 条",
      r.file,
    ));
  }
  preview();
}

async function preview() {
  const file = reportSel.value;
  if (!file) return;
  previewEl.textContent = "预览中…";
  const qs = new URLSearchParams({ file, scope: scope(), min_score: minScore.value || "7" });
  try {
    const d = await fetch("/api/export/preview?" + qs).then((r) => r.json());
    if (!d.ok) { previewEl.textContent = d.error || "预览失败"; return; }
    if (!d.count) { previewEl.innerHTML = '<span class="exp-empty">该范围下没有条目。</span>'; return; }
    const lines = d.items.slice(0, 12).map((it) => {
      const my = (typeof it.human_score === "number") ? `我的 ${it.human_score}` : "我的 —";
      return `<li><span class="exp-sc">AI ${it.score ?? "—"} · ${my}</span> ${escapeHtml(it.title)}</li>`;
    }).join("");
    const more = d.count > 12 ? `<li class="exp-more">… 共 ${d.count} 条</li>` : "";
    previewEl.innerHTML = `<div class="exp-count">将导出 ${d.count} 条：</div><ul class="exp-list">${lines}${more}</ul>`;
  } catch (e) {
    previewEl.textContent = "预览失败：" + e.message;
  }
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function doExport() {
  const file = reportSel.value;
  if (!file) { toast("没有可导出的报告", "err"); return; }
  const formats = [...document.querySelectorAll(".fmt-row input:checked")].map((c) => c.value);
  if (!formats.length) { toast("请至少选择一种格式", "err"); return; }
  const btn = document.getElementById("btn-export");
  btn.disabled = true;
  resultsEl.innerHTML = '<div class="exp-working">导出中…（独立网页会就地解析配图，稍候）</div>';
  try {
    const d = await fetch("/api/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, scope: scope(), min_score: Number(minScore.value || 7), formats }),
    }).then((r) => r.json());
    if (!d.ok) { resultsEl.innerHTML = `<div class="exp-err">${d.error || "导出失败"}</div>`; return; }
    renderResults(d);
    toast(`✓ 已导出 ${d.count} 条`, "ok");
  } catch (e) {
    resultsEl.innerHTML = `<div class="exp-err">导出失败：${e.message}</div>`;
  } finally {
    btn.disabled = false;
  }
}

const FMT_LABEL = { wechat: "微信图文 HTML", share_html: "独立分享网页", markdown: "Markdown 文件", clipboard: "剪贴板文本" };

function renderResults(d) {
  const rows = [];
  for (const [fmt, info] of Object.entries(d.results || {})) {
    if (fmt === "clipboard") {
      rows.push(`<div class="exp-rrow"><b>${FMT_LABEL[fmt]}</b>
        <button class="chip-btn" data-copy="1" type="button">复制到剪贴板</button></div>`);
      renderResults._clip = info.text || "";
    } else {
      const hint = info.hint ? `<span class="exp-hint">${escapeHtml(info.hint)}</span>` : "";
      rows.push(`<div class="exp-rrow"><b>${FMT_LABEL[fmt] || fmt}</b>
        <a href="${info.open}" target="_blank" rel="noopener">打开</a>
        <code title="${escapeHtml(info.path)}">${escapeHtml(info.name)}</code>${hint}</div>`);
    }
  }
  resultsEl.innerHTML = rows.join("") || '<div class="exp-empty">没有产出。</div>';
  const copyBtn = resultsEl.querySelector('[data-copy]');
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(renderResults._clip || ""); toast("✓ 已复制", "ok"); }
      catch (_) { toast("浏览器拒绝写剪贴板，请手动复制", "err"); }
    });
  }
}

async function genWeekly() {
  const btn = document.getElementById("btn-weekly");
  const out = document.getElementById("wk-results");
  btn.disabled = true;
  out.innerHTML = '<div class="exp-working">生成中…</div>';
  try {
    const d = await fetch("/api/weekly_from_ratings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        top_n: Number(document.getElementById("wk-topn").value || 15),
        days: Number(document.getElementById("wk-days").value || 7),
      }),
    }).then((r) => r.json());
    if (!d.ok) { out.innerHTML = `<div class="exp-err">${d.error || "生成失败"}</div>`; return; }
    out.innerHTML = `<div class="exp-rrow"><b>已生成 ${d.count} 条精选周报</b>
      <a href="${d.open}" target="_blank" rel="noopener">打开</a>
      <code title="${escapeHtml(d.path)}">${escapeHtml(d.name)}</code></div>`;
    toast(`✓ 已生成周报（${d.count} 条）`, "ok");
  } catch (e) {
    out.innerHTML = `<div class="exp-err">生成失败：${e.message}</div>`;
  } finally {
    btn.disabled = false;
  }
}

reportSel.addEventListener("change", preview);
document.getElementById("exp-scope").addEventListener("change", preview);
minScore.addEventListener("input", () => { if (scope() === "rated_min") preview(); });
document.getElementById("btn-export").addEventListener("click", doExport);
document.getElementById("btn-weekly").addEventListener("click", genWeekly);

loadReports();
