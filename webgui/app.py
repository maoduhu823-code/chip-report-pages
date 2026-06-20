"""
webgui/app.py — 轻量控制台 Flask 后端

职责（随阶段递增）：
- 阶段2：控制面板（信息源/变量/关键词词库/Prompt 读写）
- 阶段3：触发运行 + SSE 实时日志（单运行锁）
- 阶段4：评审看板 + 1–10 人工评分（图片走 /api/img 代理，剥 base64）
- 阶段5：四格式导出 + 周报挑选

设计：复用工程根的 settings_store / ratings_store / config / reporter，不分叉流水线。
触发运行起子进程 main.py / analyze_raw.py，子进程自动继承配置覆盖层。
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime

# 把工程根加入 sys.path，保证从任意 cwd 启动都能 import 根模块
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import (  # noqa: E402
    Flask, Response, jsonify, render_template, request, stream_with_context,
)

import settings_store  # noqa: E402
import ratings_store  # noqa: E402
import config  # noqa: E402
import reporter  # noqa: E402

OUTPUT_DIR = os.path.join(_ROOT, "output")

# 报告文件 basename（不含扩展名）合法形态：daily_YYYY-MM-DD_business / weekly_YYYY-MM-DD_tech
_REPORT_NAME_RE = re.compile(r"^(daily|weekly)_(\d{4}-\d{2}-\d{2})_(business|tech)$")


# ============================================================
# 设置快照
# ============================================================

def _current_settings() -> dict:
    """当前有效设置快照（供表单回填）。"""
    return settings_store.snapshot_from_config()


# ============================================================
# 报告 JSON 读取（带 mtime 缓存，避免反复解析数 MB 的 base64 大文件）
# ============================================================

_report_cache: dict[str, tuple[float, dict]] = {}


def _safe_report_path(name: str) -> str | None:
    """校验 basename 合法且文件存在，返回绝对路径；否则 None（防路径穿越）。"""
    if not name or not _REPORT_NAME_RE.match(name):
        return None
    path = os.path.join(OUTPUT_DIR, name + ".json")
    if not os.path.isfile(path):
        return None
    return path


def _load_report(name: str) -> dict | None:
    path = _safe_report_path(name)
    if not path:
        return None
    mtime = os.path.getmtime(path)
    cached = _report_cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    _report_cache[name] = (mtime, data)
    return data


def _list_reports() -> list[dict]:
    """列出 output/ 下所有报告（business 日报 + tech 周报），最新在前。"""
    out: list[dict] = []
    try:
        names = os.listdir(OUTPUT_DIR)
    except FileNotFoundError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        name = fn[:-5]
        m = _REPORT_NAME_RE.match(name)
        if not m:
            continue
        rep = _load_report(name)
        if rep is None:
            continue
        out.append({
            "file": name,
            "kind": m.group(3),
            "date": m.group(2),
            "count": len(rep.get("articles", [])),
            "generated_at": rep.get("generated_at", ""),
        })
    out.sort(key=lambda r: (r["date"], r["file"]), reverse=True)
    return out


def _slim_article(name: str, i: int, a: dict) -> dict:
    """剥掉 base64 大图，图片改走 /api/img 懒加载代理。"""
    return {
        "i": i,
        "rank": a.get("rank"),
        "url": a.get("url", ""),
        "title": a.get("title", ""),
        "summary": a.get("summary", ""),
        "score": a.get("score"),
        "weighted_score": a.get("weighted_score"),
        "source": a.get("source", ""),
        "tier": a.get("tier", "media"),
        "language": a.get("language", "zh"),
        "pub_date": a.get("pub_date"),
        "keywords": a.get("keywords", ""),
        "tags": a.get("tags", ""),
        "category": a.get("category", ""),
        "img": f"/api/img?file={name}&i={i}",
    }


def _category_svg_bytes(article: dict) -> tuple[bytes, str]:
    cat = article.get("category") or ("行业商业" if article.get("tags") else "其他")
    svg = reporter._CATEGORY_SVG.get(cat, reporter._CATEGORY_SVG["其他"])
    return svg.encode("utf-8"), "image/svg+xml"


# ============================================================
# 运行管理器：起子进程 + 单运行锁 + SSE 日志
# ============================================================

class RunManager:
    """同一时刻只允许一个运行；后台线程逐行收集 stdout，SSE 实时推送。"""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.status: str = "idle"   # idle | running | done | error
        self.report: str | None = None
        self.kind: str | None = None
        self.mode: str | None = None
        self.started_at: str | None = None

    def start(self, kind: str, mode: str) -> tuple[bool, str]:
        with self.cond:
            if self.status == "running":
                return False, "已有运行进行中，请等待当前运行结束"
            self.lines = []
            self.status = "running"
            self.report = None
            self.kind = kind
            self.mode = mode
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.cond.notify_all()

        script = "main.py" if mode == "full" else "analyze_raw.py"
        cmd = [sys.executable, script, "--report", kind]
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        self._push(f"$ {os.path.basename(sys.executable)} {script} --report {kind}")
        try:
            proc = subprocess.Popen(
                cmd, cwd=_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
            )
        except Exception as e:  # noqa: BLE001
            with self.cond:
                self.status = "error"
                self.lines.append(f"启动失败: {e}")
                self.cond.notify_all()
            return False, str(e)

        self.proc = proc
        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
        return True, "started"

    def _push(self, line: str) -> None:
        with self.cond:
            self.lines.append(line)
            self.cond.notify_all()

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._push(line.rstrip("\n"))
        except Exception as e:  # noqa: BLE001
            self._push(f"[读取输出异常] {e}")
        finally:
            code = proc.wait()
            report = self._detect_report()
            with self.cond:
                self.report = report
                self.lines.append(f"——— 进程退出码 {code} ———")
                self.status = "done" if code == 0 else "error"
                self.cond.notify_all()

    def _detect_report(self) -> str | None:
        """从日志里抓本次生成的报告 basename；抓不到则回退 output/ 最新同类报告。"""
        slug = self.kind or ""
        prefix = "daily" if slug == "business" else "weekly"
        pat = re.compile(rf"({prefix}_\d{{4}}-\d{{2}}-\d{{2}}_{slug})\.html")
        for line in reversed(self.lines):
            m = pat.search(line)
            if m:
                return m.group(1)
        try:
            file_pat = re.compile(rf"^{prefix}_\d{{4}}-\d{{2}}-\d{{2}}_{slug}\.json$")
            cands = sorted(
                (fn[:-5] for fn in os.listdir(OUTPUT_DIR) if file_pat.match(fn)),
                reverse=True,
            )
            return cands[0] if cands else None
        except FileNotFoundError:
            return None

    def snapshot(self) -> dict:
        with self.cond:
            return {
                "status": self.status, "report": self.report,
                "kind": self.kind, "mode": self.mode,
                "lines": len(self.lines), "started_at": self.started_at,
            }

    def stream(self):
        """SSE 生成器：先补发历史日志，再实时推送，结束时发 done 事件。"""
        idx = 0
        yield ": connected\n\n"
        while True:
            with self.cond:
                while idx >= len(self.lines) and self.status == "running":
                    self.cond.wait(timeout=15)
                new = self.lines[idx:]
                idx = len(self.lines)
                status = self.status
                report = self.report
            for line in new:
                yield f"data: {json.dumps({'line': line}, ensure_ascii=False)}\n\n"
            if status != "running":
                payload = {"status": status, "report": report}
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            if not new:
                yield ": ping\n\n"  # 心跳，保持连接


RUN = RunManager()


# ============================================================
# 应用工厂
# ============================================================

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    # ── 控制面板 ───────────────────────────────────────────────
    @app.route("/")
    def control():
        return render_template("control.html", active="control", settings=_current_settings())

    @app.get("/api/settings")
    def api_get_settings():
        return jsonify(_current_settings())

    @app.post("/api/settings")
    def api_save_settings():
        payload = request.get_json(force=True, silent=True) or {}
        clean = settings_store.sanitize_settings(payload)
        settings_store.save(clean)
        importlib.reload(config)  # 让本进程后续快照反映刚保存的值
        return jsonify({"ok": True, "settings": _current_settings()})

    @app.post("/api/settings/reset")
    def api_reset_settings():
        settings_store.reset()
        importlib.reload(config)
        return jsonify({"ok": True, "settings": _current_settings()})

    # ── 运行触发 + SSE 日志 ────────────────────────────────────
    @app.post("/api/run")
    def api_run():
        payload = request.get_json(force=True, silent=True) or {}
        kind = payload.get("report")
        mode = payload.get("mode", "full")
        if kind not in ("business", "tech"):
            return jsonify({"ok": False, "error": "report 必须是 business 或 tech"}), 400
        if mode not in ("full", "llmOnly"):
            mode = "full"
        ok, msg = RUN.start(kind, mode)
        snap = RUN.snapshot()
        snap.update({"ok": ok, "msg": msg})
        return jsonify(snap), (200 if ok else 409)

    @app.get("/api/run/status")
    def api_run_status():
        return jsonify(RUN.snapshot())

    @app.get("/api/run/stream")
    def api_run_stream():
        return Response(
            stream_with_context(RUN.stream()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── 评审看板 ───────────────────────────────────────────────
    @app.route("/dashboard")
    def dashboard():
        return render_template("dashboard.html", active="dashboard")

    @app.get("/api/reports")
    def api_reports():
        return jsonify({"reports": _list_reports()})

    @app.get("/api/report")
    def api_report():
        name = request.args.get("file", "")
        rep = _load_report(name)
        if rep is None:
            return jsonify({"ok": False, "error": "报告不存在或文件名非法"}), 404
        m = _REPORT_NAME_RE.match(name)
        articles = [_slim_article(name, i, a) for i, a in enumerate(rep.get("articles", []))]
        return jsonify({
            "ok": True,
            "file": name,
            "kind": m.group(3) if m else "",
            "date": m.group(2) if m else "",
            "generated_at": rep.get("generated_at", ""),
            "executive_summary": rep.get("executive_summary", ""),
            "articles": articles,
        })

    @app.get("/api/img")
    def api_img():
        name = request.args.get("file", "")
        rep = _load_report(name)
        try:
            i = int(request.args.get("i", "-1"))
        except ValueError:
            i = -1
        articles = (rep or {}).get("articles", [])
        if rep is None or i < 0 or i >= len(articles):
            return Response(status=404)

        article = articles[i]
        src = article.get("image_url", "") or ""
        headers = {"Cache-Control": "public, max-age=3600"}

        # 1) 已嵌入的 data URI：解码直出
        if src.startswith("data:") and "," in src:
            try:
                header, b64 = src.split(",", 1)
                mime = header[5:].split(";")[0] or "image/jpeg"
                raw = base64.b64decode(b64)
                return Response(raw, mimetype=mime, headers=headers)
            except Exception:  # noqa: BLE001
                pass
        # 2) 远程 URL：带 Referer 代理下载（绕防盗链）
        elif src.startswith(("http://", "https://")):
            try:
                import requests
                resp = requests.get(
                    src, timeout=8, stream=True,
                    headers={**reporter.REQUEST_HEADERS, "Referer": article.get("url", "")},
                )
                ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
                if resp.ok and ct.startswith("image/"):
                    return Response(resp.content, mimetype=ct, headers=headers)
            except Exception:  # noqa: BLE001
                pass
        # 3) 兜底：分类 SVG
        svg_bytes, mime = _category_svg_bytes(article)
        return Response(svg_bytes, mimetype=mime, headers=headers)

    # ── 人工评分读写 ───────────────────────────────────────────
    @app.get("/api/ratings")
    def api_get_ratings():
        return jsonify(ratings_store.load())

    @app.post("/api/ratings")
    def api_post_ratings():
        payload = request.get_json(force=True, silent=True) or {}
        url = (payload.get("url") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "缺少 url"}), 400
        if payload.get("delete"):
            ratings_store.delete(url)
            return jsonify({"ok": True, "entry": {}})
        entry = ratings_store.upsert(url, payload)
        return jsonify({"ok": True, "entry": entry})

    # ── 占位路由（阶段5 填充）─────────────────────────────────
    @app.route("/export")
    def export_page():
        return render_template(
            "placeholder.html", active="export", title="导出分享",
            note="阶段5 实现：微信图文 HTML · 独立分享网页(走 Pages) · Markdown/纯文本 · 复制剪贴板。",
        )

    return app
