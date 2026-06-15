# 任务：生成半导体行业商业动态日报

> 仅用于 `run_business.ps1 -codex` 的旧式 agent 模式。默认流程请使用 `python main.py --report business` 或 `run_business.ps1`，由 Python 负责流程编排，LLM 仅作为轻量文本接口。

请严格按以下步骤执行，每步完成后立即继续下一步，不需要等待确认。

---

## Step 1：读取原始数据

用 Read 工具读取 `output/raw_articles.json`，获取今日爬取的原始文章列表。
再用 Read 工具读取 `output/seen_business.json`，获取已报道 URL 集合（文件不存在则视为空集）。

---

## Step 2：过滤已见文章

从 raw_articles.json 的 articles 数组中，**移除**满足以下任一条件的文章：
- url 已存在于 seen_business.json 的 urls 列表中
- pub_date 字段不为空字符串，且日期早于今天前 2 天

记录**过滤后**的文章数量作为 `candidates_count`。

---

## Step 3：去重

对过滤后的文章，识别标题含义极为相近（同一事件多来源报道）的重复条目，保留 text 字段最长的版本，移除其余重复项。

---

## Step 4：翻译英文文章

对 `language == "en"` 的文章，将 title 和 text 翻译成中文：
- 技术缩写（HBM、GAA、PCIe、CXL、EUV、RISC-V、CoWoS、NVLink 等）保留英文
- 公司名可保留英文或使用通用中文译名
- 所有数字、百分比、制程节点必须保留

---

## Step 5：商业相关度评分与标签

对每篇文章（含已翻译的）打 0-10 分，并从以下候选中选 2-4 个标签：

**可选标签**：财报业绩 / 融资并购 / 产能供应链 / 政策管制 / 客户订单 / 市场价格 / 公司战略 / 资本市场 / 其他商业

**评分参考**：
- 8-10：核心半导体产业动态（财报数据、重大并购、产能变化、出口管制等）
- 4-7：有明确半导体商业关联
- 0-3：无明确关联，或纯技术/学术/消费电子内容

**收录标准**：评分 ≥ 4 分的全部收录，不限数量。

---

## Step 6：生成摘要

为每篇**收录文章**生成以下三项（全部中文输出）：
- **title**：中文标题，≤30 字，突出公司/产业动作，不含来源站点名
- **summary**：中文摘要，3-5 句话，≤150 字，优先包含：公司名、金额/数字、产能规模、时间节点、市场影响
- **keywords**：3-5 个中文关键词，逗号分隔

---

## Step 7：生成商业动态概览

根据所有收录文章，生成一段 `executive_summary`：
- ≤200 字，中文段落（不要 bullet points，不要列表）
- 提炼 3-5 个本日核心商业动态及其意义

---

## Step 8：生成延伸研究问题

罗列完新闻信息后，以行业资深研究员及工程师带徒弟的视角，提出 3-5 个值得进一步追问的问题，写入 `research_questions` 数组：
- 每个问题使用中文，必须能独立作为 Google 搜索关键词
- 问题应围绕本日报告中的公司、供应链、工艺/产能、客户订单、价格或政策影响
- 避免空泛问题，优先包含具体公司名、技术名、地区或产业环节

---

## Step 9：将分析结果写入 JSON

用 Write 工具将以下格式的 JSON 写入 `output/analyzed_results.json`。

**注意**：
- `results` 数组按 `score × weight`（weight 来自原始文章的 weight 字段）降序排列
- `rank` 从 1 开始连续编号
- `all_urls` 包含 Step 2 过滤后**所有文章**（不限是否入选）的 url，用于 seen 记录更新
- 只写 JSON，不要生成 HTML；外部脚本会在 LLM 结束后调用 `finalize_report.py`

```json
{
  "report_type": "business",
  "report_title": "半导体行业商业动态日报",
  "candidates_count": <Step 2 过滤后文章数>,
  "executive_summary": "<Step 7 生成的概览文字>",
  "research_questions": ["<Step 8 问题1>", "<Step 8 问题2>", "..."],
  "weekly_digest": "",
  "appendix": [],
  "all_urls": ["<url1>", "<url2>", "..."],
  "results": [
    {
      "rank": 1,
      "score": 9,
      "weighted_score": 9.0,
      "source": "<原始文章 source 字段>",
      "language": "<原始文章 language 字段，zh 或 en>",
      "url": "<原始文章 url>",
      "image_url": "<原始文章 image_url>",
      "pub_date": "<原始文章 pub_date>",
      "original_title": "<翻译前的原始标题>",
      "title": "<Step 6 生成的中文标题>",
      "summary": "<Step 6 生成的中文摘要>",
      "keywords": "<Step 6 生成的关键词>",
      "tags": "<Step 5 的标签，中文顿号分隔，如：产能供应链，客户订单>",
      "category": ""
    }
  ]
}
```

---

## Step 10：结束

确认 `output/analyzed_results.json` 已写入后即完成任务，无需其他操作。
