# 芯片行业资讯自动化工具

多信息源采集（RSS + HTML 爬虫）后，通过 LLM 评分、摘要并生成两类 HTML 报告：

- 商业动态日报
- 技术周报

详细 agent/项目说明见 [CLAUDE.md](CLAUDE.md)。

## 快速开始

```powershell
pip install -r requirements.txt
copy .env.example .env
```

在 `.env` 或系统环境变量中配置 `DEEPSEEK_API_KEY` 后运行：

```powershell
.\run_business.ps1
.\run_tech.ps1
```

默认会在报告生成后调用 `publish_pages.ps1`，把最新 HTML 推送到 GitHub Pages 发布仓库。只想本地生成时使用：

```powershell
.\run_business.ps1 -NoPublish
.\run_tech.ps1 -NoPublish
```

## 安全说明

真实 API key 不应写入源码。请使用环境变量或本地 `.env` 文件；`.env` 已加入 `.gitignore`。
