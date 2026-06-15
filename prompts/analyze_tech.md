# 任务：生成半导体行业技术周报

> 仅用于 `run_tech.ps1 -codex` 的旧式 agent 模式。默认流程请使用 `python main.py --report tech` 或 `run_tech.ps1`，由 Python 负责流程编排，LLM 仅作为轻量文本接口。

请严格按以下步骤执行，每步完成后立即继续下一步，不需要等待确认。

---

## Step 1：读取原始数据

用 Read 工具读取 `output/raw_articles.json`，获取本周爬取的原始文章列表。
再用 Read 工具读取 `output/seen_tech.json`，获取已报道 URL 集合（文件不存在则视为空集）。

---

## Step 2：过滤已见文章

从 raw_articles.json 的 articles 数组中，**移除**满足以下任一条件的文章：
- url 已存在于 seen_tech.json 的 urls 列表中
- pub_date 字段不为空字符串，且日期早于今天前 7 天

记录**过滤后**的文章数量作为 `candidates_count`。

---

## Step 3：去重

对过滤后的文章，识别标题含义极为相近（同一事件多来源报道）的重复条目，保留 text 字段最长的版本，移除其余重复项。

---

## Step 4：翻译英文文章

对 `language == "en"` 的文章，将 title 和 text 翻译成中文：
- 技术缩写（HBM、GAA、PCIe、CXL、EUV、RISC-V、CoWoS、NVLink、UCIe 等）保留英文
- 所有数字、百分比、制程节点（如 2nm、3nm）必须保留
- 忠实原文，不省略关键技术细节

---

## Step 5：技术相关度评分与分类

对每篇文章打 0-10 分，并从以下门类中选择最匹配的一类：

**技术门类**：制造&工艺 / 芯片架构 / EDA工具 / 标准&会议 / 其他技术

**评分参考**：
- 8-10：深度技术内容（制程节点突破、架构创新、EDA新工具、重要标准进展）
- 4-7：有明确技术关联
- 0-3：纯商业/财报/泛消费电子，无实质技术内容

**选出 Top 15**（按 `score × weight` 降序，weight 来自原始文章的 weight 字段），其余进入附录。

---

## Step 6：生成精读摘要（仅 Top 15）

为每篇 **Top 15 文章**生成以下三项（全部中文输出）：
- **title**：中文标题，≤30 字，突出技术主题
- **summary**：精读摘要，5-7 句话，≤250 字，按以下层次：背景/问题 → 技术路线或方案 → 关键指标与数据（制程节点、性能、功耗、良率、带宽等，有数字必须保留）→ 对工程实践或产业的影响
- **keywords**：3-5 个技术关键词，中文，逗号分隔（技术缩写可保留英文）

---

## Step 7：生成技术趋势概览

根据 Top 15 文章，生成一段 `executive_summary`：
- ≤200 字，中文段落（不要 bullet points）
- 提炼制造工艺、芯片架构、EDA工具、标准会议中的 3-5 个关键技术变化

---

## Step 8：生成本周讯息总结（weekly_digest）

根据**所有评分后的文章**（含未入选 Top 15 的），生成 `weekly_digest`：
- 300-450 字，中文，分 3-5 段（如：制造与工艺、芯片架构与算力、EDA与设计方法、标准与生态、其他值得关注）
- 每段 2-4 句
- 指出本周讯息分布特点：哪个方向最热、有什么共同趋势、有什么孤立但重要的信号
- 不要逐条罗列，不要 bullet points

---

## Step 9：准备附录

附录（appendix）为排名 16 及以后的文章，每条只需：url、title（中文，若是英文则使用 Step 4 的译文）、source、pub_date、score、category。

---

## Step 10：将分析结果写入 JSON

用 Write 工具将以下格式的 JSON 写入 `output/analyzed_results.json`。

**注意**：
- `results` 数组仅含 Top 15，按 weighted_score 降序，rank 从 1 开始
- `all_urls` 包含 Step 2 过滤后**所有文章**的 url
- 只写 JSON，不要生成 HTML；外部脚本会在 LLM 结束后调用 `finalize_report.py`

```json
{
  "report_type": "tech",
  "report_title": "半导体行业技术周报",
  "candidates_count": <Step 2 过滤后文章数>,
  "executive_summary": "<Step 7 生成的趋势概览>",
  "weekly_digest": "<Step 8 生成的本周讯息总结>",
  "all_urls": ["<url1>", "<url2>", "..."],
  "results": [
    {
      "rank": 1,
      "score": 10,
      "weighted_score": 11.0,
      "source": "<原始文章 source 字段>",
      "language": "<zh 或 en>",
      "url": "<原始文章 url>",
      "image_url": "<原始文章 image_url>",
      "pub_date": "<原始文章 pub_date>",
      "original_title": "<翻译前的原始标题>",
      "title": "<Step 6 生成的中文标题>",
      "summary": "<Step 6 生成的中文精读摘要>",
      "keywords": "<Step 6 生成的关键词>",
      "tags": "",
      "category": "<Step 5 分配的门类>"
    }
  ],
  "appendix": [
    {
      "title": "<中文标题>",
      "url": "<url>",
      "source": "<来源>",
      "pub_date": "<日期>",
      "score": 6,
      "category": "<门类>"
    }
  ]
}
```

---

## Step 11：结束

确认 `output/analyzed_results.json` 已写入后即完成任务，无需其他操作。
