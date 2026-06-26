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
from datetime import date, datetime, timedelta

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
import wechat_render  # noqa: E402
import analyzer  # noqa: E402
import image_fetch  # noqa: E402

OUTPUT_DIR = os.path.join(_ROOT, "output")

# 报告文件 basename（不含扩展名）合法形态：
# daily_YYYY-MM-DD_business / weekly_YYYY-MM-DD_tech / weekly_YYYY-MM-DD_tech-handpick
_REPORT_NAME_RE = re.compile(r"^(daily|weekly)_(\d{4}-\d{2}-\d{2})_(business|tech(?:-handpick)?)$")


def _report_kind(slug: str) -> str:
    return "tech" if str(slug).startswith("tech") else "business"


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
            "kind": _report_kind(m.group(3)),
            "variant": m.group(3),
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
        "followup": a.get("followup_question", ""),
        "img": f"/api/img?file={name}&i={i}",
    }


def _category_svg_bytes(article: dict) -> tuple[bytes, str]:
    cat = article.get("category") or ("行业商业" if article.get("tags") else "其他")
    svg = reporter._CATEGORY_SVG.get(cat, reporter._CATEGORY_SVG["其他"])
    return svg.encode("utf-8"), "image/svg+xml"


# ============================================================
# 导出子集 + 周报挑选（阶段5）
# ============================================================

# 生成的导出物文件名白名单（防路径穿越；供 /api/export/open 校验）
_EXPORT_NAME_RE = re.compile(r"^(share|wechat|export|weekly)_[\w\-]+\.(html|md)$")


def _rating_file_for(entry: dict) -> str | None:
    """由 rating 快照里的 slug+date 反推其来源报告 basename。"""
    slug, dt = entry.get("slug"), entry.get("date")
    if slug not in ("business", "tech") or not dt:
        return None
    prefix = "daily" if slug == "business" else "weekly"
    return f"{prefix}_{dt}_{slug}"


def _full_article_by_url(file: str, url: str) -> dict | None:
    rep = _load_report(file)
    if not rep:
        return None
    for a in rep.get("articles", []):
        if a.get("url") == url:
            return a
    return None


def _export_subset(file: str, scope: str, min_score: int, urls: list) -> tuple[dict | None, list[dict]]:
    """按范围从某报告挑出文章子集，并并入人工评分(human_score)/备注(note)。"""
    rep = _load_report(file)
    if rep is None:
        return None, []
    ratings = ratings_store.load()
    want = set(urls or [])
    out: list[dict] = []
    for a in rep.get("articles", []):
        r = ratings.get(a.get("url"), {})
        hs = r.get("human_score")
        flags = r.get("flags", []) or []
        if scope == "all":
            keep = True
        elif scope == "rated_min":
            keep = isinstance(hs, int) and hs >= min_score
        elif scope == "shared":
            keep = "分享" in flags
        elif scope == "manual":
            keep = a.get("url") in want
        else:
            keep = False
        if not keep:
            continue
        merged = dict(a)
        if isinstance(hs, int):
            merged["human_score"] = hs
        if r.get("note"):
            merged["note"] = r["note"]
        out.append(merged)
    # 导出排序：先我的分（无则 -1），再 AI 分
    out.sort(key=lambda x: (x.get("human_score", -1), x.get("score", 0)), reverse=True)
    return rep, out


def _write_output(name: str, text: str) -> dict:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"name": name, "path": os.path.abspath(path), "open": f"/api/export/open?name={name}"}


# ============================================================
# 增值交互（阶段6）：试评分 / 源健康度 / 运行历史
# ============================================================

def _keyword_hits_by_bucket(article: dict, buckets: dict) -> dict:
    """返回该文章在各关键词桶里命中的词 {分类: [命中词,...]}，用于「试评分」高亮解释。"""
    blob = f"{article.get('title', '')}\n{article.get('text', '')[:800]}".lower()
    out: dict[str, list[str]] = {}
    for cat, words in (buckets or {}).items():
        hit = [str(w) for w in words if str(w).lower() in blob]
        if hit:
            out[cat] = hit
    return out


def _tail_log_warnings(limit: int = 20) -> list[dict]:
    """读 crawl.log / run.log 尾部的 WARNING/ERROR 行，辅助判断源健康度。"""
    out: list[dict] = []
    for log in ("crawl.log", "run.log"):
        path = os.path.join(_ROOT, log)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        hits = [ln.rstrip() for ln in lines if "WARNING" in ln or "ERROR" in ln]
        for ln in hits[-limit:]:
            out.append({"log": log, "line": ln})
    return out


def _source_output_tally() -> dict:
    """统计各信息源在 output/ 全部报告里累计产出的条目数。"""
    tally: dict[str, int] = {}
    for r in _list_reports():
        rep = _load_report(r["file"])
        for a in (rep or {}).get("articles", []):
            s = a.get("source", "")
            if s:
                tally[s] = tally.get(s, 0) + 1
    return tally


# ============================================================
# 已处理列表（防重复 seen-URL）编辑：删除条目即可让其下次重新处理/生成
# ============================================================

SEEN_SLUGS = ("business", "tech")
_seen_lock = threading.Lock()


def _seen_path(slug: str) -> str:
    return os.path.join(OUTPUT_DIR, f"seen_{slug}.json")


def _load_seen(slug: str) -> list[str]:
    try:
        with open(_seen_path(slug), encoding="utf-8") as f:
            return list(json.load(f).get("urls", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_seen(slug: str, urls: list[str]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = _seen_path(slug)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"urls": urls}, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def _url_meta_index() -> dict:
    """URL → {title, source, date, kind}，跨全部报告（含附录）建索引，给 seen 列表补充可读信息。"""
    idx: dict[str, dict] = {}
    for r in _list_reports():
        rep = _load_report(r["file"]) or {}
        for a in rep.get("articles", []):
            idx.setdefault(a.get("url", ""), {
                "title": a.get("title", ""), "source": a.get("source", ""),
                "date": r["date"], "kind": r["kind"],
            })
        for a in rep.get("appendix", []):
            idx.setdefault(a.get("url", ""), {
                "title": a.get("title", ""), "source": a.get("source", ""),
                "date": r["date"], "kind": r["kind"],
            })
    return idx


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
            "kind": _report_kind(m.group(3)) if m else "",
            "variant": m.group(3) if m else "",
            "date": m.group(2) if m else "",
            "generated_at": rep.get("generated_at", ""),
            "metadata": rep.get("metadata", {}) or {},
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
        # 2) 远程 URL：带 Referer 代理下载（绕防盗链），找图/验图逻辑与出报告时共用 image_fetch
        elif src.startswith(("http://", "https://")):
            data, mime = image_fetch.fetch_image_bytes(
                src, referer=article.get("url", ""), source_name=article.get("source", ""),
            )
            if data:
                return Response(data, mimetype=mime, headers=headers)
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

    # ── 导出分享（阶段5）──────────────────────────────────────
    @app.route("/export")
    def export_page():
        return render_template("export.html", active="export")

    @app.get("/api/export/preview")
    def api_export_preview():
        """返回某报告在给定范围下会导出的条目，供 UI 预览。"""
        file = request.args.get("file", "")
        scope = request.args.get("scope", "shared")
        try:
            min_score = int(request.args.get("min_score", "7"))
        except ValueError:
            min_score = 7
        rep, subset = _export_subset(file, scope, min_score, [])
        if rep is None:
            return jsonify({"ok": False, "error": "报告不存在"}), 404
        items = [{
            "url": a.get("url", ""), "title": a.get("title", ""),
            "source": a.get("source", ""), "score": a.get("score"),
            "human_score": a.get("human_score"),
        } for a in subset]
        return jsonify({"ok": True, "count": len(items), "items": items})

    @app.post("/api/export")
    def api_export():
        p = request.get_json(force=True, silent=True) or {}
        file = p.get("file", "")
        scope = p.get("scope", "shared")
        formats = p.get("formats", []) or []
        try:
            min_score = int(p.get("min_score", 7) or 7)
        except (TypeError, ValueError):
            min_score = 7
        rep, subset = _export_subset(file, scope, min_score, p.get("urls", []))
        if rep is None:
            return jsonify({"ok": False, "error": "报告不存在或文件名非法"}), 404
        if not subset:
            return jsonify({"ok": False, "error": "所选范围内没有条目"}), 400

        m = _REPORT_NAME_RE.match(file)
        kind, dt = _report_kind(m.group(3)), m.group(2)
        title = ("商业动态精选" if kind == "business" else "技术周报精选") + f" · {dt}"
        today = datetime.now().strftime("%Y-%m-%d")
        results: dict[str, dict] = {}

        if "markdown" in formats:
            md = wechat_render.render_markdown(subset, title)
            results["markdown"] = _write_output(f"export_{today}_{kind}.md", md)
        if "clipboard" in formats:
            results["clipboard"] = {"text": wechat_render.render_markdown(subset, title)}
        if "wechat" in formats:
            html = wechat_render.render_wechat(subset, title)
            results["wechat"] = _write_output(f"wechat_{today}_{kind}.html", html)
        if "share_html" in formats:
            # 复用正式报告渲染：同款样式与配图（save_report 会就地解析图片）
            path = reporter.save_report(
                [dict(a) for a in subset],
                rep.get("executive_summary", ""),
                rep.get("sources", []),
                report_type=kind, report_slug=kind, report_title=title,
                metadata=rep.get("metadata"), file_prefix_word="share",
            )
            base = os.path.basename(path)
            results["share_html"] = {
                "name": base, "path": os.path.abspath(path),
                "open": f"/api/export/open?name={base}",
                "hint": "样式与正式报告一致；如需可分享链接，运行 publish_pages.ps1 发布到 Pages",
            }
        return jsonify({"ok": True, "count": len(subset), "results": results})

    @app.get("/api/export/open")
    def api_export_open():
        """在浏览器打开生成的导出物（html 直接渲染，md 以纯文本）。"""
        name = request.args.get("name", "")
        if not _EXPORT_NAME_RE.match(name):
            return Response("非法文件名", status=400)
        path = os.path.join(OUTPUT_DIR, name)
        if not os.path.isfile(path):
            return Response("文件不存在", status=404)
        with open(path, encoding="utf-8") as f:
            body = f.read()
        # Flask 会为 text/* mimetype 自动补 charset=utf-8，这里不要再手写以免重复
        return Response(body, mimetype="text/html" if name.endswith(".html") else "text/plain")

    @app.post("/api/weekly_from_ratings")
    def api_weekly_from_ratings():
        """从近 N 天的人工评分里取最高分 Top-N，直接渲染技术周报（跳过爬取与 AI 评分）。"""
        p = request.get_json(force=True, silent=True) or {}
        try:
            top_n = int(p.get("top_n") or config.TECH_TOP_N)
            days = int(p.get("days") or 7)
        except (TypeError, ValueError):
            top_n, days = config.TECH_TOP_N, 7
        top_n = min(max(top_n, 1), 100)
        days = min(max(days, 1), 60)

        cutoff = date.today() - timedelta(days=days)
        cands = []
        for url, r in ratings_store.load().items():
            hs = r.get("human_score")
            if not isinstance(hs, int):
                continue
            dt = r.get("date")
            try:
                if dt and date.fromisoformat(dt) < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
            cands.append((hs, r))
        cands.sort(key=lambda x: x[0], reverse=True)
        cands = cands[:top_n]
        if not cands:
            return jsonify({"ok": False, "error": f"近 {days} 天没有已评分的条目"}), 400

        results = []
        for rank, (hs, r) in enumerate(cands, 1):
            src_file = _rating_file_for(r)
            full = _full_article_by_url(src_file, r.get("url")) if src_file else None
            art = dict(full) if full else {
                "url": r.get("url", ""), "title": r.get("title", ""), "summary": "",
                "source": r.get("source", ""), "keywords": "", "image_url": "",
                "language": "zh", "tier": "media", "pub_date": r.get("date", ""),
            }
            art["rank"] = rank
            art["human_score"] = hs
            art["score"] = art.get("score") or r.get("ai_score") or 0
            art["weighted_score"] = art.get("weighted_score") or art["score"]
            # 保证落在技术门类内，否则归「其他技术」，避免按门类分组时丢条目
            if art.get("category") not in config.TECH_CATEGORIES:
                art["category"] = "其他技术"
            results.append(art)

        title = f"半导体技术周报 · 人工精选 Top {len(results)}"
        path = reporter.save_report(
            results, executive_summary="", site_names=[],
            report_type="tech", report_slug="tech-handpick", report_title=title,
            file_prefix_word="weekly", metadata={"ai_executor": "人工精选"},
        )
        base = os.path.basename(path)
        return jsonify({
            "ok": True, "count": len(results), "name": base,
            "path": os.path.abspath(path), "open": f"/api/export/open?name={base}",
        })

    # ── 洞察 / 增值交互（阶段6）─────────────────────────────────
    @app.route("/insights")
    def insights_page():
        return render_template("insights.html", active="insights")

    @app.get("/api/history")
    def api_history():
        """运行历史：各报告的收录数/候选数/token 花费/执行器。"""
        out = []
        for r in _list_reports():
            rep = _load_report(r["file"]) or {}
            meta = rep.get("metadata", {}) or {}
            out.append({
                **r,
                "candidates_count": rep.get("candidates_count"),
                "appendix_count": len(rep.get("appendix", [])),
                "ai_executor": meta.get("ai_executor", "—"),
                "calls": meta.get("calls"),
                "total_tokens": meta.get("total_tokens"),
                "cost_usd": meta.get("cost_usd"),
            })
        return jsonify({"reports": out})

    @app.get("/api/source_health")
    def api_source_health():
        """信息源健康度：启用状态 + 全部报告累计产出 + 近期日志告警。"""
        tally = _source_output_tally()
        sources = []
        for listname, kind in (("RSS_SOURCES", "rss"), ("HTML_SOURCES", "html")):
            for s in getattr(config, listname, []) or []:
                name = s.get("name", "")
                enabled = s.get("enabled", True)
                cnt = tally.get(name, 0)
                status = "disabled" if not enabled else ("ok" if cnt > 0 else "warn")
                sources.append({
                    "name": name, "kind": kind, "tier": s.get("tier", "media"),
                    "weight": s.get("weight", 1.0), "enabled": enabled,
                    "recent_count": cnt, "status": status,
                })
        return jsonify({"sources": sources, "warnings": _tail_log_warnings()})

    @app.post("/api/rescore")
    def api_rescore():
        """改词后的「试评分」：用关键词兜底对 raw_articles.json 重打分，
        即时、免费、不重爬、不写任何文件。直接反映 GUI 刚保存的关键词词库改动。"""
        p = request.get_json(force=True, silent=True) or {}
        kind = p.get("report", "business")
        if kind not in ("business", "tech"):
            kind = "business"
        raw_path = os.path.join(OUTPUT_DIR, "raw_articles.json")
        try:
            with open(raw_path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify({
                "ok": False,
                "error": "未找到 output/raw_articles.json，请先运行一次完整流程或 crawl_only.py",
            }), 404
        articles = data.get("articles", data) if isinstance(data, dict) else data

        buckets = config.TECH_KEYWORD_BUCKETS if kind == "tech" else config.BUSINESS_KEYWORD_BUCKETS
        items = []
        for a in articles:
            if kind == "tech":
                score, label = analyzer._fallback_tech_score(a)
            else:
                score, label = analyzer._fallback_business_tags(a)
            items.append({
                "title": a.get("title", ""), "source": a.get("source", ""),
                "url": a.get("url", ""), "score": score, "label": label,
                "hits": _keyword_hits_by_bucket(a, buckets),
            })
        items.sort(key=lambda x: x["score"], reverse=True)
        return jsonify({
            "ok": True, "kind": kind, "count": len(items),
            "threshold": config.BUSINESS_MIN_SCORE if kind == "business" else None,
            "top_n": config.TECH_TOP_N if kind == "tech" else None,
            "items": items,
        })

    # ── 已处理列表（防重复 seen-URL）查看/编辑 ─────────────────
    @app.get("/api/seen")
    def api_seen():
        slug = request.args.get("slug", "business")
        if slug not in SEEN_SLUGS:
            return jsonify({"ok": False, "error": "slug 必须是 business 或 tech"}), 400
        urls = _load_seen(slug)
        idx = _url_meta_index()
        items = []
        for u in urls:
            m = idx.get(u, {})
            items.append({
                "url": u, "title": m.get("title", ""),
                "source": m.get("source", ""), "date": m.get("date", ""),
            })
        items.reverse()  # seen 新条目追加在尾部，倒序让最近处理的在最前
        return jsonify({"ok": True, "slug": slug, "count": len(items), "items": items})

    @app.post("/api/seen/delete")
    def api_seen_delete():
        """从 seen 列表移除选中 URL，使其下次运行重新处理（重新评分/生成）。"""
        p = request.get_json(force=True, silent=True) or {}
        slug = p.get("slug", "")
        urls = p.get("urls", [])
        if slug not in SEEN_SLUGS:
            return jsonify({"ok": False, "error": "slug 必须是 business 或 tech"}), 400
        if not isinstance(urls, list) or not urls:
            return jsonify({"ok": False, "error": "未选择要移除的条目"}), 400
        remove = set(urls)
        with _seen_lock:
            cur = _load_seen(slug)
            kept = [u for u in cur if u not in remove]
            removed = len(cur) - len(kept)
            if removed:
                _save_seen(slug, kept)
        return jsonify({"ok": True, "removed": removed, "remaining": len(kept)})

    return app
