<#
.SYNOPSIS
    半导体行业技术周报 — 自动化生成脚本

.DESCRIPTION
    默认：Python 爬虫 → Python 编排批量评分/摘要/周总结 → 渲染 HTML 报告。
    Token 统计由 analyzer.py 在运行时捕获 DeepSeek API usage 并写入报告 metadata。
    -codex：旧式 agent 模式，额外解析 CLI 日志 token 统计。

    调用方式：
      .\run_tech.ps1
      .\run_tech.ps1 -NoPublish
      .\run_tech.ps1 -codex
    或计划任务：
      pwsh -NonInteractive -File "C:\...\run_tech.ps1"
#>

param(
    [switch]$codex,
    [switch]$NoPublish
)

$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot
Push-Location $proj

try {
    # 从用户级注册表继承环境变量，兼容"新会话未继承"和"计划任务"场景
    foreach ($var in @('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL_FAST','LLM_PROVIDER','LLM_SCORING_BATCH_SIZE')) {
        if (-not (Get-Item "Env:$var" -ErrorAction SilentlyContinue)) {
            $val = [System.Environment]::GetEnvironmentVariable($var, 'User')
            if ($val) { Set-Item "Env:$var" $val }
        }
    }

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding = $utf8NoBom
    [Console]::OutputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:LANG = "C.UTF-8"
    $env:LC_ALL = "C.UTF-8"

    $ts = { (Get-Date -Format 'HH:mm:ss') }

    function Publish-Report {
        if ($NoPublish) {
            Write-Host "$(& $ts) [Publish] 已跳过发布（-NoPublish）"
            return
        }

        Write-Host "$(& $ts) [Pages] 更新并推送 GitHub Pages..."
        & (Join-Path $proj "publish_pages.ps1") -Report tech
        if ($LASTEXITCODE -ne 0) { throw "GitHub Pages 发布失败 (exit $LASTEXITCODE)" }
    }

    Write-Host "$(& $ts) ===== 技术周报生成开始 ====="

    if (-not $codex) {
        Write-Host "$(& $ts) [Python] 爬取并通过轻量 LLM 接口分析..."
        python main.py --report tech
        if ($LASTEXITCODE -ne 0) { throw "Python 技术周报流程失败 (exit $LASTEXITCODE)" }
        Write-Host "$(& $ts) ===== 技术周报生成完成 ====="
        Publish-Report
        return
    }

    # Step 1: 爬取（7 天时效，技术版每周跑）
    Write-Host "$(& $ts) [Step 1] 爬取原始文章（7 天窗口）..."
    python crawl_only.py --age-days 7
    if ($LASTEXITCODE -ne 0) { throw "crawl_only.py 失败 (exit $LASTEXITCODE)" }

    # Step 2: Codex agent 分析（仅 -codex 手动模式保留；默认不再使用代理式整包处理）
    $prompt = "请读取 prompts/analyze_tech.md 并严格按其中的步骤执行，生成本周半导体行业技术周报。"
    New-Item -ItemType Directory -Force -Path (Join-Path $proj "output") | Out-Null
    $llmLog = Join-Path $proj ("output\llm_tech_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    if ($codex) {
        Write-Host "$(& $ts) [Step 2] 唤醒 Codex 进行分析..."
        $promptBody = Get-Content -LiteralPath (Join-Path $proj "prompts\analyze_tech.md") -Raw -Encoding UTF8
        $codexPrompt = @"
你正在 Windows PowerShell 环境中运行。所有中文文件必须按 UTF-8 读取和写入。
不要再用 shell 读取 prompts/analyze_tech.md；完整任务说明已经粘贴在下方。
读取 output/raw_articles.json / output/seen_tech.json / 写入 output/analyzed_results.json 时，优先使用 Python 并显式指定 encoding="utf-8"。
请严格执行下方任务说明，只写 output/analyzed_results.json，不要生成 HTML。

$promptBody
"@
        $codexPrompt | & codex exec --json --skip-git-repo-check -C $proj --sandbox danger-full-access - 2>&1 | Tee-Object -FilePath $llmLog
    } else {
        Write-Host "$(& $ts) [Step 2] 唤醒 Claude Code 进行分析..."
        & claude --model claude-haiku-4-5-20251001 -p --output-format json $prompt 2>&1 | Tee-Object -FilePath $llmLog
    }
    $llmExitCode = $LASTEXITCODE
    $llmName = if ($codex) { "Codex" } else { "Claude Code" }
    if ($llmExitCode -ne 0) { throw "$llmName 分析失败 (exit $llmExitCode)" }

    Write-Host "$(& $ts) [Step 3] 解析 LLM token 统计..."
    python llm_metadata.py --executor $llmName --log $llmLog --out output/llm_run_metadata.json
    if ($LASTEXITCODE -ne 0) { throw "llm_metadata.py 失败 (exit $LASTEXITCODE)" }

    Write-Host "$(& $ts) [Step 4] 渲染 HTML 报告..."
    python finalize_report.py output/analyzed_results.json
    if ($LASTEXITCODE -ne 0) { throw "finalize_report.py 失败 (exit $LASTEXITCODE)" }

    Write-Host "$(& $ts) ===== 技术周报生成完成 ====="
    Publish-Report
}
catch {
    Write-Host "ERROR: $_"
    exit 1
}
finally {
    Pop-Location
}
