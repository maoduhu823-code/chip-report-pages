"""
webgui/app.py — 轻量控制台 Flask 后端

职责（随阶段递增）：
- 阶段2：控制面板（信息源/变量/关键词词库/Prompt 读写）
- 阶段3：触发运行 + SSE 实时日志
- 阶段4：评审看板 + 1–10 人工评分
- 阶段5：四格式导出 + 周报挑选

设计：复用工程根的 settings_store / config / analyzer / reporter，不分叉流水线。
"""

from __future__ import annotations

import importlib
import json
import os
import sys

# 把工程根加入 sys.path，保证从任意 cwd 启动都能 import 根模块
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from flask import Flask, jsonify, render_template, request  # noqa: E402

import settings_store  # noqa: E402
import config  # noqa: E402


def _current_settings() -> dict:
    """当前有效设置快照（供表单回填）。"""
    return settings_store.snapshot_from_config()


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

    # ── 占位路由（后续阶段填充，先避免导航 404）─────────────────
    @app.route("/dashboard")
    def dashboard():
        return render_template(
            "placeholder.html", active="dashboard", title="评审看板",
            note="阶段4 实现：浏览报告条目 · 1–10 人工评分 · 快捷标记与备注 · AI/人工分对比。",
        )

    @app.route("/export")
    def export_page():
        return render_template(
            "placeholder.html", active="export", title="导出分享",
            note="阶段5 实现：微信图文 HTML · 独立分享网页(走 Pages) · Markdown/纯文本 · 复制剪贴板。",
        )

    return app
