"use strict";

// 运行触发 + SSE 实时日志（挂在控制台页「▶️ 运行流水线」卡片上）
(function () {
  const btn = document.getElementById("btn-run");
  if (!btn) return;
  const logEl = document.getElementById("run-log");
  const statusEl = document.getElementById("run-status");
  const gotoEl = document.getElementById("run-goto");
  let es = null;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "run-status " + (kind || "");
  }

  function appendLog(line) {
    logEl.hidden = false;
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 48;
    logEl.textContent += line + "\n";
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  function closeStream() {
    if (es) { es.close(); es = null; }
  }

  function openStream() {
    closeStream();
    es = new EventSource("/api/run/stream");
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.line !== undefined) appendLog(d.line);
      } catch (_) { /* 忽略心跳/非 JSON */ }
    };
    es.addEventListener("done", (e) => {
      let d = {};
      try { d = JSON.parse(e.data); } catch (_) {}
      closeStream();
      btn.disabled = false;
      if (d.status === "done") {
        setStatus("✓ 运行完成", "ok");
        if (d.report) {
          gotoEl.href = "/dashboard?file=" + encodeURIComponent(d.report);
          gotoEl.hidden = false;
        }
      } else {
        setStatus("✗ 运行失败（详见日志）", "err");
      }
    });
    // 不在 onerror 里关闭：浏览器会自动重连，配合后端补发历史日志可无缝接续
  }

  async function run() {
    const report = document.getElementById("run-report").value;
    const mode = document.getElementById("run-mode").value;
    btn.disabled = true;
    gotoEl.hidden = true;
    logEl.hidden = false;
    logEl.textContent = "";
    setStatus("运行中…", "");
    try {
      const resp = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report, mode }),
      });
      const d = await resp.json();
      if (!resp.ok || !d.ok) {
        setStatus("无法启动：" + (d.msg || d.error || resp.status), "err");
        btn.disabled = false;
        return;
      }
      openStream();
    } catch (e) {
      setStatus("请求失败：" + e.message, "err");
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", run);

  // 页面加载时若已有运行在进行，自动接管并补流日志
  fetch("/api/run/status").then((r) => r.json()).then((s) => {
    if (s.status === "running") {
      btn.disabled = true;
      setStatus("运行中…（接管已有运行）", "");
      logEl.hidden = false;
      openStream();
    }
  }).catch(() => {});
})();
