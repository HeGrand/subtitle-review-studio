# 从源码构建 Windows 便携包

构建需要 Windows x64、可用 Python、对应版本的依赖、本地 CTranslate2 Whisper 模型，以及合法可再发行的 x64 Visual C++ 运行组件。模型、依赖和第三方二进制均不存放在 Git 中。

## 1. 准备依赖和模型

```powershell
python -m pip install --target .runtime/deps -r requirements.txt
$env:PYTHONPATH = Join-Path (Get-Location) '.runtime/deps'
python -c "from faster_whisper.utils import download_model; print(download_model('large-v3-turbo', output_dir='.runtime/build-model'))"
```

也可以使用已有的 `large-v3-turbo` CTranslate2 模型目录，必须包含 `model.bin`、`config.json`、`tokenizer.json`、`vocabulary.json` 和 `preprocessor_config.json`。

## 2. 准备桌面运行组件

```powershell
npm ci --ignore-scripts
python scripts/prepare_desktop.py
```

使用官方Electron Windows组件，并验证官方SHA-256。构建会复制到便携包的`.runtime/desktop`，不会要求使用者安装Node.js。桌面窗口只加载本机页面，页面不开放Node权限，通过有限桥接读取用户拖入文件的真实路径。

## 3. 可选准备 GPU 组件

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prepare_gpu.ps1
```

脚本从 NVIDIA 在 PyPI 发布的 Windows 软件包准备应用本地运行库，固定版本见 `requirements-gpu.txt`。它们需要符合各自的再发行条款，许可证及来源信息随包保留。已有 GPU 目录时脚本会提示保留原组件，避免混装不同版本。

## 4. 构建独立目录

从 Visual Studio 的 `VC/Redist/MSVC/<版本>/x64` 目录选择 CRT 和 OpenMP 可再发行组件；不要从另一种架构或普通系统目录随意拷 DLL。

```powershell
python scripts/build_portable.py `
  --output ..\portable-build `
  --model .runtime/build-model `
  --vc-redist '<Visual Studio x64 Redist 目录>'
```

构建会复制当前 Python 的运行文件和标准库，使用相对 `_pth` 隔离系统 Python、注册表路径和用户 site-packages，并复制 `.runtime/deps`。模型缓存中的链接会转换为独立文件。存在 `.runtime/gpu` 时也会包含它。

如需完整英文文稿匹配包，使用已验证装有 WhisperX 3.2.0、faster-whisper 1.2.1 和 CPU PyTorch 的同版 Python 构建，追加 `--whisperx-checkpoint <本机的 wav2vec2_fairseq_base_ls960_asr_ls960.pth>`。构建会将这一组依赖与基础识别依赖分开放置，并保留组件版本及英文对齐模型许可。

只有构建产物会写到指定输出目录，不读取业务项目、最近项目或个人测速配置。

## 5. 验收并封包

建议把目录复制到新的中文/空格路径，用包内 Python 验证依赖、音频转换、离线模型推理、项目保存重开和页面启动。CPU/GPU 实机测试需要自己的合法音频样本；不要使用业务项目作为构建输入。

```powershell
python scripts/package_portable.py `
  --root '..\portable-build\字幕工作室_Windows便携版' `
  --zip '..\portable-build\SubtitleReviewStudio-full.zip'
```

完整 GPU 包可能大于 GitHub 单附件限制。发布到 Releases 时使用按功能拆分的封包方式：

```powershell
python scripts/package_release.py `
  --root '..\portable-build\字幕工作室_Windows便携版' `
  --output ..\release-assets
```

会生成 CPU 可用的主程序包、可选 GPU 和英文 WhisperX 组件包、文件校验和使用说明。每个附件都会检查小于 2 GiB。将所需组件解压到含启动器的主程序目录，合并 `.runtime` 后即可使用。

仓库中的 `release-portable.yml` 是手动运行的发布工作流，不会在每次提交时构建大模型包。它在 GitHub Windows runner 上准备组件、运行检查、创建草稿 release，附件齐全后才公开发布。
