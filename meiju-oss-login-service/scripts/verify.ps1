# 启动前后端后，一键验证完整链路：health → 登录 → 设备列表 → 控制 → 读回。
# 用法：powershell -File scripts/verify.ps1
param(
    [string]$Api = 'http://127.0.0.1:8080',
    [string]$Frontend = 'http://127.0.0.1:4300'
)

$ErrorActionPreference = 'Stop'
$Mobile = '13800138000'

function Get-Json([string]$Url, [hashtable]$Headers) {
    $h = if ($Headers) { $Headers } else { @{} }
    $r = Invoke-WebRequest -Uri $Url -Headers $h -UseBasicParsing -TimeoutSec 10
    $r.Content | ConvertFrom-Json
}

function Post-Json([string]$Url, $Body, [hashtable]$Headers) {
    $json = $Body | ConvertTo-Json -Depth 8 -Compress
    $h = @{ 'Content-Type' = 'application/json' }
    if ($Headers) { foreach ($k in $Headers.Keys) { $h[$k] = $Headers[$k] } }
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Post -Body $json -Headers $h -UseBasicParsing -TimeoutSec 10
    } catch {
        throw "请求失败 $Url ：$($_.Exception.Message)"
    }
    $r.Content | ConvertFrom-Json
}

# 期望失败的请求用它：直接返回 HTTP 状态码，不抛异常。
function Get-PostStatus([string]$Url, $Body, [hashtable]$Headers) {
    $json = $Body | ConvertTo-Json -Depth 8 -Compress
    $h = @{ 'Content-Type' = 'application/json' }
    if ($Headers) { foreach ($k in $Headers.Keys) { $h[$k] = $Headers[$k] } }
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Post -Body $json -Headers $h -UseBasicParsing -TimeoutSec 10
        return [int]$r.StatusCode
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        throw
    }
}

function Step([string]$Name, [scriptblock]$Action) {
    try {
        & $Action
        Write-Host "[PASS] $Name"
    } catch {
        Write-Host "[FAIL] $Name -> $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

Step '后端 /health 可达' { (Get-Json "$Api/health").ok | Out-Null }
Step '前端 / 可达' { $r = Invoke-WebRequest -Uri $Frontend -UseBasicParsing -TimeoutSec 10; if ($r.StatusCode -ne 200) { throw "status $($r.StatusCode)" } }

Step '请求验证码' { Post-Json "$Api/v1/login/request-sms" @{ mobile = $Mobile } | Out-Null }
Step '取本地验证码(LocalProvider 固定 123456)' { (Get-Json "$Api/v1/login/debug/local-code?mobile=$Mobile").code | Out-Null }

$token = $null
Step '登录（验证码路径）' {
    $resp = Post-Json "$Api/v1/login/verify-sms" @{ mobile = $Mobile; sms_code = '123456' }
    if (-not $resp.access_token) { throw '未返回 access_token' }
    $script:token = $resp.access_token
}
$auth = @{ Authorization = "Bearer $token" }

Step '密码登录' {
    $resp = Post-Json "$Api/v1/login/password" @{ mobile = $Mobile; password = '123456' }
    if (-not $resp.access_token) { throw '未返回 access_token' }
    $me = Get-Json "$Api/v1/me" -Headers @{ Authorization = "Bearer $($resp.access_token)" }
    if ($me.mobile -ne $Mobile) { throw "me.mobile=$($me.mobile)" }
}
Step '密码登录拒绝错误密码（应 400）' {
    $status = Get-PostStatus "$Api/v1/login/password" @{ mobile = $Mobile; password = 'definitely-wrong' }
    if ($status -ne 400) { throw "期望 400，实际 $status" }
}

$code = 0
Step '设备列表' {
    $resp = Get-Json "$Api/v1/user/device/list" -Headers $auth
    $count = @($resp.data.applianceList).Count
    if ($count -lt 1) { throw '设备数为 0' }
    $script:code = $resp.data.applianceList[0].applianceCode
    Write-Host "       共 $count 台设备，控制第一台 applianceCode=$code"
}
Step '控制（开电源 + 设温度 24）' {
    $resp = Post-Json "$Api/v1/thing/batch/properties" @{ applianceCode = $code; props = @(@{code='power';value='on'}, @{code='targetTemperature';value=24}) } -Headers $auth
    if ($resp.code -ne 0) { throw "code=$($resp.code) $($resp.msg)" }
}
Step '读回校验' {
    $resp = Get-Json "$Api/v1/thing/properties/$code" -Headers $auth
    if ($resp.data.props.power -ne 'on') { throw "power=$($resp.data.props.power)" }
    Write-Host "       power=$($resp.data.props.power) targetTemperature=$($resp.data.props.targetTemperature)"
}
Step '拒绝非法值（温控 99 应 400）' {
    $status = Get-PostStatus "$Api/v1/thing/batch/properties" @{ applianceCode = $code; props = @(@{code='targetTemperature';value=99}) } -Headers $auth
    if ($status -ne 400) { throw "期望 400，实际 $status" }
}

Write-Host ''
Write-Host '全部通过。打开前端：' $Frontend -ForegroundColor Green
