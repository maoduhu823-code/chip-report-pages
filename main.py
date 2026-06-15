"""
main.py — 主入口

运行方式：
  python main.py --report business   商业日报（每天跑：阈值收录，不限条目，标签粗分组）
  python main.py --report tech       技术周报（每周跑：Top N 精读 + 本周讯息总结 + 附录）
  python main.py --report both       两份都生成（手动调试用，默认值）

跨天/跨周防重复：已收录（含附录）的 URL 记入 output/seen_<slug>.json，下次运行跳过。
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

# 计划任务可能从任意工作目录启动，统一切到脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

from config import (
    OUTPUT_DIR,
    RSS_SOURCES, HTML_SOURCES,
    TECH_ARTICLE_AGE_DAYS, BUSINESS_ARTICLE_AGE_DAYS,
    SEEN_URLS_KEEP,
)
from rss_crawler import crawl_all_rss
from crawler import crawl_all_html, enrich_rss_articles
from analyzer import analyze_business_daily, analyze_tech_weekly
from reporter import save_report


# ============================================================
# seen 记录：防止 HTML 站点无日期文章跨天/跨周重复收录
# ============================================================

def _seen_path(slug: str) -> str:
    return os.path.join(OUTPUT_DIR, f"seen_{slug}.json")


def load_seen(slug: str) -> set[str]:
    try:
        with open(_seen_path(slug), encoding="utf-8") as f:
            return set(json.load(f).get("urls", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(slug: str, seen: set[str], new_urls: list[str]) -> None:
    """新 URL 追加在尾部，超限时丢弃最旧的。"""
    merged = [u for u in seen if u not in set(new_urls)] + new_urls
    merged = merged[-SEEN_URLS_KEEP:]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(_seen_path(slug), "w", encoding="utf-8") as f:
        json.dump({"urls": merged}, f, ensure_ascii=False, indent=0)


def filter_unseen(articles: list[dict], slug: str) -> list[dict]:
    seen = load_seen(slug)
    fresh = [a for a in articles if a["url"] not in seen]
    skipped = len(articles) - len(fresh)
    if skipped:
        logger.info(f"[{slug}] 跳过 {skipped} 篇已收录过的文章，剩余 {len(fresh)} 篇")
    return fresh


# ============================================================
# 报告流程
# ============================================================

def _within_days(articles: list[dict], days: int) -> list[dict]:
    """按发布日期过滤；无日期的（HTML 站点）保留，靠 seen 记录防重复。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [
        a for a in articles
        if a.get("pub_date") is None or not getattr(a["pub_date"], "tzinfo", None) or a["pub_date"] >= cutoff
    ]


def run_business_daily(all_articles: list[dict]) -> str:
    """商业日报：有啥说啥，阈值收录不限条目，按主标签粗分组。"""
    articles = filter_unseen(_within_days(all_articles, BUSINESS_ARTICLE_AGE_DAYS), "business")
    if not articles:
        logger.warning("商业日报：本次无新文章，跳过生成")
        return ""

    report = analyze_business_daily(articles)
    site_names = [s["name"] for s in RSS_SOURCES] + [s["name"] for s in HTML_SOURCES]
    html_path = save_report(
        report["results"],
        report["executive_summary"],
        site_names,
        candidates_count=report["candidates_count"],
        report_type="business",
        report_slug="business",
        report_title="半导体行业商业动态日报",
        metadata=report.get("metadata"),
        file_prefix_word="daily",
    )
    # 本次处理过的 URL 全部记入 seen（含未达阈值的，避免明天重复评分）
    save_seen("business", load_seen("business"), [a["url"] for a in articles])
    logger.info(f"商业日报已生成: {os.path.abspath(html_path)}")
    return html_path


def run_tech_weekly(all_articles: list[dict]) -> str:
    """技术周报：Top N 精读 + 本周讯息总结 + 未入选附录。"""
    articles = filter_unseen(all_articles, "tech")
    if not articles:
        logger.warning("技术周报：本次无新文章，跳过生成")
        return ""

    report = analyze_tech_weekly(articles)
    site_names = [s["name"] for s in RSS_SOURCES] + [s["name"] for s in HTML_SOURCES]
    html_path = save_report(
        report["results"],
        report["executive_summary"],
        site_names,
        candidates_count=report["candidates_count"],
        report_type="tech",
        report_slug="tech",
        report_title="半导体行业技术周报",
        weekly_digest=report["weekly_digest"],
        appendix=report["appendix"],
        metadata=report.get("metadata"),
        file_prefix_word="weekly",
    )
    save_seen("tech", load_seen("tech"), [a["url"] for a in articles])
    logger.info(f"技术周报已生成: {os.path.abspath(html_path)}")
    return html_path


# ============================================================
# 主入口
# ============================================================

def check_config():
    import shutil
    from config import DEEPSEEK_API_KEY, LLM_PROVIDER

    provider = LLM_PROVIDER if LLM_PROVIDER in {"auto", "deepseek", "claude"} else "auto"
    if provider in {"auto", "deepseek"} and DEEPSEEK_API_KEY:
        return
    if provider == "deepseek":
        logger.error("LLM_PROVIDER=deepseek，但未设置 DEEPSEEK_API_KEY")
        sys.exit(1)
    if not shutil.which("claude"):
        logger.error("未找到 claude 命令，请先安装 Claude Code CLI（https://claude.ai/code）")
        sys.exit(1)


def crawl_candidates(max_age_days: int) -> list[dict]:
    all_articles: list[dict] = []

    logger.info(f"\n[Step 1] RSS 订阅源抓取（{len(RSS_SOURCES)} 个源，时效 {max_age_days} 天）")
    rss_articles = crawl_all_rss(max_age_days=max_age_days)
    all_articles.extend(rss_articles)
    logger.info(f"RSS 共获取 {len(rss_articles)} 篇")

    logger.info(f"\n[Step 2] HTML 站点爬取（{len(HTML_SOURCES)} 个站点）")
    html_articles = crawl_all_html()
    all_articles.extend(html_articles)
    logger.info(f"HTML 共获取 {len(html_articles)} 篇")

    logger.info(f"\n全部候选文章: {len(all_articles)} 篇")

    logger.info("\n[Step 3] 补全短文本文章（RSS teaser → 原文页全文）")
    enrich_rss_articles(all_articles)

    return all_articles


def main():
    parser = argparse.ArgumentParser(description="半导体行业报告生成器")
    parser.add_argument(
        "--report", choices=["business", "tech", "both"], default="both",
        help="business=商业日报（每天） tech=技术周报（每周） both=两份都生成",
    )
    args = parser.parse_args()

    check_config()

    logger.info("=" * 60)
    logger.info(f"半导体行业报告生成器 — 模式: {args.report}")
    logger.info("=" * 60)

    if args.report == "business":
        max_age_days = BUSINESS_ARTICLE_AGE_DAYS
    elif args.report == "tech":
        max_age_days = TECH_ARTICLE_AGE_DAYS
    else:
        max_age_days = max(BUSINESS_ARTICLE_AGE_DAYS, TECH_ARTICLE_AGE_DAYS)

    all_articles = crawl_candidates(max_age_days)
    if not all_articles:
        logger.error("未能抓取到任何文章，请检查网络连接或站点配置")
        sys.exit(1)

    generated = []
    if args.report in ("business", "both"):
        logger.info("\n[商业日报] 分析中...")
        path = run_business_daily(all_articles)
        if path:
            generated.append(("商业日报", path))

    if args.report in ("tech", "both"):
        logger.info("\n[技术周报] 分析中...")
        path = run_tech_weekly(all_articles)
        if path:
            generated.append(("技术周报", path))

    print("\n" + "=" * 60)
    for label, path in generated:
        print(f"{label} HTML: {os.path.abspath(path)}")
        print(f"{label} JSON: {os.path.abspath(path.replace('.html', '.json'))}")
    print("=" * 60)


if __name__ == "__main__":
    main()
