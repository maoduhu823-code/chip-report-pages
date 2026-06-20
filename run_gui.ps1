<#
.SYNOPSIS
    启动芯片资讯轻量控制台（本地 Flask Web 应用）。

.DESCRIPTION
    控制台 = 运行参数控制面板 + 评审看板 + 导出分享。
    继承用户级环境变量（DeepSeek Key 等），使从控制台触发的运行也能正常调用 LLM。
    默认监听 127.0.0.1:5000（仅本机）。

    用法：
      .\run_gui.ps1
      .\run_gui.ps1 -Port 5050
#>

param(
    [int]$Port = 5000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot
Push-Location $proj

try {
    foreach ($var in @('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL_FAST','LLM_PROVIDER','LLM_SCORING_BATCH_SIZE','ANTHROPIC_API_KEY')) {
        if (-not (Get-Item "Env:$var" -ErrorAction SilentlyContinue)) {
            $val = [System.Environment]::GetEnvironmentVariable($var, 'User')
            if ($val) { Set-Item "Env:$var" $val }
        }
    }

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $utf8NoBom
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:GUI_HOST = $BindHost
    $env:GUI_PORT = "$Port"

    python -m webgui
}
finally {
    Pop-Location
}
