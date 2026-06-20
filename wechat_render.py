"""
wechat_render.py — 把评审看板挑出的子集渲染成「可分享」格式

两个纯函数，输入是报告 JSON 里的完整文章 dict 列表（含 image_url / summary /
followup_question 等；可附加 human_score / note）：

- render_wechat(articles, title, intro="") -> str
  微信公众号图文 HTML：纯 <section>/<p> + **全内联 style**、无 <style>/class
  （公众号编辑器会剥离 class 与 <style>，样式必须内联）；base64 图直接内嵌、带「阅读全文」链接。
- render_markdown(articles, title) -> str
  Markdown/纯文本：标题 + 链接 + 摘要 + AI/我的分 + 备注，便于贴到 IM / 笔记 / 剪贴板。

不复制评分/抓取逻辑：数据来自已生成的报告 JSON 与 ratings_store。
"""

from __future__ import annotations

from html import escape

# 复用报告配色，全部写成内联 style
_C_PRIMARY = "#0f2044"
_C_ACCENT = "#1a6cf5"
_C_TEXT = "#1e2230"
_C_MUTED = "#6b7280"
_C_VIOLET = "#5b21b6"


def _score_bits(a: dict) -> str:
    bits = []
    if isinstance(a.get("score"), (int, float)):
        bits.append(f"AI {a['score']}/10")
    if isinstance(a.get("human_score"), (int, float)):
        bits.append(f"我的评分 {a['human_score']}/10")
    if a.get("source"):
        bits.insert(0, str(a["source"]))
    return " · ".join(bits)


# ============================================================
# 微信公众号图文 HTML（全内联样式）
# ============================================================

def render_wechat(articles: list[dict], title: str, intro: str = "") -> str:
    """生成可直接粘贴进公众号编辑器的图文 HTML（无 class / 无 <style>，样式全内联）。"""
    parts: list[str] = []
    parts.append(
        f'<section style="max-width:677px;margin:0 auto;'
        f'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;'
        f'color:{_C_TEXT};line-height:1.75;">'
    )
    parts.append(
        f'<section style="padding:16px 0 8px;border-bottom:2px solid {_C_ACCENT};margin-bottom:18px;">'
        f'<strong style="font-size:20px;color:{_C_PRIMARY};">{escape(title)}</strong></section>'
    )
    if intro:
        parts.append(
            f'<section style="font-size:15px;color:#374151;background:#f5f7fb;'
            f'border-left:3px solid {_C_ACCENT};padding:12px 14px;margin-bottom:20px;">'
            f'{escape(intro)}</section>'
        )

    for idx, a in enumerate(articles, 1):
        parts.append('<section style="margin-bottom:26px;">')

        img = a.get("image_url", "") or ""
        if img.startswith(("data:image/", "http://", "https://")) and not img.startswith("data:image/svg"):
            parts.append(
                f'<section style="margin-bottom:10px;"><img src="{escape(img, quote=True)}" '
                f'style="width:100%;border-radius:8px;display:block;" /></section>'
            )

        parts.append(
            f'<section style="margin-bottom:6px;"><strong style="font-size:17px;color:{_C_PRIMARY};">'
            f'{idx}. {escape(a.get("title", "") or "（无标题）")}</strong></section>'
        )

        meta = _score_bits(a)
        if meta:
            parts.append(
                f'<section style="font-size:13px;color:{_C_MUTED};margin-bottom:8px;">{escape(meta)}</section>'
            )

        summary = (a.get("summary") or "").strip()
        if summary:
            parts.append(
                f'<p style="font-size:15px;color:#333;margin:0 0 8px;">{escape(summary)}</p>'
            )

        followup = (a.get("followup_question") or "").strip()
        if followup:
            parts.append(
                f'<section style="font-size:14px;color:{_C_VIOLET};background:#f5f3ff;'
                f'border-left:3px solid #8b5cf6;padding:8px 12px;border-radius:6px;margin-bottom:8px;">'
                f'🤔 分析师追问：{escape(followup)}</section>'
            )

        note = (a.get("note") or "").strip()
        if note:
            parts.append(
                f'<section style="font-size:13px;color:#92400e;background:#fff7ed;'
                f'padding:7px 12px;border-radius:6px;margin-bottom:8px;">📝 {escape(note)}</section>'
            )

        url = a.get("url", "")
        if url:
            parts.append(
                f'<section style="font-size:14px;"><a href="{escape(url, quote=True)}" '
                f'style="color:{_C_ACCENT};text-decoration:none;">阅读全文 →</a></section>'
            )

        parts.append('</section>')

    parts.append(
        f'<section style="font-size:12px;color:{_C_MUTED};border-top:1px solid #e5e7eb;'
        f'padding-top:12px;margin-top:8px;">由芯片资讯控制台导出 · 摘要由 AI 生成，仅供参考</section>'
    )
    parts.append('</section>')
    return "".join(parts)


# ============================================================
# Markdown / 纯文本
# ============================================================

def render_markdown(articles: list[dict], title: str) -> str:
    """生成 Markdown 文本（也用作剪贴板内容）。"""
    lines: list[str] = [f"# {title}", ""]
    for idx, a in enumerate(articles, 1):
        lines.append(f"## {idx}. {a.get('title', '') or '（无标题）'}")
        meta = _score_bits(a)
        if meta:
            lines.append(f"> {meta}")
        summary = (a.get("summary") or "").strip()
        if summary:
            lines.append("")
            lines.append(summary)
        followup = (a.get("followup_question") or "").strip()
        if followup:
            lines.append("")
            lines.append(f"🤔 *分析师追问：{followup}*")
        note = (a.get("note") or "").strip()
        if note:
            lines.append("")
            lines.append(f"📝 备注：{note}")
        url = a.get("url", "")
        if url:
            lines.append("")
            lines.append(f"🔗 {url}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
