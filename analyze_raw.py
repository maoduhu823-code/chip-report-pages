"""
analyze_raw.py - analyze existing output/raw_articles.json without crawling.

This is useful for llmOnly runs: Python owns the workflow and calls the LLM only
through analyzer.py's lightweight text interface.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from main import run_business_daily, run_tech_weekly


def _load_raw_articles() -> list[dict]:
    path = os.path.join("output", "raw_articles.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("articles", [])
    if isinstance(data, list):
        return data
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze existing raw_articles.json")
    parser.add_argument("--report", choices=["business", "tech"], required=True)
    args = parser.parse_args()

    articles = _load_raw_articles()
    if not articles:
        print("未找到可分析文章：output/raw_articles.json 为空或格式不正确", file=sys.stderr)
        return 1

    if args.report == "business":
        path = run_business_daily(articles)
    else:
        path = run_tech_weekly(articles)
    return 0 if path else 1


if __name__ == "__main__":
    raise SystemExit(main())
