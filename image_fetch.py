"""
image_fetch.py — 图片抓取统一模块

背景：找图/校验图逻辑原先分散在 crawler.py（HTML 站点爬虫 + RSS 文章补全）、
reporter.py（出报告时的最终兜底）、webgui/app.py（看板懒加载代理）、
rss_crawler.py（RSS 条目内嵌图片过滤）四处，互相独立演化导致选择器范围、
重试次数、Content-Type 校验松紧都不一致——同一类问题（如某 CDN 把图片
Content-Type 误标）只在其中一处修复，其他几处仍会复现。

本模块是「找图 + 验图」的唯一实现，调用方只负责把结果嵌进报告/响应里。
新发现某站点的抓取怪癖时，优先看能否归纳进通用规则（如字节签名兜底）；
确实是该站独有时，去 config.SITE_IMAGE_RULES 登记一条规则，不要在调用方各自加 hack。
"""

import base64
import logging
import os
import re
from functools import lru_cache
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    REQUEST_HEADERS, IMAGE_FETCH_TIMEOUT, IMAGE_FETCH_RETRIES, SITE_IMAGE_RULES,
)

logger = logging.getLogger(__name__)

# 原文页 og:image 抓取结果缓存：同一篇文章在一次出报告流程里可能被多处引用，避免重复请求。
ARTICLE_IMAGE_CACHE: dict[str, str] = {}

_BAD_IMAGE_TOKENS = (
    "logo", "icon", "avatar", "pixel", "tracking", "tracker",
    "matomo", "analytics", "beacon", "spacer", "blank", "spinner", "quantcast",
)
# 必须按单词边界匹配，不能用裸子串：纯子串会把域名/路径里恰好包含 token 的正常图片误杀——
# 实测案例：news.samsungsemiconductor.com 因含 "semic-ICON-ductor" 被误判成图标类 URL 整条排除，
# 三星的图片连下载尝试都没发生，比 NVIDIA 的 Content-Type 误标更彻底地丢图。
_BAD_IMAGE_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in _BAD_IMAGE_TOKENS) + r")(?![a-z0-9])"
)

_META_IMAGE_SELECTORS = [
    'meta[property="og:image"]',
    'meta[property="og:image:secure_url"]',
    'meta[name="twitter:image"]',
    'meta[name="twitter:image:src"]',
    'meta[itemprop="image"]',
    'link[rel="image_src"]',
]

_CONTENT_IMG_SELECTORS = [
    "article img[src]", "figure img[src]", ".article img[src]",
    ".content img[src]", "img[src]",
]

_MAX_EMBED_BYTES = 2 * 1024 * 1024


def looks_like_content_image(url: str) -> bool:
    """排除 logo / 埋点像素等明显不是正文配图的 URL（按单词边界匹配，见 _BAD_IMAGE_TOKEN_RE 注释）。"""
    if not url:
        return False
    return not _BAD_IMAGE_TOKEN_RE.search(url.lower())


def is_remote_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def has_data_payload(url: str) -> bool:
    """判断 data URI 是否真的带有图片载荷（而非空壳）。"""
    return url.startswith("data:image/") and "," in url and bool(url.rsplit(",", 1)[1])


def sniff_image_mime(data: bytes) -> str | None:
    """按文件头字节签名识别图片格式；用于 CDN 把 Content-Type 误标成通用类型时的兜底校验。
    实测案例：NVIDIA 新闻室（nvidianews.nvidia.com）配图存于 iprsoftwaremedia.com，
    Header 给 Content-Type: binary/octet-stream，但字节确是合法 JPEG（FF D8 FF 开头）——
    若只信 Header 会把这类有效图片错判成下载失败，进而在浏览器端也大概率因同样原因加载失败。
    """
    if not data:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


def validate_image_bytes(data: bytes, declared_content_type: str = "") -> str | None:
    """判断字节内容是否为可用图片，返回应使用的 mime type；不是图片返回 None。
    优先信声明的 Content-Type；非 image/* 时按字节签名兜底识别（见 sniff_image_mime）。
    """
    ct = (declared_content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct
    return sniff_image_mime(data)


def site_image_rule(source_name: str) -> dict:
    """读取某信息源在 config.SITE_IMAGE_RULES 里登记的找图规则；未登记返回空 dict。
    crawler.py（首次爬取 / RSS 补全，soup 已在手）与本模块的 fetch_page_image_url
    （reporter 出报告时的最后兜底，需自行发请求）共用同一份规则表。
    """
    return SITE_IMAGE_RULES.get(source_name, {})


_FALLBACK_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


@lru_cache(maxsize=None)
def source_fallback_image_uri(source_name: str) -> str:
    """返回信息源的固定兜底图（品牌色背景 + 公有领域 logo 的本地位图）的 Base64 data URI；
    未在 config.SITE_IMAGE_RULES 配 fallback_image、或文件缺失/格式不支持时返回空字符串，
    由调用方继续退回分类 SVG。

    为什么必须是位图而非 SVG：微信图文导出（wechat_render）会跳过 data:image/svg，
    SVG 兜底在公众号里根本不显示；PNG 才能在 微信图文/邮件/浏览器/离线 四个场景一致呈现。
    与分类 SVG 的分工——本函数专给「公司新闻室」类源提供可一眼辨识的品牌兜底图，
    媒体/中文聚合源仍走分类 SVG。
    """
    rel = SITE_IMAGE_RULES.get(source_name, {}).get("fallback_image")
    if not rel:
        return ""
    path = rel if os.path.isabs(rel) else os.path.join(os.path.dirname(__file__), rel)
    mime = _FALLBACK_IMAGE_MIME.get(os.path.splitext(path)[1].lower())
    if not mime:
        logger.warning(f"[图片] 兜底图格式不支持 source={source_name!r} path={rel}")
        return ""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:  # noqa: BLE001 — 兜底图缺失不应中断出报告
        logger.warning(f"[图片] 兜底图读取失败 source={source_name!r} path={rel} err={e}")
        return ""
    if not data:
        return ""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def extract_image_from_soup(soup: BeautifulSoup, base_url: str, extra_selectors: list[str] | None = None) -> str:
    """从已抓取的页面 soup 中找配图：站点专属选择器 → 通用 meta 标签 → 正文首图。
    crawler.py（HTML 站点爬取 / RSS 文章补全）与本模块的页面级抓取共用此函数，
    确保「同一个站点该怎么找图」只有一份判定逻辑。
    """
    for selector in (extra_selectors or []) + _META_IMAGE_SELECTORS:
        tag = soup.select_one(selector)
        if not tag:
            continue
        src = (tag.get("content") or tag.get("href") or "").strip()
        if src and looks_like_content_image(src):
            return urljoin(base_url, src)

    for selector in _CONTENT_IMG_SELECTORS:
        for img in soup.select(selector):
            src = (img.get("data-src") or img.get("data-original") or img.get("src") or "").strip()
            if src and not src.lower().endswith(".gif") and looks_like_content_image(src):
                return urljoin(base_url, src)

    return ""


def fetch_page_image_url(
    article_url: str, source_name: str = "",
    timeout: int = IMAGE_FETCH_TIMEOUT, retries: int = IMAGE_FETCH_RETRIES,
) -> str:
    """回原文页抓配图 URL（og:image / twitter:image / 正文首图）。
    带重试：海外站点经代理访问时偶发 TLS 握手被重置，单次失败不代表该站真的没有图
   （实测 Samsung Semiconductor 新闻室即如此：og:image 标签存在，仅连接偶发被重置）。
    """
    if not article_url:
        return ""
    if article_url in ARTICLE_IMAGE_CACHE:
        return ARTICLE_IMAGE_CACHE[article_url]

    rule = site_image_rule(source_name)
    if rule.get("skip_page_scrape"):
        ARTICLE_IMAGE_CACHE[article_url] = ""
        return ""
    extra_selectors = rule.get("extra_selectors")

    image_url = ""
    last_err = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(article_url, headers=REQUEST_HEADERS, timeout=timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            image_url = extract_image_from_soup(soup, article_url, extra_selectors)
            last_err = None
            break
        except Exception as e:  # noqa: BLE001 — 抓取失败不应拖垮出报告主流程
            last_err = e
    if last_err:
        logger.warning(f"[图片] 原文页抓取失败 source={source_name!r} url={article_url[:80]} err={last_err}")

    ARTICLE_IMAGE_CACHE[article_url] = image_url
    return image_url


def fetch_image_bytes(
    url: str, referer: str = "", source_name: str = "",
    timeout: int = IMAGE_FETCH_TIMEOUT, retries: int = IMAGE_FETCH_RETRIES,
) -> tuple[bytes, str] | tuple[None, None]:
    """下载远程图片，返回 (原始字节, mime type)；失败返回 (None, None)。
    referer: 传入文章原始页面 URL，绕过防盗链检查。
    被 fetch_image_as_data_uri（出报告内嵌）与 webgui 看板懒加载代理共用，
    确保两处对同一张图的下载/校验/重试策略完全一致。
    """
    if not url or url.startswith("data:"):
        return None, None
    headers = {**REQUEST_HEADERS}
    if referer:
        headers["Referer"] = referer

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct.startswith("text/"):
                return None, None  # 明确是网页（如 404/付墙页），不必下载正文再判断
            data = resp.content
            if not data or len(data) > _MAX_EMBED_BYTES:
                return None, None
            mime = validate_image_bytes(data, ct)
            if not mime:
                return None, None
            return data, mime
        except Exception as e:  # noqa: BLE001
            last_err = e
    if last_err:
        logger.warning(f"[图片] 下载失败 source={source_name!r} url={url[:80]} err={last_err}")
    return None, None


def fetch_image_as_data_uri(
    url: str, referer: str = "", source_name: str = "",
    timeout: int = IMAGE_FETCH_TIMEOUT, retries: int = IMAGE_FETCH_RETRIES,
) -> str:
    """下载远程图片并返回 Base64 Data URI；失败返回空字符串。"""
    if not url or url.startswith("data:"):
        return url
    data, mime = fetch_image_bytes(url, referer=referer, source_name=source_name, timeout=timeout, retries=retries)
    if not data:
        return ""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def resolve_article_image(article: dict) -> str:
    """返回报告可用的图片地址（内嵌 data URI 或远程 URL）；找不到返回空字符串，调用方兜底分类 SVG。

    优先级：
    1) 已是内嵌 data URI（非 SVG）：直接复用；
    2) RSS/正文已知远程图 或 回原文页抓到的 og:image：尝试下载内嵌（带 Referer 绕防盗链）；
    3) 内嵌失败但候选 URL 看起来有效：保留 URL 交给浏览器直接加载
      （报告 <img onerror> 已兜底分类 SVG，不会白屏；此前仅 RSS 已知图享有此兜底，
        回原文页抓到的图找到了却下载失败时直接退到 SVG，本函数统一了这一行为）。
    """
    current_url = article.get("image_url", "") or ""
    original_url = article.get("original_image_url", "") or ""
    article_url = article.get("url", "")
    source_name = article.get("source", "")

    if current_url.startswith("data:") and has_data_payload(current_url) and not current_url.startswith("data:image/svg+xml"):
        return current_url

    candidates = []
    for c in (original_url, current_url):
        if is_remote_url(c) and looks_like_content_image(c) and c not in candidates:
            candidates.append(c)

    if not candidates:
        page_image = fetch_page_image_url(article_url, source_name=source_name)
        if is_remote_url(page_image) and looks_like_content_image(page_image):
            candidates.append(page_image)

    for candidate in candidates:
        embedded = fetch_image_as_data_uri(candidate, referer=article_url, source_name=source_name)
        if embedded:
            return embedded

    if candidates:
        logger.info(f"[图片] 内嵌失败，回退浏览器直载 source={source_name!r} url={candidates[0][:80]}")
        return candidates[0]

    return ""
