"""
reporter.py — 报告生成模块
图片策略：优先使用原文真实图片 URL → 加载失败则降级到分类专属 SVG。
"""

import base64
from html import escape
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    OUTPUT_DIR, CATEGORIES,
    IMAGE_FETCH_TIMEOUT, IMAGE_FETCH_RETRIES, IMAGE_FETCH_WORKERS,
)


REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
ARTICLE_IMAGE_CACHE: dict[str, str] = {}


# ─── 分类专属 SVG 缩略图 ───────────────────────────────────────────
_CATEGORY_SVG = {
    "工艺制程": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#1e40af"/><stop offset="100%" style="stop-color:#3b82f6"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><circle cx="55" cy="30" r="18" fill="none" stroke="rgba(255,255,255,0.25)" stroke-width="1.5"/><circle cx="55" cy="30" r="11" fill="none" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/><circle cx="55" cy="30" r="4" fill="rgba(255,255,255,0.7)"/><line x1="37" y1="30" x2="44" y2="30" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="66" y1="30" x2="73" y2="30" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="55" y1="12" x2="55" y2="19" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="55" y1="41" x2="55" y2="48" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><text x="55" y="63" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#24037;&#33402;&#21046;&#31243;</text></svg>',
    "封装工艺": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#5b21b6"/><stop offset="100%" style="stop-color:#8b5cf6"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><rect x="32" y="16" width="46" height="32" rx="3" fill="rgba(255,255,255,0.15)" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><rect x="39" y="22" width="32" height="20" rx="2" fill="rgba(255,255,255,0.25)"/><circle cx="37" cy="54" r="3" fill="rgba(255,255,255,0.6)"/><circle cx="47" cy="54" r="3" fill="rgba(255,255,255,0.6)"/><circle cx="57" cy="54" r="3" fill="rgba(255,255,255,0.6)"/><circle cx="67" cy="54" r="3" fill="rgba(255,255,255,0.6)"/><circle cx="77" cy="54" r="3" fill="rgba(255,255,255,0.6)"/><text x="55" y="66" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#23553;&#35013;&#24037;&#33402;</text></svg>',
    "芯片架构": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#0e7490"/><stop offset="100%" style="stop-color:#06b6d4"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><rect x="28" y="14" width="54" height="38" rx="3" fill="rgba(255,255,255,0.1)" stroke="rgba(255,255,255,0.45)" stroke-width="1.5"/><line x1="28" y1="24" x2="82" y2="24" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><line x1="28" y1="33" x2="82" y2="33" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><line x1="28" y1="42" x2="82" y2="42" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><line x1="46" y1="14" x2="46" y2="52" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><line x1="64" y1="14" x2="64" y2="52" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><rect x="31" y="17" width="12" height="7" rx="1" fill="rgba(255,255,255,0.4)"/><rect x="49" y="27" width="12" height="7" rx="1" fill="rgba(255,255,255,0.4)"/><rect x="67" y="17" width="12" height="7" rx="1" fill="rgba(255,255,255,0.4)"/><text x="55" y="65" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#33096;&#29255;&#26550;&#26500;</text></svg>',
    "EDA与AI设计": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#065f46"/><stop offset="100%" style="stop-color:#10b981"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><polyline points="20,42 30,28 42,36 54,18 66,30 78,16 90,26" fill="none" stroke="rgba(255,255,255,0.7)" stroke-width="2" stroke-linejoin="round"/><circle cx="30" cy="28" r="3" fill="rgba(255,255,255,0.85)"/><circle cx="54" cy="18" r="3" fill="rgba(255,255,255,0.85)"/><circle cx="78" cy="16" r="3" fill="rgba(255,255,255,0.85)"/><line x1="20" y1="48" x2="90" y2="48" stroke="rgba(255,255,255,0.2)" stroke-width="1"/><text x="55" y="63" text-anchor="middle" font-family="sans-serif" font-size="8" font-weight="bold" fill="rgba(255,255,255,0.9)">EDA&#19982;AI&#35774;&#35745;</text></svg>',
    "互连标准": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#92400e"/><stop offset="100%" style="stop-color:#f59e0b"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><circle cx="55" cy="30" r="8" fill="rgba(255,255,255,0.5)" stroke="rgba(255,255,255,0.8)" stroke-width="1.5"/><circle cx="25" cy="18" r="5" fill="rgba(255,255,255,0.35)" stroke="rgba(255,255,255,0.6)" stroke-width="1.2"/><circle cx="85" cy="18" r="5" fill="rgba(255,255,255,0.35)" stroke="rgba(255,255,255,0.6)" stroke-width="1.2"/><circle cx="25" cy="44" r="5" fill="rgba(255,255,255,0.35)" stroke="rgba(255,255,255,0.6)" stroke-width="1.2"/><circle cx="85" cy="44" r="5" fill="rgba(255,255,255,0.35)" stroke="rgba(255,255,255,0.6)" stroke-width="1.2"/><line x1="30" y1="20" x2="47" y2="27" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="80" y1="20" x2="63" y2="27" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="30" y1="42" x2="47" y2="34" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><line x1="80" y1="42" x2="63" y2="34" stroke="rgba(255,255,255,0.5)" stroke-width="1.5"/><text x="55" y="63" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#20114;&#36830;&#26631;&#20934;</text></svg>',
    "行业商业": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#991b1b"/><stop offset="100%" style="stop-color:#ef4444"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><rect x="22" y="38" width="10" height="12" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="36" y="30" width="10" height="20" rx="1" fill="rgba(255,255,255,0.6)"/><rect x="50" y="22" width="10" height="28" rx="1" fill="rgba(255,255,255,0.7)"/><rect x="64" y="16" width="10" height="34" rx="1" fill="rgba(255,255,255,0.85)"/><rect x="78" y="10" width="10" height="40" rx="1" fill="white"/><line x1="18" y1="50" x2="96" y2="50" stroke="rgba(255,255,255,0.4)" stroke-width="1"/><text x="55" y="64" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#34892;&#19994;&#21830;&#19994;</text></svg>',
    "会议与论坛": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#9d174d"/><stop offset="100%" style="stop-color:#ec4899"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><ellipse cx="55" cy="13" rx="28" ry="5" fill="rgba(255,255,255,0.15)" stroke="rgba(255,255,255,0.4)" stroke-width="1.2"/><rect x="27" y="13" width="56" height="22" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.3)" stroke-width="1.2"/><circle cx="40" cy="44" r="5" fill="rgba(255,255,255,0.5)"/><circle cx="55" cy="44" r="5" fill="rgba(255,255,255,0.5)"/><circle cx="70" cy="44" r="5" fill="rgba(255,255,255,0.5)"/><line x1="40" y1="49" x2="40" y2="56" stroke="rgba(255,255,255,0.4)" stroke-width="2"/><line x1="55" y1="49" x2="55" y2="56" stroke="rgba(255,255,255,0.4)" stroke-width="2"/><line x1="70" y1="49" x2="70" y2="56" stroke="rgba(255,255,255,0.4)" stroke-width="2"/><text x="55" y="68" text-anchor="middle" font-family="sans-serif" font-size="8" font-weight="bold" fill="rgba(255,255,255,0.9)">&#20250;&#35758;&#19982;&#35542;&#22363;</text></svg>',
    "其他": '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="72" viewBox="0 0 110 72"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#374151"/><stop offset="100%" style="stop-color:#6b7280"/></linearGradient></defs><rect width="110" height="72" fill="url(#g)" rx="6"/><rect x="34" y="16" width="42" height="34" rx="4" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.4)" stroke-width="1.5"/><line x1="34" y1="11" x2="34" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="44" y1="11" x2="44" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="54" y1="11" x2="54" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="64" y1="11" x2="64" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="74" y1="11" x2="74" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="34" y1="50" x2="34" y2="55" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="44" y1="50" x2="44" y2="55" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="54" y1="50" x2="54" y2="55" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="64" y1="50" x2="64" y2="55" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><line x1="74" y1="50" x2="74" y2="55" stroke="rgba(255,255,255,0.5)" stroke-width="2"/><text x="55" y="66" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="bold" fill="rgba(255,255,255,0.9)">&#20854;&#20182;</text></svg>',
}


def _get_category_svg_uri(category: str) -> str:
    """返回分类对应的 SVG data URI，无需任何网络请求。"""
    svg = _CATEGORY_SVG.get(category, _CATEGORY_SVG["其他"])
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _fetch_image_as_data_uri(url: str, timeout: int = IMAGE_FETCH_TIMEOUT, referer: str = "",
                             retries: int = IMAGE_FETCH_RETRIES) -> str:
    """尝试下载外部图片并返回 Base64 Data URI；失败返回空字符串。
    referer: 传入文章原始页面 URL，绕过防盗链检查。
    海外图床较慢，超时偏大并支持有限次重试以应对瞬时抖动。"""
    if not url or url.startswith("data:"):
        return url
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SemiBot/1.0)"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if not ct.startswith("image/"):
                return ""
            data = resp.content
            if not data or len(data) > 2 * 1024 * 1024:
                return ""
            return f"data:{ct};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            if attempt < retries:
                continue
            return ""
    return ""


def _has_data_payload(url: str) -> bool:
    """判断 data URI 是否真的带有图片载荷。"""
    return url.startswith("data:image/") and "," in url and bool(url.rsplit(",", 1)[1])


def _is_remote_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _looks_like_content_image(url: str) -> bool:
    lowered = url.lower()
    bad_tokens = (
        "logo", "icon", "avatar", "pixel", "quantcast", "tracking", "tracker",
        "matomo", "analytics", "beacon", "spacer", "blank", "spinner",
    )
    return not any(token in lowered for token in bad_tokens)


def _extract_article_image_url(article_url: str, timeout: int = IMAGE_FETCH_TIMEOUT) -> str:
    """从原文页提取 og/twitter/正文首图 URL。"""
    if not article_url:
        return ""
    if article_url in ARTICLE_IMAGE_CACHE:
        return ARTICLE_IMAGE_CACHE[article_url]

    image_url = ""
    try:
        response = requests.get(article_url, headers=REQUEST_HEADERS, timeout=timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        meta_selectors = [
            'meta[property="og:image"]',
            'meta[property="og:image:secure_url"]',
            'meta[name="twitter:image"]',
            'meta[name="twitter:image:src"]',
            'meta[itemprop="image"]',
            'link[rel="image_src"]',
        ]
        for selector in meta_selectors:
            tag = soup.select_one(selector)
            src = (tag.get("content") or tag.get("href") or "").strip() if tag else ""
            if src and _looks_like_content_image(src):
                image_url = urljoin(article_url, src)
                break

        if not image_url:
            for selector in ("article img[src]", "figure img[src]", ".article img[src]", ".content img[src]", "img[src]"):
                for img in soup.select(selector):
                    src = (img.get("data-src") or img.get("data-original") or img.get("src") or "").strip()
                    if src and _looks_like_content_image(src):
                        image_url = urljoin(article_url, src)
                        break
                if image_url:
                    break
    except Exception:
        image_url = ""

    ARTICLE_IMAGE_CACHE[article_url] = image_url
    return image_url


def _resolve_image_src(article: dict) -> str:
    """返回报告图片地址。
    优先级：已嵌入图 → RSS/正文已知远程图 → 回原文页抓 og:image → 分类 SVG。
    远程图一律带文章 URL 作 Referer 下载嵌入（绕防盗链）。
    """
    current_url = article.get("image_url", "")
    original_url = article.get("original_image_url", "")
    article_url = article.get("url", "")
    category = article.get("category") or ("行业商业" if article.get("tags") else "其他")

    # 已是嵌入 data URI（非 SVG），直接复用
    if current_url.startswith("data:") and _has_data_payload(current_url) and not current_url.startswith("data:image/svg+xml"):
        return current_url

    # 1) RSS/正文已知远程图：带文章 Referer 下载嵌入，避免防盗链
    for candidate in (original_url, current_url):
        if _is_remote_url(candidate) and _looks_like_content_image(candidate):
            embedded = _fetch_image_as_data_uri(candidate, referer=article_url)
            if embedded:
                return embedded

    # 2) RSS 无图：回原文页抓 og:image / 正文首图
    #    仅当 original_url / current_url 均为空时才额外请求页面，避免重复抓取。
    #    （EDN、Semiconductor Engineering 等英文源 feed 不带图，图只在页面里；
    #      若 enrich 步骤已补图，此处会因 original_url 非空而跳过）
    if not original_url and not current_url:
        page_image = _extract_article_image_url(article_url)
        if _is_remote_url(page_image) and _looks_like_content_image(page_image):
            embedded = _fetch_image_as_data_uri(page_image, referer=article_url)
            if embedded:
                return embedded

    # 3) 兜底：分类 SVG（浏览器端 onerror 也会再兜一层）
    return _get_category_svg_uri(category)


# ─── HTML 模板 ──────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report_title} — {date}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{--bg:#f0f2f7;--card:#fff;--primary:#0f2044;--accent:#1a6cf5;--accent2:#e05c1f;--text:#1e2230;--muted:#6b7280;--border:#e5e7eb}}
  body{{font-family:"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}
  header{{background:linear-gradient(135deg,#0f2044,#1a3a6e 60%,#1a5ca8);color:#fff;padding:36px 48px 28px}}
  .header-top{{display:flex;align-items:center;gap:16px;margin-bottom:8px}}
  .header-icon{{width:42px;height:42px;background:rgba(255,255,255,.15);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px}}
  header h1{{font-size:24px;font-weight:700;letter-spacing:.02em}}
  header .subtitle{{font-size:13px;color:rgba(255,255,255,.65);margin-top:2px}}
  .stats-bar{{display:flex;gap:32px;margin-top:20px;flex-wrap:wrap}}
  .stat-item{{display:flex;flex-direction:column}}
  .stat-value{{font-size:22px;font-weight:700;color:#fff}}
  .stat-label{{font-size:11px;color:rgba(255,255,255,.55);margin-top:2px}}
  .exec-summary{{max-width:900px;margin:28px auto 0;background:#fff;border-radius:12px;padding:22px 28px;box-shadow:0 2px 10px rgba(0,0,0,.07);border-left:4px solid var(--accent)}}
  .exec-summary h2{{font-size:14px;font-weight:600;color:var(--accent);margin-bottom:10px;text-transform:uppercase;letter-spacing:.06em}}
  .exec-summary p{{font-size:14px;color:#374151;line-height:1.8}}
  .container{{max-width:900px;margin:0 auto;padding:28px 20px 48px}}
  .category-section{{margin-bottom:36px}}
  .category-header{{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid var(--border)}}
  .category-dot{{width:10px;height:10px;border-radius:50%}}
  .category-title{{font-size:16px;font-weight:600;color:var(--primary)}}
  .category-count{{font-size:12px;color:var(--muted);background:var(--bg);padding:1px 8px;border-radius:10px}}
  .card{{background:var(--card);border-radius:10px;margin-bottom:16px;display:flex;gap:18px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.05);transition:box-shadow .18s,transform .18s;border:1px solid var(--border)}}
  .card:hover{{box-shadow:0 6px 20px rgba(0,0,0,.1);transform:translateY(-1px)}}
  .rank{{min-width:32px;height:32px;border-radius:8px;background:var(--primary);color:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}}
  .rank.top3{{background:var(--accent2)}}
  .thumb{{width:176px;height:126px;border-radius:6px;object-fit:cover;flex-shrink:0;background:#e5e7eb}}
  .body{{flex:1;min-width:0}}
  .body h3{{font-size:15px;font-weight:600;margin-bottom:6px;line-height:1.4}}
  .body h3 a{{color:var(--text);text-decoration:none}}
  .body h3 a:hover{{color:var(--accent)}}
  .body p{{font-size:13px;line-height:1.7;color:#4b5563}}
  .meta{{margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
  .badge{{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:500;white-space:nowrap}}
  .badge-score{{background:#eff6ff;color:#1d4ed8}}
  .badge-source{{background:#f0fdf4;color:#15803d}}
  .badge-tag{{background:#fff7ed;color:#c2410c}}
  .badge-lang-zh{{background:#fef9c3;color:#854d0e}}
  .badge-lang-en{{background:#fce7f3;color:#9d174d}}
  .badge-date{{background:var(--bg);color:var(--muted)}}
  .keywords{{font-size:11px;color:var(--muted)}}
  .keywords a{{color:var(--muted);text-decoration:none;border-bottom:1px dotted #cbd5e1;margin-right:4px}}
  .keywords a:hover{{color:var(--accent);border-bottom-color:var(--accent)}}
  .question-section{{background:var(--card);border-radius:12px;padding:22px 28px;margin:8px 0 36px;box-shadow:0 1px 4px rgba(0,0,0,.05);border:1px solid var(--border)}}
  .question-section h2{{font-size:15px;font-weight:600;color:var(--primary);margin-bottom:10px}}
  .question-list{{list-style:none;display:flex;flex-direction:column;gap:9px}}
  .question-list a{{font-size:13px;color:var(--text);text-decoration:none;line-height:1.6}}
  .question-list a:hover{{color:var(--accent);text-decoration:underline}}
  footer{{text-align:center;padding:24px;font-size:12px;color:var(--muted);border-top:1px solid var(--border)}}
  .dot-工艺制程{{background:#3b82f6}}.dot-封装工艺{{background:#8b5cf6}}.dot-芯片架构{{background:#06b6d4}}
  .dot-EDA与AI设计{{background:#10b981}}.dot-互连标准{{background:#f59e0b}}.dot-行业商业{{background:#ef4444}}
  .dot-会议与论坛{{background:#ec4899}}.dot-其他{{background:#9ca3af}}
  .dot-制造-工艺{{background:#3b82f6}}.dot-EDA工具{{background:#10b981}}.dot-标准-会议{{background:#f59e0b}}
  .dot-其他技术{{background:#9ca3af}}
  .digest-section{{background:var(--card);border-radius:12px;padding:24px 28px;margin-bottom:36px;box-shadow:0 2px 10px rgba(0,0,0,.07);border-left:4px solid #16a34a}}
  .digest-section h2{{font-size:15px;font-weight:600;color:#15803d;margin-bottom:12px;letter-spacing:.04em}}
  .digest-section p{{font-size:14px;color:#374151;line-height:1.9;margin-bottom:10px}}
  .digest-section p:last-child{{margin-bottom:0}}
  .appendix-section{{background:var(--card);border-radius:12px;padding:24px 28px;margin-bottom:36px;box-shadow:0 1px 4px rgba(0,0,0,.05);border:1px solid var(--border)}}
  .appendix-section h2{{font-size:15px;font-weight:600;color:var(--primary);margin-bottom:6px}}
  .appendix-note{{font-size:12px;color:var(--muted);margin-bottom:14px}}
  .appendix-list{{list-style:none}}
  .appendix-list li{{padding:7px 0;border-bottom:1px dashed var(--border);font-size:13px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}}
  .appendix-list li:last-child{{border-bottom:none}}
  .appendix-list a{{color:var(--text);text-decoration:none;flex:1;min-width:240px}}
  .appendix-list a:hover{{color:var(--accent);text-decoration:underline}}
  .appendix-meta{{font-size:11px;color:var(--muted);white-space:nowrap}}
  @media (max-width:640px){{.card{{gap:12px;padding:14px}}.thumb{{width:120px;height:96px}}header{{padding:28px 22px 24px}}}}
</style>
</head>
<body>
<header>
  <div class="header-top">
    <div class="header-icon">🔬</div>
    <div><h1>{report_title}</h1><div class="subtitle">{report_subtitle}</div></div>
  </div>
  <div class="stats-bar">
    <div class="stat-item"><span class="stat-value">{date}</span><span class="stat-label">生成日期</span></div>
    <div class="stat-item"><span class="stat-value">{total}</span><span class="stat-label">{total_label}</span></div>
    <div class="stat-item"><span class="stat-value">{source_count}</span><span class="stat-label">信息源</span></div>
    <div class="stat-item"><span class="stat-value">{candidates}</span><span class="stat-label">候选文章</span></div>
    <div class="stat-item"><span class="stat-value">{category_count}</span><span class="stat-label">{coverage_label}</span></div>
  </div>
</header>
<div class="container">{exec_summary_html}{category_sections}{questions_html}{digest_html}{appendix_html}</div>
<footer>数据来源：{sites} &nbsp;·&nbsp; 摘要由 {ai_executor} 自动生成，仅供参考 &nbsp;·&nbsp; Token：{token_stats} &nbsp;·&nbsp; {datetime}</footer>
</body></html>"""

EXEC_SUMMARY_HTML = """<div class="exec-summary"><h2>{summary_title}</h2><p>{text}</p></div>"""

WEEKLY_DIGEST_HTML = """<div class="digest-section"><h2>📋 本周讯息总结</h2>{paragraphs}</div>"""

APPENDIX_HTML = """<div class="appendix-section">
  <h2>📎 附录 · 其他讯息（{count} 条）</h2>
  <p class="appendix-note">以下条目未入选上方精读，仅列标题；感兴趣可点击阅读原文。</p>
  <ul class="appendix-list">{items}</ul>
</div>"""

APPENDIX_ITEM_HTML = """<li><a href="{url}" target="_blank" rel="noopener">{title}</a><span class="appendix-meta">{meta}</span></li>"""

QUESTIONS_HTML = """<div class="question-section">
  <h2>🔎 延伸追问</h2>
  <ul class="question-list">{items}</ul>
</div>"""

QUESTION_ITEM_HTML = """<li><a href="{url}" target="_blank" rel="noopener">{question}</a></li>"""

CATEGORY_SECTION_TEMPLATE = """<div class="category-section">
  <div class="category-header">
    <div class="category-dot dot-{cat_key}"></div>
    <span class="category-title">{category}</span>
    <span class="category-count">{count} 篇</span>
  </div>
  {cards}
</div>"""

CARD_TEMPLATE = """<div class="card">
  <div class="rank {rank_class}">{rank}</div>
  {thumb_html}
  <div class="body">
    <h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
    <p>{summary}</p>
    <div class="meta">
      <span class="badge badge-score">相关度 {score}/10</span>
      <span class="badge badge-source">{source}</span>
      {tags_html}
      <span class="badge badge-lang-{lang_key}">{lang_label}</span>
      {date_badge}
      <span class="keywords">{keywords}</span>
    </div>
  </div>
</div>"""


# ─── 构建函数 ────────────────────────────────────────────────────────

def _google_search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query.strip())


def _split_terms(value: str) -> list[str]:
    if not value:
        return []
    normalized = str(value).replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
    return [term.strip() for term in normalized.split(",") if term.strip()]


def _format_token_count(value) -> str:
    if not isinstance(value, (int, float)):
        return "未知"
    return f"{value / 1000:.1f}k"


def _build_token_stats(metadata: dict) -> str:
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    total_tokens = metadata.get("total_tokens")
    if any(isinstance(v, (int, float)) for v in (input_tokens, output_tokens, total_tokens)):
        return (
            f"输入 {_format_token_count(input_tokens)} / "
            f"输出 {_format_token_count(output_tokens)} / "
            f"合计 {_format_token_count(total_tokens)}"
        )
    return "未捕获"


def _build_keyword_links(keywords: str) -> str:
    terms = _split_terms(keywords)
    if not terms:
        return ""
    links = [
        f'<a href="{escape(_google_search_url(term), quote=True)}" target="_blank" rel="noopener">{escape(term)}</a>'
        for term in terms
    ]
    return "关键词：" + " ".join(links)


def _build_questions_html(questions: list[str]) -> str:
    if not questions:
        return ""
    items = []
    for question in questions[:5]:
        question = str(question).strip()
        if not question:
            continue
        items.append(QUESTION_ITEM_HTML.format(
            url=escape(_google_search_url(question), quote=True),
            question=escape(question),
        ))
    if not items:
        return ""
    return QUESTIONS_HTML.format(items="\n".join(items))


def _build_card(article: dict, show_tags: bool = True) -> str:
    rank = article["rank"]
    rank_class = "top3" if rank <= 3 else ""

    # 图片在 save_report 阶段已解析为真实远程图或兜底 SVG；这里保留 onerror 兜底。
    category = article.get("category") or ("行业商业" if article.get("tags") else "其他")
    img_src = article.get("image_url") or _get_category_svg_uri(category)
    svg_uri = _get_category_svg_uri(category)
    thumb_html = (
        f'<img class="thumb" src="{escape(img_src, quote=True)}" alt="" loading="eager" '
        f'decoding="async" referrerpolicy="no-referrer" '
        f'onerror="this.onerror=null;this.src=\'{escape(svg_uri, quote=True)}\'">'
    )

    lang = article.get("language", "zh")
    lang_label = "中文" if lang == "zh" else "EN"
    lang_key = lang if lang in ("zh", "en") else "zh"

    date_badge = ""
    if article.get("pub_date"):
        date_badge = f'<span class="badge badge-date">{article["pub_date"]}</span>'

    tags_html = ""
    if show_tags and article.get("tags"):
        tags_html = "".join(
            f'<span class="badge badge-tag">{escape(tag.strip())}</span>'
            for tag in str(article["tags"]).replace(",", "，").split("，")
            if tag.strip()
        )

    return CARD_TEMPLATE.format(
        rank=rank,
        rank_class=rank_class,
        thumb_html=thumb_html,
        url=escape(article["url"], quote=True),
        title=escape(article["title"]),
        summary=escape(article.get("summary") or "（摘要生成失败）"),
        score=article["score"],
        source=escape(article.get("source", "未知来源")),
        tags_html=tags_html,
        lang_key=lang_key,
        lang_label=lang_label,
        date_badge=date_badge,
        keywords=_build_keyword_links(article.get("keywords", "")),
    )


def _category_class(category: str) -> str:
    return category.replace("&", "-")


def _build_category_sections(results: list) -> tuple:
    grouped = defaultdict(list)
    for article in results:
        grouped[article.get("category", "其他")].append(article)

    sections_html = []
    for cat in CATEGORIES:
        arts = grouped.get(cat, [])
        if not arts:
            continue
        cards_html = "\n".join(_build_card(a) for a in arts)
        sections_html.append(CATEGORY_SECTION_TEMPLATE.format(
            cat_key=_category_class(cat), category=cat, count=len(arts), cards=cards_html,
        ))
    return "\n".join(sections_html), len(grouped)


def _build_flat_business_cards(results: list) -> str:
    """商业版：不展示标签分类，按排序结果直接平铺全部条目。"""
    return "\n".join(_build_card(article, show_tags=False) for article in results)


def _build_digest_html(weekly_digest: str) -> str:
    """渲染「本周讯息总结」板块，LLM 输出按空行分段。"""
    if not weekly_digest:
        return ""
    paragraphs = "".join(
        f"<p>{escape(p.strip())}</p>"
        for p in weekly_digest.split("\n") if p.strip()
    )
    return WEEKLY_DIGEST_HTML.format(paragraphs=paragraphs)


def _build_appendix_html(appendix: list) -> str:
    """渲染附录：未入选条目的标题超链接列表，不含图片和摘要。"""
    if not appendix:
        return ""
    items = []
    for entry in appendix:
        meta_parts = [p for p in (entry.get("source", ""), entry.get("pub_date", "")) if p]
        items.append(APPENDIX_ITEM_HTML.format(
            url=escape(entry["url"], quote=True),
            title=escape(entry["title"]),
            meta=escape(" · ".join(meta_parts)),
        ))
    return APPENDIX_HTML.format(count=len(appendix), items="\n".join(items))


def save_report(
    results: list,
    executive_summary: str,
    site_names: list,
    candidates_count: int = 0,
    report_type: str = "tech",
    report_slug: str = "tech",
    report_title: str = "半导体行业技术周报",
    weekly_digest: str = "",
    appendix: list | None = None,
    research_questions: list | None = None,
    metadata: dict | None = None,
    file_prefix_word: str = "weekly",
) -> str:
    """保存 HTML 报告和 JSON，返回 HTML 路径。

    report_type="tech"：按技术门类分组，可带 weekly_digest（本周讯息总结）和
    appendix（未入选条目超链接列表）。
    report_type="business"：不限条目，按排序结果直接平铺。
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 图片策略：优先 RSS/正文图，缺图回原文页抓 og:image，再失败降级分类 SVG。
    # 逐篇均涉及网络下载，用线程池并发，避免串行等待拖慢出报告。
    for article in results:
        article["original_image_url"] = article.get("original_image_url") or article.get("image_url", "")
    if results:
        with ThreadPoolExecutor(max_workers=min(IMAGE_FETCH_WORKERS, len(results))) as pool:
            resolved = list(pool.map(_resolve_image_src, results))
        for article, src in zip(results, resolved):
            article["image_url"] = src

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    datetime_str = now.strftime("%Y-%m-%d %H:%M")
    file_prefix = f"{OUTPUT_DIR}/{file_prefix_word}_{date_str}_{report_slug}"

    if report_type == "business":
        category_sections_html = _build_flat_business_cards(results)
        category_count = "平铺"
        report_subtitle = "AI 自动采集 · 商业动态全收录 · 按相关度排序"
        summary_title = "📊 商业动态概览"
        coverage_label = "展示方式"
        total_label = "收录条目"
    else:
        category_sections_html, category_count = _build_category_sections(results)
        report_subtitle = "AI 自动采集 · 技术门类聚合 · 精读筛选"
        summary_title = "📊 技术趋势概览"
        coverage_label = "覆盖类别"
        total_label = "精读文章"

    exec_html = EXEC_SUMMARY_HTML.format(
        summary_title=summary_title,
        text=executive_summary,
    ) if executive_summary else ""

    html = HTML_TEMPLATE.format(
        report_title=report_title, report_subtitle=report_subtitle,
        date=date_str, datetime=datetime_str,
        sites="、".join(site_names),
        ai_executor=(metadata or {}).get("ai_executor", "AI"),
        token_stats=_build_token_stats(metadata or {}),
        total=len(results), total_label=total_label,
        source_count=len(site_names),
        candidates=candidates_count, category_count=category_count,
        coverage_label=coverage_label,
        exec_summary_html=exec_html, category_sections=category_sections_html,
        questions_html=_build_questions_html(research_questions or []),
        digest_html=_build_digest_html(weekly_digest),
        appendix_html=_build_appendix_html(appendix or []),
    )

    html_path = f"{file_prefix}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    json_path = f"{file_prefix}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime_str, "report_type": report_type,
            "sources": site_names,
            "candidates_count": candidates_count,
            "executive_summary": executive_summary,
            "weekly_digest": weekly_digest,
            "research_questions": research_questions or [],
            "metadata": metadata or {},
            "articles": results,
            "appendix": appendix or [],
        }, f, ensure_ascii=False, indent=2)

    return html_path
