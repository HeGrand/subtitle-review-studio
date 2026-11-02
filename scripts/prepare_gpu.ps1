param([string]$Target = '')
$ErrorActionPreference = 'Stop'
$appDir = Split-Path -Parent $PSScriptRoot
if (-not $Target) { $Target = Join-Path $appDir '.runtime/gpu' }
if (Test-Path -LiteralPath $Target) {
    if (Get-ChildItem -LiteralPath $Target -Force | Select-Object -First 1) {
        throw 'GPU目录已有文件。请保留已有组件；若确需重建，先将旧目录改名，再运行此脚本，避免混装。'
    }
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Write-Host '正在准备本项目的NVIDIA运行组件。不修改系统CUDA或显卡驱动。'
python -m pip install --target $Target --only-binary=:all: --no-deps --no-compile --no-warn-script-location -r (Join-Path $appDir 'requirements-gpu.txt')
if ($LASTEXITCODE -ne 0) { throw 'GPU运行组件下载或准备失败。已准备的目录请保留检查；CPU仍可使用。' }
Write-Host 'GPU组件已准备。首次识别将实际测试GPU是否可用，并与CPU比较速度。'
