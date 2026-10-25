# 停止占用 8080 / 4300 的进程。
# 用法：powershell -File scripts/stop.ps1
$ports = 8080, 4300

foreach ($port in $ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Host "端口 $port 未占用"
        continue
    }
    foreach ($procId in ($connections.OwningProcess | Select-Object -Unique)) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        Write-Host ("停止端口 {0}: pid={1} {2}" -f $port, $procId, $proc.ProcessName)
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Write-Host '完成。'
