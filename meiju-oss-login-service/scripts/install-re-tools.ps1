# 安装逆向所需工具：JDK + JADX + Ghidra（均走 scoop）。
#   JADX   -> 反编译 DEX/APK 到 Java
#   Ghidra -> 反汇编 native .so（JNI/C++）
# 用法：powershell -File scripts/install-re-tools.ps1
$ErrorActionPreference = 'Stop'

if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
    throw '未找到 scoop。先安装 scoop，或改用 zip 方式手动安装 JDK/JADX/Ghidra。'
}

function Ensure-Bucket([string]$name) {
    $buckets = scoop bucket list 2>$null
    if ($buckets -match "\b$name\b") {
        Write-Host "  $name bucket 已存在"
    } else {
        scoop bucket add $name
    }
}

Write-Host '== 1/6 java bucket ==' -ForegroundColor Cyan
Ensure-Bucket 'java'

Write-Host '== 2/6 JADX 用的 JDK 17 ==' -ForegroundColor Cyan
if (Test-Path 'C:\Scoop\apps\temurin17-jdk\current\bin\java.exe') {
    Write-Host '  temurin17-jdk 已安装'
} else {
    scoop install java/temurin17-jdk
}

Write-Host '== 3/6 Ghidra 用的 JDK 25（Ghidra 12.x 要求 25+）==' -ForegroundColor Cyan
if (Test-Path 'C:\Scoop\apps\temurin25-jdk\current\bin\java.exe') {
    Write-Host '  temurin25-jdk 已安装'
} else {
    scoop install java/temurin25-jdk
}

Write-Host '== 4/6 JADX ==' -ForegroundColor Cyan
if (Get-Command jadx -ErrorAction SilentlyContinue) {
    Write-Host "  已存在：$((Get-Command jadx).Source)"
} else {
    scoop install extras/jadx
}

Write-Host '== 5/6 Ghidra ==' -ForegroundColor Cyan
if (Test-Path 'C:\Scoop\apps\ghidra\current\support\analyzeHeadless.bat') {
    Write-Host '  Ghidra 已安装'
} else {
    scoop install extras/ghidra
}

Write-Host '== 6/6 验证 ==' -ForegroundColor Cyan
$env:JAVA_HOME = 'C:\Scoop\apps\temurin25-jdk\current'
$env:PATH = "C:\Scoop\apps\temurin25-jdk\current\bin;C:\Scoop\apps\ghidra\current\support;$env:PATH"
$env:GHIDRA_INSTALL_DIR = 'C:\Scoop\apps\ghidra\current'
& "$env:JAVA_HOME\bin\java.exe" -version 2>&1 | Select-Object -First 1
& jadx --version 2>&1 | Select-Object -First 1
Write-Host "  GHIDRA_INSTALL_DIR=$env:GHIDRA_INSTALL_DIR"

Write-Host ''
Write-Host '注意：' -ForegroundColor Yellow
Write-Host '  * JADX 默认写 %APPDATA%\skylot；受限环境请设 JADX_CONFIG_DIR / JADX_CACHE_DIR' -ForegroundColor Yellow
Write-Host '  * Ghidra 需要 JDK 25+，首次运行要写 %APPDATA%\ghidra' -ForegroundColor Yellow
Write-Host '卸载：powershell -File scripts/uninstall-re-tools.ps1' -ForegroundColor Yellow
