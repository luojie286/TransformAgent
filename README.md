# TransformAgent

Windows 11 x64 屏幕翻译工具。默认按住 `Alt+A`，再按 `S`，在光标所在的显示器上框选文字。浮窗贴近选区，同时显示原文和流式中文译文。

右键托盘图标，打开 **快捷键设置** 即可修改，例如 `Ctrl+Shift+S`。用 `+` 分隔按键。支持 `Ctrl`、`Alt`、`Shift`、`Win`，以及字母、数字、`F1` 到 `F12`、`Space`、`Tab`、`Enter`。新的单普通键组合被 Windows 拒绝时，原快捷键继续可用。也可通过托盘菜单直接开始框选。

## 开发与运行

需要 Python 3.13 和 uv。OCR 模型下载后在本机运行，只有识别得到的文字会发送给翻译 API。

```powershell
uv sync --locked
uv run python scripts/prepare_models.py
uv run python -m transformagent.app
```

默认使用北京区百炼普通 API 端点和 `qwen-mt-flash`。可在 Windows 用户环境变量 `QWEN_API_KEY` 中设置普通 API Key，也可通过托盘的**设置**指定端点、模型和密钥。通过设置输入的密钥保存在 Windows 凭据管理器中。

## 运行测试

```powershell
uv run pytest -m "not desktop"
uv run pytest -m desktop -s
```

桌面测试需要解锁的 Windows 会话。它会打开合成文本窗口、通过系统热键框选，并验证真实截图、OCR 和浮窗。设置 `TRANSFORMAGENT_LIVE_TEST=1` 可一并使用已配置的 `QWEN_API_KEY` 验收真实百炼接口，发送的内容为固定的合成样例。

## 打包

```powershell
uv run python scripts/prepare_models.py
uv run pyinstaller TransformAgent.spec --noconfirm
```

发布和启动时请保留 `dist/TransformAgent` 下的完整目录。入口为 `TransformAgent.exe`。

要把打包后的可执行文件也纳入桌面验收，可设置 `$env:TRANSFORMAGENT_PACKAGE_TEST = 'dist/TransformAgent/TransformAgent.exe'` 后再运行桌面测试。
