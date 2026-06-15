"""
finalize_report.py — Claude Code 分析完成后调用此脚本生成 HTML 报告。

用法：
  python finalize_report.py output/analyzed_results.json

Claude Code 将分析结果写入 analyzed_results.json，本脚本负责：
  1. 读取 JSON
  2. 调用 reporter.save_report() 渲染 HTML + JSON 报告
  3. 更新 output/seen_<slug>.json（防止跨天/跨周重复收录）
"""

import json
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import RSS_SOURCES, HTML_SOURCES
from reporter import save_report
from main import load_seen, save_seen

input_path = sys.argv[1] if len(sys.argv) > 1 else "output/analyzed_results.json"
print(f"读取分析结果: {input_path}")

data = json.load(open(input_path, encoding="utf-8"))
report_type = data["report_type"]          # "business" 或 "tech"
slug = "business" if report_type == "business" else "tech"
site_names = [s["name"] for s in RSS_SOURCES] + [s["name"] for s in HTML_SOURCES]
metadata = data.get("metadata") or {}
metadata_path = os.path.join("output", "llm_run_metadata.json")
if not metadata and os.path.exists(metadata_path):
    try:
        metadata = json.load(open(metadata_path, encoding="utf-8"))
    except Exception:
        metadata = {}

html_path = save_report(
    results=data["results"],
    executive_summary=data.get("executive_summary", ""),
    site_names=site_names,
    candidates_count=data.get("candidates_count", 0),
    report_type=report_type,
    report_slug=slug,
    report_title=data.get("report_title", "半导体行业报告"),
    weekly_digest=data.get("weekly_digest", ""),
    appendix=data.get("appendix", []),
    research_questions=data.get("research_questions", []),
    metadata=metadata,
    file_prefix_word="daily" if report_type == "business" else "weekly",
)

print(f"HTML 已生成: {os.path.abspath(html_path)}")
print(f"JSON 已生成: {os.path.abspath(html_path.replace('.html', '.json'))}")

# 更新 seen 记录：本次处理过的所有 URL（含未入选），防止下次重复
all_urls = data.get("all_urls", [])
if all_urls:
    seen = load_seen(slug)
    save_seen(slug, seen, all_urls)
    print(f"seen_{slug}.json 已更新（新增 {len(all_urls)} 条）")
