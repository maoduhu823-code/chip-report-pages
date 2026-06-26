"""
crawl_only.py — 纯爬虫脚本（不调用任何大语言模型）
负责：RSS 抓取 + HTML 爬取 → 跨源去重 → 保存 raw_articles.json

由定时任务调用，产出的 raw_articles.json 供 Claude 在上下文中直接分析。
"""

import json
import logging
import sys
import os
from datetime import datetime
from difflib import SequenceMatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawl.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

from config import OUTPUT_DIR, DEDUP_SIMILARITY_THRESHOLD, TECH_ARTICLE_AGE_DAYS
from rss_crawler import crawl_all_rss
from crawler import crawl_all_html, enrich_rss_articles


def deduplicate(articles: list[dict], threshold: float = DEDUP_SIMILARITY_THRESHOLD) -> list[dict]:
    """基于标题相似度去重，保留正文较长的版本"""
    kept: list[dict] = []
    for article in articles:
        is_dup = False
        for existing in kept:
            t1 = article["title"].lower()
            t2 = existing["title"].lower()
            if SequenceMatcher(None, t1, t2).ratio() >= threshold:
                if len(article.get("text", "")) > len(existing.get("text", "")):
                    kept.remove(existing)
                    kept.append(article)
                is_dup = True
                break
        if not is_dup:
            kept.append(article)
    return kept


def serialize_article(a: dict) -> dict:
    """将 datetime 对象转为字符串，确保可 JSON 序列化"""
    result = dict(a)
    if hasattr(result.get("pub_date"), "strftime"):
        result["pub_date"] = result["pub_date"].strftime("%Y-%m-%d")
    elif result.get("pub_date") is None:
        result["pub_date"] = ""
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="纯爬虫，不调用 LLM")
    parser.add_argument("--age-days", type=int, default=TECH_ARTICLE_AGE_DAYS,
                        help="RSS 文章时效窗口（天），默认 7；商业日报传 2")
    args = parser.parse_args()

    logger.info("=" * 55)
    logger.info(f"爬虫启动（无 LLM 模式，时效 {args.age_days} 天）")
    logger.info("=" * 55)

    all_articles = []

    # Step 1: RSS
    logger.info("[Step 1] 抓取 RSS 订阅源...")
    rss_articles = crawl_all_rss(max_age_days=args.age_days)
    all_articles.extend(rss_articles)
    logger.info(f"RSS: {len(rss_articles)} 篇")

    # Step 2: HTML
    logger.info("[Step 2] 爬取 HTML 站点...")
    html_articles = crawl_all_html()
    all_articles.extend(html_articles)
    logger.info(f"HTML: {len(html_articles)} 篇，合计: {len(all_articles)} 篇")

    # Step 3: 补全 RSS 文章缺失的图片（回原文页抓 og:image）
    logger.info("[Step 3] 补全缺失图片...")
    enrich_rss_articles(rss_articles)

    # Step 4: 去重
    before = len(all_articles)
    all_articles = deduplicate(all_articles)
    logger.info(f"去重后: {len(all_articles)} 篇（移除 {before - len(all_articles)} 篇重复）")

    if not all_articles:
        logger.error("未抓取到任何文章，请检查网络或站点配置")
        sys.exit(1)

    # Step 5: 保存 JSON
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, "raw_articles.json")

    payload = {
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(all_articles),
        "articles": [serialize_article(a) for a in all_articles],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    abs_path = os.path.abspath(output_path)
    logger.info(f"\n✓ 已保存 {len(all_articles)} 篇原始文章到: {abs_path}")
    print(f"\nRAW_ARTICLES_PATH={abs_path}")  # 方便定时任务解析路径
    return abs_path


if __name__ == "__main__":
    main()
