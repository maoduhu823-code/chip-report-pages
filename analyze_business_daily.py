#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行 prompts/analyze_business.md 中的所有步骤
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
import sys

# 确保在脚本目录运行
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

def load_json(path, default=None):
    """安全地加载 JSON 文件"""
    if not os.path.exists(path):
        print(f"[INFO] {path} 不存在，使用默认值")
        return default if default is not None else {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] 无法读取 {path}: {e}")
        return default if default is not None else {}

def save_json(path, data):
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已写入 {path}")

# ============================================================================
# STEP 1：读取原始数据
# ============================================================================
print("\n=== STEP 1: 读取原始数据 ===")
raw_data = load_json('output/raw_articles.json', {'articles': []})
seen_business = load_json('output/seen_business.json', {'urls': []})

articles = raw_data.get('articles', [])
seen_urls = set(seen_business.get('urls', []))

print(f"[INFO] 加载 {len(articles)} 篇原始文章")
print(f"[INFO] 加载 {len(seen_urls)} 条已见 URL")

# ============================================================================
# STEP 2：过滤已见文章
# ============================================================================
print("\n=== STEP 2: 过滤已见文章 ===")
today = datetime.now().date()
cutoff_date = today - timedelta(days=2)

filtered_articles = []
for article in articles:
    # 检查 URL 是否已见
    if article.get('url') in seen_urls:
        print(f"[SKIP] URL 已见: {article.get('url', '?')[:60]}")
        continue

    # 检查日期
    pub_date_str = article.get('pub_date', '')
    if pub_date_str and pub_date_str.strip():
        try:
            pub_date = datetime.strptime(pub_date_str[:10], '%Y-%m-%d').date()
            if pub_date < cutoff_date:
                print(f"[SKIP] 日期过旧 {pub_date}: {article.get('title', '?')[:50]}")
                continue
        except:
            pass

    filtered_articles.append(article)

candidates_count = len(filtered_articles)
print(f"[INFO] 过滤后：{candidates_count} 篇候选文章")

# ============================================================================
# STEP 3：去重 (按标题相似度)
# ============================================================================
print("\n=== STEP 3: 去重 ===")
deduped = []
seen_titles = {}

for article in filtered_articles:
    title = article.get('title', '').strip()
    if not title:
        deduped.append(article)
        continue

    # 简单的相似度检查：标题的前 20 个字符相同视为重复
    title_key = title[:20]

    if title_key in seen_titles:
        old_idx = seen_titles[title_key]
        old_text = deduped[old_idx].get('text', '')
        new_text = article.get('text', '')
        # 保留更长的文本版本
        if len(new_text) > len(old_text):
            deduped[old_idx] = article
            print(f"[DEDUP] 替换文章（更长版本）：{title[:50]}")
        else:
            print(f"[DEDUP] 跳过重复：{title[:50]}")
    else:
        seen_titles[title_key] = len(deduped)
        deduped.append(article)

print(f"[INFO] 去重后：{len(deduped)} 篇文章")
filtered_articles = deduped

# ============================================================================
# STEP 4：翻译英文文章
# ============================================================================
print("\n=== STEP 4: 翻译英文文章 ===")
for article in filtered_articles:
    if article.get('language') == 'en':
        # 这里需要调用翻译 API，目前用占位符
        original_title = article.get('title', '')
        original_text = article.get('text', '')

        # 保存原始标题供后续使用
        article['original_title'] = original_title

        # 实际翻译将由下一步的 Claude API 处理
        print(f"[INFO] 标记英文文章待翻译：{original_title[:50]}")

# ============================================================================
# STEP 5 & 6：评分、标签、摘要 - 调用 Claude API
# ============================================================================
print("\n=== STEP 5-6: 评分、标签、摘要（使用 Claude API）===")

from anthropic import Anthropic

client = Anthropic()

# 分批处理文章（避免 token 超出）
BATCH_SIZE = 5
results = []

for batch_idx in range(0, len(filtered_articles), BATCH_SIZE):
    batch = filtered_articles[batch_idx:batch_idx+BATCH_SIZE]
    print(f"\n[INFO] 处理第 {batch_idx//BATCH_SIZE + 1} 批（{len(batch)} 篇）...")

    # 构建 batch 内容
    batch_content = []
    for i, article in enumerate(batch):
        idx = batch_idx + i + 1
        batch_content.append(f"""
文章 #{idx}:
标题: {article.get('title', '未知')}
来源: {article.get('source', '未知')}
发布日期: {article.get('pub_date', '未知')}
语言: {article.get('language', 'unknown')}
网址: {article.get('url', '未知')}
正文摘录 (前 800 字):
{article.get('text', '')[:800]}
""")

    prompt = f"""你是一位资深半导体产业分析师。请对以下文章进行评分、标签分配和摘要生成。

{chr(10).join(batch_content)}

对每篇文章，请以 JSON 格式输出：
[
  {{
    "article_idx": 1,
    "score": <0-10 分>,
    "tags": ["标签1", "标签2", "标签3"],
    "translated_title": "<中文标题，≤30字>",
    "translated_summary": "<中文摘要，3-5句，≤150字>",
    "keywords": "<3-5个关键词，逗号分隔>",
    "translation_notes": "<英文文章翻译注记（英文或中文）>"
  }},
  ...
]

评分参考：
- 8-10：核心半导体产业动态（财报数据、重大并购、产能变化、出口管制等）
- 4-7：有明确半导体商业关联
- 0-3：无明确关联，或纯技术/学术/消费电子内容

可选标签：财报业绩、融资并购、产能供应链、政策管制、客户订单、市场价格、公司战略、资本市场、其他商业

要求：
1. 所有输出均为中文
2. 英文文章的翻译要保留技术缩写（HBM、GAA、PCIe 等）和公司名英文形式
3. 评分 ≥4 分的文章才会被收录
4. 标题不含来源站点名
5. 只返回 JSON，无额外文字"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text

        # 解析 JSON
        try:
            analyses = json.loads(response_text)
        except json.JSONDecodeError:
            # 尝试找到 JSON 块
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start >= 0 and end > start:
                analyses = json.loads(response_text[start:end])
            else:
                print(f"[ERROR] 无法解析 Claude 响应中的 JSON")
                analyses = []

        # 将分析结果附加到对应的文章
        for analysis in analyses:
            article_idx = analysis.get('article_idx', 1) - 1
            if 0 <= article_idx < len(batch):
                article = batch[article_idx]
                article['score'] = analysis.get('score', 5)
                article['tags'] = analysis.get('tags', [])
                article['title'] = analysis.get('translated_title', article.get('title', ''))
                article['summary'] = analysis.get('translated_summary', '')
                article['keywords'] = analysis.get('keywords', '')

                # 记录分数
                score = article['score']
                if score >= 4:
                    results.append(article)
                    print(f"  ✓ 文章 #{batch_idx + article_idx + 1}: 分数 {score}")
                else:
                    print(f"  ✗ 文章 #{batch_idx + article_idx + 1}: 分数 {score}（未收录）")

    except Exception as e:
        print(f"[ERROR] Claude API 调用失败: {e}")
        # 降级处理：使用规则评分
        for article in batch:
            title = article.get('title', '').lower()
            text = (article.get('text', '') + title).lower()

            # 简单规则评分
            score = 5  # 默认 5 分
            tags = ['其他商业']

            keywords_list = []
            if any(w in text for w in ['财报', '营收', '利润', '盈利', 'revenue', 'earnings']):
                score += 1
                tags = ['财报业绩']
                keywords_list.append('财报')
            if any(w in text for w in ['并购', '收购', '融资', 'acquisition', 'merger', 'funding']):
                score += 1
                tags.append('融资并购')
                keywords_list.append('并购')
            if any(w in text for w in ['产能', '供应', '制造', 'capacity', 'supply', 'production']):
                score += 1
                tags.append('产能供应链')
                keywords_list.append('产能')

            article['score'] = min(score, 10)
            article['tags'] = tags
            article['title'] = article.get('title', '')
            article['summary'] = article.get('text', '')[:150]
            article['keywords'] = ','.join(keywords_list) if keywords_list else '行业动态'

            if article['score'] >= 4:
                results.append(article)

# ============================================================================
# STEP 7：生成商业动态概览
# ============================================================================
print("\n=== STEP 7: 生成商业动态概览 ===")

if results:
    # 取分数最高的 5 篇生成概览
    top_articles = sorted(results, key=lambda x: x.get('score', 0), reverse=True)[:5]

    articles_summary = []
    for art in top_articles:
        articles_summary.append(f"- {art.get('title', '')}: {art.get('summary', '')[:100]}")

    overview_prompt = f"""根据以下今日半导体行业核心商业动态，生成一段 ≤200 字的中文概览段落（不要 bullet points，不要列表形式，写成连贯的段落）。

{chr(10).join(articles_summary)}

要求：
1. 提炼 3-5 个核心动态及其产业意义
2. 避免过度总结，保留具体公司名和关键数字
3. 只返回段落文本，无标题或前缀"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": overview_prompt}]
        )
        executive_summary = response.content[0].text.strip()
    except:
        executive_summary = "详见下方收录文章。"
else:
    executive_summary = "本日未发现符合条件的行业商业动态。"

print(f"[INFO] 概览已生成（{len(executive_summary)} 字）")

# ============================================================================
# STEP 8：生成延伸研究问题
# ============================================================================
print("\n=== STEP 8: 生成延伸研究问题 ===")

if results:
    articles_for_questions = results[:10]  # 取前 10 篇
    articles_titles = [f"- {a.get('title', '')} ({a.get('source', '')})" for a in articles_for_questions]

    questions_prompt = f"""作为一名资深半导体产业研究员和工程师导师，请根据以下今日行业新闻，提出 3-5 个值得进一步追问的研究问题。

今日新闻标题：
{chr(10).join(articles_titles)}

要求：
1. 每个问题必须能独立作为 Google 搜索关键词
2. 问题应围绕：公司动态、供应链、工艺/产能、客户订单、价格或政策影响
3. 包含具体公司名、技术名或地区
4. 避免空泛问题
5. 返回 JSON 数组格式，如 ["问题1", "问题2", ...]
6. 只返回数组，无额外文字"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": questions_prompt}]
        )

        response_text = response.content[0].text
        try:
            research_questions = json.loads(response_text)
        except:
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start >= 0 and end > start:
                research_questions = json.loads(response_text[start:end])
            else:
                research_questions = []
    except:
        research_questions = []
else:
    research_questions = []

print(f"[INFO] 已生成 {len(research_questions)} 个研究问题")

# ============================================================================
# STEP 9：生成 JSON 输出
# ============================================================================
print("\n=== STEP 9: 生成 JSON 输出 ===")

# 排序：按 score × weight 降序
for article in results:
    weight = article.get('weight', 1.0)
    article['weighted_score'] = article.get('score', 5) * weight

results_sorted = sorted(results, key=lambda x: x.get('weighted_score', 0), reverse=True)

# 构建最终 JSON
output = {
    "report_type": "business",
    "report_title": "半导体行业商业动态日报",
    "report_date": today.isoformat(),
    "candidates_count": candidates_count,
    "executive_summary": executive_summary,
    "research_questions": research_questions,
    "weekly_digest": "",
    "appendix": [],
    "all_urls": [a.get('url', '') for a in filtered_articles],
    "results": []
}

for rank, article in enumerate(results_sorted, 1):
    result_item = {
        "rank": rank,
        "score": article.get('score', 5),
        "weighted_score": round(article.get('weighted_score', 0), 2),
        "source": article.get('source', ''),
        "language": article.get('language', 'unknown'),
        "url": article.get('url', ''),
        "image_url": article.get('image_url', ''),
        "pub_date": article.get('pub_date', ''),
        "original_title": article.get('original_title', article.get('title', '')),
        "title": article.get('title', ''),
        "summary": article.get('summary', ''),
        "keywords": article.get('keywords', ''),
        "tags": '、'.join(article.get('tags', [])) if article.get('tags') else '',
        "category": ""
    }
    output["results"].append(result_item)

# 保存
save_json('output/analyzed_results.json', output)

print(f"\n[✓] 分析完成！")
print(f"  - 候选文章：{candidates_count}")
print(f"  - 收录文章：{len(results)}")
print(f"  - 研究问题：{len(research_questions)}")
print(f"  - 输出文件：output/analyzed_results.json")
