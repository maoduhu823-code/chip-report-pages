# CLAUDE.md

芯片行业资讯自动化工具：多信息源采集（RSS + HTML 爬虫）→ Claude API 评分 → 两份独立报告：**商业动态日报**（工作日，不限条目）+ **技术周报**（每周，Top N 精读）。当前为测试阶段。

## 运行方式

| 命令 | 用途 |
|------|------|
| `python main.py --report business` | 商业日报：阈值收录不限条目，按主标签粗分组（日志 `run.log`） |
| `python main.py --report tech` | 技术周报：Top N 精读 + 本周讯息总结 + 未入选附录 |
| `python main.py` / `--report both` | 两份都生成（手动调试用） |
| `python crawl_only.py` | 纯爬虫，不调 LLM，存 `output/raw_articles.json`（日志 `crawl.log`） |
| `.\run_business.ps1 [-llmOnly] [-codex] [-NoPublish]` | 商业日报流程：默认直接运行 Python 主流程，生成后更新 `github_pages_site` 并推送 GitHub Pages；带 `-NoPublish` 时只生成不发布；带 `-llmOnly` 时复用 `output/raw_articles.json`；带 `-codex` 时才使用旧式 Codex agent 任务文档 |
| `.\run_tech.ps1 [-codex] [-NoPublish]` | 技术周报流程：默认直接运行 Python 主流程，生成后更新 `github_pages_site` 并推送 GitHub Pages；带 `-NoPublish` 时只生成不发布；带 `-codex` 时才使用旧式 Codex agent 任务文档 |
| `info-com [-codex]` | PowerShell 快捷入口：商业日报完整流程，含 Python 爬虫；带 `-codex` 时改用 Codex |
| `info-semi-com [-codex]` | PowerShell 快捷入口：商业日报后半程，复用已有 `raw_articles.json`；带 `-codex` 时改用 Codex |
| `python -m webgui` / `.\run_gui.ps1` | 本地控制台 GUI（默认 `127.0.0.1:5000`）：控制台（信息源/阈值/词库/Prompt 编辑 + 一键运行 + SSE 实时日志）· 评审看板（1–10 人工评分/标记/备注）· 导出分享（微信图文/独立网页/Markdown/剪贴板）· 洞察（改词试评分/源健康度/运行历史） |

未配置真实 `ANTHROPIC_API_KEY` 时不报错：analyzer 自动降级为关键词规则评分 + 截断式摘要，可离线调试版式。

## 定时任务（已注册 Windows 计划任务）

| 任务名 | 计划 | 命令 |
|--------|------|------|
| `ChipReport_Business_Daily` | 周一至周五 05:00 | `run_business.ps1` |
| `ChipReport_Tech_Weekly` | 每周一 05:30 | `run_tech.ps1` |

均带 StartWhenAvailable（错过时间开机补跑）。管理：`Get-ScheduledTask ChipReport_*`。

## 两份报告的差异

| | 商业日报 | 技术周报 |
|--|---------|---------|
| 收录方式 | 评分 ≥ `BUSINESS_MIN_SCORE`（4 分）全收，**不限条目** | `TECH_TOP_N`（15）精选 |
| 摘要深度 | 常规摘要（正文取 600 字） | 精读摘要：背景→方案→关键指标→影响（正文取 1000 字） |
| 组织方式 | 不分组，按相关度平铺全部条目 | 按门类分组（`TECH_CATEGORIES`） |
| 专属板块 | 「延伸追问」3-5 个 Google 搜索问题 | 「本周讯息总结」（基于全部候选的全景总结）+ 附录（未入选条目标题超链接，无图无摘要） |
| 时效窗口 | `BUSINESS_ARTICLE_AGE_DAYS`（2 天） | `TECH_ARTICLE_AGE_DAYS`（7 天） |
| 输出 | `output/daily_<date>_business.html/.json` | `output/weekly_<date>_tech.html/.json` |

**防重复机制**：每次运行把处理过的 URL 记入 `output/seen_<slug>.json`（商业/技术分开记，上限 `SEEN_URLS_KEEP`），下次跳过——主要解决 HTML 站点文章无日期、跨天/跨周重复入选的问题。

## 文件结构

| 文件 | 职责 |
|------|------|
| `config.py` | API Key、信息源清单（`RSS_SOURCES` / `HTML_SOURCES`）、双版本 prompt（含 `TECH_WEEKLY_DIGEST_PROMPT`）、阈值与窗口参数 |
| `rss_crawler.py` | RSS/Atom 解析（feedparser），`crawl_all_rss(max_age_days=...)` 窗口参数化 |
| `crawler.py` | 通用 HTML 爬虫，由 `HTML_SOURCES` 配置驱动；站点改版时调整 `article_url_pattern` / `skip_url_keywords` 或正文选择器 |
| `analyzer.py` | `analyze_business_daily()`（去重→批量评分→阈值收录→摘要→概览）/ `analyze_tech_weekly()`（去重→批量评分→TopN 精读→概览+`generate_weekly_digest()`→附录）；优先 DeepSeek API，失败时回退 Claude Code Haiku |
| `reporter.py` | `save_report()` 渲染 HTML/JSON：商业 `_build_tag_sections`、技术 `_build_category_sections` + digest + 附录；图片缺失时降级——公司新闻室用品牌兜底图，其余用分类 SVG |
| `image_fetch.py` | 找图/验图唯一实现（供 crawler/reporter/webgui 复用）：og:image 抓取、字节签名校验、四级兜底链 `resolve_article_image()`；`source_fallback_image_uri()` 读公司源品牌兜底图。站点专属规则见 `config.SITE_IMAGE_RULES`（`extra_selectors` / `skip_page_scrape` / `fallback_image`） |
| `tools/build_source_logos.py` | 构建期脚本（仅手动运行，依赖 Pillow）：从 Wikimedia Commons 取公有领域 logo 合成「品牌色 + logo」兜底图到 `assets/source_logos/*.png` 并产出 `CREDITS.md` 版权清单；运行期只读图、不联网、不加运行时依赖 |
| `main.py` | 入口：`--report` 分流、seen 记录、时效窗口选择；启动时 `os.chdir` 到脚本目录以兼容计划任务 |
| `crawl_only.py` | 无 LLM 纯爬虫入口 |
| `publish_pages.ps1` | 将 `output/` 中最新商业日报/技术周报 HTML 复制到 `github_pages_site/reports/`，重建静态站点首页，提交并推送 GitHub Pages |
| `settings_store.py` | GUI 配置覆盖层：读写 `data/gui_settings.json`，`apply_overrides()` 在 `config.py` 末尾合并；手动运行/计划任务/GUI 三者共用同一份设置 |
| `ratings_store.py` | 人工评分存储：按文章 URL 主键存 `data/ratings.json`（1–10 分/标记/备注 + 快照），并发写加锁 |
| `wechat_render.py` | 导出渲染：微信图文（全内联样式、无 `class`/`<style>`）+ Markdown/剪贴板文本 |
| `webgui/` | 轻量 Flask 控制台：`app.py`（路由 + SSE 运行日志 + 单运行锁 + 看板/导出/洞察 API）、`templates/`、`static/`；复用上述模块、不分叉流水线 |

> GUI 通过「配置覆盖层」喂参数复用现有流水线，不复制评分/渲染逻辑；§8「每条引导性追问」由 `config.ARTICLE_FOLLOWUP_PROMPT` 随摘要产出 `followup_question`，报告卡片与看板均渲染。`data/` 为运行态（已 gitignore）。

## 信息源（在 `config.py` 增删）

- RSS 英文 6 源：EE Times、Semiconductor Engineering、IEEE Spectrum、The Next Platform、EDN、Electronic Design
- HTML 中文 2 站：ICsmart（icsmart.cn）、爱集微（aijiweinews.com）
- 每源带 `weight` 权重倍率，最终排名按 score × weight

LLM 接入（见 `config.py` / `analyzer.py`）：`LLM_PROVIDER=auto` 时优先使用 DeepSeek OpenAI-compatible API（默认 `deepseek-v4-flash`），API 无效/余额不足/请求失败时自动回退 Claude Code CLI（默认 `claude-haiku-4-5-20251001`，概览可配置为 `claude-sonnet-4-6`）。评分使用标题+首段批量请求；英文文章不再先全文翻译，而是直接生成中文标题/摘要/关键词。

常用环境变量：

```powershell
$env:DEEPSEEK_API_KEY="your_deepseek_api_key"
$env:LLM_PROVIDER="auto"          # auto / deepseek / claude
$env:DEEPSEEK_MODEL_FAST="deepseek-v4-flash"
$env:LLM_SCORING_BATCH_SIZE="40"
```

`prompts/analyze_business.md` / `prompts/analyze_tech.md` 仅用于 `run_business.ps1 -codex` / `run_tech.ps1 -codex` 的旧式 agent 模式；默认计划任务不再让 Claude Code/Codex 读取整包任务文档。

## 技术栈

Python + requests + BeautifulSoup(lxml) + feedparser + anthropic。

## 兼容性注意

- `config.py` 保留旧命名别名（`TOP_N`、`MAX_ARTICLE_AGE_DAYS`、`CATEGORIES`、`RELEVANCE_PROMPT` 等）。
- `AGENTS.md` 仅重定向到本文件，不在其中维护内容。

## 后续可扩展

邮件推送、更多中文信息源。
