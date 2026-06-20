"""
analyzer.py — 分析模块
商业日报：评分 → 阈值收录（不限条目）→ 摘要 → 概览
技术周报：评分 → Top N 精读 → 概览 + 本周讯息总结 → 未入选附录

LLM 调用：Python 准备输入，优先 DeepSeek API；失败时回退 Claude Code CLI。
"""

import json
import logging
import os
import subprocess
import tempfile
from copy import deepcopy
from difflib import SequenceMatcher

import requests

import config
from config import (
    BUSINESS_EXECUTIVE_SUMMARY_PROMPT,
    BUSINESS_MIN_SCORE,
    BUSINESS_RELEVANCE_PROMPT,
    BUSINESS_SUMMARY_PROMPT,
    CLAUDE_HAIKU_MODEL,
    CLAUDE_SONNET_MODEL,
    DEDUP_SIMILARITY_THRESHOLD,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_FAST,
    DEEPSEEK_MODEL_STRONG,
    LLM_PROVIDER,
    LLM_SCORING_BATCH_SIZE,
    TECH_CATEGORIES,
    TECH_EXECUTIVE_SUMMARY_PROMPT,
    TECH_RELEVANCE_PROMPT,
    TECH_SUMMARY_PROMPT,
    TECH_TOP_N,
    TECH_WEEKLY_DIGEST_PROMPT,
    TIER_PRIORITY,
    TRANSLATION_PROMPT,
)

logger = logging.getLogger(__name__)

LLM_USAGE = {
    "ai_executor": "AI",
    "provider": None,
    "models": {},
    "calls": 0,
    "input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "captured_token_calls": 0,
    "token_unit": "tokens",
    "token_source": "runtime",
    "cost_usd": 0.0,
}


def reset_llm_usage() -> None:
    LLM_USAGE.update({
        "ai_executor": "AI",
        "provider": None,
        "models": {},
        "calls": 0,
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "captured_token_calls": 0,
        "token_unit": "tokens",
        "token_source": "runtime",
        "cost_usd": 0.0,
    })


def get_llm_usage_metadata() -> dict:
    metadata = dict(LLM_USAGE)
    metadata["models"] = dict(LLM_USAGE["models"])
    if metadata.get("captured_token_calls", 0) == 0:
        metadata["input_tokens"] = None
        metadata["cache_creation_input_tokens"] = None
        metadata["cache_read_input_tokens"] = None
        metadata["output_tokens"] = None
        metadata["total_tokens"] = None
        metadata["cost_usd"] = None
    return metadata


def _normalize_deepseek_usage(usage: dict) -> dict:
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": 0.0,
    }


def _normalize_claude_usage(usage: dict, result_data: dict | None = None) -> dict:
    base_input = int(usage.get("input_tokens", 0) or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    billed_input = base_input + cache_creation + cache_read
    cost_usd = 0.0
    if result_data:
        try:
            cost_usd = float(result_data.get("total_cost_usd", 0) or 0)
        except (TypeError, ValueError):
            cost_usd = 0.0
    return {
        "input_tokens": billed_input,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "total_tokens": billed_input + output_tokens,
        "cost_usd": cost_usd,
    }


def _record_llm_usage(provider: str, model: str, usage: dict | None, result_data: dict | None = None) -> None:
    LLM_USAGE["provider"] = provider
    LLM_USAGE["ai_executor"] = f"{provider} API" if provider == "DeepSeek" else provider
    LLM_USAGE["calls"] += 1
    LLM_USAGE["models"][model] = LLM_USAGE["models"].get(model, 0) + 1

    if not usage:
        return

    if provider == "DeepSeek":
        normalized = _normalize_deepseek_usage(usage)
    elif provider == "Claude Code":
        normalized = _normalize_claude_usage(usage, result_data)
    else:
        normalized = _normalize_deepseek_usage(usage)

    LLM_USAGE["captured_token_calls"] += 1
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                "output_tokens", "total_tokens"):
        LLM_USAGE[key] += int(normalized.get(key, 0) or 0)
    LLM_USAGE["cost_usd"] += float(normalized.get("cost_usd", 0.0) or 0.0)


# ============================================================
# LLM 调用：DeepSeek API 优先，Claude Code CLI 回退
# ============================================================

def _resolve_claude_exe() -> str:
    """
    找到 claude 可执行文件的完整路径。
    Windows 下 npm 将 claude 安装为 .cmd 包装器（内部调用 claude.exe），
    直接解析出 .exe 路径以避免 cmd.exe 的 stdin 管道问题。
    """
    import shutil
    claude_path = shutil.which("claude")
    if not claude_path:
        raise FileNotFoundError("未找到 claude 命令，请确认 Claude Code CLI 已安装并在 PATH 中")
    # Windows npm .cmd 包装器 → 内部调用同目录下的 .exe 或 node_modules 里的 .exe
    if claude_path.upper().endswith(".CMD") or claude_path.upper().endswith(".BAT"):
        cmd_dir = os.path.dirname(os.path.abspath(claude_path))
        # 尝试读取 .cmd，将 %dp0% 展开为 cmd 所在目录，提取 .exe 路径
        try:
            with open(claude_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    # npm 生成的 .cmd 典型格式: "%dp0%\node_modules\...\claude.exe" %*
                    if ".exe" in line.lower() and "%" in line:
                        # 展开 %dp0% 和 %~dp0
                        line = line.replace("%dp0%", cmd_dir).replace("%~dp0", cmd_dir)
                        # 提取第一个引号对里的路径
                        parts = line.split('"')
                        if len(parts) >= 3:
                            candidate = parts[1]
                            if os.path.isfile(candidate):
                                return candidate
        except Exception:
            pass
        raise FileNotFoundError(
            f"无法从 {claude_path} 解析出实际 .exe，请检查 Claude Code 安装"
        )
    return claude_path


def _build_claude_cmd(model: str, system_prompt: str) -> list[str]:
    """构造 claude CLI 调用命令，返回可直接传入 subprocess.run 的列表。"""
    claude_exe = _resolve_claude_exe()
    model_id = CLAUDE_SONNET_MODEL if model == "sonnet" else CLAUDE_HAIKU_MODEL if model == "haiku" else model
    return [
        claude_exe, "--print", "--output-format", "json",
        "--model", model_id, "--system-prompt", system_prompt, "--tools", "",
    ]


def _call_claude_cli(system_prompt: str, user_content: str, max_tokens: int = 256,
                     model: str = "haiku") -> str:
    """
    将 user_content 写入本地临时文本，通过 claude CLI stdin 管道执行推理。
    model: 别名 haiku / sonnet / opus，或完整 model ID。
    max_tokens: 保留参数（CLI 无对应 flag），仅作文档用途。
    失败时返回空字符串，由调用方触发规则兜底。
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="sem_prompt_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(user_content)
        cmd = _build_claude_cmd(model, system_prompt)
        with open(tmp_path, "r", encoding="utf-8") as prompt_file:
            result = subprocess.run(
                cmd,
                stdin=prompt_file,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=120,
            )
        if result.returncode == 0:
            raw_output = result.stdout.strip()
            try:
                data = json.loads(raw_output)
                model_id = CLAUDE_SONNET_MODEL if model == "sonnet" else CLAUDE_HAIKU_MODEL if model == "haiku" else model
                _record_llm_usage("Claude Code", model_id, data.get("usage"), data)
                return str(data.get("result", "")).strip()
            except json.JSONDecodeError:
                logger.warning(f"claude CLI JSON解析失败，使用原始输出: {raw_output[:120]}")
                return raw_output
        logger.error(f"claude CLI rc={result.returncode}: {result.stderr[:200]}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error("claude CLI 超时（>120s），请检查网络或模型响应")
        return ""
    except FileNotFoundError as e:
        logger.error(str(e))
        return ""
    except Exception as e:
        logger.error(f"claude CLI 异常: {type(e).__name__}: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _deepseek_model(model: str) -> str:
    return DEEPSEEK_MODEL_STRONG if model == "sonnet" else DEEPSEEK_MODEL_FAST


def _call_deepseek_api(system_prompt: str, user_content: str, max_tokens: int = 256,
                       model: str = "haiku", prefer_json: bool = False) -> str:
    """通过 DeepSeek OpenAI-compatible Chat Completions API 调用模型。"""
    if not DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API Key 未配置（DEEPSEEK_API_KEY 环境变量为空）")
        return ""

    url = DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": _deepseek_model(model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    if prefer_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        if response.status_code != 200:
            logger.warning(f"DeepSeek API rc={response.status_code}: {response.text[:200]}")
            return ""
        data = response.json()
        _record_llm_usage("DeepSeek", payload["model"], data.get("usage"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"DeepSeek API 异常: {type(e).__name__}: {e}")
        return ""


def _call_llm(system_prompt: str, user_content: str, max_tokens: int = 256,
              model: str = "haiku", prefer_json: bool = False) -> str:
    """轻量文本接口：Python 准备输入，LLM 只返回文本/JSON。"""
    provider = LLM_PROVIDER if LLM_PROVIDER in {"auto", "deepseek", "claude"} else "auto"

    if provider in {"auto", "deepseek"}:
        response = _call_deepseek_api(system_prompt, user_content, max_tokens, model, prefer_json)
        if response:
            return response
        if provider == "deepseek":
            return ""
        # auto 模式：不自动切换，抛出异常要求用户明确决策
        raise RuntimeError(
            "DeepSeek 不可用，已停止运行。\n"
            "原因见上方日志（Key 未配置 / API 请求失败 / 余额不足）。\n"
            "若需改用 Claude Code CLI，请设置环境变量 LLM_PROVIDER=claude 后重新运行。"
        )

    # provider == "claude"：用户已明确选择 Claude Code CLI
    response = _call_claude_cli(system_prompt, user_content, max_tokens, model)
    return response


# ============================================================
# 内部工具函数
# ============================================================

def _strip_json_md(text: str) -> str:
    """剥离 LLM 可能加的 ```json ... ``` 包装，返回裸 JSON 字符串。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # 丢弃首行（```json 或 ```）和尾行（```）
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    return text


def _title_similarity(t1: str, t2: str) -> float:
    return SequenceMatcher(None, t1.lower(), t2.lower()).ratio()


def _article_blob(article: dict, limit: int = 800) -> str:
    return f"{article.get('title', '')}\n{article.get('text', '')[:limit]}".lower()


def _lead_text(article: dict, limit: int = 500) -> str:
    """评分只使用标题和首段/前导文字，降低输入量并避免全文进入评分。"""
    text = article.get("text", "").replace("\r\n", "\n").strip()
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    lead = paragraphs[0] if paragraphs else text
    return lead[:limit]


def _keyword_hits(blob: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for kw in keywords if kw.lower() in blob)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _looks_like_auth_boilerplate(text: str) -> bool:
    lowered = str(text or "").lower()
    tokens = (
        "login", "sign up", "username", "password", "remember me",
        "forgot password", "register", "email address",
    )
    return sum(1 for token in tokens if token in lowered) >= 3


def _ensure_chinese_summary(article: dict, summary: str) -> str:
    """报告面向中文读者；兜底文本不能把登录框/英文噪声直接带进报告。"""
    summary = str(summary or "").strip()
    title = str(article.get("title", "")).strip()
    if summary and _has_cjk(summary) and not _looks_like_auth_boilerplate(summary):
        return summary
    if _has_cjk(title):
        return f"正文提取异常，暂以原始标题判断：{title}"
    return "摘要生成失败，未能获得有效中文正文。"


def _fallback_summary(article: dict, report_type: str) -> dict:
    """CLI 调用失败时的轻量兜底摘要，方便测试报告版式。"""
    title = article.get("title", "").strip()
    text = article.get("text", "").strip()
    summary = text[:300] if len(text) <= 300 else text[:297] + "..."
    if not summary:
        summary = title
    summary = _ensure_chinese_summary(article, summary)

    blob = _article_blob(article)
    if report_type == "business":
        keywords = _fallback_business_tags(article)[1]
    else:
        terms = []
        for kw in ("GAA", "HBM", "CoWoS", "Chiplet", "RISC-V", "EDA", "CXL", "UCIe", "PCIe", "AI"):
            if kw.lower() in blob:
                terms.append(kw)
        keywords = "，".join(terms[:5])

    return {
        "title": title[:60],
        "summary": summary,
        "keywords": keywords,
    }


# ============================================================
# 去重
# ============================================================

def deduplicate(articles: list[dict], threshold: float = DEDUP_SIMILARITY_THRESHOLD) -> list[dict]:
    """
    基于标题相似度跨源去重。
    重复时优先保留层级更高的信源（官方公告 > 监管 > 行业组织 > 论文 > 媒体 > 聚合）；
    同层级则保留正文更长的版本。
    """
    kept: list[dict] = []
    for article in articles:
        is_dup = False
        for i, existing in enumerate(kept):
            sim = _title_similarity(article["title"], existing["title"])
            if sim >= threshold:
                new_prio = TIER_PRIORITY.get(article.get("tier", "media"), 4)
                old_prio = TIER_PRIORITY.get(existing.get("tier", "media"), 4)
                if new_prio < old_prio or (
                    new_prio == old_prio
                    and len(article.get("text", "")) > len(existing.get("text", ""))
                ):
                    kept[i] = article   # 原地替换，保持发现顺序
                is_dup = True
                break
        if not is_dup:
            kept.append(article)

    removed = len(articles) - len(kept)
    if removed:
        logger.info(f"去重移除 {removed} 篇重复文章，剩余 {len(kept)} 篇")
    return kept


# ============================================================
# 技术版评分
# ============================================================

def _fallback_tech_score(article: dict) -> tuple[int, str]:
    blob = _article_blob(article)
    buckets = config.TECH_KEYWORD_BUCKETS  # 词库可被 GUI 覆盖，动态取最新值

    best_category = "其他技术"
    best_hits = 0
    for category, keywords in buckets.items():
        hits = _keyword_hits(blob, keywords)
        if hits > best_hits:
            best_category = category
            best_hits = hits

    score = min(10, 4 + best_hits)
    if best_hits == 0:
        score = 2
    return score, best_category


def score_tech_relevance(article: dict) -> tuple[int, str]:
    """对单篇文章进行技术版相关度评分（0-10）并分类。"""
    user_content = f"标题：{article['title']}\n\n首段：{_lead_text(article)}"
    response = _call_llm(TECH_RELEVANCE_PROMPT, user_content, max_tokens=256, model="haiku", prefer_json=True)

    if response:
        try:
            data = json.loads(_strip_json_md(response))
            score = int(data.get("score", 0))
            category = data.get("category", "其他技术")
            if category not in TECH_CATEGORIES:
                category = "其他技术"
            reason = data.get("reason", "")
            logger.info(f"  技术 {score}/10 [{category}] ({reason}) — {article['title'][:35]}")
            return score, category
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"  技术评分解析失败，原始响应: {response[:80]}")

    score, category = _fallback_tech_score(article)
    logger.info(f"  技术 {score}/10 [{category}] (规则兜底) — {article['title'][:35]}")
    return score, category


# ============================================================
# 商业版评分
# ============================================================

def _fallback_business_tags(article: dict) -> tuple[int, str]:
    blob = _article_blob(article)
    buckets = config.BUSINESS_KEYWORD_BUCKETS  # 词库可被 GUI 覆盖，动态取最新值

    tag_hits: list[tuple[str, int]] = []
    for tag, keywords in buckets.items():
        hits = _keyword_hits(blob, keywords)
        if hits:
            tag_hits.append((tag, hits))

    tag_hits.sort(key=lambda item: item[1], reverse=True)
    tags = [tag for tag, _ in tag_hits[:4]]
    if not tags:
        tags = ["其他商业"]

    score = min(10, 3 + sum(hits for _, hits in tag_hits[:3]))
    if tags == ["其他商业"]:
        score = 2
    return score, "，".join(tags)


def score_business_relevance(article: dict) -> tuple[int, str]:
    """对单篇文章进行商业版相关度评分（0-10）并输出标签。"""
    user_content = f"标题：{article['title']}\n\n首段：{_lead_text(article)}"
    response = _call_llm(BUSINESS_RELEVANCE_PROMPT, user_content, max_tokens=256, model="haiku", prefer_json=True)

    if response:
        try:
            data = json.loads(_strip_json_md(response))
            score = int(data.get("score", 0))
            tags = data.get("tags", "其他商业")
            reason = data.get("reason", "")
            logger.info(f"  商业 {score}/10 [{tags}] ({reason}) — {article['title'][:35]}")
            return score, tags
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"  商业评分解析失败，原始响应: {response[:80]}")

    score, tags = _fallback_business_tags(article)
    logger.info(f"  商业 {score}/10 [{tags}] (规则兜底) — {article['title'][:35]}")
    return score, tags


# ============================================================
# 英文文章翻译（摘要生成前的独立步骤）
# ============================================================

def _translate_article(article: dict) -> dict:
    """将英文文章的 title/text 翻译成中文，返回新 dict（原文保留在 _en_title/_en_text）。
    仅对 language=='en' 时执行；CLI 失败则原样返回（保留英文，属预期降级行为）。
    """
    if article.get("language") != "en":
        return article

    user_content = f"标题：{article['title']}\n\n正文摘录：{article['text'][:1200]}"
    response = _call_llm(TRANSLATION_PROMPT, user_content, max_tokens=1024, model="haiku", prefer_json=True)

    if not response:
        logger.warning(f"  翻译失败，保留英文原文: {article['title'][:40]}")
        return article

    try:
        data = json.loads(_strip_json_md(response))
        title_zh = data.get("title_zh", "").strip()
        text_zh = data.get("text_zh", "").strip()
        if not title_zh:
            return article
        translated = dict(article)
        translated["_en_title"] = article["title"]
        translated["_en_text"] = article["text"]
        translated["title"] = title_zh
        translated["text"] = text_zh or article["text"]
        logger.info(f"  已翻译: {article['title'][:30]} → {title_zh[:30]}")
        return translated
    except (json.JSONDecodeError, KeyError):
        logger.warning(f"  翻译解析失败，保留英文: {article['title'][:40]}")
        return article


# ============================================================
# 摘要生成
# ============================================================

def generate_summary(article: dict, report_type: str) -> dict:
    """为单篇文章生成精炼标题、摘要和关键词（中文输出）。英文原文直接生成中文摘要。"""
    prompt = BUSINESS_SUMMARY_PROMPT if report_type == "business" else TECH_SUMMARY_PROMPT
    text_limit = 600 if report_type == "business" else 1500
    user_content = f"原始标题：{article['title']}\n\n正文：{article['text'][:text_limit]}"
    response = _call_llm(prompt, user_content, max_tokens=768, model="haiku", prefer_json=True)

    if response:
        try:
            data = json.loads(_strip_json_md(response))
            summary = str(data.get("summary", "")).strip()
            if not _has_cjk(summary) or _looks_like_auth_boilerplate(summary):
                logger.warning(f"  摘要非中文或疑似页面噪声，使用兜底摘要: {article['title'][:30]}")
                return _fallback_summary(article, report_type)
            title = str(data.get("title", article["title"])).strip() or article["title"]
            if not _has_cjk(title) and _has_cjk(article.get("title", "")):
                title = article["title"]
            return {
                "title": title,
                "summary": summary,
                "keywords": data.get("keywords", ""),
            }
        except (json.JSONDecodeError, KeyError):
            logger.warning(f"  摘要解析失败，使用兜底摘要: {article['title'][:30]}")

    return _fallback_summary(article, report_type)


def generate_executive_summary(top_articles: list[dict], report_type: str) -> str:
    """基于 Top N 文章生成报告概览。"""
    lines = []
    for i, a in enumerate(top_articles, 1):
        label = a.get("tags") if report_type == "business" else a.get("category", "其他技术")
        lines.append(f"{i}. 【{label}】{a['title']}\n   {a['summary'][:80]}")

    prompt = BUSINESS_EXECUTIVE_SUMMARY_PROMPT if report_type == "business" else TECH_EXECUTIVE_SUMMARY_PROMPT
    response = _call_llm(
        prompt.format(n=len(top_articles)),
        "\n\n".join(lines),
        max_tokens=512,
        model="sonnet",
    )
    return response or "（本期概览生成失败，请检查 claude CLI 是否正常）"


# ============================================================
# 本周讯息总结（技术周报专属板块）
# ============================================================

def generate_weekly_digest(all_scored: list[dict]) -> str:
    """基于本周全部候选文章（含未入选）生成分主题的全景总结。"""
    if not all_scored:
        return ""

    lines = []
    for a in all_scored:
        hint = a.get("text", "")[:60].replace("\n", " ")
        lines.append(f"[{a.get('category', '其他技术')}] {a['title']} — {hint}")

    response = _call_llm(
        TECH_WEEKLY_DIGEST_PROMPT.format(n=len(all_scored)),
        "\n".join(lines),
        max_tokens=1024,
        model="sonnet",
    )
    return response or "（本周讯息总结生成失败，请检查 claude CLI 是否正常）"


# ============================================================
# 主流程
# ============================================================

def _build_result(article: dict, rank: int, report_type: str) -> dict:
    # 英文文章不再全文翻译；摘要 prompt 要求直接输出中文标题/摘要/关键词。
    summary_data = generate_summary(article, report_type)

    pub_date_str = ""
    if article.get("pub_date"):
        if hasattr(article["pub_date"], "strftime"):
            pub_date_str = article["pub_date"].strftime("%Y-%m-%d")
        else:
            pub_date_str = str(article["pub_date"])

    result = {
        "rank": rank,
        "score": article["score"],
        "weighted_score": article["weighted_score"],
        "source": article.get("source", ""),
        "tier": article.get("tier", "media"),
        "language": article.get("language", "zh"),
        "url": article["url"],
        "image_url": article.get("image_url", ""),
        "pub_date": pub_date_str,
        "original_title": article["title"],   # 始终保留爬取到的原始标题（英文源即英文）
        "title": summary_data["title"],
        "summary": summary_data["summary"],
        "keywords": summary_data["keywords"],
    }

    if report_type == "business":
        result["tags"] = article.get("tags", "其他商业")
        result["category"] = ""
    else:
        result["category"] = article.get("category", "其他技术")
    return result


def _parse_batch_items(response: str) -> list[dict]:
    data = json.loads(_strip_json_md(response))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "articles"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _score_batch(articles: list[dict], report_type: str) -> bool:
    """批量评分：一次请求处理多篇标题+首段，显著减少小调用数量。"""
    if not articles:
        return True

    if report_type == "business":
        system_prompt = BUSINESS_RELEVANCE_PROMPT + """

现在请批量处理用户给出的文章数组。返回严格 JSON：
{"items":[{"id":1,"score":0-10整数,"tags":"标签1,标签2","reason":"15字以内"}]}
"""
    else:
        system_prompt = TECH_RELEVANCE_PROMPT + """

现在请批量处理用户给出的文章数组。返回严格 JSON：
{"items":[{"id":1,"score":0-10整数,"category":"技术分类","reason":"15字以内"}]}
"""

    success = True
    for start in range(0, len(articles), LLM_SCORING_BATCH_SIZE):
        chunk = articles[start:start + LLM_SCORING_BATCH_SIZE]
        payload = {
            "articles": [
                {
                    "id": i,
                    "source": a.get("source", ""),
                    "language": a.get("language", ""),
                    "title": a.get("title", ""),
                    "lead": _lead_text(a, 500),
                }
                for i, a in enumerate(chunk, 1)
            ]
        }
        response = _call_llm(
            system_prompt,
            json.dumps(payload, ensure_ascii=False),
            max_tokens=max(1024, len(chunk) * 120),
            model="haiku",
            prefer_json=True,
        )
        if not response:
            success = False
            break
        try:
            items = _parse_batch_items(response)
            by_id = {int(item.get("id")): item for item in items if item.get("id") is not None}
            if len(by_id) < len(chunk):
                raise ValueError("批量评分返回条目不完整")

            for i, article in enumerate(chunk, 1):
                item = by_id[i]
                score = max(0, min(10, int(item.get("score", 0))))
                article["score"] = score
                if report_type == "business":
                    article["tags"] = item.get("tags", "其他商业") or "其他商业"
                    logger.info(f"  商业 {score}/10 [{article['tags']}] ({item.get('reason', '')}) — {article['title'][:35]}")
                else:
                    category = item.get("category", "其他技术")
                    article["category"] = category if category in TECH_CATEGORIES else "其他技术"
                    logger.info(f"  技术 {score}/10 [{article['category']}] ({item.get('reason', '')}) — {article['title'][:35]}")
                article["weighted_score"] = round(score * article.get("weight", 1.0), 2)
        except Exception as e:
            logger.warning(f"批量评分解析失败，回退逐篇评分: {type(e).__name__}: {e}; 原始响应: {response[:120]}")
            success = False
            break

    return success


def _score_all(articles: list[dict], report_type: str) -> list[dict]:
    """对全部候选评分，返回按加权分降序的列表（原地写入 score/weighted_score）。"""
    if _score_batch(articles, report_type):
        return sorted(articles, key=lambda x: x["weighted_score"], reverse=True)

    logger.info("批量评分不可用，改用逐篇评分。")
    def _score_one(article: dict) -> None:
        if report_type == "business":
            score, tags = score_business_relevance(article)
            article["score"] = score
            article["tags"] = tags
        else:
            score, category = score_tech_relevance(article)
            article["score"] = score
            article["category"] = category
        article["weighted_score"] = round(score * article.get("weight", 1.0), 2)

    for article in articles:
        _score_one(article)

    return sorted(articles, key=lambda x: x["weighted_score"], reverse=True)


def _appendix_entry(article: dict) -> dict:
    """附录条目：仅标题超链接所需的最小信息，不含图片和摘要。"""
    pub = article.get("pub_date")
    if hasattr(pub, "strftime"):
        pub_str = pub.strftime("%Y-%m-%d")
    else:
        pub_str = str(pub) if pub else ""
    return {
        "title": article["title"],
        "url": article["url"],
        "source": article.get("source", ""),
        "language": article.get("language", "zh"),
        "pub_date": pub_str,
        "score": article.get("score", 0),
        "category": article.get("category", "其他技术"),
    }


def analyze_business_daily(articles: list[dict]) -> dict:
    """
    商业日报：去重 → 评分 → 阈值收录（有啥说啥，不限条目）→ 摘要 → 概览。
    """
    reset_llm_usage()
    work = deduplicate(deepcopy(articles))
    logger.info(f"商业日报：去重后 {len(work)} 篇，开始评分...")

    ranked = _score_all(work, "business")
    selected = [a for a in ranked if a["score"] >= BUSINESS_MIN_SCORE]
    logger.info(f"商业日报：{len(selected)} 篇达到 {BUSINESS_MIN_SCORE} 分收录线（不限条目）")

    logger.info("生成摘要中...")
    results = []
    for i, article in enumerate(selected, 1):
        logger.info(f"  [{i}/{len(selected)}] {article['title'][:35]}")
        results.append(_build_result(article, i, "business"))

    logger.info("生成日报概览...")
    executive_summary = generate_executive_summary(results, "business")

    return {
        "results": results,
        "executive_summary": executive_summary,
        "candidates_count": len(work),
        "metadata": get_llm_usage_metadata(),
    }


def analyze_tech_weekly(articles: list[dict]) -> dict:
    """
    技术周报：去重 → 评分 → Top N 精读摘要 → 趋势概览 + 本周讯息总结 → 未入选附录。
    """
    reset_llm_usage()
    work = deduplicate(deepcopy(articles))
    logger.info(f"技术周报：去重后 {len(work)} 篇，开始评分...")

    ranked = _score_all(work, "tech")
    selected = ranked[:TECH_TOP_N]
    rest = ranked[TECH_TOP_N:]
    logger.info(f"技术周报：精读 Top {len(selected)}，附录收录其余 {len(rest)} 条")

    logger.info("生成精读摘要中...")
    results = []
    for i, article in enumerate(selected, 1):
        logger.info(f"  [{i}/{len(selected)}] {article['title'][:35]}")
        results.append(_build_result(article, i, "tech"))

    logger.info("生成趋势概览...")
    executive_summary = generate_executive_summary(results, "tech")

    logger.info("生成本周讯息总结...")
    weekly_digest = generate_weekly_digest(ranked)

    return {
        "results": results,
        "executive_summary": executive_summary,
        "weekly_digest": weekly_digest,
        "appendix": [_appendix_entry(a) for a in rest],
        "candidates_count": len(work),
        "metadata": get_llm_usage_metadata(),
    }
