<#
.SYNOPSIS
    将最新 HTML 报告发布到 GitHub Pages 仓库。

.DESCRIPTION
    从 output/ 复制最新商业日报或技术周报到 github_pages_site/reports/，
    重新生成 GitHub Pages 首页，提交并推送，然后输出可分享链接。

    首次使用前，需在 github_pages_site 中配置 GitHub 远端：
      git -C github_pages_site remote add origin https://github.com/<用户名>/<仓库名>.git
#>

param(
    [ValidateSet("business", "tech", "both")]
    [string]$Report = "both",

    [string]$PagesDir = (Join-Path $PSScriptRoot "github_pages_site"),
    [string]$OutputDir = (Join-Path $PSScriptRoot "output"),

    # 可手动覆盖 Pages 地址，例如：https://corgi-mao.github.io/chip-report-pages/
    [string]$BaseUrl = $env:PAGES_BASE_URL,

    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Write-Step([string]$Message) {
    Write-Host "$(Get-Date -Format 'HH:mm:ss') [Pages] $Message"
}

function Ensure-GitRepo([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Path ".git"))) {
        & git -C $Path init -b main | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "初始化 GitHub Pages 仓库失败" }
    }
}

function Get-BaseUrlFromRemote([string]$Path) {
    if ($BaseUrl) {
        if ($BaseUrl.EndsWith("/")) { return $BaseUrl }
        return "$BaseUrl/"
    }

    $remote = & git -C $Path remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $remote) { return "" }

    $text = ($remote | Select-Object -First 1).Trim()
    if ($text -match "github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$") {
        $owner = $Matches[1]
        $repo = $Matches[2]
        if ($repo -ieq "$owner.github.io") {
            return "https://$owner.github.io/"
        }
        return "https://$owner.github.io/$repo/"
    }
    return ""
}

function Get-ReportFilesToPublish([string]$Kind) {
    $patterns = @()
    if ($Kind -in @("business", "both")) { $patterns += "daily_*_business.html" }
    if ($Kind -in @("tech", "both")) { $patterns += "weekly_*_tech.html" }

    foreach ($pattern in $patterns) {
        $file = Get-ChildItem -LiteralPath $OutputDir -Filter $pattern -File |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if (-not $file) {
            throw "未找到可发布的报告：$pattern"
        }
        $file
    }
}

function Get-ReportInfo([System.IO.FileInfo]$File) {
    $name = $File.Name
    $title = "报告"
    $kindOrder = 9
    if ($name -like "daily_*_business.html") {
        $title = "商业动态日报"
        $kindOrder = 1
    } elseif ($name -like "weekly_*_tech.html") {
        $title = "技术周报"
        $kindOrder = 2
    }

    $date = ""
    $dateValue = [datetime]::MinValue
    if ($name -match "(\d{4}-\d{2}-\d{2})") {
        $date = $Matches[1]
        [datetime]::TryParse($date, [ref]$dateValue) | Out-Null
    }

    [pscustomobject]@{
        Name = $name
        Title = $title
        Date = $date
        DateValue = $dateValue
        KindOrder = $kindOrder
        SizeMb = [Math]::Round($File.Length / 1MB, 2)
    }
}

function New-IndexHtml([string]$ReportsDir) {
    $reports = Get-ChildItem -LiteralPath $ReportsDir -Filter "*.html" -File |
        ForEach-Object { Get-ReportInfo $_ } |
        Sort-Object @{ Expression = "DateValue"; Descending = $true }, KindOrder, Name

    $items = foreach ($report in $reports) {
        $href = "reports/$([System.Net.WebUtility]::HtmlEncode($report.Name))"
        $title = [System.Net.WebUtility]::HtmlEncode($report.Title)
        $date = [System.Net.WebUtility]::HtmlEncode($report.Date)
        $name = [System.Net.WebUtility]::HtmlEncode($report.Name)
        $size = [System.Net.WebUtility]::HtmlEncode("$($report.SizeMb) MB")
@"
        <a class="report" href="$href">
          <div class="report-title">$title</div>
          <div class="report-meta">$date · $name · $size</div>
        </a>
"@
    }

    $generatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
@"
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>芯片行业资讯报告</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      color: #202532;
      background: #f3f5f8;
      line-height: 1.6;
    }
    header {
      background: #14233f;
      color: white;
      padding: 36px 24px 30px;
    }
    .wrap {
      max-width: 880px;
      margin: 0 auto;
    }
    h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 700;
    }
    .subtitle {
      margin-top: 8px;
      color: rgba(255, 255, 255, .72);
      font-size: 14px;
    }
    main {
      padding: 28px 24px 44px;
    }
    .report-list {
      display: grid;
      gap: 14px;
    }
    .report {
      display: block;
      padding: 18px 20px;
      border: 1px solid #dde2ea;
      border-radius: 8px;
      background: white;
      color: inherit;
      text-decoration: none;
      box-shadow: 0 1px 4px rgba(20, 35, 63, .06);
    }
    .report:hover {
      border-color: #2f6feb;
      box-shadow: 0 5px 18px rgba(20, 35, 63, .12);
    }
    .report-title {
      font-size: 16px;
      font-weight: 700;
      color: #14233f;
    }
    .report-meta {
      margin-top: 4px;
      color: #697386;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    footer {
      margin-top: 22px;
      color: #697386;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>芯片行业资讯报告</h1>
      <div class="subtitle">GitHub Pages 静态分享页</div>
    </div>
  </header>
  <main>
    <div class="wrap">
      <div class="report-list">
$($items -join "`n")
      </div>
      <footer>报告由本地自动化流程生成；最后更新：$generatedAt</footer>
    </div>
  </main>
</body>
</html>
"@
}

function Push-PagesRepo([string]$Path) {
    $remote = & git -C $Path remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $remote) {
        throw "github_pages_site 尚未配置 GitHub 远端。请先执行：git -C `"$Path`" remote add origin https://github.com/<用户名>/<仓库名>.git"
    }

    $branch = (& git -C $Path branch --show-current).Trim()
    if (-not $branch) { $branch = "main" }

    $upstream = & git -C $Path rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $upstream) {
        & git -C $Path push | Out-Host
    } else {
        & git -C $Path push -u origin $branch | Out-Host
    }
    if ($LASTEXITCODE -ne 0) { throw "推送 GitHub Pages 仓库失败" }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "未找到 git 命令"
}
if (-not (Test-Path -LiteralPath $OutputDir)) {
    throw "未找到输出目录：$OutputDir"
}

Ensure-GitRepo $PagesDir

$reportsDir = Join-Path $PagesDir "reports"
New-Item -ItemType Directory -Force -Path $reportsDir | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $PagesDir ".nojekyll") | Out-Null

$files = @(Get-ReportFilesToPublish $Report)
$publishedLinks = @()
$publicBaseUrl = Get-BaseUrlFromRemote $PagesDir

foreach ($file in $files) {
    $dest = Join-Path $reportsDir $file.Name
    Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
    Write-Step "已复制 $($file.Name)"
    if ($publicBaseUrl) {
        $publishedLinks += "$publicBaseUrl$([uri]::EscapeDataString("reports/$($file.Name)").Replace('%2F', '/'))"
    }
}

$indexHtml = New-IndexHtml $reportsDir
[System.IO.File]::WriteAllText((Join-Path $PagesDir "index.html"), $indexHtml, $utf8NoBom)

& git -C $PagesDir add . | Out-Host
if ($LASTEXITCODE -ne 0) { throw "git add 失败" }

$status = & git -C $PagesDir status --short
if ($status) {
    $message = "Publish reports $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    & git -C $PagesDir commit -m $message | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "git commit 失败" }
    Write-Step "已提交 GitHub Pages 更新"
} else {
    Write-Step "没有新的 Pages 文件变更"
}

if ($NoPush) {
    Write-Step "已跳过推送（-NoPush）"
} else {
    Push-PagesRepo $PagesDir
    Write-Step "已推送到 GitHub"
}

if ($publicBaseUrl) {
    Write-Host ""
    Write-Host "GitHub Pages 首页：$publicBaseUrl"
    foreach ($link in $publishedLinks) {
        Write-Host "报告链接：$link"
    }
} else {
    Write-Host ""
    Write-Host "GitHub Pages 首页：远端配置后可自动推断；也可设置 PAGES_BASE_URL。"
    foreach ($file in $files) {
        Write-Host "本地报告：$(Join-Path $reportsDir $file.Name)"
    }
}
