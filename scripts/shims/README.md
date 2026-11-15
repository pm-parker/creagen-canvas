# shims/ — 本地推理占位脚本（快照中缺失）

原仓库在 NAS 部署目录中只同步了 `acceptance-w*.sh`，三个本地推理 shim
（`qwen_image_shim.py` / `zimage_shim.py` / `h3_video_shim.py`）随快照缺失。
本目录的占位文件让引用它们的测试可以**优雅跳过**而不是报错。

- `qwen_image_shim.py`：OpenAI 兼容端点 → ComfyUI workflow（Qwen-Image t2i/i2i/edit/多参考图）
- `zimage_shim.py`：同构的 Z-Image shim，含协作取消端点
- `h3_video_shim.py`：H3 视频本地生成 shim，含任务注册表与取消协议

若你有原构建环境，把真实 shim 放回本目录即可自动恢复对应测试。
