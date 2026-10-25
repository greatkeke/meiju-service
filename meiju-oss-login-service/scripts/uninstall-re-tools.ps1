# 卸载 install-re-tools.ps1 安装的逆向工具，并清理环境变量与临时目录。
# 用法：powershell -File scripts/uninstall-re-tools.ps1
#       powershell -File scripts/uninstall-re-tools.ps1 -KeepJavaBucket
param([switch]$KeepJavaBucket)

$ErrorActionPreference = 'Continue'

if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
    Write-Host '未找到 scoop，跳过 scoop 相关卸载。' -ForegroundColor Yellow
    return
}

Write-Host '== 卸载 Ghidra ==' -ForegroundColor Cyan
scoop uninstall ghidra

Write-Host '== 卸载 JADX ==' -ForegroundColor Cyan
scoop uninstall jadx

Write-Host '== 卸载 JDK ==' -ForegroundColor Cyan
foreach ($jdk in 'temurin25-jdk', 'temurin21-jdk', 'temurin17-jdk') {
    scoop uninstall $jdk
}

if (-not $KeepJavaBucket) {
    Write-Host '== 移除 java bucket ==' -ForegroundColor Cyan
    scoop bucket rm java
} else {
    Write-Host '保持 java bucket（-KeepJavaBucket）'
}

Write-Host '== 清理环境变量 ==' -ForegroundColor Cyan
foreach ($name in 'JAVA_HOME', 'GHIDRA_INSTALL_DIR') {
    $current = [Environment]::GetEnvironmentVariable($name, 'User')
    if ($current -and $current -like '*Scoop\apps\*') {
        [Environment]::SetEnvironmentVariable($name, $null, 'User')
        Write-Host "  已清除用户级 $name（原值 $current）"
    } else {
        Write-Host "  未改动 $name（当前值：$current）"
    }
}

Write-Host '== 清理仓库内临时目录 ==' -ForegroundColor Cyan
$repo = Split-Path -Parent $PSScriptRoot
$analysis = Join-Path (Split-Path -Parent $repo) 'meiju-analysis'
foreach ($dir in @(
        (Join-Path $analysis '.jadx-config'),
        (Join-Path $analysis '.jadx-cache'),
        (Join-Path $analysis 'ghidra-proj'))) {
    if (Test-Path $dir) {
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        Write-Host "  已删除 $dir"
    }
}

Write-Host ''
Write-Host '完成。检查残留：' -ForegroundColor Green
Get-Command java -ErrorAction SilentlyContinue | ForEach-Object { "  java: $($_.Source)" }
Get-Command jadx -ErrorAction SilentlyContinue | ForEach-Object { "  jadx: $($_.Source)" }
Write-Host '（无输出即已清理干净；新开终端再验证 PATH。）'
