# 报告配图抓取：根因与备选方案备忘

<!-- updated: 2026-06-12 -->

记录「报告里部分新闻配图显示失败」的根因与三套修复方案。**方案 1 已实装**；方案 2/3 为后备——若方案 1 在真实网络下仍大面积降级 SVG，再按需启用。

## 根因回顾

报告 `<img>` 的图在 `reporter.save_report` 出报告阶段由 `_resolve_image_src()` 解析：能下到真实图就 Base64 内嵌进 HTML，否则降级为分类 SVG。英文源失败分两类：

1. **RSS feed 不带图**：EDN、Semiconductor Engineering 的 feed 没有 `media:thumbnail / enclosure / media:content`（实测 EDN 0/9、SemiEng 0/10 篇自带图），`rss_crawler._extract_image_from_entry` 返回空 → 没有图 URL 可嵌。图其实在**原文页的 `og:image`** 里，但 RSS 流程原本从不抓原文页。
2. **海外图床下载超时**：EE Times 的 feed 带了图 URL（如 `…/Hero-image-5.jpg`），但出报告时同步下载常 ReadTimeout（实测带 Referer 抓 4 次全超时，~10s），旧 `timeout=8` 撑不住 → 降级。

对照：中文站（ICsmart）服务器快、HTML 爬虫已抓到正文图，所以内嵌正常。

## 方案 1（已实装）：og:image 兜底 + 抓取加固

- `reporter._resolve_image_src` 新增第 2 步：RSS/正文图拿不到时，调用既有的 `_extract_article_image_url()`（此前是**从未被调用的死代码**）回原文页抓 `og:image`/正文首图，再带 Referer 内嵌。
- `_fetch_image_as_data_uri`：超时由 8 提到 `IMAGE_FETCH_TIMEOUT`（12s），新增 `IMAGE_FETCH_RETRIES`（1 次）重试。
- `save_report`：图片解析改用 `ThreadPoolExecutor`（`IMAGE_FETCH_WORKERS`=10）并发，抵消加大超时带来的串行耗时。
- 调参入口：`config.py` 的「图片抓取」段。
- **局限**：仍依赖出报告的机器能连到这些海外站点；网络完全不通时回落到 SVG。

## 方案 2（后备）：浏览器端直接加载远程图

思路：不在服务端下载内嵌，直接把真实图 URL 放进 `<img src>`，由「打开报告的浏览器」去加载；`onerror` 仍降级 SVG（机制已在 `_build_card` 里）。

- 改动点：`reporter._build_card` 用远程 URL 作 `src`（而非内嵌 data URI）；把 `referrerpolicy="no-referrer"` 改为 `strict-origin-when-cross-origin`（否则防盗链站点 403）。
- 优点：出报告快、不卡在出报告那一刻的网络；浏览器侧通常有更好的连通性/缓存/代理。
- 缺点：离线看报告、邮件客户端不加载远程图、或防盗链拦截时显示 SVG；图片不再随 HTML 自包含。
- 适合：报告主要在能联网的浏览器里看，且希望出报告快。

## 方案 3（后备）：图片代理 / CDN 转码

思路：所有图经公共图片代理（如 `https://images.weserv.nl/?url=<编码后的图片URL>`）服务端取回，顺带 `webp→jpg` 转码、绕防盗链。

- 改动点：`_resolve_image_src` 下载前给候选图 URL 包一层代理前缀；可叠加在方案 1 之上（代理失败再回原逻辑）。
- 优点：同时缓解「慢」和「防盗链」；不必自己处理 UA / Referer。
- 缺点：引入外部依赖，代理需从出报告机器可达且需信任该第三方；大图/限流可能失败。
- 适合：方案 1 仍频繁超时、又不想改成浏览器端加载时。

## 触发条件

方案 1 上线后，若观察到英文源仍大面积 SVG（看 `output/*_business.json` 里 `image_url` 是否多为 `data:image/svg+xml`），优先试方案 2（改动小、最稳），仍不行再上方案 3。
