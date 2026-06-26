# 报告配图抓取：根因与备选方案备忘

<!-- updated: 2026-06-26 -->

记录「报告里部分新闻配图显示失败」的根因与三套修复方案。**方案 1 已实装，且已吸收方案 2 作为其内部兜底层**（内嵌失败但 URL 看起来有效时保留 URL 交浏览器直载，而非直接退到 SVG）；方案 3 仍是纯后备——若方案 1 在真实网络下仍大面积降级 SVG，再按需启用。

> 2026-06-23 起，找图/验图逻辑已从 `reporter.py` 单文件收口为独立模块 `image_fetch.py`，本文档下方「方案 1」一节里提到的函数名为历史记录，现行实现见 [2026-06-23 更新] 一节。

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

## 2026-06-23 更新：NVIDIA/三星丢图根因 + 找图逻辑统一收口

用户反馈《欧洲部署35台NVIDIA AI超算，创纪录》等 NVIDIA、三星新闻原文有图，报告/看板里却没有。排查发现是两个独立 bug，且找图/验图实现当时已分散在四处（`crawler.py`、`reporter.py`、`webgui/app.py`、`rss_crawler.py`），同一类问题只在某一处修过，其他几处仍会复现——这也是本节标题里"统一收口"的动机。

### 根因 1：CDN 把图片 Content-Type 误标

NVIDIA 新闻室（`nvidianews.nvidia.com`）配图实际存于 `iprsoftwaremedia.com`，该 CDN 返回 `Content-Type: binary/octet-stream`，但字节内容确是合法 JPEG（`FF D8 FF` 开头）。旧逻辑只信 Header，`startswith("image/")` 为假就判定下载失败 → 降级 SVG。

- 修复：`image_fetch.sniff_image_mime()` 按文件头字节签名识别格式（JPEG/PNG/GIF/WEBP/BMP），Content-Type 非 `image/*` 时兜底用字节签名；`validate_image_bytes()` 统一这一判定，所有下载路径都过这一层。

### 根因 2："icon" 是 "semiconductor" 的子串——三星域名被误杀

更隐蔽的一个：图片候选 URL 的"是否像正文配图"过滤（排除 logo/埋点像素等）原先用纯子串匹配 `_BAD_IMAGE_TOKENS`（含 `"icon"`）。三星的域名 `news.samsungsemiconductor.com` 里 "sem**icon**ductor" 恰好包含 "icon"，导致三星的图片 URL 整条被误判成图标类直接排除——**连下载尝试都没发生**，比 NVIDIA 的 Content-Type 误标更彻底地丢图。`semiconductors.org`（SIA）等本项目其他信息源域名同样会撞上这个坑。

- 修复：`looks_like_content_image()` 改为按单词边界匹配（`_BAD_IMAGE_TOKEN_RE`，token 前后不能是字母/数字），子串落在完整单词内部时不再误判。回归测试覆盖：三星/SIA 域名应保留，真正的 `logo.png`/`icon-32x32.png`/`tracking-pixel.gif` 仍应排除。

### 根因 3（已知，非本次新发现）：偶发连接重置

三星新闻室页面偶发 `ConnectionResetError`（代理/GFW 环境下 TLS 握手被重置），单次请求失败不代表该站真的没图。`IMAGE_FETCH_RETRIES` 重试 + 详细 WARNING 日志已覆盖此类抖动，与上面两个根因相互独立、共同导致三星图片此前几乎必然失败。

### 收口：四处独立实现 → `image_fetch.py` 单一模块

新增 `image_fetch.py`，对外暴露：

| 函数 | 用途 | 调用方 |
|------|------|--------|
| `looks_like_content_image(url)` | 排除 logo/埋点像素类 URL | 四处全部 |
| `validate_image_bytes(data, content_type)` / `sniff_image_mime(data)` | Content-Type 不可信时的字节签名兜底 | `fetch_image_bytes` 内部 |
| `extract_image_from_soup(soup, base_url, extra_selectors)` | 从已抓取的 soup 里找配图（meta → 正文首图） | `crawler.py`（首次爬取/RSS补全，soup已在手）、`fetch_page_image_url`（自行发请求） |
| `fetch_page_image_url(article_url, source_name)` | 回原文页抓配图 URL，带重试 | `resolve_article_image` 的最后兜底 |
| `fetch_image_bytes(url, referer, source_name)` | 下载并校验图片，返回 (原始字节, mime) | `webgui/app.py` 看板代理、`fetch_image_as_data_uri` |
| `fetch_image_as_data_uri(...)` | 包一层 Base64，返回可直接内嵌的 data URI | `resolve_article_image` |
| `resolve_article_image(article)` | 整篇文章的找图总入口（四级兜底链） | `reporter._resolve_image_src` |

`reporter.py` 现在只保留"实在找不到真实图时该用哪张分类 SVG"这一展示层决策；`crawler.py`、`rss_crawler.py`、`webgui/app.py` 均改为调用上表函数，不再各自维护选择器列表/重试次数/校验松紧度。

### 扩展机制：`config.SITE_IMAGE_RULES`（用户本次明确要求的"固化+随测试推进逐步完善"）

通用兜底链路（RSS/正文已知图 → 回原文页抓 og:image → 内嵌失败时改交浏览器直载 → 分类 SVG 兜底）配合上面两个根因修复后，已能处理大多数情况。后续若发现**某个新站点的专属怪癖**（不是上面已修的通用问题），在 `config.py` 的 `SITE_IMAGE_RULES` 按信息源名称登记规则即可，不需要再改 `image_fetch.py` 或任何调用方：

```python
SITE_IMAGE_RULES = {
    "某站点": {"extra_selectors": ['meta[property="article:image"]']},  # 配图藏在非常规标签
    "某站点B": {"skip_page_scrape": True},                              # 已确认从不带可用配图，省一次必败请求
}
```

排查顺序：某站长期只出 SVG → 先查 `run.log`/`crawl.log` 里 `[图片]` 开头的 WARNING 定位是"找不到 URL"还是"下载/校验失败"环节 → 前者多半要加 `extra_selectors`，后者多半是该站本身的反爬/防盗链，可考虑 `skip_page_scrape` 或个例放弃。

## 2026-06-26 更新：站点兜底图（公司新闻室「找不到真图」时的品牌兜底）

用户反馈：部分资源站点（美光、三星等公司官方新闻室）经常抓不到真实配图，但不希望所有缺图文章都退到同一张分类 SVG，而是按站点要素特征各用一张固定的、版权安全的小图兜底。

### 为什么是 PNG 位图，不是 SVG

`wechat_render.render_wechat` 渲染微信图文时显式跳过 `data:image/svg`（公众号编辑器对内联 SVG 支持差）——即现有「分类 SVG 兜底」在微信图文里**根本不显示**。所以站点兜底图必须是**位图（PNG）**，才能在 微信图文 / 邮件 / 浏览器 / 离线 四个场景一致呈现。这也是本次没有沿用 SVG 的根本原因。

### 机制（三层兜底）

`reporter._resolve_image_src` 兜底链：**真实配图 → 公司源品牌兜底图 → 分类 SVG**。

- 登记：`config.SITE_IMAGE_RULES[源名]["fallback_image"]` = 相对本文件的图片路径（仅给公司新闻室配；媒体/中文聚合源不配，自动退回分类 SVG）；
- 读取：`image_fetch.source_fallback_image_uri(源名)` 读本地 PNG → Base64 data URI（`lru_cache`，同源只读一次盘）；文件缺失/格式不支持时返回空串、安全退回 SVG；
- `save_report` 把解析结果写回 `article["image_url"]`，故报告 HTML、报告 JSON、微信导出共享同一张兜底图。

### 兜底图怎么来的（满足「不涉及版权」）

`tools/build_source_logos.py`（构建期脚本，依赖 Pillow + requests，**不进 `requirements.txt`**）：

- 经 Wikimedia Commons API 读取每个候选文件的许可，**仅采用 Public Domain / CC0**（很多科技公司纯文字 wordmark 属 PD-textlogo）；需署名的 CC-BY 等、或无可用 PD 文件的公司，退回自制 wordmark（品牌色 + 公司名文字，纯排版不构成版权）；
- 版式：白底 + 品牌色边框 + logo 居中 + 底部品牌色条（公司名），640×360（16:9，cover 裁切时 logo 居中留白不被切）；
- 对 Commons/上传站经代理偶发的 TLS 连接重置（10054）与 429 限流，`_get` 带退避重试；
- 产物 `assets/source_logos/*.png` 提交进仓库（「固定下来」、每次结果一致），来源 URL 与许可写入同目录 `CREDITS.md` 供审计；
- 本轮 11 家（NVIDIA / Intel / AMD / Micron / Samsung / SK hynix / Synopsys / Broadcom / Qualcomm / TSMC / ASML）全部命中 Commons 的 Public Domain logo，无一退回 wordmark。

### 新增/更换某公司兜底图

在 `config.SITE_IMAGE_RULES` 给该源加 `fallback_image`，并在脚本 `COMPANIES` 加一条（slug / 品牌色 / Commons 候选文件名），重跑 `python tools/build_source_logos.py` 即可；没命中 PD 文件的公司会打印出来，按需补候选文件名后重跑。
