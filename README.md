# screen-bridge

以 **skill** 形式交付的 Windows 屏幕中间层：持续采集当前前台窗口，将文字、位置、图像区域和采集时间写入本地文件，让 AI 按需看图并调用点击、输入、滚动和按键工具。

## 能做什么

- **本地读屏**：优先读取 Windows 无障碍接口（软件提供的文字、按钮与位置），用本地中英文 OCR 补充截图中的文字。
- **复用结果**：画面不变时跳过识别；变化时默认只对变化区域重新做 OCR，并保留其他区域的文字。
- **图像语义**：返回候选图像区域或任意局部截图，让当前 AI 看图后将描述写回状态文件。相同图片可复用描述，不额外配置或自动调用云端看图模型。
- **实际坐标**：文字和区域记录 `[x, y, width, height]`，坐标使用物理屏幕像素，支持显示器上的负坐标。
- **有依据的操作**：点击、Unicode 输入、快捷键和滚轮都绑定屏幕版本；操作前重新检查窗口和像素，拒绝已经变化的界面及窗口外坐标。
- **两种接入**：skill 直接使用配套命令，无需 MCP 配置；也提供可选 MCP 接口。

`v0.1.0` 的定位是可运行的第一版。复杂图像区域检测、OCR 结果和不同应用的无障碍支持仍有误差；尚未证明整个 AI 工作流比现有方案快多少。

## 安装 skill

需要 Windows 10/11、Python 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。uv 会按项目设置准备 Python 3.12 和锁定依赖。运行时需要联网安装依赖，安装完成后的屏幕识别在本地执行。

### 使用交付包

将 `screen-bridge-skill.zip` 中的 **整个 `screen-bridge` 文件夹**解压到 Codex 的技能目录，例如：

```text
C:\Users\你的用户名\.codex\skills\screen-bridge\
  SKILL.md
  scripts/run.py
  references/commands.md
  runtime/                  # 已包含完整源码和依赖锁文件
```

下一轮对话可以输入：

```text
使用 $screen-bridge 读取当前窗口，告诉我有哪些文字和按钮。
```

首次运行会准备依赖。配套脚本不要求全局安装 Python 包，也不要求设置模型 API key。skill 自身不会把屏幕发往网络；当前 AI 在查看图片或接收文字时，其数据处理方式由该 AI 的运行环境决定。

### 从源码构建并安装

```powershell
git clone https://github.com/JIN9XXX/screen-bridge.git
cd screen-bridge
uv sync --locked
uv run python scripts/build_skill.py --install "$env:USERPROFILE\.codex\skills\screen-bridge"
```

安装脚本会同时生成 `dist/screen-bridge-skill.zip`。目标技能目录已经存在时会停止，不覆盖已有文件。只复制 `skills/screen-bridge` 子目录不足以独立分发；使用构建脚本生成含运行时的完整包。

## 直接使用

源码目录下可以用以下命令体验，不必先安装 skill：

```powershell
uv run screen-bridge doctor --check-ocr
uv run screen-bridge start
uv run screen-bridge screen
uv run screen-bridge find "保存"
uv run screen-bridge stop
```

`start` 会启动隐藏的本地后台进程，并返回 `screen.json` 的绝对路径。默认数据目录是 `%LOCALAPPDATA%/screen-bridge/runtime`；同一个目录的重复启动会复用现有进程。使用 `--output PATH` 可以创建独立实例，后续命令使用同一路径。

返回状态包含 `snapshot_id`。将当前返回值用于操作，操作后再次读屏确认：

```powershell
uv run screen-bridge click --snapshot 当前版本 --element 当前元素ID
uv run screen-bridge click --snapshot 当前版本 --x 640 --y 400
uv run screen-bridge type --snapshot 当前版本 --text "你好，screen-bridge"
uv run screen-bridge key --snapshot 当前版本 Ctrl+A
uv run screen-bridge scroll --snapshot 当前版本 down --amount 3
```

输入会发送给当前获得焦点的控件，需要先点击目标输入框。复杂文本支持 `--text-file`。出现 `STALE_SNAPSHOT` 表示需要重新读取并定位，不能仅替换版本后沿用旧坐标。

### 图片描述写回文件

```powershell
uv run screen-bridge image --snapshot 当前版本 --region 当前区域ID
uv run screen-bridge image --snapshot 当前版本 --bbox 100 200 400 300
uv run screen-bridge describe --snapshot 当前版本 --region 返回的区域ID --description "图片的实际内容"
```

`image` 返回本地 PNG 的绝对路径。AI 查看这张图后调用 `describe`，描述才会进入 `screen.json`。本项目不会凭候选框自动推断图像含义；不支持看图的模型可以使用文字和截图路径，但无法完成图像语义描述。

## 文件和接口

- `screen.json`：当前窗口、文字、坐标、图像区域、时间、识别来源、警告和处理耗时。
- `snapshots/`：最近 5 个屏幕版本的截图。
- `descriptions.json`：按图像内容缓存的 AI 描述。
- `regions/`：按需导出的局部图片。
- `session.json`、`collector.lock`、`service.log`：本地后台连接、进程锁和日志。

屏幕状态采用先写临时文件再替换的方式发布，避免读到半份 JSON。Windows 可能短暂占用文件，直接读取时可以使用 `uv run screen-bridge read`；skill 的 `screen` 命令直接从后台进程获取状态。停止采集后文件标为 `stopped`，不能把它当作实时画面。

完整约定见 [架构与状态格式](docs/architecture.md)。skill 的命令说明见 [commands.md](skills/screen-bridge/references/commands.md)。

## 可选 MCP

MCP 是 AI 助手调用外部工具的一种统一接口；使用 skill 时不需要配置它。需要 MCP 的客户端可用下列配置启动独立采集进程：

```toml
[mcp_servers.screen_bridge]
command = "uv"
args = ["run", "--locked", "--no-dev", "--project", "C:/绝对路径/screen-bridge", "python", "-m", "screen_bridge", "mcp", "--output", "C:/绝对路径/screen-bridge-mcp-state"]
startup_timeout_sec = 30
tool_timeout_sec = 45
```

提供基础的 `get_screen`、`find_text`、`get_region_image`、`describe_region`、`click`、`type_text`、`press_key`、`scroll` 工具，以及实时任务工具。MCP 与 skill 后台不能同时占用一个输出目录。

### 实时任务与通用组件

MCP 还提供实时任务工具。Codex 可以先调用 `subscribe_task`，然后使用 `wait_for_event` 等待屏幕状态变化。事件只保留最新的一小段队列，避免 Agent 处理过期画面。使用 `get_current_state` 可以获得通用组件，每个组件包含 `id`、`type`、`text`、`bbox`、`center`、`confidence`、`source`、`visible` 和 `enabled`。

可用工具包括 `subscribe_task`、`wait_for_event`、`get_current_state`、`get_region_image`、`describe_region`、`act`、`verify_action` 和 `stop_task`。推荐的交互顺序是订阅任务、等待事件、读取状态、执行带 `snapshot_id` 的操作、验证状态变化。`auto_execute` 默认关闭，只有已经验证过的任务才应开启自动操作。

视觉识别采用本地分层方案。优先使用 Windows UI Automation，其次使用本地 OCR、OpenCV 模板匹配和轮廓检测。复杂分割模型可以通过后续视觉适配器接入，当前接口为 CPU 运行保留了扩展点，也不要求上传屏幕内容到云端。

简单任务可以使用 JSON 策略配置，复杂任务可以实现 Python 适配器。策略和适配器共享同一个组件、事件和操作接口。打地鼠示例位于 `tools/whack_mole.py`，可用来验证目标识别、事件等待、点击和结果确认。

## 开发与验证

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run python scripts/smoke_desktop.py
uv run python scripts/verify_skill.py
uv build
uv run python scripts/build_skill.py
```

桌面测试会临时打开并激活一个专用测试窗口，验证 OCR、MCP、skill 命令、真实点击、中文与 emoji 输入、快捷键、滚轮和旧版本拒绝；结束后关闭窗口和服务。运行期间应保持该测试窗口在前台。GitHub CI 运行不依赖交互桌面的回归、检查与打包流程。

`uv run screen-bridge benchmark --samples 10 --output ./runtime-benchmark` 测量本地采集时间，排除 AI 推理和网络延迟。默认轮询间隔为 350 毫秒，属于目标调度间隔，OCR 本身较慢时不会保证每 350 毫秒产生完整新状态。

## 当前边界

- 处理当前前台窗口的可见区域，包括浏览器；不遍历所有后台窗口或网页源码。
- 图像候选框使用应用信息与启发式图像分析，不能保证每张图片都被精确分割；可以手动指定区域或查看整窗。
- 动画、视频、闪烁光标会导致频繁更新和操作拒绝；不能保证任意动态界面都能稳定自动操作。
- 装饰窗框的一像素边缘会被裁去，圆角像素会被归一化，防止背后的动画干扰检测。
- 无障碍读取在独立进程中执行；首次请求最多等待 1.5 秒，后续请求最多等待 400 毫秒，超时会终止辅助进程并改用 OCR。受保护或高权限窗口可能不可访问。
- 版本检查降低误点风险，不能消除最后一次检查与输入实际发生之间的时间差；输入成功不等于任务成功。

仓库和 skill 包仅包含代码与说明，不包含真实屏幕、登录凭据或运行记录。
