# 开发与贡献

欢迎提交问题和改进。请使用合成字幕、公开样例或已经获得分享许可的材料复现问题，不要附带业务项目、账号、真实剧本或私人视频。

## 运行检查

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node --check web/app.js
```

单元测试不加载识别模型。临时项目保存在系统临时目录，可通过 `REVIEW_TEST_ROOT` 指定另一位置。

浏览器回归使用合成字幕和合成音频：

```powershell
python -m pip install -r requirements.txt
npm install
npx playwright install chromium
npm run test:ui
```

## 手工验收入口

- `tests/benchmark_devices.py --model <本地模型> --audio <16k单声道WAV> --output <新的输出目录>`：同一完整音频的 CPU/GPU 顺序对照。
- `tests/automatic_device_check.py`：独立项目中的真实自动选路与人工进度保护，需要可用 NVIDIA GPU。
- `tests/portable_smoke.py --app <便携目录> --work <新的输出目录>`：使用便携 Python 执行，验证依赖、模型推理、项目保存和启动脚本。
- `node tests/portable_ui_check.cjs <便携目录> <新的输出目录>`：真实浏览器检查便携页面。

请提供自己的测试音频和独立输出目录。这些入口不会自带或寻找任何特定剧集。

## 修改原则

- 原始 SRT、媒体、稳定 ID、人工确认、草稿和备注需要得到保护。
- 修改字幕时间只影响字幕，不裁剪媒体、不平移后续条目。
- “机器候选”“完成文字对照”“实际试听”“人工确认”需要分开记录。
- GPU 测速与推理失败应能回退，暂停和继续不能丢失已完成分段。
- 修改导出/导入字段时，同时更新 `app/resources` 内的 AI 流程和回传契约。
- 不将 `.runtime`、模型、媒体、真实项目或个人测速数据提交到 Git。
