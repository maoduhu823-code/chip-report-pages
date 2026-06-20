"""
settings_store.py — GUI 配置覆盖层

单一信息源原则：config.py 定义「代码默认值」，本模块从 data/gui_settings.json
读取用户在 GUI 里保存的覆盖项，在 config 模块导入末尾通过 apply_overrides()
合并到 config 全局上。于是「手动运行 main.py」「计划任务」「GUI 触发的子进程」
三者共用同一份设置，无需 GUI 在线。

设计约束：
- 顶层不 import config（config.py 末尾会 import 本模块，避免循环导入）；
  需要读取 config 实时值的 snapshot_from_config() 用惰性导入。
- 覆盖项缺失/类型不对时静默跳过，保证坏掉的 JSON 不会拖垮主流程。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# data/ 目录相对本文件定位，不依赖当前工作目录（计划任务可能从任意 cwd 启动）
_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_ROOT, "data")
SETTINGS_PATH = os.path.join(DATA_DIR, "gui_settings.json")

# 标量覆盖项：JSON key -> config 全局名 -> 类型
_SCALAR_OVERRIDES: dict[str, tuple[str, type]] = {
    "business_min_score": ("BUSINESS_MIN_SCORE", int),
    "tech_top_n": ("TECH_TOP_N", int),
    "business_age_days": ("BUSINESS_ARTICLE_AGE_DAYS", int),
    "tech_age_days": ("TECH_ARTICLE_AGE_DAYS", int),
    "scoring_batch_size": ("LLM_SCORING_BATCH_SIZE", int),
    "dedup_similarity_threshold": ("DEDUP_SIMILARITY_THRESHOLD", float),
}

# 可被 GUI 覆盖的 prompt 全局名（JSON 里放在 "prompts" 子对象下）
OVERRIDABLE_PROMPTS: tuple[str, ...] = (
    "TECH_RELEVANCE_PROMPT", "BUSINESS_RELEVANCE_PROMPT",
    "TECH_SUMMARY_PROMPT", "BUSINESS_SUMMARY_PROMPT",
    "TECH_EXECUTIVE_SUMMARY_PROMPT", "BUSINESS_EXECUTIVE_SUMMARY_PROMPT",
    "TECH_WEEKLY_DIGEST_PROMPT", "TRANSLATION_PROMPT",
    "ARTICLE_FOLLOWUP_PROMPT",
)

# 关键词桶：JSON 子键 -> config 全局名
_BUCKET_OVERRIDES = {
    "tech": "TECH_KEYWORD_BUCKETS",
    "business": "BUSINESS_KEYWORD_BUCKETS",
}


# ============================================================
# 文件读写
# ============================================================

def load() -> dict:
    """读取 GUI 设置；文件不存在或损坏时返回 {}（走 config 默认值）。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"gui_settings.json 读取失败，忽略覆盖: {e}")
        return {}


def save(settings: dict) -> None:
    """原子写入 GUI 设置（先写临时文件再 os.replace，避免写一半损坏）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="gui_settings_", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def reset() -> None:
    """删除覆盖文件，让所有设置回到 config.py 的代码默认值。"""
    try:
        os.unlink(SETTINGS_PATH)
    except FileNotFoundError:
        pass


# ============================================================
# 类型强转工具
# ============================================================

def _coerce(value, typ):
    """把 JSON 里的值强转为目标类型；失败返回 None 表示「忽略此覆盖」。"""
    if value is None:
        return None
    try:
        return typ(value)
    except (TypeError, ValueError):
        return None


def _coerce_buckets(raw) -> dict | None:
    """关键词桶 JSON（{分类: [词,...]}）转成 {分类: tuple(词)}，与 config 默认结构一致。"""
    if not isinstance(raw, dict):
        return None
    out: dict[str, tuple[str, ...]] = {}
    for category, words in raw.items():
        if isinstance(words, (list, tuple)):
            out[str(category)] = tuple(str(w).strip() for w in words if str(w).strip())
    return out


def sanitize_settings(payload: dict) -> dict:
    """把 GUI POST 上来的表单数据净化成只含已知键、类型正确的设置 dict，再交给 save()。"""
    if not isinstance(payload, dict):
        return {}
    out: dict = {}

    for key, (_gname, typ) in _SCALAR_OVERRIDES.items():
        if key in payload:
            val = _coerce(payload[key], typ)
            if val is not None:
                out[key] = val

    for json_key in ("rss_sources", "html_sources"):
        items = payload.get(json_key)
        if not isinstance(items, list):
            continue
        clean = []
        for it in items:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            entry = {"name": str(it["name"])}
            if "enabled" in it:
                entry["enabled"] = bool(it["enabled"])
            weight = _coerce(it.get("weight"), float)
            if weight is not None:
                entry["weight"] = weight
            clean.append(entry)
        out[json_key] = clean

    prompts = payload.get("prompts")
    if isinstance(prompts, dict):
        clean_prompts = {
            name: text for name, text in prompts.items()
            if name in OVERRIDABLE_PROMPTS and isinstance(text, str) and text.strip()
        }
        if clean_prompts:
            out["prompts"] = clean_prompts

    buckets = payload.get("keyword_buckets")
    if isinstance(buckets, dict):
        clean_kb = {}
        for sub in ("tech", "business"):
            coerced = _coerce_buckets(buckets.get(sub))
            if coerced:
                clean_kb[sub] = {cat: list(words) for cat, words in coerced.items()}
        if clean_kb:
            out["keyword_buckets"] = clean_kb

    return out


# ============================================================
# 覆盖应用：在 config.py 末尾调用 apply_overrides(globals())
# ============================================================

def apply_overrides(g: dict) -> None:
    """把 data/gui_settings.json 中的覆盖项合并到 config 模块全局 g 上。"""
    data = load()
    if not data:
        return

    # 1) 标量阈值/窗口
    for key, (gname, typ) in _SCALAR_OVERRIDES.items():
        if key in data:
            val = _coerce(data[key], typ)
            if val is not None and gname in g:
                g[gname] = val

    # 2) prompt 文本（仅白名单内、非空字符串才覆盖）
    prompts = data.get("prompts")
    if isinstance(prompts, dict):
        for name, text in prompts.items():
            if name in OVERRIDABLE_PROMPTS and name in g and isinstance(text, str) and text.strip():
                g[name] = text

    # 3) 关键词桶
    buckets = data.get("keyword_buckets")
    if isinstance(buckets, dict):
        for sub, gname in _BUCKET_OVERRIDES.items():
            coerced = _coerce_buckets(buckets.get(sub))
            if coerced and gname in g:
                g[gname] = coerced

    # 4) 信息源 enabled / weight，按名称匹配（不新增/删除源，只调开关与权重）
    #    覆盖项格式与 snapshot_from_config 输出一致：rss_sources / html_sources 列表
    overrides_by_name: dict[str, dict] = {}
    for json_key in ("rss_sources", "html_sources"):
        for item in data.get(json_key, []) or []:
            if isinstance(item, dict) and item.get("name"):
                overrides_by_name[item["name"]] = item
    if overrides_by_name:
        for listname in ("RSS_SOURCES", "HTML_SOURCES"):
            for src in g.get(listname, []) or []:
                over = overrides_by_name.get(src.get("name"))
                if not isinstance(over, dict):
                    continue
                if "enabled" in over:
                    src["enabled"] = bool(over["enabled"])
                weight = _coerce(over.get("weight"), float)
                if weight is not None:
                    src["weight"] = weight


# ============================================================
# 给 GUI 用：从当前（已覆盖的）config 拼一份可编辑快照
# ============================================================

def snapshot_from_config() -> dict:
    """
    读取 config 模块的实时有效值，拼成 GUI 表单可直接渲染的设置 dict。
    惰性导入 config，避免与 config 末尾 import 本模块形成循环。
    返回结构即 save() 接受的结构。
    """
    import config  # 惰性导入

    def _sources(listname: str) -> list[dict]:
        out = []
        for s in getattr(config, listname, []) or []:
            out.append({
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "language": s.get("language", ""),
                "tier": s.get("tier", "media"),
                "weight": s.get("weight", 1.0),
                "enabled": s.get("enabled", True),
            })
        return out

    def _buckets(gname: str) -> dict:
        return {cat: list(words) for cat, words in getattr(config, gname, {}).items()}

    return {
        "business_min_score": config.BUSINESS_MIN_SCORE,
        "tech_top_n": config.TECH_TOP_N,
        "business_age_days": config.BUSINESS_ARTICLE_AGE_DAYS,
        "tech_age_days": config.TECH_ARTICLE_AGE_DAYS,
        "scoring_batch_size": config.LLM_SCORING_BATCH_SIZE,
        "dedup_similarity_threshold": config.DEDUP_SIMILARITY_THRESHOLD,
        "rss_sources": _sources("RSS_SOURCES"),
        "html_sources": _sources("HTML_SOURCES"),
        "keyword_buckets": {
            "tech": _buckets("TECH_KEYWORD_BUCKETS"),
            "business": _buckets("BUSINESS_KEYWORD_BUCKETS"),
        },
        "prompts": {name: getattr(config, name, "") for name in OVERRIDABLE_PROMPTS},
    }
