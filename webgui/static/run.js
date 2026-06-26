"use strict";

// 运行触发 + SSE 实时日志（挂在采集需求页）
(function () {
  const buttons = [...document.querySelectorAll(".js-run")];
  if (!buttons.length) return;
  const btn = document.getElementById("btn-run") || buttons[0];
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

  function setButtonsDisabled(disabled) {
    buttons.forEach((b) => { b.disabled = disabled; });
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
      setButtonsDisabled(false);
      if (d.status === "done") {
        setStatus("✓ 运行完成", "ok");
        if (d.report) {
          const target = "/dashboard?file=" + encodeURIComponent(d.report);
          gotoEl.href = target;
          gotoEl.hidden = false;
          window.setTimeout(() => { window.location.assign(target); }, 500);
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
    setButtonsDisabled(true);
    gotoEl.hidden = true;
    logEl.hidden = false;
    logEl.textContent = "";
    setStatus("正在保存采集需求...", "");
    if (typeof save === "function") {
      const saved = await save({ quiet: true });
      if (!saved) {
        setStatus("保存失败，未启动采集", "err");
        setButtonsDisabled(false);
        return;
      }
    }
    setStatus("采集中...", "");
    try {
      const resp = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report, mode }),
      });
      const d = await resp.json();
      if (!resp.ok || !d.ok) {
        setStatus("无法启动：" + (d.msg || d.error || resp.status), "err");
        setButtonsDisabled(false);
        return;
      }
      openStream();
    } catch (e) {
      setStatus("请求失败：" + e.message, "err");
      setButtonsDisabled(false);
    }
  }

  buttons.forEach((b) => b.addEventListener("click", run));

  // 页面加载时若已有运行在进行，自动接管并补流日志
  fetch("/api/run/status").then((r) => r.json()).then((s) => {
    if (s.status === "running") {
      setButtonsDisabled(true);
      setStatus("采集中...（接管已有运行）", "");
      logEl.hidden = false;
      openStream();
    }
  }).catch(() => {});
})();
