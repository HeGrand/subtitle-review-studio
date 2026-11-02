param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appDir
$env:PYTHONPATH = Join-Path $appDir '.runtime\deps'
$env:PYTHONUTF8 = '1'
$embeddedPython = Join-Path $appDir '.runtime\python\python.exe'
$isPortable = Test-Path -LiteralPath (Join-Path $appDir 'portable.json')
if ($isPortable) {
    if (-not (Test-Path -LiteralPath $embeddedPython)) { throw '便携运行环境不完整，请先完整解压压缩包再启动。' }
    $env:HF_HUB_OFFLINE = '1'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:PYTHONNOUSERSITE = '1'
}
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonPath = if (Test-Path -LiteralPath $embeddedPython) { $embeddedPython } elseif ($pythonCommand) { $pythonCommand.Source } else { $null }
if (-not $pythonPath) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
    $pythonPath = $embeddedPython
}
# Reuse only our own live server, identified by its PID and local address.
$serverFile = Join-Path $appDir '.runtime\server.json'
if (Test-Path -LiteralPath $serverFile) {
    try {
        $serverInfo = Get-Content -LiteralPath $serverFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $serverProcess = Get-Process -Id $serverInfo.pid -ErrorAction Stop
        if ($serverProcess.ProcessName -like 'python*' -and $serverInfo.url -match '^http://127\.0\.0\.1:\d+/$') {
            $response = Invoke-WebRequest -Uri $serverInfo.url -UseBasicParsing -TimeoutSec 2
            if ($response.Content -match 'LOCAL REVIEW STUDIO') { if (-not $NoBrowser) { Start-Process $serverInfo.url }; exit 0 }
        }
    } catch { }
}
& $pythonPath -c 'import faster_whisper,imageio_ffmpeg,pypdf' 2>$null
if ($LASTEXITCODE -ne 0) {
    if ($isPortable) { throw '便携组件不完整，请完整解压到可写目录；未尝试安装或下载。' }
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { throw '环境准备失败，请查看上方原因。' }
}
if ($isPortable) {
    $bundledModel = Get-ChildItem -LiteralPath (Join-Path $appDir '.runtime/models') -Filter 'model.bin' -Recurse -File -ErrorAction SilentlyContinue
    if (-not $bundledModel) { throw '便携识别模型缺失，请重新完整解压压缩包；未尝试联网下载。' }
}
Write-Host '字幕审查正在本机运行。请保留此窗口；结束时先在页面保存并关闭项目，再关闭此窗口。' -ForegroundColor Green
if ($NoBrowser) { & $pythonPath -m app.server --no-browser } else { & $pythonPath -m app.server }
exit $LASTEXITCODE
