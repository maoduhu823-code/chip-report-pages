"""tools/build_source_logos.py — 构建期工具：为「公司官方新闻室」信息源生成兜底图。

每张兜底图 = 白底 + 品牌色边框 + 该公司 logo（居中）+ 底部品牌色条（公司名）。
当某篇文章在通用找图链路里拿不到真实配图时，reporter 用这张图代替统一的分类 SVG
（见 config.SITE_IMAGE_RULES 的 fallback_image / image_fetch.source_fallback_image_uri）。

为什么是构建期脚本、产物提交进仓库：
- 运行期（出报告）只读取 assets/source_logos/*.png，不联网、不依赖 Pillow；
- 图「固定下来」，每次结果一致，便于版权审计。

版权策略（满足「不涉及版权」）：
- logo 取自 Wikimedia Commons，经其 API 读取每个文件的许可；
- 仅采用 Public Domain / CC0 的文件（很多科技公司纯文字 wordmark 属 PD-textlogo）；
- 需署名的（CC-BY / CC-BY-SA 等）或无可用 PD 文件的，一律退回「自制 wordmark」
  （白底 + 品牌色 + 公司名文字，纯排版不构成版权）；
- 每家的来源 URL 与许可写入 assets/source_logos/CREDITS.md。

用法：
    pip install pillow requests   # 仅构建期需要，不进 requirements.txt
    python tools/build_source_logos.py
没命中 PD 文件的公司会打印出来，按需在 candidates 里补 Commons 文件名后重跑。
"""

import io
import os
import time

import requests
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "source_logos"))
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia 要求带描述性 User-Agent，否则可能 403。
UA = "infocollect-logo-fetch/1.0 (chip-news fallback images; non-commercial)"

CANVAS = (640, 360)        # 16:9，与卡片缩略图比例一致（移动端 .thumb aspect-ratio:16/9）
THUMB_W = 600              # 向 Commons 请求的栅格化宽度（SVG → PNG）
BORDER = 12                # 品牌色边框宽
FOOT_H = 58                # 底部品牌色条高
PAD = 30                   # logo 区内边距

# 仅这些许可视为「无署名义务、可直接内嵌」。其余退回自制 wordmark。
ACCEPT_TOKENS = ("public domain", "cc0", "no restrictions")

# 每家：slug=输出文件名 / name=底部条与 wordmark 文案 / color=品牌主色 /
#       candidates=Commons 文件名候选（按优先级，命中第一个 PD/CC0 的即用）。
COMPANIES = [
    {"slug": "nvidia",   "name": "NVIDIA",   "color": "#76B900",
     "candidates": ["Nvidia logo.svg", "NVIDIA logo.svg", "Nvidia (logo).svg"]},
    {"slug": "intel",    "name": "Intel",    "color": "#0071C5",
     "candidates": ["Intel logo (2020, light blue).svg", "Intel logo (2020, dark blue).svg",
                    "Intel-logo.svg", "Intel logo.svg", "Intel logo (2006-2020).svg"]},
    {"slug": "amd",      "name": "AMD",      "color": "#ED1C24",
     "candidates": ["AMD Logo.svg", "AMD logo.svg", "AMD-Logo.svg"]},
    {"slug": "micron",   "name": "Micron",   "color": "#0046AD",
     "candidates": ["Micron Technology logo.svg", "Micron logo.svg", "Micron Technology.svg"]},
    {"slug": "samsung",  "name": "Samsung",  "color": "#1428A0",
     "candidates": ["Samsung Logo.svg", "Samsung logo.svg", "Samsung wordmark.svg"]},
    {"slug": "skhynix",  "name": "SK hynix", "color": "#E5231B",
     "candidates": ["SK hynix.svg", "SK Hynix logo.svg", "SK hynix logo.svg", "SK Hynix.svg"]},
    {"slug": "synopsys", "name": "Synopsys", "color": "#4F2D7F",
     "candidates": ["Synopsys logo.svg", "Synopsys Logo.svg", "Synopsys.svg"]},
    {"slug": "broadcom", "name": "Broadcom", "color": "#CC0000",
     "candidates": ["Broadcom logo (2016-present).svg", "Broadcom Logo.svg", "Broadcom logo.svg"]},
    {"slug": "qualcomm", "name": "Qualcomm", "color": "#3253DC",
     "candidates": ["Qualcomm-Logo.svg", "Qualcomm logo.svg", "Qualcomm.svg"]},
    {"slug": "tsmc",     "name": "TSMC",     "color": "#C8102E",
     "candidates": ["TSMC wordmark.svg", "Tsmc-text.svg", "TSMC.svg", "TSMC logo.svg"]},
    {"slug": "asml",     "name": "ASML",     "color": "#1666B0",
     "candidates": ["ASML Logo.svg", "ASML logo.svg", "ASML Holding logo.svg"]},
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "Arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


RETRIES = 3   # Commons/上传站经代理偶发 TLS 连接重置（10054），重试可救回 nvidia/三星/美光等


def _get(url: str, *, timeout: int, **kw) -> requests.Response:
    last = None
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))   # 退避：缓解 429 限流与偶发 TLS 重置
    raise last


def commons_imageinfo(filename: str) -> dict | None:
    """查 Commons 文件的许可与栅格化缩略图 URL；文件不存在返回 None。"""
    params = {
        "action": "query", "format": "json", "titles": f"File:{filename}",
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": THUMB_W,
    }
    r = _get(COMMONS_API, params=params, timeout=25)
    pages = r.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if "missing" in page or "imageinfo" not in page:
        return None
    ii = page["imageinfo"][0]
    ext = ii.get("extmetadata", {})
    return {
        "thumburl": ii.get("thumburl", ""),
        "license": (ext.get("LicenseShortName", {}).get("value", "") or "").strip(),
        "descurl": ii.get("descriptionurl", ""),
    }


def acceptable(license_short: str) -> bool:
    low = license_short.lower()
    return any(tok in low for tok in ACCEPT_TOKENS)


def fetch_png(url: str) -> Image.Image:
    r = _get(url, timeout=30)
    return Image.open(io.BytesIO(r.content)).convert("RGBA")


def _frame(color: tuple[int, int, int], name: str) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
    """白底 + 品牌色边框 + 底部品牌色条（公司名）。返回 (画布, draw, logo 可用区 bbox)。"""
    w, h = CANVAS
    canvas = Image.new("RGB", (w, h), color)
    canvas.paste(Image.new("RGB", (w - 2 * BORDER, h - 2 * BORDER), "white"), (BORDER, BORDER))
    draw = ImageDraw.Draw(canvas)
    foot_top = h - BORDER - FOOT_H
    draw.rectangle([BORDER, foot_top, w - BORDER, h - BORDER], fill=color)
    font = load_font(30)
    tb = draw.textbbox((0, 0), name, font=font)
    draw.text(((w - (tb[2] - tb[0])) / 2, foot_top + (FOOT_H - (tb[3] - tb[1])) / 2 - tb[1]),
              name, font=font, fill="white")
    area = (BORDER + PAD, BORDER + PAD, w - BORDER - PAD, foot_top - PAD)
    return canvas, draw, area


def compose_logo(logo: Image.Image, color_hex: str, name: str) -> Image.Image:
    color = hex_rgb(color_hex)
    canvas, _, (ax0, ay0, ax1, ay1) = _frame(color, name)
    aw, ah = ax1 - ax0, ay1 - ay0
    ratio = min(aw / logo.width, ah / logo.height)
    logo = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))), Image.LANCZOS)
    x = ax0 + (aw - logo.width) // 2
    y = ay0 + (ah - logo.height) // 2
    canvas.paste(logo, (x, y), logo)   # 用 alpha 作 mask
    return canvas


def wordmark(color_hex: str, name: str) -> Image.Image:
    """无可用 PD logo 时的兜底：品牌色大字公司名（纯排版，不构成版权）。"""
    color = hex_rgb(color_hex)
    canvas, draw, (ax0, ay0, ax1, ay1) = _frame(color, name)
    cx, cy = (ax0 + ax1) / 2, (ay0 + ay1) / 2
    font = load_font(76)
    tb = draw.textbbox((0, 0), name, font=font)
    draw.text((cx - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1]), name, font=font, fill=color)
    return canvas


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for c in COMPANIES:
        chosen = None
        for fn in c["candidates"]:
            try:
                info = commons_imageinfo(fn)
            except Exception as e:  # noqa: BLE001
                print(f"[{c['slug']}] api error for {fn!r}: {e}")
                continue
            if not info:
                continue
            if acceptable(info["license"]):
                chosen = (fn, info)
                break
            print(f"[{c['slug']}] skip {fn!r}: license={info['license']!r} (not PD/CC0)")

        if chosen:
            fn, info = chosen
            try:
                img = compose_logo(fetch_png(info["thumburl"]), c["color"], c["name"])
                rows.append((c["name"], info["descurl"], info["license"], f"PD/CC0 logo ({fn})"))
                print(f"[{c['slug']}] OK  logo={fn!r} license={info['license']!r}")
            except Exception as e:  # noqa: BLE001
                img = wordmark(c["color"], c["name"])
                rows.append((c["name"], "-", "-", f"自制 wordmark（下载/合成失败: {e}）"))
                print(f"[{c['slug']}] FALLBACK wordmark (compose failed: {e})")
        else:
            img = wordmark(c["color"], c["name"])
            rows.append((c["name"], "-", "-", "自制 wordmark（无可用 PD/CC0 logo）"))
            print(f"[{c['slug']}] FALLBACK wordmark (no PD/CC0 candidate)")

        out = os.path.join(OUT_DIR, f"{c['slug']}.png")
        img.save(out, "PNG", optimize=True)
        print(f"[{c['slug']}] saved {out} {img.size} {os.path.getsize(out)} bytes")

    lines = [
        "# 兜底图 logo 版权来源", "",
        "运行期不读取本文件，仅供版权审计。仅采用 Public Domain / CC0 的 Commons 文件；",
        "需署名或无可用 PD 文件的公司退回自制 wordmark（纯排版，不构成版权）。",
        "由 tools/build_source_logos.py 生成。", "",
        "| 公司 | 来源 | 许可 | 处理 |", "|------|------|------|------|",
    ]
    lines += [f"| {n} | {u} | {lic} | {note} |" for n, u, lic, note in rows]
    with open(os.path.join(OUT_DIR, "CREDITS.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nwrote {os.path.join(OUT_DIR, 'CREDITS.md')}")


if __name__ == "__main__":
    main()
