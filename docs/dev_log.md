# 开发进展日志

轻量化 GUI 控制台 + 评审看板的分阶段进展（方案见 `~/.claude/plans/iterative-conjuring-summit.md`）。

## 2026-06-20 · 阶段3-4：运行触发 + SSE 日志、评审看板 + 1–10 评分

### 阶段3 运行触发 + SSE 实时日志
- `webgui/app.py` 新增 `RunManager`（单运行锁 + 后台线程逐行收集子进程 stdout）与三个路由：
  - `POST /api/run` `{report, mode:full|llmOnly}`：起子进程 `main.py`（完整）/ `analyze_raw.py`（仅评分），子进程继承配置覆盖层；并发再次请求返回 409。
  - `GET /api/run/stream`：SSE 先补发历史日志再实时推送，结束发 `done` 事件携带报告 basename；15s 心跳保活。
  - `GET /api/run/status`：供页面加载时接管在跑的运行。
- 控制台页加「▶️ 运行流水线」卡片 + `run.js`（EventSource 流式日志、完成后给出「→ 评审看板」跳转）。

### 阶段4 评审看板 + 1–10 人工评分
- 新增 `ratings_store.py`：按文章 URL 主键存 `data/ratings.json`，含人工分/标记/备注 + 标题/来源/AI分/分类快照；分数净化到 1–10、标记白名单（分享/追问/忽略）、空记录自动删除；`_LOCK` 串行化读改写防并发丢更新。
- `webgui/app.py` 新增看板后端：`/api/reports`（列报告，最新在前）、`/api/report`（**剥 base64 大图**、图改走代理 URL）、`/api/img`（按需解码 data URI / 带 Referer 代理远程图 / 兜底分类 SVG）、`GET|POST /api/ratings`；报告 JSON 带 mtime 缓存；文件名白名单正则防路径穿越。
- `dashboard.html` + `dashboard.js`：复用报告卡片版式 + 10 颗 pip 的 1–10 评分控件、快捷标记 chips、备注框；筛选(全部/已评/未评/已标分享/分歧)、排序(AI分/我的分/排名/来源)、搜索；AI 分 vs 我的分并排，分歧(≥3)高亮；保存按 URL 防抖合并 + 单条在途串行，STATE 为权威源不被迟到响应回灌。
- `.gitignore` 忽略 `data/`（本地设置 + 人工评分属运行态）。

### 验证（test client + 浏览器 DOM）
- 端点全过：reports/report/img（base64 解码直出 + 路径穿越 404 拦截）、ratings（净化/白名单/空删/缺 url 400）。
- 真实子进程 llmOnly 运行：强制 DeepSeek 401 → 关键词兜底（零 token），SSE 实时 148 行、退出码 0 → done，新报告被正确识别并能在看板加载；并发锁返回 409。
- 浏览器实操：17 卡渲染、打分/标记/备注/筛选/分歧高亮/统计均正确；复刻「flag+分数快速连点」竞态，修复后服务端两者都正确持久化（此前会丢分）。
- 回归：core 模块全部 import OK，配置覆盖层快照正常；未改动 analyzer/reporter/main/config 逻辑。

> 阶段1-2（配置覆盖层 + 控制面板）已在更早提交完成。

## 2026-06-20 · 阶段5：四格式导出 + 周报挑选 + 每条引导性追问

### 每条新闻引导性追问（§8）
- `config.py` 新增 `ARTICLE_FOLLOWUP_PROMPT`（可在 GUI Prompt 编辑器改人设）；`analyzer.generate_summary` 把它追加到摘要 system prompt 末尾，随摘要一并产出 `followup` 字段（**零额外 LLM 调用**），`_build_result` 写 `followup_question`，`_fallback_summary` 兜底为 `""`。
- `reporter.CARD_TEMPLATE` 加追问行（商业平铺卡 + 技术分类卡都渲染）；`settings_store.OVERRIDABLE_PROMPTS` 与 `control.js` PROMPT_META 收录该人设；看板 `/api/report` + `dashboard.js` 也展示。

### 四格式导出
- 新增 `wechat_render.py`：`render_wechat`（公众号图文，**全内联 style、无 `<style>`/`class`**、base64 内嵌图、阅读全文链接）+ `render_markdown`（也用作剪贴板文本）。
- `webgui/app.py`：`/api/export/preview`（按范围预览子集）、`/api/export`（微信/独立网页[复用 `reporter.save_report`]/Markdown/剪贴板四格式）、`/api/export/open`（浏览器打开产物，文件名白名单防穿越）。
- `export.html` + `export.js` 替换占位页：选报告 + 范围(分享/我评分≥N/全部) + 格式勾选 + 预览 + 导出结果链接/复制。

### 周报从人工评分挑 Top-N
- `/api/weekly_from_ratings`：取近 N 天 `human_score` 最高的 Top-N，按 slug+date 回源报告取完整文章，非技术门类归「其他技术」防分组丢条目，直接 `reporter.save_report(report_type="tech")` 渲染（跳过爬取与 AI 评分）。

### 验证
- §8：reporter 卡片有/无追问分别渲染/省略；看板卡片 `.rfollowup` 渲染（浏览器实测）；真实 DeepSeek 调用确认能产出中文追问，无 LLM 时兜底 `""`。
- 导出：预览计数正确（shared 3 / rated≥8 2 条）；四格式全产出；微信 HTML 经校验无 `<style>`/`class=`、有内联 `style=` 与阅读全文；`/api/export/open` 拦截 `../config.py`、`raw_articles.json`（400）。
- 周报：从评分生成 Top-N 技术周报，`open` 返回 HTML 含标题。
- 回归：core 模块全部 import + `create_app` OK；测试产物与评分已清理，`output/` 复原。

> **注**：`reporter.py` 含工作区既有的非本任务改动（tier 徽章/响应式/图片兜底约 97 行）。§8 的卡片追问渲染（CARD_TEMPLATE 槽位 + `_build_card` + `.followup` 样式）已写入 reporter.py 但**未随本批提交**，以免把既有 WIP 卷入；请单独 review 后提交 reporter.py。
>
> 阶段6（增值交互：试评分预览 / 分歧视图 / 运行历史 / 源健康度）待续。
