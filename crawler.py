"""
crawler.py — 通用 HTML 爬取模块
负责：抓取文章列表 → 提取文章链接 → 抓取正文和图片
"""

import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY,
    HTML_SOURCES, MAX_CANDIDATES_PER_SITE,
    CRAWL_TEXT_LIMIT, CRAWL_ENRICH_MIN_TEXT,
)

logger = logging.getLogger(__name__)


def fetch_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    """抓取单个页面，返回 BeautifulSoup 对象；失败返回 None"""
    try:
        # 随机 UA，降低被识别风险
        headers = {**REQUEST_HEADERS}
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"抓取失败 {url}: {e}")
        return None


def is_article_url(url: str, site_config: dict) -> bool:
    """判断一个链接是否是文章页（排除导航、标签等无效链接）"""
    skip_keywords = site_config.get("skip_url_keywords", [])
    pattern = site_config.get("article_url_pattern", "")

    for kw in skip_keywords:
        if kw in url:
            return False

    # 排除过短的路径
    path = urlparse(url).path.rstrip("/")
    if path.count("/") < 1:
        return False

    if pattern and not re.search(pattern, url):
        return False

    return True


def collect_article_links(site_config: dict, session: requests.Session, max_count: int) -> list[str]:
    """从站点的索引页中收集文章链接，返回去重后的列表"""
    base_url = site_config["url"]
    found_links: set[str] = set()

    for index_url in site_config["index_urls"]:
        logger.info(f"扫描列表页: {index_url}")
        soup = fetch_page(index_url, session)
        if not soup:
            continue

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            absolute_url = urljoin(base_url, href)

            if urlparse(absolute_url).netloc != urlparse(base_url).netloc:
                continue

            if is_article_url(absolute_url, site_config):
                found_links.add(absolute_url)

        time.sleep(REQUEST_DELAY + random.uniform(0, 0.8))

        if len(found_links) >= max_count:
            break

    logger.info(f"收集到 {len(found_links)} 个候选链接")
    return list(found_links)[:max_count]


def extract_article_content(url: str, session: requests.Session) -> dict | None:
    """
    抓取单篇文章，提取 title / text / image_url。
    返回标准格式 dict，与 rss_crawler.py 输出对齐。
    """
    soup = fetch_page(url, session)
    if not soup:
        return None

    # ---- 标题 ----
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "").strip()
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    # ---- 正文 ----
    text = ""
    article_selectors = [
        "article",
        '[class*="article"]', '[class*="content"]', '[class*="detail"]',
        '[class*="post"]', '[class*="news"]', '[class*="body"]',
        '[id*="article"]', '[id*="content"]', '[id*="detail"]',
    ]
    article_node = None
    for sel in article_selectors:
        candidates = soup.select(sel)
        if candidates:
            article_node = max(candidates, key=lambda n: len(n.get_text()))
            break

    if article_node:
        text = article_node.get_text(separator=" ", strip=True)
    else:
        body = soup.find("body")
        if body:
            text = body.get_text(separator=" ", strip=True)

    text = re.sub(r"\s+", " ", text).strip()[:CRAWL_TEXT_LIMIT]

    # ---- 图片 ----
    image_url = ""
    og_image = soup.find("meta", property="og:image")
    if og_image:
        image_url = og_image.get("content", "").strip()

    if not image_url and article_node:
        for img in article_node.find_all("img", src=True):
            src = img["src"].strip()
            if src and not src.endswith(".gif") and "icon" not in src.lower():
                image_url = urljoin(url, src)
                break

    if not image_url:
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if src and not src.endswith(".gif") and "icon" not in src.lower() and len(src) > 10:
                image_url = urljoin(url, src)
                break

    if not title and not text:
        logger.warning(f"页面内容为空，跳过: {url}")
        return None

    return {
        "url": url,
        "title": title,
        "text": text,
        "image_url": image_url,
        "pub_date": None,   # HTML 爬虫暂不解析日期
        "source": "",       # 由调用方填充
        "language": "zh",
        "weight": 1.0,
    }


def crawl_site(site_config: dict, max_candidates: int = MAX_CANDIDATES_PER_SITE) -> list[dict]:
    """完整爬取单个 HTML 站点，返回文章列表"""
    session = requests.Session()
    articles = []
    links = collect_article_links(site_config, session, max_candidates)

    for i, url in enumerate(links, 1):
        logger.info(f"[{i}/{len(links)}] 抓取: {url}")
        content = extract_article_content(url, session)
        if content:
            content["source"] = site_config["name"]
            content["language"] = site_config.get("language", "zh")
            content["weight"] = site_config.get("weight", 1.0)
            articles.append(content)
        time.sleep(REQUEST_DELAY + random.uniform(0, 0.5))

    logger.info(f"{site_config['name']}: 成功抓取 {len(articles)} 篇")
    return articles


def crawl_all_html() -> list[dict]:
    """爬取所有 HTML 站点，返回合并后的文章列表"""
    all_articles = []
    for site in HTML_SOURCES:
        logger.info(f"{'='*40}")
        logger.info(f"开始爬取 HTML 站点: {site['name']}")
        articles = crawl_site(site)
        all_articles.extend(articles)
    logger.info(f"HTML 爬取完毕，共 {len(all_articles)} 篇")
    return all_articles


def enrich_rss_articles(articles: list[dict], max_workers: int = 8) -> None:
    """
    对 text 过短的文章（RSS 摘要 teaser）回抓原文页，一次请求同时补全：
    - 正文 text（替换过短的 RSS teaser）
    - image_url（填充 RSS feed 未提供图片的来源）
    原地修改 articles，无返回值。
    """
    to_enrich = [a for a in articles if len(a.get("text", "")) < CRAWL_ENRICH_MIN_TEXT]
    if not to_enrich:
        return
    logger.info(f"[Enrich] {len(to_enrich)} 篇文章文本不足 {CRAWL_ENRICH_MIN_TEXT} 字，并发补全原文...")

    def _fetch_and_update(article: dict) -> None:
        url = article.get("url", "")
        if not url:
            return
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # 补全缺失的 image_url（og:image 优先）
            if not article.get("image_url"):
                for sel in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
                    tag = soup.select_one(sel)
                    if tag:
                        src = (tag.get("content") or "").strip()
                        if src:
                            article["image_url"] = urljoin(url, src)
                            break

            # 提取正文（保留比已有内容更长的结果）
            new_text = ""
            for sel in ("article", '[class*="article"]', '[class*="content"]',
                        '[class*="post"]', '[class*="news"]', '[class*="body"]'):
                nodes = soup.select(sel)
                if nodes:
                    node = max(nodes, key=lambda n: len(n.get_text()))
                    t = re.sub(r"\s+", " ", node.get_text(separator=" ", strip=True)).strip()
                    if t:
                        new_text = t
                        break
            if not new_text:
                body = soup.find("body")
                if body:
                    new_text = re.sub(r"\s+", " ", body.get_text(separator=" ", strip=True)).strip()

            if len(new_text) > len(article.get("text", "")):
                article["text"] = new_text[:CRAWL_TEXT_LIMIT]
                logger.info(
                    f"  [Enrich] {article.get('source', ''):20s} "
                    f"{len(article['text'])}字 — {article.get('title', '')[:35]}"
                )
        except Exception as e:
            logger.debug(f"  [Enrich] 失败 {url[:60]}: {e}")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(_fetch_and_update, to_enrich))
    logger.info("[Enrich] 文本补全完毕")
