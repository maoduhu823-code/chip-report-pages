"""
ratings_store.py — 人工评分存储

评审看板里给单条新闻打的 1–10 人工分、快捷标记、备注，按**文章 URL 主键**
持久化到 data/ratings.json（与 seen-URL 思路一致，跨报告/跨天稳定）。

每条记录顺带存标题/来源/AI 分/分类等快照，使「导出」「周报从人工评分挑 Top-N」
无需再回读几 MB 的报告 JSON。

设计约束（与 settings_store 一致）：
- 原子写（临时文件 + os.replace），坏 JSON 不拖垮主流程；
- data/ 相对本文件定位，不依赖当前工作目录。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_ROOT, "data")
RATINGS_PATH = os.path.join(DATA_DIR, "ratings.json")

# 串行化 load→改→save_all，避免 Flask threaded 下并发写丢更新
# （评审看板上对同一条快速连点 flag/分数时尤为关键）
_LOCK = threading.Lock()

# 报告文件名 → (slug, date)，用于从 file 参数反推归属报告。
# tech-handpick 归入 tech，便于人工精选周报继续参与评分/导出。
_FILE_RE = re.compile(r"^(?:daily|weekly)_(\d{4}-\d{2}-\d{2})_(business|tech(?:-handpick)?)$")

# 允许写入记录的快照字段（其余字段忽略，避免前端塞脏数据）
_SNAPSHOT_FIELDS = ("title", "source", "ai_score", "category", "slug", "date")

# 合法的快捷标记
ALLOWED_FLAGS = ("分享", "追问", "忽略")


# ============================================================
# 文件读写（原子）
# ============================================================

def load() -> dict:
    """读取全部评分；文件不存在或损坏时返回 {}。"""
    try:
        with open(RATINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"ratings.json 读取失败，忽略: {e}")
        return {}


def save_all(data: dict) -> None:
    """原子写入全部评分。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="ratings_", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, RATINGS_PATH)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ============================================================
# 字段净化
# ============================================================

def _clean_score(value) -> int | None:
    """人工分强转 1–10 整数；越界/空值返回 None（表示未评分）。"""
    if value in (None, "", "null"):
        return None
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if n < 1 or n > 10:
        return None
    return n


def _clean_flags(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for f in value:
        s = str(f).strip()
        if s in ALLOWED_FLAGS and s not in out:
            out.append(s)
    return out


def _is_empty(entry: dict) -> bool:
    """没有人工分、没有标记、没有备注 → 视为空评分，可从库里移除。"""
    return (
        entry.get("human_score") is None
        and not entry.get("flags")
        and not (entry.get("note") or "").strip()
    )


# ============================================================
# 增删查
# ============================================================

def upsert(url: str, payload: dict) -> dict:
    """
    按 URL 写入/更新一条评分，返回最终存储的记录（被清空时返回 {}）。
    payload 可含：human_score、flags、note、file（用于反推 slug/date）、
    及 title/source/ai_score/category 等快照字段。
    """
    url = (url or "").strip()
    if not url:
        return {}

    with _LOCK:
        data = load()
        entry = dict(data.get(url, {}))

        # 从 file 名反推归属报告（前端只需传报告 basename）
        m = _FILE_RE.match(str(payload.get("file", "")))
        if m:
            payload.setdefault("date", m.group(1))
            payload.setdefault("slug", "tech" if m.group(2).startswith("tech") else "business")

        if "human_score" in payload:
            entry["human_score"] = _clean_score(payload["human_score"])
        if "flags" in payload:
            entry["flags"] = _clean_flags(payload["flags"])
        if "note" in payload:
            entry["note"] = str(payload.get("note") or "").strip()

        for key in _SNAPSHOT_FIELDS:
            if key in payload and payload[key] not in (None, ""):
                entry[key] = payload[key]

        entry["url"] = url
        entry["rated_at"] = datetime.now().isoformat(timespec="seconds")

        if _is_empty(entry):
            if url in data:
                data.pop(url, None)
                save_all(data)
            return {}

        data[url] = entry
        save_all(data)
        return entry


def delete(url: str) -> bool:
    with _LOCK:
        data = load()
        if url in data:
            data.pop(url, None)
            save_all(data)
            return True
        return False
