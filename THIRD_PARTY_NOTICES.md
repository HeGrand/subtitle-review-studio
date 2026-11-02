# Third-party components

The MIT license in this repository applies to the project's original code. Third-party software, models, and redistributable binaries remain subject to their own licenses.

- **CPython** — Python Software Foundation license; [source and license](https://github.com/python/cpython). Portable distributions retain `LICENSE.txt` with the runtime.
- **faster-whisper** — MIT; [project](https://github.com/SYSTRAN/faster-whisper).
- **CTranslate2** — MIT; [project](https://github.com/OpenNMT/CTranslate2).
- **Whisper model** — original Whisper code and weights are MIT; [license](https://github.com/openai/whisper/blob/main/LICENSE). The initial portable model uses the [mobiuslabsgmbh faster-whisper-large-v3-turbo conversion](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo). Keep its source information and license when redistributing it.
- **imageio-ffmpeg / FFmpeg** — imageio-ffmpeg is BSD-2-Clause; the bundled FFmpeg binary has its own build configuration and license. See [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) and the notices included in its wheel. Preserve those notices with binary distributions.
- **pypdf** — BSD-3-Clause; [project](https://github.com/py-pdf/pypdf).
- **NumPy, PyAV, ONNX Runtime, tokenizers and other dependencies** — retain package metadata and licenses from each installed wheel. Portable builds include a component-version inventory.
- **Microsoft Visual C++ runtime** — Microsoft redistributable terms; builders must use the appropriate x64 redistributable components rather than development tools or arbitrary system DLLs.
- **NVIDIA CUDA / cuDNN runtime** — NVIDIA terms; optional GPU bundles retain CUDA and cuDNN license documents. [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html), [cuDNN EULA](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html).
- **Playwright** — Apache-2.0; optional development test dependency, not required by the app.

This source repository does not store third-party models, Python distributions, NVIDIA libraries, media, or project data in Git. Prepared portable packages carry the relevant notices alongside their runtime components.
