$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
$serverFile = Join-Path $appDir '.runtime\server.json'
if (Test-Path -LiteralPath $serverFile) {
    $info = Get-Content -LiteralPath $serverFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($info.url -notmatch '^http://127\.0\.0\.1:\d+/$') { throw '服务地址不正确，未停止任何程序。' }
    $live = $false
    try {
        $page = Invoke-WebRequest -Uri $info.url -UseBasicParsing -TimeoutSec 2
        $live = $page.Content -match 'LOCAL REVIEW STUDIO'
    } catch { }
    if ($live) {
        Write-Host '请先在页面保存并关闭项目，然后关闭本应用的全部网页。正在等待释放页面会话（约35秒）…' -ForegroundColor Yellow
        $tokenMatch = [regex]::Match($page.Content, 'name="app-token" content="([^"]+)"')
        if (-not $tokenMatch.Success) { throw '无法核对本应用，未停止任何程序。' }
        $headers = @{ 'X-App-Token' = $tokenMatch.Groups[1].Value; 'X-Client' = [guid]::NewGuid().ToString() }
        $claimed = $false
        for ($attempt = 0; $attempt -lt 25; $attempt++) {
            try {
                $null = Invoke-RestMethod -Uri ($info.url + 'api/state') -Headers $headers -TimeoutSec 3
                $claimed = $true
                break
            } catch { Start-Sleep -Seconds 2 }
        }
        if (-not $claimed) { throw '页面仍在使用中。请关闭本应用的全部网页后再次双击重新启动；未停止程序或覆盖进度。' }
        $null = Invoke-RestMethod -Uri ($info.url + 'api/pause') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 30
        $null = Invoke-RestMethod -Uri ($info.url + 'api/close') -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 30
        $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($info.pid)"
        if (-not $serverProcess -or $serverProcess.CommandLine -notmatch '\bapp\.server\b') { throw '未能核对服务进程；项目已保存，但未强制结束其他程序。' }
        Stop-Process -Id $info.pid -ErrorAction Stop
        Write-Host '项目已保存，正在启动更新后的程序。' -ForegroundColor Green
    }
}
& (Join-Path $PSScriptRoot 'launch.ps1')
