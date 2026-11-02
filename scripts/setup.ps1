$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appDir
$runtimeDir = Join-Path $appDir '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $runtimeDir 'deps'
$embeddedDir = Join-Path $runtimeDir 'python'
$embeddedPython = Join-Path $embeddedDir 'python.exe'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonPath = if (Test-Path -LiteralPath $embeddedPython) { $embeddedPython } elseif ($pythonCommand) { $pythonCommand.Source } else { $null }
if (-not $pythonPath) {
    Write-Host '首次准备：正在下载官方 Python 本地运行环境（约 11 MB）…'
    $archive = Join-Path $runtimeDir 'python-runtime.zip'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $embeddedDir -Force
    $pathConfig = Join-Path $embeddedDir 'python312._pth'
    @('python312.zip', '.', '..\..', '..\deps', 'import site') | Set-Content -LiteralPath $pathConfig -Encoding ASCII
    $pythonPath = $embeddedPython
    Write-Host '正在准备依赖安装工具…'
    $getPip = Join-Path $runtimeDir 'get-pip.py'
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getPip -UseBasicParsing
    & $pythonPath $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw '安装依赖工具失败。' }
}
Write-Host '正在准备本地识别、音频与 PDF 组件。依赖仅写入程序目录；此步不会下载剧集或上传素材。'
& $pythonPath -m pip install --target (Join-Path $runtimeDir 'deps') --upgrade --no-warn-script-location -r (Join-Path $appDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖准备失败，请检查网络后再次双击准备环境；上方保留了具体错误。' }
& $pythonPath -c 'import faster_whisper,imageio_ffmpeg,pypdf'
if ($LASTEXITCODE -ne 0) { throw '组件加载检查失败。' }
Write-Host '环境准备完成，可双击“启动字幕审查.cmd”。' -ForegroundColor Green
