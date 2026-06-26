"""
rss_crawler.py — RSS/Atom 订阅解析模块
负责：拉取 RSS 源 → 解析条目 → 过滤日期 → 统一输出格式
输出格式与 crawler.py 保持一致，可直接合并进 analyze_articles()
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import feedparser

from config import (
    RSS_SOURCES, REQUEST_HEADERS, RSS_FETCH_TIMEOUT,
    MAX_RSS_ITEMS_PER_SOURCE, MAX_ARTICLE_AGE_DAYS, CRAWL_TEXT_LIMIT,
)
from image_fetch import looks_like_content_image

logger = logging.getLogger(__name__)


def _parse_pub_date(entry) -> datetime | None:
    """
    从 feedparser entry 中提取发布时间，统一转为 UTC aware datetime。
    尝试 published_parsed → updated_parsed → published 字符串解析。
    """
    # feedparser 已解析的 time.struct_time（UTC）
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass

    # 原始字符串回退
    for field in ("published", "updated"):
        raw = getattr(entry, field, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass

    return None


def _is_recent(pub_date: datetime | None, max_days: int) -> bool:
    """判断文章是否在 max_days 天内。无法获取日期时默认保留。"""
    if pub_date is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    return pub_date >= cutoff


def _extract_text_from_entry(entry) -> str:
    """从 RSS 条目中提取可用文本（summary > content > title）"""
    # content 字段（部分源提供全文）
    content_list = getattr(entry, "content", None)
    if content_list:
        raw = content_list[0].get("value", "")
        # 剥离 HTML 标签
        import re
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            return text[:CRAWL_TEXT_LIMIT]

    # summary 字段
    summary = getattr(entry, "summary", "") or ""
    import re
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary[:CRAWL_TEXT_LIMIT]


def _extract_image_from_entry(entry) -> str:
    """尝试从 RSS 条目中提取封面图 URL"""
    # media:thumbnail
    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail and isinstance(media_thumbnail, list):
        url = media_thumbnail[0].get("url", "")
        if url:
            return url

    # enclosure（播客/RSS 图片附件）
    enclosures = getattr(entry, "enclosures", [])
    for enc in enclosures:
        if enc.get("type", "").startswith("image/"):
            return enc.get("href", "")

    # media:content
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for mc in media_content:
            if mc.get("medium") == "image" or mc.get("type", "").startswith("image/"):
                return mc.get("url", "")

    # content:encoded HTML（WordPress 等站点提供全文，首图往往在此，无需额外请求）
    content_list = getattr(entry, "content", None)
    if content_list:
        import re
        raw_html = content_list[0].get("value", "")
        if raw_html:
            for m in re.finditer(r'(?:src|data-src|data-original)=["\']([^"\']+\.(jpg|jpeg|png|webp)(?:[^"\']*)?)["\']', raw_html, re.IGNORECASE):
                src = m.group(1).strip()
                if src and looks_like_content_image(src):
                    return src

    return ""


def fetch_rss_source(source_config: dict, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> list[dict]:
    """
    拉取单个 RSS 源，返回标准化文章列表。
    每篇文章格式：
    {
        "url": str,
        "title": str,
        "text": str,        # 正文摘录（≤1000字）
        "image_url": str,
        "pub_date": datetime | None,
        "source": str,      # 来源名称
        "language": str,    # "zh" | "en"
        "weight": float,    # 来源权重
    }
    """
    name = source_config["name"]
    feed_url = source_config["url"]
    language = source_config.get("language", "en")
    weight = source_config.get("weight", 1.0)

    logger.info(f"[RSS] 拉取 {name}: {feed_url}")

    try:
        resp = requests.get(
            feed_url,
            headers={
                **REQUEST_HEADERS,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
            timeout=RSS_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning(f"[RSS] 拉取失败 {name}: {e}")
        return []

    if feed.bozo and not feed.entries:
        logger.warning(f"[RSS] 解析异常 {name}: {feed.bozo_exception}")
        return []

    tier = source_config.get("tier", "media")
    max_items = source_config.get("max_items", MAX_RSS_ITEMS_PER_SOURCE)
    articles = []
    for entry in feed.entries[:max_items * 2]:  # 多取一些，过滤后再截断
        link = getattr(entry, "link", "") or ""
        title = getattr(entry, "title", "") or ""

        if not link or not title:
            continue

        pub_date = _parse_pub_date(entry)

        if not _is_recent(pub_date, max_age_days):
            logger.debug(f"  [跳过-过期] {title[:40]}")
            continue

        text = _extract_text_from_entry(entry)
        image_url = _extract_image_from_entry(entry)

        articles.append({
            "url": link,
            "title": title.strip(),
            "text": text,
            "image_url": image_url,
            "pub_date": pub_date,
            "source": name,
            "language": language,
            "weight": weight,
            "tier": tier,
        })

        if len(articles) >= max_items:
            break

    logger.info(f"[RSS] {name}: 收到 {len(feed.entries)} 条，保留 {len(articles)} 篇（近{max_age_days}天）")
    return articles


def crawl_all_rss(max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> list[dict]:
    """拉取所有 RSS 源，返回合并后的文章列表"""
    all_articles = []
    for source in RSS_SOURCES:
        if not source.get("enabled", True):
            logger.info(f"[RSS] 跳过已禁用源: {source['name']}")
            continue
        articles = fetch_rss_source(source, max_age_days=max_age_days)
        all_articles.extend(articles)
        time.sleep(0.5)  # 礼貌间隔
    logger.info(f"[RSS] 全部源合计: {len(all_articles)} 篇")
    return all_articles
