# 芯片行业资讯报告发布页

这个目录只用于 GitHub Pages 发布，包含静态首页和 HTML 报告文件。

## 发布步骤

1. 在 GitHub 新建一个公开仓库，例如 `chip-report-pages`。
2. 在本目录执行：

```powershell
git remote add origin https://github.com/<你的用户名>/chip-report-pages.git
git push -u origin main
```

3. 打开 GitHub 仓库的 `Settings -> Pages`。
4. 在 `Build and deployment` 中选择 `Deploy from a branch`。
5. Branch 选择 `main`，目录选择 `/root`，保存。

几分钟后页面地址通常是：

```text
https://<你的用户名>.github.io/chip-report-pages/
```

以后运行项目根目录的 `run_business.ps1` / `run_tech.ps1` 会自动复制最新 HTML、更新首页、提交并推送。

也可以手动发布：

```powershell
..\publish_pages.ps1 -Report business
..\publish_pages.ps1 -Report tech
```
