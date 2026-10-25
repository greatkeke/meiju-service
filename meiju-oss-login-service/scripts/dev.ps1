# 启动后端（uv）+ 前端静态服务，各开一个独立窗口，关窗口即停止。
# 用法：
#   powershell -File scripts/dev.ps1          # 本地模拟（LocalProvider）
#   powershell -File scripts/dev.ps1 -Real    # 真实环境（MideaProvider，走美的网关）
param([switch]$Real)

$ErrorActionPreference = 'Stop'

if ($Real) {
    # 真实模式：通过环境变量注入（子进程继承）。已设的值不覆盖。
    if (-not $env:MIDEA_SECRET) { $env:MIDEA_SECRET = 'prod_secret123@muc' }
    if (-not $env:MIDEA_BASE) { $env:MIDEA_BASE = 'https://mp-prod.smartmidea.net/mas/v5/app/proxy' }
    $env:MIDEA_PROVIDER = 'midea'
    Write-Host ''
    Write-Host '=== 真实环境模式 MIDEA_PROVIDER=midea ===' -ForegroundColor Yellow
    Write-Host "  网关    $($env:MIDEA_BASE)"
    Write-Host "  签名密钥 $($env:MIDEA_SECRET)（来源 libKwProtectSDK.so，PRO 版）"
    if ($env:MIDEA_IOT_KEY) {
        Write-Host "  IOT_KEY $($env:MIDEA_IOT_KEY)" -ForegroundColor Yellow
        Write-Host '  → 密码登录输入齐备：先 /v1/user/login/id/get 取 loginId，再打 /v6/mj/user/login' -ForegroundColor Yellow
    } else {
        Write-Host '  IOT_KEY 未设置 → 密码登录返回 501，不会向网关发送任何密码请求' -ForegroundColor Yellow
        Write-Host '  生产值已从 libKwProtectSDK.so 还原，需要时显式启用（不建议默认开启）：' -ForegroundColor Yellow
        Write-Host '    $env:MIDEA_IOT_KEY="PROD_VnoClJI9aikS8dyy"; powershell -File scripts\dev.ps1 -Real' -ForegroundColor DarkGray
    }
    Write-Host ''
}

$backend = Split-Path -Parent $PSScriptRoot          # meiju-oss-login-service
$web = Join-Path (Split-Path -Parent $backend) 'meiju-web'

# Windows PowerShell 5.1 没有 pwsh，按可用性挑一个。
$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
$shellExe = if ($pwshCmd) { $pwshCmd.Source } else { (Get-Process -Id $PID).Path }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv 未安装，先执行: pipx install uv'
}

# 默认缓存目录若不可写（例如受限 shell），退到工作区内，避免 "Failed to initialize cache"。
if (-not $env:UV_CACHE_DIR) {
    $candidate = Join-Path (Split-Path -Parent $backend) '.uv-cache'
    try {
        $probe = Join-Path $candidate '.probe'
        New-Item -ItemType Directory -Path $candidate -Force -ErrorAction Stop | Out-Null
        Set-Content -Path $probe -Value 'ok' -ErrorAction Stop
        Remove-Item -Path $probe -Force -ErrorAction Stop
        $env:UV_CACHE_DIR = $candidate
    } catch {
        # 不可写就维持 uv 默认行为
    }
}

if (-not (Test-Path (Join-Path $backend '.venv'))) {
    Write-Host '首次运行，uv sync 安装依赖…'
    Push-Location $backend
    try { uv sync } finally { Pop-Location }
}

$dist = Join-Path $web 'dist\meiju-web\browser'
if (-not (Test-Path $dist)) {
    Write-Host "未找到构建产物 $dist"
    Write-Host "请先执行: cd $web; npm install --ignore-scripts; npx ng build --configuration development"
    throw '前端未构建'
}

Write-Host '启动后端 http://127.0.0.1:8080 …'
Start-Process $shellExe -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location '$backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8080"
) | Out-Null

Write-Host '启动前端 http://127.0.0.1:4300 …'
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
$pyStart = if ($pyCmd) { "& `"$($pyCmd.Source)`" -m http.server" } else { "uv run --no-project python -m http.server" }
Start-Process $shellExe -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location '$dist'; $pyStart 4300 --bind 127.0.0.1"
) | Out-Null

Write-Host ''
Write-Host '前端  http://127.0.0.1:4300/'
Write-Host '后端  http://127.0.0.1:8080/docs'
Write-Host '健康检查：Invoke-WebRequest http://127.0.0.1:8080/health'
Write-Host '关闭弹出的两个窗口即可停止服务（或执行 scripts\stop.ps1）。'
