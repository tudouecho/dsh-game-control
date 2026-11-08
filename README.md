# DSH 游戏操作插件（dsh-game-control）

给 [DSH](https://github.com/deepseek-ai) 桌面端用的「游戏操作」插件：在左侧栏提供一个面板，直接控制本机鼠标/键盘并
随时全屏或区域截图；v0.2 起能**枚举窗口并对指定窗口做后台截图**（Windows Graphics Capture，窗口非前台、
被其它窗口遮挡时也能抓到画面）；v0.3 起提供**游戏自动化动作**（窗口激活、圆形轨迹、等待、模板找图/等图）和
**技能库**（把已适配的游戏流程保存成可读 skill，支持人工查看/编辑/自动备份/版本恢复/复用执行）；
v0.4 起技能文件升级为**带中文注释的 JSONC**，每步参数（点击次数、按住/等待时间、圆形半径/周期/时长等）都显式写入，
既方便人看也方便 agent 复用；v0.5 起默认中止键为 **Esc**、支持 `templates` 多模板与技能运行进度；
v0.6 新增 **Host 级长循环**（`loop_start`/`loop_status`/`loop_stop`，每轮独立 `skill_run`、可跑上千轮）与
**连接中断修复**（worker 异常退出后进度标记 stale、面板显示「后端连接中断」并可重置），并让长动作在 Host 超时前
由 Python 按总 deadline 优雅中止；v0.6.1 把「重复次数」与「轮数」融合为**唯一轮数语义**（`rounds=1` 单次、
`>1` 循环，多轮统一用 `loop_start.rounds`，`skill_run.repeat` 已 deprecated）；v0.6.2 修复**模板等待超时**
（`wait_image` 超时默认让该步失败并返回 `WAIT_IMAGE_TIMEOUT` + 失败截图，不再出现 `$btn.match.center.x` 的
NoneType 变量错误；可选 `onMissing:"continue"`，循环可勾选「失败后继续」对应 `stopOnError=false`）。向 agent 注册
`game_control` 与 `game_skill` 两个工具。所有动作都由本机 **Python 跨平台后端**执行，Windows 优先
（纯 `ctypes` 调 `SendInput` + GDI 截图）、macOS / Linux 走 `pynput` / `xdotool` 等后端。

- 插件包名：`@local/dsh-game-control`（Loader 行 id：`game-control`），版本 `0.6.2`（以 `dsh-game-control/package.json` 为准）
- 适配 DSH：桌面端 `0.2.2-rc.1`（`package.json` 声明 `dsh >=0.2.1-rc.2 <0.3.0`）
- 运行环境：Node.js ≥ 20（宿主自带）、Python ≥ 3.10（DSH 内置 Python 可直接用）、Pillow、numpy
  （模板匹配/WGC 需要，DSH 内置 Python 已自带）；后台窗口截图的 `windows_capture` 随包分发（MIT），**运行时不需要联网**
- 安全边界：**不联网、无后台自动输入、只在被显式调用时动作；长动作（圆形轨迹/技能运行）按 Esc 可中止**

---

## 目录

- [功能清单](#功能清单)
- [目录结构](#目录结构)
- [安装步骤](#安装步骤)
- [依赖安装说明](#依赖安装说明)
- [侧栏面板使用说明](#侧栏面板使用说明)
- [插件接口 / 动作注册](#插件接口--动作注册)
- [后台窗口截图（v0.2）](#后台窗口截图v02)
- [游戏自动化（v0.3）](#游戏自动化v03)
- [技能库（v0.3）](#技能库v03)
- [长循环与连接修复（v0.6）](#长循环与连接修复v06)
- [模板等待超时修复（v0.6.2）](#模板等待超时修复v062)
- [技能文件格式（JSONC + 中文注释）](#技能文件格式jsonc--中文注释)
- [调用示例](#调用示例)
- [异常处理与日志](#异常处理与日志)
- [安全边界](#安全边界)
- [跨平台支持矩阵与限制](#跨平台支持矩阵与限制)
- [最小验收用例](#最小验收用例)
- [参考项目与致谢](#参考项目与致谢)
- [English summary](#english-summary)

---

## 功能清单

| 能力 | 说明 |
|---|---|
| 左侧栏入口 + 主面板 | 侧栏图标「游戏操作」，点开是完整操作面板（状态 / 鼠标 / 键盘 / 截图 / 窗口截图 / 圆形轨迹 / 模板找图 / 技能库 / 最近操作日志） |
| 鼠标 | 移动到绝对坐标、相对移动、单击/右键/双击、按下/抬起、拖拽、滚轮（垂直/水平）、读取当前位置 |
| 键盘 | 单键按键、按住/松开、组合键（如 `ctrl+shift+s`）、输入任意 Unicode 文本 |
| 截图 | 全屏（主屏）、指定显示器、整个虚拟桌面、区域截图；PNG/JPEG；保存文件并返回路径，可选返回 base64 |
| 窗口枚举 | `list_windows` 列出本机窗口（hwnd / 标题 / 类名 / 进程 / 矩形 / **clientRect** / 最小化 / 可见 / 前台 / cloaked，z 序） |
| 后台窗口截图 | `capture_window` 抓取指定窗口画面：**WGC 可在窗口非前台、被其它窗口遮挡时抓取**；auto 回退链 WGC→PrintWindow→window-dc→screen，结果如实返回实际 `engine` |
| 窗口激活（v0.3） | `activate_window` 恢复最小化窗口并切到前台（`SetForegroundWindow`/`AttachThreadInput`/轻点菜单键兜底），返回 `foreground:true` 与 `clientRect`；不注入点击 |
| 圆形轨迹（v0.3） | `mouse_circle` 在指定圆心/半径做连续平滑的圆（可设周期、时长、方向、采样率、按住鼠标键），**默认 Esc 中止**，退出必释放按键 |
| 等待（v0.3） | `wait` 同步等待指定毫秒（零输入），用于等游戏转场 |
| 模板找图（v0.3/0.5/0.6.2） | `find_image` 用 numpy FFT 归一化互相关做多尺度模板匹配；`wait_image` 轮询直到出现/消失或超时；**支持 `templates` 多模板（1–8 个，任一命中）**，等待阶段按 **Esc 立即中止**；v0.6.2 起步骤内找不到模板默认**该步失败**（`onMissing:"fail"`，`WAIT_IMAGE_TIMEOUT`/`TEMPLATE_NOT_FOUND` + 失败截图），可选 `onMissing:"continue"` 继续 |
| 技能库（v0.3–0.5） | `game_skill` 工具 + 面板（第二项）：结构化步骤表格 + 高级 JSONC 编辑；保存/查看/自动备份/版本恢复/软删除/复用执行；运行时有进度文件/进度接口防卡死 |
| 技能运行进度（v0.5/v0.6） | `<GAME_CONTROL_HOME>/logs/skill-progress.json` 原子写 + `GET /api/game-control/skill-progress`（返回 `{progress, bridge}`）；面板显示「第 N/M 步 · action · 已等待 X 秒」；worker 退出后进度标记 `stale:true`，面板提示「后端连接中断」并可重置 |
| 统一轮数（v0.6.1） | **只有一个「轮数 rounds」概念**（1–10000）：`rounds=1` 单次运行、`rounds>1` 循环；多轮统一调用 `loop_start`（每轮独立 `skill_run`），面板只有「轮数 + 运行/停止 + 立即停止」，不再有「重复次数/运行一次/启动循环」双控制 |
| 长循环（v0.6） | Host 级 `loop_start`/`loop_status`/`loop_stop`：按轮独立调用 `skill_run`（intervalMs、stopOnError、roundTimeoutMs、立即停止），不再受单请求 310s 限制；状态写 `logs/loop-state.json` + 每轮 `logs/loop.jsonl`；`loop_status` 含当前步骤 `currentStep` |
| 模板失败语义（v0.6.2） | 技能步骤 `onMissing`（`fail` 默认 / `continue`）：找不到模板时该步失败（`WAIT_IMAGE_TIMEOUT` / `TEMPLATE_NOT_FOUND`），不执行后续步骤、不再报 `NoneType` 变量错误；自动存失败截图 `logs/skill-fail-*.png`（`failedStep.screenshotPath`）；循环可「失败后继续」（`stopOnError=false`），每轮 `results` 带 `failedStep` |
| 连接中断修复（v0.6） | worker 退出/被强杀时进度文件立即标记 `stale:true, abortReason:"backend-stopped"`，面板不再永久「运行中/保存中」，可「重置状态」恢复 |
| agent 工具 | Host 侧注册 `game_control`（23 个控制动作）与 `game_skill`（8 个技能动作 + 3 个循环动作）；`loop_start` 是统一 N 轮入口，`skill_run` 仅单轮底层原语（`repeat` 已 deprecated，仅 CLI/内部兼容） |
| HTTP 接口 | 面板通过 `/api/game-control/*` 同源路由调用宿主 Python 桥（health / status / exec / logs） |
| 常驻后端 | Host 以 `--serve` 启动一个 Python worker，逐行 JSON 请求/响应；超时自动重启（v0.3 长动作有独立超时预算） |
| 无副作用 | 不联网、不存在后台线程/定时器主动注入输入；每条请求必须显式触发 |
| 审计与日志 | `game-control.log` 滚动日志 + `actions.jsonl` 动作审计（不记录 `type_text` 原文、不记录画面内容） |

---

## 目录结构

```
D:\dsh-chajian\
├── SPEC.md                          # 团队实现契约（开发文档，不随插件安装）
├── README.md                        # 本文件
├── requirements.txt                 # 根级 Python 依赖（pip install -r requirements.txt）
├── test\
│   └── acceptance.mjs               # 最小验收用例（纯 Node，无第三方依赖）
├── docs\                            # 本机验证截图与开发文档
│   ├── v0.3-开发提示词.md           # v0.3 需求/接口说明（开发文档）
│   ├── v0.3-acceptance.md           # v0.3 实机验收记录（挖矿 2 轮 + 中止键测试）
│   ├── v0.3-mining-start.png        # 实机：挖矿开始
│   ├── v0.3-mining-done.png         # 实机：挖矿完成面板
│   ├── v0.3-mining-skill-round.png  # 实机：技能库跑一轮
│   ├── verification-sidebar.png     # 侧栏入口
│   └── verification-window-background.png  # v0.2：WGC 后台窗口截图证据（DSH 前台遮挡下抓到 Keep on Mining! 主菜单）
└── dsh-game-control\                # DSH bundle 包（plugin_manager 的安装入口）
    ├── package.json                 # bundle / client 清单，版本号唯一来源（v0.6.2）
    ├── cordis.patch.yml             # bundle 挂载行（- insert: id: game-control）
    ├── index.js                     # Host 半：HTTP 路由 + game_control/game_skill 工具 + Python 桥
    ├── client.js                    # Client 半：侧栏图标 + 主面板（窗口截图/圆形轨迹/模板找图/技能库）
    ├── icon.svg                     # 侧栏图标（自绘 SVG 手柄）
    ├── locale\
    │   ├── zh.json                  # 中文元数据（插件管理器显示）
    │   └── en.json                  # 英文元数据
    └── python\
        ├── game_control.py          # Python 入口：CLI / JSONL / worker / selftest / 校验 / 日志 / 动作分发
        ├── win_native.py            # Windows ctypes：SendInput + GDI 截图 + DPI 感知 + 窗口/前台 API
        ├── screenshot.py            # 跨平台截图：捕获、区域裁剪、保存、base64
        ├── window_capture.py        # v0.2 窗口枚举/激活 + 后台窗口截图（WGC/PrintWindow/window-dc/screen 回退链）
        ├── circle_motion.py         # v0.3 圆形轨迹纯函数（采样点/起止角/方向/包围盒校验）+ 执行器
        ├── template_match.py        # v0.3 模板匹配（numpy FFT NCC + 多尺度 + NMS），find_image/wait_image 用
        ├── skills.py                # v0.3/0.4 技能库（JSONC 读写/中文注释/归一化/备份版本/软删除/变量替换/skill_run）
        ├── input_backend.py         # 非 Windows 键鼠后端（pynput / xdotool / osascript）
        ├── test_backend.py          # Python 单元/冒烟测试（无真实点击/按键）
        ├── requirements.txt         # Python 后端依赖（Pillow 必需；numpy 可选但推荐）
        └── vendor\
            ├── windows_capture\         # 随包分发的 windows-capture 2.0.1（MIT，无需联网安装）
            └── windows_capture_LICENSE.txt  # 上游 MIT LICENSE（随包分发）
```

---

## 安装步骤

### 1. 安装 DSH 插件 bundle

在 DSH 里用插件管理器安装本目录（**安装的是包目录本身，不是 `python` 子目录**）：

```text
plugin_manager action=install_bundle target=D:\dsh-chajian\dsh-game-control
```

> `install_bundle` 属于敏感操作，需要审批（`danger-full-access`）。安装会把
> `cordis.patch.yml` 里的挂载行合并进 profile，不会手工改 profile 文件。

安装后在 DSH 中刷新页面（或重启 DSH），然后确认：

1. 左侧栏出现手柄图标「游戏操作」；
2. 点开后显示「游戏操作」主面板，顶部「后端状态」卡片能读到 Python 路径、屏幕尺寸、截图目录；
3. 对话里的 agent 可以使用 `game_control`（23 个动作）与 `game_skill`（技能库）工具；
4. v0.3 面板应能看到「窗口截图」卡片里的「激活窗口」、以及「圆形轨迹」「模板找图」「技能库」卡片。

> 开发期也可以按 `cordis.patch.yml` 顶部注释用 `dsh plugin --profile desktop add link:<包目录绝对路径>`
> 以 link 方式挂载（仅供开发调试，正式交付以 `install_bundle` 为准）。

### 2. 准备 Python（Windows 通常零配置）

插件启动时会按以下顺序自动寻找可用的 Python，找到第一个能跑 `--version` 的即使用：

1. 环境变量 `DSH_GAME_CONTROL_PYTHON` / `GAME_CONTROL_PYTHON`（优先，填 `python.exe` 绝对路径）；
2. 插件目录下的虚拟环境 `dsh-game-control\python\.venv\Scripts\python.exe`；
3. **DSH 内置 Python**：`%USERPROFILE%\.dsh\dsh-runtimes\*\dependencies\python\python.exe`
   （桌面端自带，已包含 Pillow，通常**无需任何额外安装**）；
4. PATH 上的 `python3` / `python`（Windows 额外尝试 `py -3`）。

如果以上都找不到，`game_control` 会返回错误码 `PYTHON_NOT_FOUND`，面板「后端状态」会显示原因。
此时安装 Python 3.10+ 后执行：

```powershell
# 方式 A：用系统 Python
py -3 -m pip install -r D:\dsh-chajian\requirements.txt

# 方式 B：指定某个现成解释器
setx DSH_GAME_CONTROL_PYTHON "C:\Path\To\python.exe"
```

> 本机实测可用的 DSH 内置解释器（示例）：
> `C:\Users\<用户名>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`（Python 3.12 + Pillow）。
> 具体 `dsh-runtimes` 子目录名以本机为准。

### 3. 验证安装

- 面板里点「刷新」，「后端状态」应显示「就绪」以及后端名（Windows 为 `win32-sendinput`）；
- 面板「窗口截图」卡片点「刷新窗口列表」能列出本机窗口，选中后「抓取窗口」能出预览
  （WGC 需要 Windows 10 1903+，见[后台窗口截图（v0.2）](#后台窗口截图v02)）；点「激活窗口」能把目标窗口切到前台；
- 面板「圆形轨迹」卡片可用「干跑」验证参数（干跑不注入鼠标）；「模板找图」可对当前窗口做一次查找；
  「技能库」能刷新列表、新建/编辑/保存/恢复/运行（详见[技能库（v0.3）](#技能库v03)）；
- 或调用一次 agent 工具：`game_control` + `{"action":"status"}` / `game_skill` + `{"action":"skill_list"}`；
- 或命令行验收：`node D:\dsh-chajian\test\acceptance.mjs`（见[最小验收用例](#最小验收用例)）。

---

## 依赖安装说明

Python 后端只依赖 **Pillow**（PNG/JPEG 编码）；macOS / Linux 的键鼠输入额外需要 **pynput**（没有时会依次尝试
`xdotool` / `osascript` 兜底）。**numpy** 是 v0.2 后台窗口截图（WGC）与 v0.3 模板找图（`find_image`/`wait_image`）的
运行期依赖（DSH 内置 Python 通常已自带）；`windows_capture` 已经**随插件分发**
（`dsh-game-control/python/vendor/windows_capture/`，windows-capture 2.0.1，MIT 许可，附 LICENSE），
**不需要 pip 安装、运行时也不会联网**。技能库（`skill_*`）只用 Python 标准库 + 已有动作，无额外依赖。

```powershell
# 必需：截图编码
pip install -r D:\dsh-chajian\requirements.txt
# 等价于：pip install "Pillow>=9.0.0"
# 也可以：pip install -r D:\dsh-chajian\dsh-game-control\python\requirements.txt

# 推荐：模板找图 / WGC 后台截图需要 numpy；DSH 内置 Python 一般已自带，缺失时再装
pip install "numpy>=1.24"

# 可选：仅 macOS / Linux 键鼠后端
pip install "pynput>=1.7.6"
```

依赖缺失时的行为（不会让插件崩溃）：

| 缺失项 | 表现 |
|---|---|
| Pillow 缺失 | 截图 / 窗口截图动作返回 `MISSING_DEPENDENCY`，hint 为 `pip install pillow`；鼠标/键盘动作仍可用 |
| numpy 缺失（Windows） | `find_image` / `wait_image` 返回 `MISSING_DEPENDENCY`（hint `pip install numpy`）；WGC 真后台抓取不可用，auto 会继续回退（printwindow → window-dc → screen），全失败返回 `WINDOW_CAPTURE_FAILED`；`capture_window engine=wgc` 直接失败 |
| `python/vendor/windows_capture` 缺失（安装包不完整） | 同上，WGC 不可用，只能走回退链 |
| pynput 缺失（非 Windows） | 键鼠动作返回 `MISSING_DEPENDENCY`，hint 给出 `pip install pynput`；若安装了 `xdotool` / `osascript` 会先尝试兜底 |

> **不需要**安装 pyautogui / pynput（Windows）、不需要任何网络库、**也不需要安装 OpenCV**：
> `windows_capture` 的 `import cv2` 由后端注入最小 stub（截图不用 `cv2.imwrite`）；
> 模板匹配用 numpy FFT 自实现归一化互相关，不依赖 OpenCV。
> 后端源码不含 `socket` / `urllib` / `http` / `requests` / `websocket` 等 import（验收会扫描检查）。

---

## 侧栏面板使用说明

点击左侧栏「游戏操作」图标打开主面板（一个页面内分区、可滚动）。v0.5 起卡片顺序固定为：
**① 后端状态 → ② 技能库 → ③ 鼠标 → ④ 键盘 → ⑤ 截图 → ⑥ 窗口截图 → ⑦ 圆形轨迹 → ⑧ 模板找图 → ⑨ 最近操作日志**
（技能库从末尾移到第 2 位，方便随时查看/编辑/运行技能）。

### 1. 后端状态

- 「刷新」按钮重新读取 `/api/game-control/status`；
- 显示：桥接是否就绪、Python 路径/版本、平台、输入后端、DPI 感知、屏幕/主屏尺寸、当前指针、截图目录、日志目录；
- 后端不可用时显示红字原因（例如 `PYTHON_NOT_FOUND`、缺少 Pillow）。面板只在挂载时取一次状态，
  **不会轮询**，需要时手动点「刷新」。

### 2. 技能库（v0.3–v0.6.1，面板第二项）

- 「刷新」列出技能（`skill_list`），只读摘要显示 id / 标题 / 游戏 / 步骤数 / 更新时间 / 备份数；
- 选中技能后默认显示**结构化步骤表格**（每步一行）：
  - 列：`#`、`操作（action）`、**关键条件/参数单元格**、`等待(ms)`、`备注(note)`、`操作（上移/下移/删除）`；
  - 关键条件单元格按 action 提供可编辑输入（如 `wait_image` 的模板/多模板/超时/轮询/模式/窗口；
    `mouse_click` 的 x/y/次数/按键/间隔；`mouse_circle` 的圆心/半径/周期/时长/方向/中止键；`mouse_drag` 的起止/时长/步数/按键；
    `wait` 的 `durationMs`；其它动作用通用 `params` JSON 编辑器）；
  - 每行可改 `waitMs`（0–60000）与 `note`（≤200），可**上移/下移/删除**；「新增步骤」默认插入 `wait` 步骤（也有常用模板可选）；
  - 「保存」用结构化字段调用 `skill_save`（后端归一化参数并重新生成中文注释 JSONC），成功后刷新表格与高级编辑器；
- **「高级」折叠区**：保留完整配置文件（JSONC，含中文注释）多行等宽编辑器（`content` 模式保存），两种方式都可用；
- 「版本」下拉可「恢复」（`skill_restore`，恢复历史 JSONC 文本）；「删除」默认软删到 `skills/.trash/`；
- **运行区（v0.6.1 统一轮数，只有一套控制）**：
  - `轮数`（默认 1，1–10000）+ `轮间隔 intervalMs`（默认 500，**仅轮数>1 时生效**）+「立即停止」复选；
  - **「失败后继续」复选（v0.6.2）**：默认不勾（遇失败停止）；勾选后对应 `loop_start.stopOnError=false`，
    偶发一轮模板超时不会中断整个长循环，失败轮次仍记录在 `results` 与 `lastError`；
  - 唯一按钮「**运行**」→ 统一调用 `loop_start`（**rounds 传输入值；rounds=1 也走 LoopRunner 单次执行**）；
    「**停止**」→ `loop_stop`（勾选「立即停止」时终止当前轮）；dryRun 干跑保留（单次、不计数）；
  - 进度统一轮询 `/exec {action:'loop_status'}`（~700ms）：
    「**第 N/M 轮 · 已完成 X · 失败 Y · 当前轮已等待 Zs**」+ **当前步骤**（来自 `loop_status.currentStep`，
    Host 从 skill-progress 合并：动作名/步骤序号/已等待）；不再有「重复次数」输入，也没有「运行一次 / 启动循环」两个按钮；
  - **每轮失败信息（v0.6.2）**：`loop_status.results` 每轮条目含 `failedStep`（`{index,action,code,message,screenshotPath?}`）
    与 `aborted/abortReason`；面板每轮结果/最后错误区域显示失败步骤、错误码与 **失败截图路径**；
  - **运行/循环期间**禁用技能库的保存/刷新/新建/删除/恢复/JSONC 保存/运行（以及其它会排队到同一 Python worker 的操作），
    结束后恢复并显示结果；失败时显示 `failedStep` 与错误码；
  - **连接中断修复（v0.6）**：进度/状态请求失败（网络错误/超时）时停止轮询，显示
    「**后端连接中断（可能因超时被杀/被停止）**」并提供「**重置状态**」按钮（清除本地 running/恢复按钮），
    不再把 `TypeError: Failed to fetch` 直接显示给用户；收到 `stale:true` 或 `bridge.running===false` 时同样提示并可重置；
- 提示：保存时自动补全每步参数并重新生成中文注释，**手工注释会被覆盖；自定义备注请写步骤的 `note` 字段**；
  技能运行/等待模板阶段都可按 **Esc** 中止。

### 3. 鼠标

- 填 X / Y 坐标（物理像素，主屏左上为原点，多屏可为负），可执行：**移动**、**左键单击**、**右键单击**、
  **双击**、**按下** / **抬起**、**拖拽**（起点 X/Y + 终点 X/Y）、**滚轮上 / 滚轮下**、**读取当前位置**；
- 不填坐标直接点「左键单击/右键单击」表示在当前指针位置点击；
- 操作结果 / 错误会显示在对应区域。

### 4. 键盘

- 「单个按键」输入键名（如 `enter`、`a`、`f5`、`space`）→ 点「按键」；
- 「组合键」输入如 `ctrl+shift+s` → 点「发送」（最多 4 个键，按下顺序、反向释放）；
- 「输入文本」文本框 → 点「输入文本」（支持中文/Unicode，≤10000 字符）；
- 「快捷按键」按钮：W A S D 空格 Shift Esc Enter F Q E R Tab 1–5。
  **点击 = 单次按键；长按 = key_down，松开时补 key_up**。

### 5. 截图

- 「全屏截图」「区域截图」（区域需填 X / Y / 宽 / 高）；
- 勾选「返回 base64（用于预览）」默认开启：截图后在本面板显示预览图、保存路径（可复制）、尺寸/字节数；
- 截图会**保存到后端截图目录**（默认 `%USERPROFILE%\.dsh\game-control\shots\`），
  文件名 `shot_YYYYMMDD_HHMMSS_fff.png`；
- 不勾选 base64 时只返回保存路径（面板不显示预览）。

### 6. 窗口截图（v0.2）/ 窗口激活（v0.3）

- 「刷新窗口列表」：调用 `list_windows` 拉取本机窗口，下拉框默认选中**前台窗口**（没有则选面积最大的窗口），
  显示标题 / hwnd / 尺寸 / 是否前台/最小化；
- 「引擎」下拉：`auto（自动回退）` / `wgc（后台抓取，推荐）` / `printwindow（GDI 兼容）` / `screen（仅可见区域）`；
- 「抓取窗口」：调用 `capture_window`（带 `base64:true` 供面板预览），抓取后显示预览图、保存路径、
  尺寸/字节数、**实际使用的 engine**，以及回退原因 / `warnings`；
- 「激活窗口」（v0.3）：调用 `activate_window` 把选中窗口恢复并切到前台，显示 `foreground` / `attempts`；
  这是后续键鼠真正进入游戏窗口的前提（输入只影响前台窗口）；
- 抓取过程**不会激活目标窗口、不会注入任何键鼠**；窗口被遮挡/在后台时优先用 WGC（Windows 10 1903+）；
- 非 Windows 平台该区显示「仅支持 Windows（`UNSUPPORTED_PLATFORM`）」提示。

### 7. 圆形轨迹（v0.3）

- 填 `centerX/centerY/radius/periodMs/durationMs/direction/samplesPerSecond/button/abortKey`；
  「按当前窗口 clientRect 填圆心/半径」可一键换算（圆心=clientRect 中心，半径=窗口宽/4，即直径为窗口一半）；
- 「开始（会阻塞）」会真实执行 `mouse_circle`：**请求会阻塞 durationMs 时长**（默认 30s），期间鼠标连续画圆；
  按 **Esc** 可随时中止（`abortKey` 默认 esc，可改其它键名或 `"none"` 禁用），返回 `aborted:true` + `abortReason`
  （中止不算失败）；也可以先点「干跑」只校验参数；
- 面板会显示实际时长、圈数、采样数、是否中止；运行中不要切走窗口（传了 `hwnd` 时前台变化会自动停止）。

### 8. 模板找图（v0.3 / v0.5）

- 填模板路径（相对 `<GAME_CONTROL_HOME>/templates/` 或绝对路径；**v0.5 支持多模板**：多个路径逗号分隔，
  对应 `templates` 数组，1–8 个，任一匹配即命中）+ 阈值 + 查找区域（可选用当前窗口 `clientRect`）；
- 「查找」调用 `find_image`（只读截图，不注入输入），显示 `found`、命中数、最佳命中 `center`/`score`；
  等待阶段（`wait_image`）同样支持 **Esc 立即中止**；
- 命中后可「点击命中点」（调用 `mouse_click`，这是一次显式点击动作）。

### 9. 最近操作日志

- 面板内记录最近 **30** 条操作 `{时间, action, ok, 消息}`，失败为红字；可「清空」。

所有按钮在请求进行中会 `disabled` 并显示「执行中」；前端会先做明显参数校验（空 key、非数字坐标等），
不把明显无效的请求打到后端。

---

## 插件接口 / 动作注册

### Host 半（`index.js`）

- 导出 `name = '@local/dsh-game-control'`、`inject = ['tools']`、`apply(ctx)`；
- `webServer` 为**可选服务**（`ctx.get('webServer')`，不放进 `inject`），因此没有 Web 的 profile 也能注册工具；
- 所有注册放进 `ctx.effect(...)` 并返回清理函数，插件卸载时杀掉 Python 子进程。

#### HTTP 路由（前缀 `/api/game-control`，同源调用）

| 方法 / 路径 | 请求 | 响应 |
|---|---|---|
| `GET /api/game-control/health` | — | `{ok:true, version, bridge:{ready, pythonPath?, lastError?}}` |
| `GET /api/game-control/status` | — | `{ok:true, version, bridge:{...}, backend:<Python status 信封>}` |
| `POST /api/game-control/exec` | `{"action":"...","params":{...}}`（≤256KB） | Python 信封原样返回（`ok` 可能为 `false`）；JSON 解析失败 → HTTP 400 `{ok:false,error:{code:'INVALID_ARGUMENT',message}}` |
| `GET /api/game-control/logs?lines=80` | — | `{ok:true, logTail:string}`（读 `<logDir>/game-control.log` 尾部，读不到返回空串） |
| `GET /api/game-control/skill-progress` | — | `{ok:true, progress:{running,skillId,startedAt,elapsedMs,repeat,repeatsDone,step:{index,total,action,startedAt},updatedAt,success?,finishedAt?,stale?,abortReason?}\|null, bridge:{running,lastError}}`（v0.5/v0.6，直接读 `<logDir>/skill-progress.json`，**不经 Python worker**，技能运行期间也可用；worker 退出时 progress 会被标记 `stale:true, abortReason:"backend-stopped"`） |

响应头固定 `content-type: application/json; charset=utf-8`、`cache-control: no-store`。
路由 handler 内部异常会返回 HTTP 500 JSON（`{ok:false,error:{code:'INTERNAL'}}`）。

#### agent 工具 `game_control`

- 一个工具，参数是**扁平行参**（不是嵌套 `params`），`action` 必填，枚举与 Python 动作表一致（v0.3 共 **23 个动作**）；
- 参数 schema 中每个 `object` 节点都显式声明 `additionalProperties`（宿主注册要求）；
- 成功返回：`{ok:true, action, result, durationMs}`（JSON 安全值）；
- 失败：`throw new Error('[错误码] 消息')`，错误消息以 `[CODE]` 开头；
- **截图 / 窗口抓取都不返回 base64**：agent 调用 `screenshot` 或 `capture_window` 时宿主会移除 `base64` 参数，
  结果里只有 `path` / `absolutePath` / `width` / `height` / `format` / `bytes`（`capture_window` 另有
  `engine` / `window` / `warnings?` / `fallbackReason?`）。需要看图时先截图，再用 `read_image` 读取
  `absolutePath`。面板预览才需要 base64（面板走 HTTP 路由，不走工具）。
- 窗口能力：先 `list_windows`（可用 `titleContains` 过滤）拿到 `hwnd`，再 `capture_window` 传 `hwnd` 或标题子串；
  后台/被遮挡窗口用 WGC（详见[后台窗口截图（v0.2）](#后台窗口截图v02)）。
- v0.3 游戏自动化：先 `activate_window` 把游戏切前台，再用 `find_image`/`wait_image` 定位按钮、`mouse_click` 点击、
  `wait` 等转场、`mouse_circle` 连续画圆；`mouse_circle` 会**阻塞 durationMs**，默认 Esc 可中止。
  推荐流程与超时规则见[游戏自动化（v0.3）](#游戏自动化v03)。
  这些动作（`activate_window`/`mouse_circle`/`find_image`/`wait_image`）仅 Windows 可用，其它平台返回
  `UNSUPPORTED_PLATFORM`。
- 技能库能力在第二个工具 `game_skill`（见下），不要和 `game_control` 混淆。

### Python 后端（`python/game_control.py`）

运行模式（互斥）：

| 模式 | 命令 | 说明 |
|---|---|---|
| 单次 CLI | `python game_control.py --json '{"action":"status"}'` | 打印一行 JSON 后退出；动作失败也退出码 0 |
| stdin 流 | `python game_control.py --stdin` | 每行一个请求 JSON，每行一个响应 JSON，读到 EOF 退出 |
| 常驻 worker | `python game_control.py --serve` | 先输出 `{"ok":true,"event":"ready",...}`，之后每行请求→每行响应；Host 使用此模式 |
| 自检 | `python game_control.py --selftest` | 只做无副作用检查（不移动/点击/按键），退出码 0=通过 |
| 版本 | `python game_control.py --version` | 输出版本 JSON（Host 用来探测解释器可用） |

请求 / 响应信封（CLI / 流 / worker 通用）：

```json
// 请求
{ "id": 1, "action": "mouse_click", "params": { "x": 100, "y": 200, "button": "left" } }

// 成功
{ "id": 1, "ok": true, "action": "mouse_click",
  "result": { "x": 100, "y": 200, "button": "left" },
  "durationMs": 13, "backend": "win32-sendinput", "ts": "2026-11-08T08:00:00.123Z" }

// 失败（进程继续工作）
{ "id": 1, "ok": false, "action": "mouse_click", "durationMs": 2,
  "error": { "code": "INVALID_ARGUMENT", "message": "x 必须是数字", "hint": "传 x/y 为物理像素坐标" } }
```

- `id` 可省略；worker / `--stdin` 模式下会**回显同一 id**；
- 顶层多余字段忽略；`params` 内未知字段 → `UNKNOWN_PARAM`；
- 坐标系：**物理像素**，原点为主屏左上角，多屏虚拟桌面允许负坐标，先 `status` 读取几何。

#### 动作表（action 名与参数逐字一致，共 23 个）

| action | 参数（`*` 必需） | 返回 `result` |
|---|---|---|
| `status` | 无 | `{platform, pythonVersion, backend, dpiAware, virtualScreen:{x,y,width,height}, primary:{...}, displays:[...], pointer:{x,y}, shotDir, logDir, windowCapture:{wgcAvailable,vendor,printWindow,note?}, automation:{mouseCircle,findImage,skills}}` |
| `list_displays` | 无 | `{displays:[...]}`（每项含 `name/primary/x/y/width/height/workArea/index`） |
| `list_windows` | `titleContains?`(str), `includeInvisible?`(bool，默认 false), `limit?`(1–100，默认 50) | `{count, windows:[{hwnd,title,class,pid,process,rect,clientRect,minimized,visible,foreground,cloaked}]}`（z 序：前台/最上层在前；`clientRect` 为 v0.3 新增） |
| `capture_window` | `window?`(标题子串) **或** `hwnd?`(int)（二选一，至少一个）, `engine?`("auto"/"wgc"/"printwindow"/"screen"，默认 auto), `includeCursor?`(bool，默认 false), `timeoutMs?`(500–10000，默认 3000), `path?`, `format?`, `quality?`, `base64?` | `{window:{hwnd,title,class,pid,process,rect}, engine:"wgc"\|"printwindow"\|"window-dc"\|"screen", path, absolutePath, width, height, format, bytes, warnings?, fallbackReason?, base64?, base64Length?}` |
| `activate_window`（v0.3） | `window?`(标题子串) **或** `hwnd?`(int)（二选一），`restore?`(bool，默认 true), `timeoutMs?`(100–5000，默认 2000) | `{window:{hwnd,title,class,pid,process,rect,clientRect}, foreground:true, attempts}`；找不到 → `WINDOW_NOT_FOUND`，最终未前台 → `WINDOW_ACTIVATION_FAILED`；**不注入点击** |
| `mouse_move` | `x*`,`y*`, `durationMs?`(0–5000，默认 0) | `{x,y}`（移动后的实际位置） |
| `mouse_move_rel` | `dx*`,`dy*` | `{x,y}` |
| `mouse_click` | `x?`,`y?`,`button?`(left/right/middle/x1/x2，默认 left), `clicks?`(1–10，默认 1), `intervalMs?`(0–1000，默认 60) | `{x,y,button,clicks}` |
| `mouse_down` / `mouse_up` | `button?`，`x?`,`y?`（给了先移动） | `{x,y,button}` |
| `mouse_drag` | `x1*`,`y1*`,`x2*`,`y2*`, `button?`, `durationMs?`(0–60000，默认 300), `steps?`(2–200) | `{x1,y1,x2,y2,button,durationMs}` |
| `mouse_scroll` | `amount*`(-1000..1000，正=上/左), `x?`,`y?`, `horizontal?`(bool) | `{amount,horizontal,x,y}` |
| `mouse_circle`（v0.3） | `centerX*`,`centerY*`, `radius*`(1–5000), `periodMs?`(300–20000，默认 2500), `durationMs?`(100–120000，默认 30000), `direction?`("cw" 默认/"ccw"), `startAngleDeg?`(默认 -90), `samplesPerSecond?`(30–240，默认 120), `leadInMs?`(0–2000，默认 300), `button?`("none" 默认/left/right/middle), `abortKey?`(默认 "esc"，"none" 禁用), `hwnd?`(画圆期间校验前台), `dryRun?`(默认 false) | `{centerX,centerY,radius,periodMs,durationMs,direction,samples,loops,start:{x,y},end:{x,y},actualDurationMs,aborted,abortReason,abortKey}`；中止仍 `ok:true`；`dryRun:true` 不注入不等待（`abortKey` 回显实际使用的键） |
| `mouse_position` | 无 | `{x,y}` |
| `key_press` | `key*`, `presses?`(1–20，默认 1), `intervalMs?`(0–1000) | `{key,presses}` |
| `key_down` / `key_up` | `key*` | `{key}` |
| `hotkey` | `keys*`（数组 1–4 个键名，如 `["ctrl","shift","s"]`，按下顺序、反向释放） | `{keys}` |
| `type_text` | `text*`（≤10000 字符）, `intervalMs?`(0–200，默认 12) | `{length}`（**不返回原文**） |
| `wait`（v0.3） | `durationMs*`(0–600000) | `{waitedMs}`（零输入、同步等待） |
| `find_image`（v0.3 / v0.5） | `template?`(PNG/JPG，绝对路径或相对 `<GAME_CONTROL_HOME>/templates/`，≤2MB、宽高≤1024) **或** `templates?`(1–8 个模板数组，任一命中即返回，二选一), `region?`{x,y,width,height}, `window?`/`hwnd?`(用 clientRect 作搜索区域), `threshold?`(0–1，默认 0.85), `scales?`(1–5 个比例，默认 [1.0]), `grayscale?`(默认 true), `maxResults?`(1–20，默认 3), `abortKey?`(默认 "esc"，"none" 禁用), `debugPath?` | `{found, source, searchRegion, template:{path,width,height}, matches:[{x,y,width,height,center:{x,y},score,scale}], elapsedMs}`（多模板时 `template` 为命中的那个）；**找不到不是错误**（`ok:true, found:false`）；按 `abortKey` 可立即中止 |
| `wait_image`（v0.3 / v0.5） | 继承 `find_image` 参数（含 `template`/`templates`、`abortKey`），另加 `timeoutMs?`(100–600000，默认 15000), `intervalMs?`(50–2000，默认 250), `mode?`("appear" 默认/"disappear") | `{found, timedOut, match?, attempts, elapsedMs}`；超时返回 `ok:true, found:false, timedOut:true`（不抛错）；**等待阶段按 Esc 立即中止**（返回 `aborted:true, abortReason:"abort-key"`） |
| `screenshot` | `region?`{x,y,width,height}（与虚拟桌面求交集）, `monitor?`(int，**从 1 开始**), `allScreens?`(bool), `path?`(绝对/相对截图目录), `format?`("png"/"jpeg"，默认 png), `quality?`(1–100，jpeg 用), `base64?`(bool，默认 false) | `{path, absolutePath, width, height, format, bytes, base64?, base64Length?}` |

`region` 语义：`width/height` 必须 > 0（否则 `INVALID_REGION`）；与虚拟桌面**无交集** → `OUT_OF_BOUNDS`；
**部分越界** → 不报错，按交集裁剪后返回裁剪后的 `width/height`。
`region`、`monitor`、`allScreens` 三选一，不能同时使用。

`activate_window` 语义（详见[游戏自动化（v0.3）](#游戏自动化v03)）：

- 复用 `capture_window` 的选窗逻辑；最小化且 `restore:true` 时先 `ShowWindow(SW_RESTORE)`；
- 依次尝试 `SetForegroundWindow` → `AttachThreadInput` + `BringWindowToTop` + `SetForegroundWindow` →
  轻点 `VK_MENU` 解锁后再试，轮询 `GetForegroundWindow()` 最多 `timeoutMs`；**不注入点击**；
- 找不到窗口 → `WINDOW_NOT_FOUND`；最终没到前台 → `WINDOW_ACTIVATION_FAILED`（hint：UAC/其它桌面会话/需手动点一次）。

`mouse_circle` 语义：`samples = floor(durationMs/1000 × samplesPerSecond) + 1`，用 `perf_counter` 绝对时间调度
（不是累加 sleep）；圆包围盒必须完整落在虚拟桌面内（否则 `INVALID_ARGUMENT`）；执行期间轮询中止键（默认 Esc）
与前台窗口，退出路径保证释放按下的鼠标键；**请求会阻塞 `durationMs`**（Host 侧有单独超时预算，见下）。

`find_image` / `wait_image` 语义：只读截图 + numpy FFT 归一化互相关（NCC）+ 多尺度 + NMS；不注入输入、不激活窗口；
`template` 与 `templates` **二选一**（`templates` 为 1–8 个模板数组，任一命中即返回，多模板时结果 `template` 为命中的那个；
同时给 / 超过 8 个 / 空数组 → `INVALID_ARGUMENT`）；模板缺失 → `TEMPLATE_NOT_FOUND`，模板损坏/过大 → `INVALID_TEMPLATE`，
numpy 缺失 → `MISSING_DEPENDENCY`。

`abortKey`（v0.5）：`mouse_circle` / `find_image` / `wait_image` / `skill_run` 默认 **`"esc"`**（兼容 `escape`），
传 `"none"` 禁用。`find_image`/`wait_image` 在每次轮询/扫描前后用 `GetAsyncKeyState` 检查，**等待模板阶段按 Esc
会立即停止**（不再等满 timeout）；中止返回 `ok:true, aborted:true, abortReason:"abort-key"`，鼠标键保证释放。

`skill_run` 的**前置时长校验**：执行前（`dryRun` 也校验）估算最坏时长
`estimatedDurationMs = Σ(每步最大耗时) + ΣwaitMs + 500×步数`（`wait→durationMs`、`mouse_circle→leadInMs+durationMs`、
`wait_image→timeoutMs`、`find_image→8000`、其它→1000）；若 `estimatedDurationMs > timeoutMs` → `INVALID_ARGUMENT`
（hint：至少需要 X ms，可增大 timeoutMs 或缩小 wait_image/mouse_circle 参数）；通过时 dryRun/结果附带 `estimatedDurationMs`。

#### agent 工具 `game_skill`（v0.3）

第二个 agent 工具，专门管理/执行技能库；参数同样是扁平行参（`action` 为 `skill_*` 之一），每个 object 显式
`additionalProperties`。动作与 Python `skill_*` 动作一一对应：

| action | 参数 | 返回 |
|---|---|---|
| `skill_list` | 无 | `{count, skills:[{id,title,description,game,tags,stepCount,updatedAt,backupCount}]}` |
| `skill_get` | `id*` | `{skill:{...解析/归一化后的 skill...}, content, stepsView:[{index,action,comment,waitMs,saveAs?}]}`；`content` 是磁盘上的 JSONC 原文（含中文注释），`stepsView` 供面板展示；不存在 → `SKILL_NOT_FOUND` |
| `skill_save` | 结构化：`title*`,`description?`,`game?`,`tags?`,`markdown?`,`steps*`,`id?`；或全文：`content?`（整份 JSONC ≤1,000,000 字符，传了它则忽略其它内容字段，`id` 需与 content.id 一致） | `{id, created, backupPath, updatedAt, backupCount, content, stepsView}`（保存后重新渲染 JSONC，`content` 用于刷新编辑器；覆盖前自动备份旧版本） |
| `skill_versions` | `id*` | `{id, count, versions:[{name,savedAt,bytes}]}`（新→旧；备份保留原始 JSONC 文本） |
| `skill_restore` | `id*`,`backup*`（文件名或 `"latest"`） | `{id, restoredFrom, backupOfCurrent?, updatedAt}`（恢复前先备份当前；原样恢复历史 JSONC） |
| `skill_delete` | `id*`,`hard?`（默认 false 软删） | `{id, deleted:true, trashPath, hardDeleted}` |
| `skill_run` | `id*`,`dryRun?`,`timeoutMs?`(1000–600000，默认 300000),`abortKey?`(默认 "esc"),`stopOnError?`(默认 true) | `{skillId, dryRun, repeat, repeatsDone, success, aborted, abortReason, durationMs, steps:[...], failedStep?, estimatedDurationMs?}`；**单轮底层原语**（LoopRunner 每轮内部调用一次）；读取时自动剥离注释并归一化参数；执行前做时长前置校验。**`repeat` 已 deprecated**：工具 schema 不再暴露，仅 Python CLI/内部兼容；多轮请用 `loop_start.rounds` |
| `skill_progress`（v0.5） | 无 | `{progress:{running,skillId,startedAt,elapsedMs,repeat,repeatsDone,step:{index,total,action,startedAt},updatedAt,success?,finishedAt?,stale?,abortReason?}\|null}`（读 `<GAME_CONTROL_HOME>/logs/skill-progress.json`，运行期间也可用） |
| `loop_start`（v0.6/v0.6.1，Host 级，**统一 N 轮入口**） | `skillId*`,`rounds*`(1–10000；**rounds=1 即单次运行**),`intervalMs?`(0–60000，默认 500),`stopOnError?`(默认 true),`abortKey?`(默认 "esc"),`roundTimeoutMs?`(1000–600000；省略时 dryRun 估算 `estimatedDurationMs` 后取 `estimated+30000` 并 clamp) | `{jobId, running:true, skillId, rounds, intervalMs, perRoundTimeoutMs, estimatedDurationMs, startedAt}`；先 dryRun 校验，失败直接返回错误；后台**每轮单独调用一次** `skill_run`；同一时间只允许一个循环（重复启动 → `LOOP_ALREADY_RUNNING`） |
| `loop_status`（v0.6/v0.6.1，Host 级） | 无 | `{running, jobId, skillId, rounds, completedRounds, failedRounds, currentRound, stopRequested, startedAt, finishedAt?, perRoundTimeoutMs, estimatedDurationMs, lastError?, currentStep?:{index,total,action,elapsedMs}, results:[最近 20 轮 {round,ok,aborted,success,durationMs,errorCode?,message?}]}` |
| `loop_stop`（v0.6，Host 级） | `immediate?`(默认 false) | 置 `stopRequested`：当前轮结束后停止；`immediate:true` 时终止 worker 使当前轮立即失败（该轮 error `BACKEND_UNAVAILABLE`）并停止循环 |

技能库的存储位置、变量语法与完整用法见[技能库（v0.3）](#技能库v03)，文件格式/中文注释/归一化参数见
[技能文件格式（JSONC + 中文注释）](#技能文件格式jsonc--中文注释)。

#### 键名（canonical，大小写不敏感）

- 单字符：`a`–`z`、`0`–`9`、`-` `=` `[` `]` `;` `'` `,` `.` `/` `` ` ``；
- 具名键：`enter, esc, tab, space, backspace, delete, insert, home, end, pageup, pagedown, up, down, left, right,
  f1..f24, ctrl, control, alt, shift, win, meta, caps_lock, num_lock, scroll_lock, print_screen, pause, menu`；
- 别名：`escape→esc`, `return→enter`, `del→delete`, `pgup→pageup`, `pgdn→pagedown`, `cmd/command/super→win`；
- 未知键 → `INVALID_KEY`。

#### 截图路径

- 默认目录：`<GAME_CONTROL_HOME>/shots/`，`GAME_CONTROL_HOME` 默认 `%USERPROFILE%\.dsh\game-control`
  （非 Windows 为 `~/.dsh/game-control`）；
- `path` 为相对路径时相对截图目录；父目录不存在会自动创建；无扩展名时按 `format` 补 `.png` / `.jpg`；
- 默认文件名：`shot_YYYYMMDD_HHMMSS_fff.(png|jpg)`。

---

## 后台窗口截图（v0.2）

v0.2 新增 `list_windows` + `capture_window`：抓取**指定窗口**的画面，目标窗口**不需要是前台、也可以被其它窗口
遮挡**，用于「游戏窗口在后台，agent 也能看到画面」。

> **本机实测证据**：DSH 位于前台、游戏窗口被其它窗口遮挡时，WGC 仍抓到了 `Keep on Mining!` 的游戏主菜单，
> 截图见 [docs/verification-window-background.png](docs/verification-window-background.png)。

### 能力边界（engine）

| engine | 实现 | 后台/被遮挡时 | 适用场景 |
|---|---|---|---|
| `wgc` | Windows Graphics Capture（随包 `windows_capture` 2.0.1 + numpy） | ✅ **可以**（本机已实测） | Windows 10 1903+；DirectX / 多数游戏窗口 |
| `printwindow` | `PrintWindow`（先试 `PW_RENDERFULLCONTENT(2)`，失败再试 flags 0） | ⚠️ 仅部分 GDI 窗口 | 传统 GDI 程序；DirectX 游戏通常是黑帧，会被判定失败并回退 |
| `window-dc` | `GetWindowDC` + `BitBlt` | ⚠️ 仅部分窗口 | 部分 GDI 窗口后台可用（`auto` 模式下自动尝试） |
| `screen` | 按窗口矩形截屏幕 | ❌ 只能拿到可见像素（被遮挡时抓到的是遮挡物） | 兜底；返回 `warnings:["window may be occluded"]` |

- `engine=auto`（默认）回退链：`wgc → printwindow → window-dc → screen`；
  黑帧判定为 `blackRatio > 0.98` 或唯一色 < 3；
- 显式 `engine=<值>` 时只走指定路径、失败即返回 `WINDOW_CAPTURE_FAILED`，不做回退；
- 结果中的 `engine` 字段如实反映**实际**路径（可能是 `window-dc`，它不在显式枚举里），发生回退时带 `fallbackReason`。

### 依赖与许可

- `python/vendor/windows_capture/`：`windows-capture` 2.0.1 wheel 解包后**随插件分发**，**MIT 许可**，
  LICENSE 一并附上（`python/vendor/windows_capture_LICENSE.txt`）；
- 需要本机 Python 有 `numpy`（DSH 内置 Python 一般已自带；`python/requirements.txt` 中 Pillow 为必需、
  numpy 以可选注释行列在末尾，numpy 缺失时只影响 WGC、会走回退链）；**不需要安装 OpenCV**——
  `windows_capture` 顶层 `import cv2`，后端会注入一个只让 `cv2.imwrite` 抛错的最小 stub，截图不用 OpenCV；
- **运行时不联网**：依赖已随包分发，抓图过程没有任何网络请求；
- 排障用：`status` 结果里有 `windowCapture:{wgcAvailable,vendor,printWindow,note}` 能力字段，
  `--selftest` 也会输出「窗口枚举 / 窗口捕获能力」检查项，可快速判断 WGC 依赖是否可导入；
- 参数注意：`window` 与 `hwnd` **必须二选一**（都不传或同时传都返回 `INVALID_ARGUMENT`）。

### Win10 兼容要点

- 要求 **Windows 10 1903+**（Windows Graphics Capture 的版本门槛）；
- Win10 不支持 `GraphicsCaptureSession.IsBorderRequired`，实现必须传 `cursor_capture=None, draw_border=None`；
  **不要传 `draw_border=False`**，否则会抛 `Toggling the capture border is not supported`；
- 面板和 agent 工具都不暴露 `draw_border` 参数，默认行为已由后端按平台处理好。

### 限制

- 窗口**最小化**时可能拿不到帧（WGC 也抓不到）；游戏在非活动窗口可能暂停渲染，画面会停在最后一帧或抓取失败；
- 受**反截图保护**的窗口（部分游戏/播放器）会失败 → `WINDOW_CAPTURE_FAILED`；
- 独占全屏（exclusive fullscreen）可能抓不到，建议游戏使用窗口化 / 无边框窗口模式；
- `screen` 引擎只能拿屏幕可见像素，窗口被遮挡或部分在屏幕外会失真/失败；
- 非 Windows 平台：`list_windows` / `capture_window` 返回 `UNSUPPORTED_PLATFORM`；
- **DSH agent 沙箱**内 WGC 可能不可用（受宿主/桌面会话限制；本机沙箱里显式 `engine=wgc` 会返回
  `WINDOW_CAPTURE_FAILED: Failed to convert item to GraphicsCaptureItem`，`auto` 会自动回退），
  DSH 宿主进程与普通终端不受影响；`test/acceptance.mjs` 因此只测 `engine=screen`，
  WGC 真后台路径由 Lead 在未受限环境单独验收。

### 最小用法示例

**agent 工具**（先列窗口，再后台抓取「Keep on Mining」窗口）：

```jsonc
// 1. 枚举窗口并按标题过滤
{ "action": "list_windows", "titleContains": "Keep on Mining", "limit": 10 }

// 2. 用返回的 hwnd 后台抓取（auto = WGC 优先，可抓被遮挡的窗口）
{ "action": "capture_window", "hwnd": 984074, "engine": "auto" }

// 3. 用标题子串选窗（与 hwnd 二选一），强制走 WGC
{ "action": "capture_window", "window": "Keep on Mining", "engine": "wgc", "timeoutMs": 5000 }
```

工具返回（注意：**agent 工具不返回 base64**，用 `absolutePath` 配合 `read_image` 看图）：

```json
{ "ok": true, "action": "capture_window",
  "result": { "window": { "hwnd": 984074, "title": "Keep on Mining!", "class": "UnityWndClass",
                          "pid": 1234, "process": "KeepOnMining.exe",
                          "rect": { "x": 949, "y": 512, "width": 1942, "height": 1136 } },
              "engine": "wgc",
              "path": "shot_20261108_171900_123.png",
              "absolutePath": "C:\\Users\\me\\.dsh\\game-control\\shots\\shot_20261108_171900_123.png",
              "width": 1942, "height": 1136, "format": "png", "bytes": 714410 },
  "durationMs": 87 }
```

**Python CLI / JSONL**（PowerShell 下用 `--stdin`，避免引号问题）：

```powershell
cd D:\dsh-chajian\dsh-game-control\python
$py = "C:\Users\<用户名>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
@'
{"id":1,"action":"list_windows","params":{"titleContains":"Keep on Mining","limit":10}}
{"id":2,"action":"capture_window","params":{"window":"Keep on Mining","engine":"wgc","base64":true}}
{"id":3,"action":"capture_window","params":{"window":"No Such Window xyz"}}
'@ | & $py game_control.py --stdin
```

**HTTP 路由**（端口以 DSH Web GUI 地址为准）：

```powershell
# 列出窗口
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' -Body '{"action":"list_windows","params":{"limit":20}}'

# 后台抓取窗口（面板预览用的就是 base64:true；下载图片可省略 base64 只取 absolutePath）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' `
  -Body '{"action":"capture_window","params":{"window":"Keep on Mining","engine":"auto","base64":true}}'
```

---

## 游戏自动化（v0.3）

v0.3 新增 5 个面向游戏自动化的动作：`activate_window`、`mouse_circle`、`wait`、`find_image`、`wait_image`，
配合 v0.2 的 `list_windows` / `capture_window` 和原有键鼠动作，agent 可以完成《Keep on Mining!》这类
「找按钮 → 点击 → 画圈挖矿 → 等完成 → 下一轮」的循环。

### 推荐流程

```
list_windows            # 找到游戏窗口（hwnd / clientRect）
  → activate_window     # 把游戏窗口切到前台（键鼠只影响前台窗口！）
  → wait_image          # 用 templates 多模板等「开始挖矿！」按钮出现（完成面板/准备界面两种样式）
  → mouse_click         # 点击命中按钮的 center
  → wait                # 等转场（如 2000ms）
  → mouse_circle        # 窗口内连续画圆 30s（半径≈clientRect.width/4，会阻塞，Esc 可中止）
  → （下一轮）重复 wait_image / click / circle
```

> v0.5 流程不再要求先等「挖矿已完成」标题再等 120s 面板：直接等「开始挖矿！」按钮（两种样式用
> `templates` 多模板覆盖），按钮出现即可点击进入下一轮。

最小示例（agent 工具，动态取值、不写死坐标）：

```jsonc
// 1) 找窗口 → 激活
{ "action": "list_windows", "titleContains": "Keep on Mining", "limit": 10 }
{ "action": "activate_window", "hwnd": 984074 }

// 2) 等「开始挖矿！」按钮出现（完成面板/准备界面两种样式，templates 任一命中即可），点击它的中心
{ "action": "wait_image",
  "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"],
  "window": "Keep on Mining", "timeoutMs": 30000, "abortKey": "esc" }
// 命中后拿 match.center.x / match.center.y 传给 mouse_click
{ "action": "mouse_click", "x": 1920, "y": 1690 }

// 3) 等 2 秒转场，再在窗口内画 30s 圆（圆心=clientRect 中心，半径取窗口宽/4=360，Esc 中止）
{ "action": "wait", "durationMs": 2000 }
{ "action": "mouse_circle", "centerX": 1920, "centerY": 1075, "radius": 360,
  "periodMs": 2500, "durationMs": 30000, "samplesPerSecond": 120,
  "button": "none", "abortKey": "esc", "hwnd": 984074 }
```

> 坐标/窗口尺寸必须**动态读取**（`list_windows.clientRect`、`wait_image.match`），不要写死；上面的数字只是示例。

### Esc 中止（v0.5 默认键）

- `mouse_circle` / `skill_run` 默认 `abortKey: "esc"`；**`find_image` / `wait_image` 也支持 `abortKey`（默认 esc）**：
  执行/轮询期间用 `GetAsyncKeyState` 检查，按下 Esc **立即停止**（等待模板阶段也不再等满 timeout），
  返回 `ok:true, aborted:true, abortReason:"abort-key"`（**中止不是错误**，不需要重试）；
- `skill_run` 会把自身 `abortKey` 自动透传给步骤里未显式设置的 `find_image`/`wait_image`/`mouse_circle`；
  技能运行中按 Esc，整个技能会中止并返回 `aborted:true`；
- 显式 `abortKey: "none"` 可禁用中止键（不建议）；键名兼容 `escape` / `esc`；
- `mouse_circle` 传了 `hwnd` 时会校验前台窗口：画圆期间前台窗口变化 → 自动停止，`abortReason:"foreground-changed"`；
- 无论中止、异常还是前台变化，**退出路径都会释放按住的鼠标键**（`button` 非 none 时）；
- 中止键是一枚全局键盘状态查询，只在动作执行期间查询，不注册全局热键、不常驻监听。

### mouse_circle 的阻塞语义

- 一次 `mouse_circle` 请求会**同步阻塞 `durationMs`**（默认 30000ms），期间 Python worker 被占用、不会响应其它请求；
  agent 调用时应预留等待时间；
- Host 有单独的超时预算，避免 30s 动作被默认 15s 杀掉（见下表）；`durationMs + leadInMs + 5s` 内不会超时；
- 想先验证参数而不真实移动鼠标，用 `dryRun: true`（只返回采样/起止点统计，**不注入、不等待**）；
- 采样公式：`samples = floor(durationMs / 1000 × samplesPerSecond) + 1`，用绝对时间调度避免漂移；
- 圆包围盒必须完整落在虚拟桌面内，否则 `INVALID_ARGUMENT`（调整 center/radius 或减小半径）。

### 能力自检

`status` 结果里的两个能力字段可用来判断当前环境是否满足：

- `status.windowCapture:{wgcAvailable,vendor,printWindow,note?}` —— WGC/PrintWindow 后台截图能力（v0.2）；
- `status.automation:{mouseCircle,findImage,skills}` —— 圆形轨迹 / 模板匹配 / 技能库能力（v0.3，Windows 上应为全 `true`；
  `findImage` 取决于 numpy 是否可用）。

`--selftest` 还会输出 `numpy 可用`、`模板目录可写`、`GetAsyncKeyState 可用` 等检查项，可作为排障的第一手信息。

### 超时规则（Host `timeoutFor`，v0.3 更新）

| action | Host 单请求超时预算 |
|---|---|
| `activate_window` | `min(10000, timeoutMs(默认 2000) + 5000)` |
| `mouse_circle` | `min(180000, 15000 + leadInMs(默认 300) + durationMs(默认 30000) + 5000)` |
| `wait` | `min(620000, durationMs + 5000)` |
| `find_image` | `30000` |
| `wait_image` | `min(620000, timeoutMs(默认 15000) + 5000)` |
| `skill_run` | `min(620000, timeoutMs(默认 300000) + 120000)`（v0.6：+120s 余量，让 Python 总 deadline 先优雅中止而不是被 SIGTERM 强杀）；其余 `skill_*` 15000 |
| `loop_start`（v0.6） | 15000（先 dryRun 校验后立即返回，循环在后台逐轮跑） |
| `loop_status`（v0.6） | 15000 |
| `loop_stop`（v0.6） | 30000（`immediate:true` 需等 worker 终止） |
| `screenshot` / `capture_window` | 30000 |
| 其它动作 | v0.2 原值（默认 15000；`type_text` 按长度放宽；`mouse_drag` 按 duration 放宽） |

超时发生后 Host 会 kill 并重启 Python worker，返回 `TIMEOUT`；长动作请给足 `durationMs`/`timeoutMs`。

### 模板制作方法（`find_image` / `wait_image`）

1. 先用 `capture_window`（WGC 可在后台/遮挡时抓）或 `screenshot` 截到目标画面；
2. 用任意图片工具（或 Pillow）**裁剪按钮的文字区域**作为模板（不要整块带边框/悬停高亮的大按钮）：
   ```powershell
   $home = "$env:USERPROFILE\.dsh\game-control"
   # 例：从 window.png 裁出 (x,y,w,h) 区域存成模板
   & $py -c "from PIL import Image; Image.open('window.png').crop((x, y, x + w, y + h)).save(r'$home\templates\keep-on-mining\start_game.png')"
   ```
3. 模板放 `<GAME_CONTROL_HOME>/templates/<game>/`（相对路径即相对该目录；也可用绝对路径）；
   支持 PNG/JPG，≤2MB、宽高 ≤1024；
4. 阈值建议 **0.82–0.88**；同一窗口分辨率下模板可反复用；分辨率/缩放变化时需重新裁剪（或用 `scales:[0.8,1.0,1.25]` 多尺度）；
5. 找不到命中不是错误（`found:false`），可用 `debugPath` 输出带匹配框的调试图排障；
6. **多模板（v0.5）**：同一个按钮有不同样式（如「开始挖矿！」在完成面板与准备界面）时，把多个模板传给
   `templates` 数组（1–8 个，任一命中即返回）；示例：
   `{"action":"wait_image","templates":["keep-on-mining/next_round.png","keep-on-mining/start_mining.png"],"window":"Keep on Mining","timeoutMs":30000}`；
   面板「模板找图」可用逗号分隔多个路径；`templates` 与 `template` 二选一（同时给/空数组/超过 8 个 → `INVALID_ARGUMENT`）。

### 剩余风险

- **SendInput 与游戏输入**：部分游戏使用 DirectInput / Raw Input / 反作弊，可能忽略 `SendInput` 注入的合成输入；
  先用小范围 `mouse_move` 验证游戏内光标是否跟随，不跟随则需另做输入后端（本插件未包含）；
- **独占全屏**：建议游戏使用窗口化/无边框窗口，否则窗口截图与画圆可能异常；
- **DSH 单次工具调用外部硬超时**：若宿主对一次工具调用有 <35s 的硬超时，30s 的 `mouse_circle` 需改为
  分段执行（例如 3×10s）或用技能库编排；本插件未引入后台任务模型（P2 预留接口 `job_*`/`run_macro` 未实现）；
- **模板随分辨率失效**：窗口尺寸/DPI/缩放变化后模板需重做；多尺度匹配可缓解但会变慢；
- **Esc 占用**：若游戏/系统已把 Esc 用作其它功能，可改用其它 `abortKey` 或 `"none"`（不建议）；
- 输入动作只影响**当前前台窗口**，务必先 `activate_window` 且确认返回 `foreground:true`。

---

## 技能库（v0.3）

技能库把「已适配好的游戏流程」保存成**可读的 skill**（Markdown 说明 + 步骤 JSON），支持人工查看/编辑、
自动备份与版本恢复、软删除、以及被 agent/面板复用执行。

### 存储位置与目录结构

根目录：`<GAME_CONTROL_HOME>/skills/`（默认 `%USERPROFILE%\.dsh\game-control\skills`）：

```
skills/
├── <id>/
│   ├── skill.json              # 当前版本：JSONC（带中文注释），原子写（tmp + os.replace）
│   └── backups/
│       └── <ts>.json           # 每次保存/恢复/删除前的旧版本（保留原始 JSONC 文本），最多保留 50 份（新的在前）
└── .trash/
    └── <id>-<ts>/              # 软删除的技能（skill_delete hard:false）
```

> v0.4 起 `skill.json` 是 **JSONC**：允许 `//` 行注释与 `/* */` 块注释；读取时先剥离注释再解析（字符串内的 `//`
> 不受影响），旧的无注释纯 JSON 文件完全兼容；写入时统一由插件重新渲染为带中文注释的 JSONC。
> 完整格式、注释规则与归一化参数见[技能文件格式（JSONC + 中文注释）](#技能文件格式jsonc--中文注释)。

技能**运行进度**文件（v0.5）：`<GAME_CONTROL_HOME>/logs/skill-progress.json`（与 `skills/` 同级、原子写；
`skill_run` 开始/每步/结束时更新，字段见[运行进度与防卡死](#运行进度与防卡死v05--v06)）。

### `skill.json` 字段与限制

（实际文件是 JSONC，下面为了紧凑用纯 JSON 展示；带中文注释的完整示例见[技能文件格式](#技能文件格式jsonc--中文注释)。）

```json
{
  "schema": 1,
  "id": "keep-on-mining-30s",
  "title": "Keep on Mining 30s 挖矿循环",
  "description": "激活窗口、等「开始挖矿！」按钮出现、点击、画 30s 圆进入下一轮",
  "game": "Keep on Mining!",
  "tags": ["mining", "loop"],
  "markdown": "## 说明\n等按钮出现即点击，不再等 120s 面板；30s 圆形轨迹；按 Esc 中止。",
  "steps": [
    { "action": "activate_window", "params": { "window": "Keep on Mining" }, "waitMs": 0, "continueOnError": false, "saveAs": "win" },
    { "action": "wait_image", "params": { "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"], "window": "Keep on Mining", "timeoutMs": 30000, "onMissing": "fail" }, "waitMs": 0, "continueOnError": false, "saveAs": "start" },
    { "action": "mouse_click", "params": { "x": "$start.match.center.x", "y": "$start.match.center.y", "button": "left", "clicks": 1, "intervalMs": 60 }, "waitMs": 0, "continueOnError": false },
    { "action": "wait", "params": { "durationMs": 2000 }, "waitMs": 0, "continueOnError": false },
    { "action": "mouse_circle", "params": { "centerX": "$window.clientCenterX", "centerY": "$window.clientCenterY", "radius": 360, "periodMs": 2500, "durationMs": 30000, "direction": "cw", "samplesPerSecond": 120, "button": "none", "abortKey": "esc" }, "waitMs": 0, "continueOnError": false, "note": "半径 360（用户指定，约等于窗口宽 1/4 的一半）；Esc 中止" }
  ],
  "createdAt": "2026-11-08T10:00:00.000Z",
  "updatedAt": "2026-11-08T10:00:00.000Z"
}
```

| 字段 | 限制 |
|---|---|
| `id` | 正则 `^[a-z0-9][a-z0-9._-]{0,63}$`；新建缺省时按 title 生成 slug |
| `title` | 1–200 字符 |
| `description` | ≤2000 字符 |
| `game` | ≤100 字符 |
| `tags` | ≤20 个，每个 ≤40 字符 |
| `markdown` | ≤200000 字符（给人看的说明） |
| `steps` | 1–200 步；每步 `params` 序列化后 ≤32768 字节 |
| `saveAs`（步骤级） | 正则 `^[A-Za-z_][A-Za-z0-9_]{0,31}$` |
| `note`（步骤级，v0.4） | 自定义中文备注，≤200 字符；**保存时保留并拼进该步注释** |
| `onMissing`（步骤级，v0.6.2） | 仅 `find_image`/`wait_image` 有效：`"fail"`（默认，找不到模板即该步失败）或 `"continue"`（继续，match 为 null）；保存时自动写显式 `"fail"` |
| 单步允许字段 | `action`、`params`、`saveAs?`、`continueOnError?`（显式布尔）、`waitMs?`（显式数字）、`note?`、`onMissing?`（v0.6.2） |

`steps[].action` 白名单 = 除 `skill_*` 外的全部控制动作（含 `wait`/`activate_window`/`mouse_circle`/
`find_image`/`wait_image`）；未知动作/未知步骤字段 → `INVALID_SKILL`。
保存前会对每步做**参数归一化**（把默认参数显式写进文件），见[归一化默认参数表](#归一化默认参数表)。

### 技能动作（HTTP `/exec` 与 `game_skill` 工具共用）

| action | 参数 | 返回 |
|---|---|---|
| `skill_list` | 无 | `{count, skills:[{id,title,description,game,tags,stepCount,updatedAt,backupCount}]}` |
| `skill_get` | `id*` | `{skill, content, stepsView:[{index,action,comment,waitMs,saveAs?}]}`；`content` 是磁盘 JSONC 原文（含中文注释），`stepsView` 是只读步骤视图；不存在 → `SKILL_NOT_FOUND` |
| `skill_save` | 结构化：`title*`,`description?`,`game?`,`tags?`,`markdown?`,`steps*`,`id?`（缺省=新建；给存在的 id=更新）；或全文：`content?`（整份 JSONC，≤1,000,000 字符，传了它则忽略其它内容字段，`id` 需与 content.id 一致） | `{id, created, backupPath, updatedAt, backupCount, content, stepsView}`；首次新建时 `backupPath=null`，更新/覆盖前**自动备份旧版本**，返回重新渲染的 `content` |
| `skill_versions` | `id*` | `{id, count, versions:[{name,savedAt,bytes}]}`（新→旧） |
| `skill_restore` | `id*`,`backup*`（文件名或 `"latest"`） | `{id, restoredFrom, backupOfCurrent?, updatedAt}`；恢复前先备份当前版本，原样恢复历史 JSONC 文本 |
| `skill_delete` | `id*`,`hard?`（默认 false） | `{id, deleted:true, trashPath, hardDeleted}`；默认移到 `.trash/`，`hard:true` 才真删 |
| `skill_run` | `id*`,`dryRun?`,`timeoutMs?`(1000–600000，默认 300000),`abortKey?`(默认 "esc"),`stopOnError?`(默认 true) | `{skillId, dryRun, repeat, repeatsDone, success, aborted, abortReason, durationMs, steps:[...], failedStep?, estimatedDurationMs?}`；**单轮底层原语**；读取时自动剥离注释并归一化参数；**执行前做时长前置校验**，见下；v0.6 起 Python 按总 deadline 优雅中止（`aborted:true, abortReason:"timeout"`）。**`repeat` 已 deprecated**：工具 schema 不再暴露，仅 CLI/内部兼容；多轮用 `loop_start.rounds` |
| `skill_progress`（v0.5） | 无 | `{progress:{running,skillId,startedAt,elapsedMs,repeat,repeatsDone,step:{index,total,action,startedAt},updatedAt,success?,finishedAt?,stale?,abortReason?}\|null}`；读 `<GAME_CONTROL_HOME>/logs/skill-progress.json`（运行期间也可用） |
| `loop_start`（v0.6/v0.6.1，Host 级，**统一 N 轮入口**） | `skillId*`,`rounds*`(1–10000；**1=单次**),`intervalMs?`(0–60000，默认 500),`stopOnError?`(默认 true),`abortKey?`(默认 "esc"),`roundTimeoutMs?`(1000–600000；省略时 dryRun 估算后取 `estimated+30000`) | `{jobId, running:true, skillId, rounds, intervalMs, perRoundTimeoutMs, estimatedDurationMs, startedAt}`；每轮单独 `skill_run`；重复启动 → `LOOP_ALREADY_RUNNING` |
| `loop_status`（v0.6/v0.6.1，Host 级） | 无 | `{running, jobId, skillId, rounds, completedRounds, failedRounds, currentRound, stopRequested, startedAt, finishedAt?, perRoundTimeoutMs, estimatedDurationMs, lastError?, currentStep?:{index,total,action,elapsedMs}, results:[最近 20 轮]}` |
| `loop_stop`（v0.6，Host 级） | `immediate?`(默认 false) | 当前轮结束后停止；`immediate:true` 终止 worker 立即停止（该轮 error `BACKEND_UNAVAILABLE`） |

> **唯一轮数语义（v0.6.1）**：`rounds=1` → 单次运行；`rounds>1` → 循环；每轮仍是独立的 `skill_run` 调用，
> 统一由 `loop_start` 编排。`skill_run.repeat` 已 deprecated（仅 Python CLI/内部兼容，面板与 agent 工具 schema 不暴露）。

`skill_run` 返回（`repeat`/`repeatsDone` 为 Python 层字段；**`repeat` 已 deprecated**，仅 CLI/内部兼容，循环请用 `loop_start.rounds`）：

```json
{ "skillId": "keep-on-mining-30s", "dryRun": false, "repeat": 1, "repeatsDone": 1,
  "success": true, "aborted": false, "abortReason": null, "durationMs": 42153,
  "estimatedDurationMs": 41000,
  "steps": [ { "index": 0, "action": "activate_window", "ok": true, "durationMs": 12 } ] }
```

- **时长前置校验（v0.5，dryRun 也执行）**：执行前估算最坏时长
  `estimatedDurationMs = Σ(每步最大耗时) + ΣwaitMs + 500×步数`（`wait→durationMs`、`mouse_circle→leadInMs+durationMs`、
  `wait_image→timeoutMs`、`find_image→8000`、其它→1000）；若 `estimatedDurationMs > timeoutMs` →
  `INVALID_ARGUMENT`（hint：至少需要 X ms，可增大 `timeoutMs` 或缩小 `wait_image`/`mouse_circle` 参数）；
  通过时结果附带 `estimatedDurationMs`；
- 运行期某步失败时返回 `ok:true`，但 `result.success=false`，并带
  `result.failedStep:{index,action,code,message,screenshotPath?}`；`stopOnError:true`（默认）立即停止；
  模板类失败（v0.6.2）`code` 为 `WAIT_IMAGE_TIMEOUT`（wait_image 超时）或 `TEMPLATE_NOT_FOUND`（find_image 模板缺失），
  且 `screenshotPath` 指向 `<GAME_CONTROL_HOME>/logs/skill-fail-*.png` 失败截图；
- `onMissing`（v0.6.2，步骤级，仅 find_image/wait_image）：默认 `"fail"` → 找不到模板立即该步失败、不执行后续步骤；
  置 `"continue"` 则继续（`match=null`，后续变量引用会报变量错误——显式选择）；详见
  [模板等待超时修复（v0.6.2）](#模板等待超时修复v062)；
- `aborted:true` 时 `abortReason` 为 `"abort-key"`（按了 Esc/中止键）或 `"timeout"`（整次运行超过 `timeoutMs`）；
  中止不是执行错误，`ok` 仍为 `true`；
- `abortKey`（默认 **esc**）会自动透传给步骤里未显式设置的 `find_image`/`wait_image`/`mouse_circle`，
  因此**等待模板阶段按 Esc 也能立即中止整个技能**；
- `dryRun:true` 只做静态校验与变量规划，**不执行任何动作、不等待**；此时 `steps` 是「计划」列表
  （每项含 `index/action/ok/planned/saveAs/waitMs/varRefs`），`repeatsDone=0`、`durationMs=0`。

### 运行进度与防卡死（v0.5 / v0.6）

- **进度文件**：`<GAME_CONTROL_HOME>/logs/skill-progress.json`（原子写）。`skill_run` 在开始 / 每步开始 /
  每步结束 / 结束时更新：`{running, skillId, startedAt, elapsedMs, repeat, repeatsDone, step:{index,total,action,startedAt}, updatedAt}`；
  结束时 `running:false`（并记录 `success`/`finishedAt`）；
- **进度接口**：`GET /api/game-control/skill-progress` 由 Host 直接读该文件（**不经 Python worker**，因此技能运行期间
  也能立刻返回），v0.6 起响应为 `{ok:true, progress, bridge:{running,lastError}}`；Python 侧也提供 `skill_progress`
  动作（读文件，返回 `{progress}`）；
- **面板运行态**：技能运行期间每 ~700ms 轮询该接口，显示「**正在执行第 N/M 步 · action · 已等待 X 秒**」；
  运行期间**禁用技能库的保存/刷新/新建/删除/恢复/JSONC 保存**（以及其它会排队到同一 worker 的操作），
  结束后恢复并显示结果；超时/失败时显示 `failedStep` 与错误码；
- **为什么禁用**：Python worker 是**单请求串行**的——`skill_run` 执行 30s+ 时，若再发 `skill_save`/`list_windows`
  等请求会排队等到运行结束，界面看起来像"卡死"。v0.5 用进度接口 + 运行期禁用避免这个问题；运行结束后再操作即可；
- **v0.6 连接中断修复**：worker 退出/被强杀时进度文件立即标记 `running:false, stale:true, abortReason:"backend-stopped"`
  （保留原 skillId/step）；面板轮询失败或收到 `stale:true`/`bridge.running===false` 时停止轮询，显示
  「后端连接中断（可能因超时被杀/被停止）」+「重置状态」按钮，不再永久「运行中」；
- 大量轮数（成百上千轮）请改用 **`loop_start` 长循环**（每轮独立 `skill_run`，不受单请求超时限制），
  见[长循环与连接修复（v0.6）](#长循环与连接修复v06)。

### 查看 / 编辑 / 保存自动备份 / 版本恢复 / 软删除

- **查看**：`skill_list` 看列表；`skill_get` 返回 `{skill, content, stepsView}`——`content` 是磁盘上的 JSONC 全文
  （含中文注释），`stepsView` 是 `[{index, action, comment, waitMs, saveAs?}]` 只读步骤列表；面板里选中技能即可看到；
- **编辑（面板结构化表格，v0.5 默认）**：技能库（面板第二项）里用**结构化步骤表格**直接改每一步：`#`、`操作(action)`、
  关键条件/参数单元格、`等待(ms)`、`备注(note)`、上移/下移/删除；「保存」用结构化字段调 `skill_save`，
  成功后再刷新表格与 JSONC 高级编辑器；
- **编辑（面板完整配置文件）**：在「高级」折叠区编辑完整 JSONC 全文 →「保存」发送 `{id, content}`，
  保存成功后用返回的 `content` 刷新编辑器（参数被自动补全、注释被重新生成）；
- **编辑（文件）**：也可以直接编辑 `<GAME_CONTROL_HOME>/skills/<id>/skill.json`（JSONC），**下次读取/运行即生效**；
  手工写的注释会在下次保存时被覆盖（数据不变），自定义说明请写步骤的 `note` 字段；
- **保存自动备份**：`skill_save`（更新已有 id）会先把旧 `skill.json` 复制到 `backups/<ts>.json`，再原子写入新版本；
  备份保留**原始 JSONC 文本**，最多保留 50 份；
- **版本恢复**：`skill_versions` 列出备份 → `skill_restore`（`backup:"latest"` 或具体文件名）回滚历史 JSONC 文本；
  恢复前会先备份当前版本；
- **软删除**：`skill_delete` 默认把技能目录移动到 `.trash/<id>-<ts>/`（不会丢数据，可手工恢复）；
  `hard:true` 才真正删除；软删后的技能不再出现在 `skill_list`。
- **旧格式兼容**：v0.3 保存的无注释纯 JSON 技能文件仍可读取/执行；下次保存时会自动升级为带注释的 JSONC。

### 变量语法（`$window` / `$saveAs`）

步骤参数里**以 `$` 开头的字符串**会被解析：

| 变量来源 | 可用字段/示例 |
|---|---|
| `$window.*` | 最近一次 `activate_window` / `list_windows` 解析出的窗口：`hwnd,title,class,pid,process,rect,clientRect`，以及计算值 `clientCenterX`、`clientCenterY`、`quarterWidth`（=round(clientRect.width/4)）、`halfWidth`、`halfHeight` |
| `$<saveAs>.*` | 该步返回结果的对象路径，支持数组数字下标：如 `$start.matches.0.center.x`、`$win.windows.0.hwnd` |

- 例：`{"action":"mouse_circle","params":{"centerX":"$window.clientCenterX","centerY":"$window.clientCenterY","radius":"$window.quarterWidth"}}`；
- 无法解析的变量 → 该步失败，错误码 `SKILL_VAR_UNRESOLVED`；
- 非 `$` 开头的字符串保持原样。

### 面板使用步骤

1. 面板第二项「技能库」点「刷新」拉取列表；
2. 选中技能：默认显示**结构化步骤表格**（`#` / 操作 / 关键条件参数 / 等待(ms) / 备注 / 上移·下移·删除），
   直接改关键参数（如 `wait_image` 的模板/多模板/超时、`mouse_circle` 的半径/时长/中止键、`mouse_click` 的坐标/次数/间隔）
   与每步 `waitMs`/`note`；「新增步骤」插入默认 `wait`（也可选常用模板）；
3. 「保存」→ 后端归一化 + 生成中文注释 JSONC，成功后刷新表格与高级编辑器；「版本」可恢复历史；
4. 「高级」折叠区可编辑完整 JSONC（`content` 模式保存），与表格两种方式互斥切换；
5. **「运行」**：填 `轮数`（默认 1，1–10000；=1 单次、>1 循环）与 `轮间隔`（>1 生效），可勾选 dryRun、
   「立即停止」；运行期间显示「第 N/M 轮 · 已完成 X · 失败 Y · 当前轮已等待 Zs + 当前步骤」并禁用保存/刷新，
   Esc 可中止；结束后显示汇总/失败信息；
6. 「停止」→ `loop_stop`（勾选「立即停止」时终止当前轮）；
7. 「删除」默认软删（进 `.trash/`）；
8. 面板所有请求都走 `/api/game-control/exec`（进度统一走 `loop_status`），错误以中文展示。

### agent 调用示例（`game_skill` 工具）

```jsonc
// 列出技能
{ "action": "skill_list" }

// 读取完整技能（skill + content(JSONC) + stepsView）
{ "action": "skill_get", "id": "keep-on-mining-30s" }

// 新建/更新：结构化字段；steps 用「等开始挖矿按钮（多模板）→ 点击 → wait 2s → 圆形 30s(Esc)」
{ "action": "skill_save",
  "title": "Keep on Mining 30s 挖矿循环", "game": "Keep on Mining!",
  "description": "激活窗口、等开始挖矿按钮、点击、画 30s 圆",
  "markdown": "## 说明\n按 Esc 可中止。",
  "steps": [
    { "action": "activate_window", "params": { "window": "Keep on Mining" } },
    { "action": "wait_image", "params": { "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"], "window": "Keep on Mining", "timeoutMs": 30000, "onMissing": "fail" }, "saveAs": "start" },
    { "action": "mouse_click", "params": { "x": "$start.match.center.x", "y": "$start.match.center.y" } },
    { "action": "wait", "params": { "durationMs": 2000 } },
    { "action": "mouse_circle", "params": { "centerX": "$window.clientCenterX", "centerY": "$window.clientCenterY", "radius": 360, "durationMs": 30000, "abortKey": "esc" } }
  ] }

// 版本与恢复
{ "action": "skill_versions", "id": "keep-on-mining-30s" }
{ "action": "skill_restore", "id": "keep-on-mining-30s", "backup": "latest" }

// 严格只校验不执行（返回 estimatedDurationMs；单轮底层原语）
{ "action": "skill_run", "id": "keep-on-mining-30s", "dryRun": true }

// 统一轮数入口：rounds=1 单次；rounds>1 循环（每轮独立 skill_run，Esc 可中止）
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 1 }
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 2, "intervalMs": 500 }
{ "action": "loop_status" }
{ "action": "loop_stop", "immediate": true }

// 运行进度（运行期间也可查；Host 路由不经 Python worker）
{ "action": "skill_progress" }

// 软删除（进 .trash/）
{ "action": "skill_delete", "id": "keep-on-mining-30s" }
```

HTTP 版（端口以 DSH Web GUI 地址为准）：

```powershell
# 运行技能（dryRun 只校验）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' `
  -Body '{"action":"skill_run","params":{"id":"keep-on-mining-30s","dryRun":true}}'

# 运行进度（技能运行期间也可用，面板每 ~700ms 轮询的就是它）
Invoke-RestMethod -Uri http://127.0.0.1:5748/api/game-control/skill-progress
```

> 安全提示：`skill_run` 会执行技能里的全部动作（可能包含真实点击/键盘/画圆）。只有在你确认技能内容后，
> 才用 `dryRun:false` 运行；运行时先 `activate_window` 并保持目标游戏在前台，随时可按 Esc 中止。

---

## 长循环与连接修复（v0.6 / v0.6.1）

### 统一轮数语义（v0.6.1）

v0.6 面板里同时存在 `skill_run.repeat`（重复次数）与 `loop_start.rounds`（轮数）两个控制，本质都是"同一技能执行
N 次"，逻辑重复且容易混淆（而且只有 loop 的逐轮独立调用才不会撞单请求超时）。v0.6.1 融合为**唯一「轮数」概念**：

- **只有一个控制：`轮数`（rounds）**，范围 1–10000：
  - `rounds = 1` → **单次运行**（等价于原来的"运行一次 / repeat=1"）；
  - `rounds > 1` → **循环执行**，每轮仍是一次**独立** `skill_run` 调用（Host LoopRunner）；
- **`loop_start` 是统一的 N 轮入口**（`rounds=1` 也走 LoopRunner 单次执行）；面板只有「轮数 + 轮间隔 + 立即停止 +
  运行 / 停止」，**不再有「重复次数」输入，也没有「运行一次 / 启动循环」两个按钮**；
- **`skill_run` 仅保留为"只执行 1 轮"的底层原语**（由 LoopRunner 每轮调用）；agent 工具 schema **移除 `repeat`
  参数**；`repeat` 在 Python 后端仍兼容（**仅 CLI/内部调试用，已 deprecated**），多轮请用 `loop_start.rounds`；
- 进度统一用 `loop_status` 轮询：每轮独立更新，`loop_status.currentStep` 提供当前步骤，单轮不再单独维护一套按钮。

### 背景：repeat=99 为什么会被强杀

v0.5 之前，`skill_run` 的 `repeat=99` 是一次**单请求**执行：Host 对单请求的超时是 `timeoutMs + 10s`（默认约
310s），一旦超时就直接 **SIGTERM 杀 Python worker**——日志没有 `mouse_circle end`、`skill-progress.json` 停在
`running:true`、面板永久"运行中"、浏览器出现 `Failed to fetch`（连接中断）。v0.6 用两件事修掉它：

1. **长循环 `loop_start`**：每轮是**独立的一次 `skill_run` 调用**，大量轮数不再受单请求 310s 限制；
2. **Host 超时余量 + Python 总 deadline 优雅中止**：Host 给 `skill_run` 的预算改为 `timeoutMs + 120000`（+120s），
   让 Python 在动作内部按总 deadline 先停下并返回 `aborted:true, abortReason:"timeout"`，而不是被强杀。

### 长循环动作：`loop_start` / `loop_status` / `loop_stop`

三个动作由 **Host 进程直接处理**（不需要 Python worker），可通过 `/api/game-control/exec` 与 `game_skill` 工具调用。

**`loop_start` 参数**：

| 参数 | 说明 |
|---|---|
| `skillId*` | 要循环执行的技能 id |
| `rounds*` | 轮数，1–10000；**rounds=1 即单次运行**、rounds>1 循环（每轮独立 `skill_run`） |
| `intervalMs?` | 轮间隔，0–60000，默认 500 |
| `stopOnError?` | 默认 true：某轮失败/中止即停止循环；面板「失败后继续」= `stopOnError:false` |
| `abortKey?` | 默认 `"esc"`，透传给每轮 `skill_run` |
| `roundTimeoutMs?` | 1000–600000；省略时先做一次 `skill_run dryRun` 取 `estimatedDurationMs`，取 `estimated+30000`（clamp） |

- 先 `dryRun` 校验（技能不存在/参数非法直接返回错误），然后**立即返回**：
  `{jobId, running:true, skillId, rounds, intervalMs, perRoundTimeoutMs, estimatedDurationMs, startedAt}`；
- 之后后台按轮执行：**每轮单独调用一次**
  `bridge.execute('skill_run', {id, repeat:1, timeoutMs:perRoundTimeoutMs, abortKey, stopOnError})`，
  记录每轮结果（success/aborted/error/durationMs），等 `intervalMs` 后进入下一轮；
- `stopRequested` 或 `stopOnError` 命中失败/中止时停止；结束后写 `running:false, completedRounds, failedRounds, finishedAt`；
- 同一时间只允许一个循环，重复启动 → **`LOOP_ALREADY_RUNNING`**；插件卸载/进程退出时停止循环并清理定时器。

**`loop_status` 返回**：

```json
{ "running": true, "jobId": "loop-1", "skillId": "keep-on-mining-30s", "rounds": 10,
  "completedRounds": 3, "failedRounds": 1, "currentRound": 5, "stopRequested": false,
  "startedAt": "2026-11-08T12:00:00.000Z", "perRoundTimeoutMs": 60000, "estimatedDurationMs": 30000,
  "currentStep": { "index": 2, "total": 5, "action": "mouse_circle", "elapsedMs": 8200 },
  "lastError": "第 1 步 wait_image: [WAIT_IMAGE_TIMEOUT] 等待模板超时（30000 ms）…",
  "results": [
    { "round": 1, "ok": false, "aborted": false, "success": false, "durationMs": 30120,
      "failedStep": { "index": 1, "action": "wait_image", "code": "WAIT_IMAGE_TIMEOUT",
                      "message": "等待模板超时（30000 ms）：模板 keep-on-mining/next_round.png, …",
                      "screenshotPath": "…\\logs\\skill-fail-20261108_120112-step1.png" } },
    { "round": 2, "ok": true, "aborted": false, "success": true, "durationMs": 31200 }
  ] }
```

- `finishedAt?`、`lastError?` 视状态出现；`results` 只保留**最近 20 轮**；
  v0.6.2 起每轮条目含 `failedStep:{index,action,code,message,screenshotPath?}` 与 `aborted/abortReason`（失败轮次带失败截图路径）；
- **`currentStep`（v0.6.1）**：运行中且 `skill-progress` 属于同一技能时，Host 把 Python 进度里的当前步骤合并进来：
  `{index,total,action,elapsedMs}`，面板据此显示「当前步骤」；worker 退出时仍由 stale 机制收口；
- **`loop_stop`**：`{immediate?}`（默认 false）。默认置 `stopRequested`，**当前轮结束后停止**；
  `immediate:true` 时终止 worker，使当前轮立即失败（该轮 `error.code = "BACKEND_UNAVAILABLE"`）并停止循环。

**状态持久化**（便于事后核对与外部监控）：

- `<GAME_CONTROL_HOME>/logs/loop-state.json`：原子写，每次状态变化更新（running/jobId/轮数/当前轮/results 摘要等）；
- `<GAME_CONTROL_HOME>/logs/loop.jsonl`：**每轮一行**（round/ok/aborted/success/durationMs/errorCode 等），可直接 tail。

### 为什么不再撞 Host 310s

- **Host 超时**：`timeoutFor('skill_run') = min(620000, (timeoutMs ?? 300000) + 120000)`（v0.6 从 +10s 提到 **+120s**）；
  `loop_start` 15000 / `loop_status` 15000 / `loop_stop` 30000（immediate 需等 worker 终止）；
- **Python 总 deadline**：`skill_run` 开始时设置 `runtime.skill_deadline = monotonic() + timeoutMs/1000`，步骤前后检查；
  到点停止执行并返回 `{success:false, aborted:true, abortReason:"timeout"}`，进度文件写
  `running:false, aborted:true, abortReason:"timeout", finishedAt`；
- 长动作内部也尊重 deadline：`mouse_circle` 有效时长=`min(durationMs, 剩余时间)`、每帧检查并提前停止（仍释放鼠标键）；
  `wait` 睡眠截断；`wait_image` 有效 `timeoutMs=min(timeoutMs, 剩余时间)` 且轮询内检查；
  `find_image` 扫描前后检查——到点都返回 `aborted:true, abortReason:"timeout"`；
- 因此正常情况下 **Python 先优雅停下、Host 再收结果**，不会出现 SIGTERM 强杀与"卡在 running:true"。

### worker 退出 / 连接中断（Failed to fetch）修复

- worker 退出（正常/超时/被杀）时，Host **立即**把 `skill-progress.json` 标记为
  `running:false, stale:true, abortReason:"backend-stopped", finishedAt`（保留原 skillId/step），原子写；
- `GET /api/game-control/skill-progress` 返回 `{ok:true, progress, bridge:{running,lastError}}`；
  若 `progress.running===true` 但 `bridge.running===false` → 返回 `running:false, stale:true` 并顺手写回文件；
- **面板**：进度轮询请求失败（网络错误/超时）→ 停止轮询，显示「**后端连接中断（可能因超时被杀/被停止）**」，
  提供「**重置状态**」按钮（清除本地 running、恢复按钮），不再显示原始 `TypeError: Failed to fetch`；
  收到 `stale:true` 或 `bridge.running===false` 而本地仍在跑 → 同样停止轮询 + 提示 + 重置；
  所有 fetch 错误统一为中文可读提示；
- 连接中断后若要继续，点「重置状态」→「刷新」→ 重新运行（worker 会在下次请求时自动重启）。

### 面板循环 UI 用法（v0.6.1 单套控制）

- 技能运行区只有：`轮数 rounds`（**默认 1**，1–10000）、`轮间隔 intervalMs`（默认 500，**仅轮数>1 时生效**）、
  「**立即停止**」复选、唯一按钮「**运行**」与「**停止**」；**没有「重复次数」输入，也没有「运行一次 / 启动循环」两套按钮**；
- v0.6.2 新增「**失败后继续**」复选（默认不勾=遇失败停止），对应 `loop_start.stopOnError=false`；
- 「运行」统一调用 `loop_start`（把轮数传入；**rounds=1 也走 LoopRunner 单次执行**），「停止」调用 `loop_stop`；
- 运行/循环期间统一轮询 `/exec {action:'loop_status'}`（~700ms）显示：
  「**第 N/M 轮 · 已完成 X · 失败 Y · 当前轮已等待 Zs**」+ **当前步骤**（`loop_status.currentStep:{index,total,action,elapsedMs}`）；
- 每轮失败时显示 `failedStep`（步骤/动作/错误码/消息）与**失败截图路径**（`logs/skill-fail-*.png`）；
- 运行/循环期间禁用保存/刷新/新建/删除/恢复/运行；结束后显示汇总（completed/failed/lastError）；
- 保留 dryRun 干跑（单次、不计数）；无 `setInterval`，定时器在停止/卸载时清理。

### 示例（agent 工具 `game_skill` / HTTP `/exec`）

```jsonc
// 统一入口：rounds=1 单次运行；rounds>1 循环（每轮独立 skill_run，先 dryRun 校验）
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 1 }
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 10, "intervalMs": 500 }

// 查询进度（运行期间可反复调用；含 currentStep 当前步骤）
{ "action": "loop_status" }

// 停止：默认等当前轮结束；immediate:true 立即终止 worker 停止
{ "action": "loop_stop" }
{ "action": "loop_stop", "immediate": true }
```

```powershell
# HTTP 版（端口以 DSH Web GUI 地址为准）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' `
  -Body '{"action":"loop_start","params":{"skillId":"keep-on-mining-30s","rounds":1}}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' -Body '{"action":"loop_status"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' -Body '{"action":"loop_stop","params":{"immediate":true}}'
```

### 注意与限制

- `loop_start` 是**后台逐轮执行**：Host 收到启动响应后循环仍在跑，用 `loop_status` 观察、`loop_stop` 停止；
  插件卸载/DSH 退出会停止循环并清理定时器；
- `immediate:true` 会终止 worker，**当前轮**会以 `BACKEND_UNAVAILABLE` 失败（循环随之停止），这是预期行为；
- 循环本身不注入输入，但每轮 `skill_run` 会按技能步骤执行真实输入；请先确认技能内容并保持目标游戏在前台；
- 同一时间只允许一个循环（`LOOP_ALREADY_RUNNING`）；大量轮数直接用 `loop_start.rounds`（1–10000），
  不要再把 `skill_run.repeat` 设大（该参数已 deprecated，仅 CLI/内部兼容）；
- 每轮仍有自己的 `timeoutMs`（`roundTimeoutMs`/估算值），单轮失败/中止按 `stopOnError` 决定是否继续；
  循环里偶发模板超时时可勾选「失败后继续」（=`stopOnError:false`），避免整轮循环中断。

---

## 模板等待超时修复（v0.6.2）

### 问题：`$btn.match.center.x` 的 NoneType 报错

v0.6.1 及更早，循环运行中偶发：

```
第 3 步 mouse_click: [SKILL_VAR_UNRESOLVED] 无法继续解析变量 '$btn.match.center.x'（当前值类型 NoneType）
```

根因：`wait_image` 在 20s 内没找到「开始挖矿！」按钮，按旧语义超时返回
`ok:true, found:false, match:null, timedOut:true`；`skill_run` 没把它当失败，继续执行下一步 `mouse_click`，
展开变量时才发现 `$btn.match` 是 `null`，于是报出与真实原因无关的变量错误，并导致整个循环意外停止
（例如 7 成功 + 1 失败）。v0.6.2 让模板缺失**先失败在该步**，并给出可排查的证据。

### 步骤级 `onMissing`（默认 fail）

- 技能步骤新增可选字段 **`onMissing`**（仅 `find_image` / `wait_image` 步骤有效）：`"fail"`（默认）或 `"continue"`；
- 保存/归一化时：`find_image` / `wait_image` 步骤自动写显式 `"onMissing": "fail"`；生成的注释追加「找不到时：失败/继续」；
- `skill_run` 执行到 `wait_image` / `find_image` 且**未找到模板**时：
  - `onMissing="fail"`（默认）→ 该步**立即记为失败**，不再执行后续步骤（受 `stopOnError` / `continueOnError` 控制）；
    `failedStep.code = "WAIT_IMAGE_TIMEOUT"`（wait_image，超时）或 `"TEMPLATE_NOT_FOUND"`（find_image，模板文件缺失），
    message 写明「等待模板超时（X ms）：模板 A, B；请确认游戏画面/提高 timeoutMs/补充模板」；
  - `onMissing="continue"` → 继续后续步骤（保存的 `match` 为 `null`，后续引用该变量的步骤会报变量错误——这是**显式选择**）；
- **失败证据截图**：模板失败时自动截图（优先失败步骤的 `searchRegion`，其次主屏），保存到
  `<GAME_CONTROL_HOME>/logs/skill-fail-<yyyymmdd_hhmmss>-step<idx>.png`，路径写入 `failedStep.screenshotPath`，
  供人工排查为什么没匹配到；截图失败不影响错误返回；
- **变量错误信息增强**：解析到 `None`/空值时给出明确 hint，如
  「`$btn.match.center.x` 中 `match` 为空：上一步 wait_image 可能未找到模板（见 failedStep.screenshotPath）」；
- **示例技能更新**：`keep-on-mining-30s` 的 `wait_image` 的 `timeoutMs` 由 **20000 调为 30000**，并**显式写 `onMissing:"fail"`**。

### 循环失败策略可配置：「失败后继续」

- 面板循环设置新增 **「失败后继续」** 复选（默认不勾 = 遇失败停止）：对应 `loop_start.stopOnError=false`，
  这样偶发一轮模板超时不会中断整个长循环；失败的轮次仍记录在 `results` / `lastError`；
- `loop_status.results` 每轮条目追加 **`failedStep`**（`{index,action,code,message,screenshotPath}`）与
  `aborted/abortReason`；面板每轮结果/最后错误区域显示失败步骤、错误码与**截图路径**；
- Host LoopRunner 把每轮 `result.failedStep` 拷进 entry，`lastError` 保持人类可读（如「第 1 步 wait_image: [WAIT_IMAGE_TIMEOUT] …」）。

### 示例（失败在该步 + 失败截图）

```json
// skill_run 结果（模板超时、onMissing="fail"）：停在第 2 步，不再执行 mouse_click
{ "success": false, "aborted": false, "abortReason": null,
  "failedStep": { "index": 1, "action": "wait_image", "code": "WAIT_IMAGE_TIMEOUT",
                  "message": "等待模板超时（30000 ms）：模板 keep-on-mining/next_round.png, keep-on-mining/start_mining.png；请确认游戏画面/提高 timeoutMs/补充模板",
                  "screenshotPath": "C:\\Users\\me\\.dsh\\game-control\\logs\\skill-fail-20261108_120112-step1.png" } }
```

```jsonc
// 技能步骤显式声明（归一化后自动写入）：
{ "action": "wait_image",
  "params": {
    "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"],
    "window": "Keep on Mining", "timeoutMs": 30000,
    "onMissing": "fail"
  }, "saveAs": "btn" }
```

### 相关错误码

| 错误码 | 触发场景 |
|---|---|
| `WAIT_IMAGE_TIMEOUT`（v0.6.2） | 技能步骤 `wait_image` 在 `timeoutMs` 内没找到模板且 `onMissing:"fail"`（默认）；`failedStep` 带失败截图路径 |
| `TEMPLATE_NOT_FOUND` | 模板文件不存在；技能步骤 `find_image` 找不到模板且 `onMissing:"fail"` 时也用它（含失败截图） |

### 注意与限制

- `onMissing:"continue"` 是**显式**选择继续：后续步骤若引用未命中结果（如 `$btn.match.center.x`）会得到变量错误
  （hint 会提示上一步模板未找到）；不确定时保持默认 `fail`；
- 失败截图只保留在 `logs/`（文件名含时间与步骤序号），排查后可手动清理；
- 失败截图在模板失败时触发，**不影响**错误返回；截图本身失败不会掩盖原错误；
- 「失败后继续」只影响**循环**是否因某轮失败而停止，不改变单轮内部 `onMissing` 语义（单轮内默认仍 fail）。

---

## 技能文件格式（JSONC + 中文注释）

v0.4 起，技能文件 `skills/<id>/skill.json` 是 **JSONC**：在标准 JSON 基础上允许 `//` 行注释与 `/* */` 块注释。
插件**读取时**先剥离注释再解析（字符串里的 `//` 不会被误删，实现 `strip_jsonc`），**保存时**对每步做参数归一化
（`normalize_skill`）后**统一重新渲染成带中文注释的 JSONC**（`render_skill_jsonc`），保证文件既方便人看也方便 agent 复用。
旧的无注释纯 JSON 文件完全兼容。

### 中文注释是硬性要求

> **生成技能文件时必须补充中文内容注释**：每一步都要用 `// 第 N 步：...` 注释标明**具体执行内容与关键参数**，
> 包括但不限于：点击次数（`clicks`）、按住/等待时间（`waitMs`/`durationMs`）、拖动起止与步数
> （`x1,y1,x2,y2,steps`）、圆形半径/周期/时长（`radius,periodMs,durationMs`）等。

注释由插件在每次保存/生成时**根据步骤自动重新生成**：

- 文件头注释：插件名、标题、适用游戏、说明、生成时间、「保存时自动补全参数并重生成注释」提示；
- 每步前一行：`// 第 N 步：<动作中文名> —— <参数摘要>；<等待/保存说明>`；
  `find_image`/`wait_image` 步骤额外注明「**找不到时：失败/继续**」（对应 `onMissing`）；
- **手工修改注释会在下次保存时被覆盖**（数据不变）；需要保留的自定义说明请写在步骤的 `note` 字段（≤200 字符），
  保存时 `note` 会保留并拼进该步注释；
- `waitMs` / `continueOnError` / `onMissing`（v0.6.2） / 归一化后的动作默认参数都会**显式写入文件**，文件可以独立读懂。

### 完整示例（`skills/keep-on-mining-30s/skill.json`）

```jsonc
// ============================================================
// DSH 游戏操作插件 · 技能文件（JSONC，允许 // 与 /* */ 注释）
// 标题：Keep on Mining 挖矿循环
// 适用游戏：Keep on Mining!
// 说明：激活窗口 → 找「开始游戏」→ 等「开始挖矿」→ 圆形轨迹挖矿 → 等完成 → 下一轮
// 生成时间：2026-11-08T10:00:00.000Z
// 注意：保存时插件会自动补全每步参数并重新生成中文注释；自定义说明请写步骤的 note 字段。
// ============================================================
{
  "schema": 1,
  "id": "keep-on-mining-30s",
  "title": "Keep on Mining 30s 挖矿循环",
  "description": "激活窗口、等「开始挖矿！」按钮出现、点击、画 30s 圆进入下一轮；按 Esc 可中止",
  "game": "Keep on Mining!",
  "tags": ["mining", "loop"],
  "markdown": "## 说明\n先激活窗口；用 templates 多模板等「开始挖矿！」按钮（完成面板/准备界面两种样式）；点击后画 30s 圆。按 Esc 可中止。",
  "steps": [
    // 第 1 步：激活窗口 —— 标题子串「Keep on Mining」，restore=true，超时 2000ms；把游戏切到前台后继续
    {
      "action": "activate_window",
      "params": { "window": "Keep on Mining", "restore": true, "timeoutMs": 2000 },
      "waitMs": 0,
      "continueOnError": false,
      "saveAs": "win",
      "note": "游戏在后台时先切前台，否则键鼠会打到别的窗口"
    },
    // 第 2 步：等待模板出现 —— templates 同时等「开始挖矿！」两种样式（next_round/start_mining），任一命中即继续；最多 30000ms、每 250ms 轮询；找不到时：失败（WAIT_IMAGE_TIMEOUT + 失败截图）；等待阶段 Esc 可中止
    {
      "action": "wait_image",
      "params": {
        "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"],
        "window": "Keep on Mining",
        "threshold": 0.85,
        "grayscale": true,
        "maxResults": 3,
        "scales": [1.0],
        "timeoutMs": 30000,
        "intervalMs": 250,
        "mode": "appear",
        "abortKey": "esc",
        "onMissing": "fail"
      },
      "waitMs": 0,
      "continueOnError": false,
      "saveAs": "start"
    },
    // 第 3 步：点击鼠标 —— 左键单击 1 次、间隔 60ms；坐标取上一步命中的中心点；步骤后等待 300ms 让按钮响应
    {
      "action": "mouse_click",
      "params": {
        "x": "$start.match.center.x",
        "y": "$start.match.center.y",
        "button": "left",
        "clicks": 1,
        "intervalMs": 60
      },
      "waitMs": 300,
      "continueOnError": false
    },
    // 第 4 步：等待 —— 等待 2000ms（等游戏转场进入挖矿界面）；不注入任何输入
    {
      "action": "wait",
      "params": { "durationMs": 2000 },
      "waitMs": 0,
      "continueOnError": false
    },
    // 第 5 步：圆形轨迹 —— 以窗口客户区中心为圆心、半径 360px 画圆 30000ms（周期 2500ms，顺时针，120Hz，按住 none，中止键 esc）；Esc 随时中止
    {
      "action": "mouse_circle",
      "params": {
        "centerX": "$window.clientCenterX",
        "centerY": "$window.clientCenterY",
        "radius": 360,
        "periodMs": 2500,
        "durationMs": 30000,
        "direction": "cw",
        "startAngleDeg": -90,
        "samplesPerSecond": 120,
        "leadInMs": 300,
        "button": "none",
        "abortKey": "esc"
      },
      "waitMs": 0,
      "continueOnError": false,
      "note": "半径 360 保留用户设定；按住 none 表示只移动不按键；Esc 随时中止"
    }
  ],
  "createdAt": "2026-11-08T10:00:00.000Z",
  "updatedAt": "2026-11-08T10:05:00.000Z"
}
```

> **v0.5 示例技能语义**：等「开始挖矿！」按钮出现（`templates` 覆盖完成面板/准备界面两种样式，20s 超时）→ 点击它 →
> 等 2s → 圆形 30s。**不再要求先等「挖矿已完成」标题/120s 面板**；半径保留用户设定的 `360`，中止键默认 `esc`。

**更多动作的注释示例（可选步骤，展示完整参数）**：

```jsonc
// 第 N 步：鼠标拖拽 —— 从 (100, 900) 拖到 (500, 900)，左键按住 500ms，20 步插值；步骤后等待 100ms
{ "action": "mouse_drag", "params": { "x1": 100, "y1": 900, "x2": 500, "y2": 900, "button": "left", "durationMs": 500, "steps": 20 }, "waitMs": 100, "continueOnError": false }

// 第 N+1 步：按键 —— 按一次 f 键（按下-抬起，间隔 60ms）
{ "action": "key_press", "params": { "key": "f", "presses": 1, "intervalMs": 60 }, "waitMs": 0, "continueOnError": false }

// 第 N+2 步：输入文本 —— 输入文本（≤10000 字符，逐字间隔 12ms），日志只记长度
{ "action": "type_text", "params": { "text": "hello", "intervalMs": 12 }, "waitMs": 0, "continueOnError": false }
```

> 上面每个步骤字段都是**固定**的：`action`、`params`、`waitMs`（默认 0，显式写）、`continueOnError`（默认 false，显式写）、
> 可选 `saveAs` 与 `note`；`params` 里未显式给的动作默认参数会在保存时自动补全（见下表）。

### 归一化默认参数表

保存/生成技能文件时，对每步按动作补全以下默认参数（写入文件的最小显式集合；未列出的参数不补全；
`params` 值为 `$window.*` / `$saveAs.*` 的变量保持原样）：

| action | 自动补全的默认参数 |
|---|---|
| `activate_window` | `restore:true`, `timeoutMs:2000` |
| `mouse_move` | `durationMs:0` |
| `mouse_click` | `button:"left"`, `clicks:1`, `intervalMs:60` |
| `mouse_down` / `mouse_up` | `button:"left"` |
| `mouse_drag` | `button:"left"`, `durationMs:300`, `steps:20` |
| `mouse_scroll` | `horizontal:false` |
| `mouse_circle` | `periodMs:2500`, `durationMs:30000`, `direction:"cw"`, `startAngleDeg:-90`, `samplesPerSecond:120`, `leadInMs:300`, `button:"none"`, `abortKey:"esc"` |
| `key_press` | `presses:1`, `intervalMs:60` |
| `type_text` | `intervalMs:12` |
| `wait` | （`durationMs` 必填） |
| `find_image` | `threshold:0.85`, `grayscale:true`, `maxResults:3`, `scales:[1.0]`, **`onMissing:"fail"`（v0.6.2）** |
| `wait_image` | 继承 `find_image` + `timeoutMs:15000`, `intervalMs:250`, `mode:"appear"`, **`onMissing:"fail"`（v0.6.2）** |
| `screenshot` | `format:"png"`, `base64:false`, `allScreens:false` |
| `capture_window` | `engine:"auto"`, `includeCursor:false`, `timeoutMs:3000` |

### 查看 / 修改 / 版本

- **面板（v0.5）**：技能库是面板**第二项**。选中技能后默认显示**结构化步骤表格**（可改 action/关键参数/等待/备注、
  上移/下移/删除/新增），「保存」用结构化字段调 `skill_save`；「高级」折叠区保留**完整配置文件（JSONC，
  含中文注释）**编辑器，用 `content` 全文模式保存；两种方式保存后都会刷新表格与 JSONC；
- **直接编辑文件**：编辑 `<GAME_CONTROL_HOME>/skills/<id>/skill.json` 后，**下次运行/读取生效**（注释会被重新生成，
  数据保留；自定义说明写 `note`）；
- **版本恢复**：`skill_versions` 列出备份、`skill_restore` 恢复（备份/恢复**保留历史 JSONC 文本**）；
- `skill_get` 返回 `{skill, content, stepsView}`；`skill_save` 接受结构化字段或 `content` 全文（`id` 与 content.id 需一致）；
- `skill_run` 运行期间面板显示进度并禁用保存/刷新，避免请求排队（见[运行进度与防卡死](#运行进度与防卡死v05--v06)）。

---

## 调用示例

### 1. agent 工具（`game_control`）

```jsonc
// 先看屏幕几何 / 后端状态
{ "action": "status" }

// 鼠标移动 + 左键单击
{ "action": "mouse_move", "x": 960, "y": 540 }
{ "action": "mouse_click", "x": 960, "y": 540, "button": "left" }

// 组合键
{ "action": "hotkey", "keys": ["ctrl", "shift", "s"] }

// 输入文本（不返回原文，只返回 length）
{ "action": "type_text", "text": "hello 游戏", "intervalMs": 20 }

// 区域截图（返回 absolutePath，agent 再配合 read_image 查看；工具不返回 base64）
{ "action": "screenshot", "region": { "x": 0, "y": 0, "width": 1280, "height": 720 } }

// v0.2 后台窗口截图：先列窗口，再抓取（agent 工具不返回 base64，用 absolutePath + read_image）
{ "action": "list_windows", "titleContains": "Keep on Mining", "limit": 10 }
{ "action": "capture_window", "hwnd": 984074, "engine": "auto" }

// v0.3/v0.5 游戏自动化：激活 → 等「开始挖矿！」按钮（多模板，Esc 可中止）→ 点击 → 等 2s → 画 30s 圆（Esc）
{ "action": "activate_window", "window": "Keep on Mining" }
{ "action": "wait_image",
  "templates": ["keep-on-mining/next_round.png", "keep-on-mining/start_mining.png"],
  "window": "Keep on Mining", "timeoutMs": 30000, "abortKey": "esc" }
{ "action": "mouse_click", "x": 1920, "y": 1690 }
{ "action": "wait", "durationMs": 2000 }
{ "action": "mouse_circle", "centerX": 1920, "centerY": 1075, "radius": 360,
  "periodMs": 2500, "durationMs": 30000, "abortKey": "esc", "hwnd": 984074 }
```

失败时抛错，消息形如 `[INVALID_KEY] 未知键名: xyz（可用键名见工具描述）`。

### 2. agent 工具（`game_skill`）

```jsonc
// 查看技能列表 / 读完整技能（content 为 JSONC 全文，stepsView 为只读步骤视图）
{ "action": "skill_list" }
{ "action": "skill_get", "id": "keep-on-mining-30s" }

// 用 content 全文编辑保存：把 skill_get 返回的 content 改好后原样回传（参数自动补全、注释重新生成）
{ "action": "skill_save", "id": "keep-on-mining-30s", "content": "// 第 1 步：激活窗口 ...\n{ ... }" }

// 只校验不执行（dryRun 不注入任何动作；skill_run 是单轮底层原语）
{ "action": "skill_run", "id": "keep-on-mining-30s", "dryRun": true }

// 统一轮数入口：rounds=1 单次运行；rounds>1 循环（每轮独立 skill_run，Esc 可中止）
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 1 }
{ "action": "loop_start", "skillId": "keep-on-mining-30s", "rounds": 2, "intervalMs": 500 }

// 循环进度（含 currentStep 当前步骤）/ 停止（可立即停止）
{ "action": "loop_status" }
{ "action": "loop_stop", "immediate": true }

// 版本恢复 / 软删除
{ "action": "skill_versions", "id": "keep-on-mining-30s" }
{ "action": "skill_restore", "id": "keep-on-mining-30s", "backup": "latest" }
{ "action": "skill_delete", "id": "keep-on-mining-30s" }
```

> `skill_run.repeat` 已 deprecated：agent 工具 schema 不再暴露它（仅 Python CLI/内部兼容）；
> 多轮请用 `loop_start.rounds`（`rounds=1` 即单次运行）。

技能库的存储结构、`skill.json` 字段与变量语法见[技能库（v0.3）](#技能库v03)；JSONC 格式、中文注释要求与
归一化默认参数见[技能文件格式（JSONC + 中文注释）](#技能文件格式jsonc--中文注释)。

成功时工具返回：

```json
{ "ok": true, "action": "screenshot",
  "result": { "path": "shot_20261108_170000_123.png",
              "absolutePath": "C:\\Users\\me\\.dsh\\game-control\\shots\\shot_20261108_170000_123.png",
              "width": 1280, "height": 720, "format": "png", "bytes": 18321 },
  "durationMs": 42 }
```

失败时抛错，消息形如 `[INVALID_KEY] 未知键名: xyz（可用键名见工具描述）`。

### 3. Python CLI / JSONL

PowerShell 下 JSON 里有引号/嵌套时**推荐 `--stdin`**（一次进程执行多条，避免逐条转义）：

> 注意：Windows PowerShell 5.1 在把参数传给原生 exe 时会**吃掉 JSON 里的双引号**，
> 所以 `--json '{"action":"status"}'` 这种写法在 PowerShell 5.1 里会变成 `{action:status}` 而报
> `INVALID_ARGUMENT`。请用下面两种稳妥写法之一：① `--stdin`（最推荐）；② 单条 `--json` 时把内部双引号
> 写成 `\"`（`--json '{\"action\":\"status\"}'`）。PowerShell 7.3+ 与 bash 则可以直接写单引号 JSON。

```powershell
cd D:\dsh-chajian\dsh-game-control\python
$py = "C:\Users\<用户名>\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
# 若 python 已在 PATH，可直接把 & $py 换成 python

# 自检（只读，不操作键鼠）
& $py game_control.py --selftest
& $py game_control.py --version

# 单条命令：PowerShell 5.1 需要反斜杠转义内部双引号
& $py game_control.py --json '{\"action\":\"status\"}'

# 多条请求：PowerShell here-string 管道给 --stdin（每行一个 JSON，逐行响应，推荐）
@'
{"id":1,"action":"status"}
{"id":2,"action":"list_displays"}
{"id":3,"action":"screenshot","params":{"region":{"x":0,"y":0,"width":800,"height":600},"base64":true}}
{"id":4,"action":"mouse_circle","params":{"centerX":1920,"centerY":1075,"radius":360,"durationMs":1000,"samplesPerSecond":30,"dryRun":true}}
{"id":5,"action":"find_image","params":{"templates":["keep-on-mining/next_round.png","keep-on-mining/start_mining.png"],"threshold":0.85}}
{"id":6,"action":"skill_list"}
{"id":7,"action":"skill_run","params":{"id":"keep-on-mining-30s","dryRun":true}}
{"id":8,"action":"skill_progress"}
'@ | & $py game_control.py --stdin
```

> v0.5 提示：`mouse_circle` / `find_image` / `wait_image` / `skill_run` 默认 `abortKey="esc"`（等待模板阶段也可中断，
> `"none"` 禁用）；`find_image`/`wait_image` 需要 numpy，模板相对路径相对 `<GAME_CONTROL_HOME>/templates/`；
> `skill_run` 前置时长校验失败会返回 `INVALID_ARGUMENT`（附 hint），dryRun 也会返回 `estimatedDurationMs`。

```bash
# macOS / Linux 的 bash 写法
cd dsh-game-control/python
python3 game_control.py --json '{"action":"status"}'
printf '%s\n' '{"id":1,"action":"status"}' '{"id":2,"action":"mouse_position"}' | python3 game_control.py --stdin
```

环境变量：

| 变量 | 作用 |
|---|---|
| `GAME_CONTROL_HOME` | 数据根目录（截图/日志），默认 `%USERPROFILE%\.dsh\game-control` 或 `~/.dsh/game-control` |
| `SHOT_DIR` / `LOG_DIR` | 单独覆盖截图目录 / 日志目录 |
| `DSH_GAME_CONTROL_PYTHON` / `GAME_CONTROL_PYTHON` | 指定 Host 使用的 Python 解释器（`python.exe` 绝对路径） |

### 3. HTTP 路由（面板使用的同源接口）

端口以当前 DSH Web GUI 地址为准（例如 `http://127.0.0.1:5748`，请替换成你的实际端口）：

```powershell
# 健康检查
Invoke-RestMethod -Uri http://127.0.0.1:5748/api/game-control/health | ConvertTo-Json -Depth 8

# 后端状态（含 Python status 信封）
Invoke-RestMethod -Uri http://127.0.0.1:5748/api/game-control/status | ConvertTo-Json -Depth 8

# 执行动作（鼠标位置；不带副作用）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' -Body '{"action":"mouse_position"}'

# v0.3/v0.5：干跑圆形轨迹（不注入鼠标）/ 技能库 dryRun / 模板多模板查找
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' `
  -Body '{"action":"mouse_circle","params":{"centerX":1920,"centerY":1075,"radius":360,"dryRun":true}}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5748/api/game-control/exec `
  -ContentType 'application/json' `
  -Body '{"action":"skill_list"}'

# 技能运行进度（运行期间也可用；面板每 ~700ms 轮询的就是它）
Invoke-RestMethod -Uri http://127.0.0.1:5748/api/game-control/skill-progress

# 读日志尾部
Invoke-RestMethod -Uri 'http://127.0.0.1:5748/api/game-control/logs?lines=80' | ConvertTo-Json -Depth 4
```

```bash
# curl 版本
curl -s http://127.0.0.1:5748/api/game-control/health
curl -s -X POST http://127.0.0.1:5748/api/game-control/exec \
  -H 'content-type: application/json' -d '{"action":"status"}'
```

---

## 异常处理与日志

### 错误码（`error.code`）

Python 后端：

| 错误码 | 触发场景 |
|---|---|
| `INVALID_ARGUMENT` | 参数类型/范围不对（如 `x` 传字符串、`clicks` 越界）、`template`+`templates` 同时给 / `templates` 数量不在 1–8、`skill_run` 前置时长校验不通过（`estimatedDurationMs > timeoutMs`）、JSON 解析失败 |
| `UNKNOWN_ACTION` | 未知 `action` |
| `UNKNOWN_PARAM` | `params` 里出现未定义字段 |
| `INVALID_KEY` | 未知键名 |
| `INVALID_REGION` | `region` 宽/高 ≤ 0 等尺寸非法 |
| `OUT_OF_BOUNDS` | 截图区域与虚拟桌面完全无交集 |
| `WINDOW_NOT_FOUND` | `capture_window` / `activate_window` 按 `hwnd`/标题子串找不到窗口（hint 提示用 `list_windows`） |
| `WINDOW_CAPTURE_FAILED` | 窗口截图失败：最小化 / 反截图保护 / 显式 engine 不可用 / WGC 拿不到帧等（hint 提示确认窗口状态） |
| `WINDOW_ACTIVATION_FAILED` | `activate_window` 最终没能把窗口切到前台（UAC/提权保护、其它桌面会话、Windows 前台锁），hint 提示手动点一次或检查权限 |
| `TEMPLATE_NOT_FOUND` | `find_image` / `wait_image` 的 `template`/`templates` 文件不存在（相对路径相对 `<GAME_CONTROL_HOME>/templates/`）；v0.6.2 起技能步骤 `find_image` 找不到模板且 `onMissing:"fail"` 时也返回它（含失败截图） |
| `INVALID_TEMPLATE` | 模板文件不是有效图片 / 过大（>2MB 或宽高 >1024）/ 解码失败 |
| `SKILL_NOT_FOUND` | `skill_get/versions/restore/delete/run` 的 id 不存在（或已被删除） |
| `INVALID_SKILL` | 技能内容不合法：id 格式/字段超限/steps 为空或超过 200 步/步骤结构错误/未知步骤字段/`content` 整份 JSONC 解析失败等 |
| `SKILL_VAR_UNRESOLVED` | `skill_run` 变量（`$window.*` / `$saveAs.*`）无法解析；v0.6.2 起若为 `None`/空值会附 hint（如「`match` 为空：上一步 wait_image 可能未找到模板（见 failedStep.screenshotPath）」） |
| `WAIT_IMAGE_TIMEOUT`（v0.6.2） | 技能步骤 `wait_image` 在 `timeoutMs` 内没找到模板且 `onMissing:"fail"`（默认）；该步失败并附失败截图 `failedStep.screenshotPath` |
| `LOOP_ALREADY_RUNNING` | v0.6：已有长循环在跑时再次 `loop_start`（同一时间只允许一个循环）；先 `loop_stop` 或等它结束 |
| `MISSING_DEPENDENCY` | 缺少 Pillow（截图）或 pynput（非 Windows 键鼠），hint 给出 pip 命令 |
| `UNSUPPORTED_PLATFORM` | 当前平台没有可用后端 |
| `PERMISSION_DENIED` | 系统权限不足（macOS 辅助功能、Wayland 等） |
| `FILE_IO_ERROR` | 截图文件写入/读取失败 |
| `BACKEND_ERROR` | 平台 API 调用失败（如 `SendInput` 返回条数不足） |
| `INTERNAL` | 未捕获异常（详情写日志，不把 traceback 返回给调用方） |

Host 侧额外错误码：

| 错误码 | 触发场景 |
|---|---|
| `PYTHON_NOT_FOUND` | 按顺序找不到可用 Python，hint 提示设置 `DSH_GAME_CONTROL_PYTHON` / 安装依赖 |
| `TIMEOUT` | 单动作超时（默认 15s；截图 30s；`type_text` 按长度放宽），后端会被 kill 并在下次调用时自动重启 |
| `BACKEND_UNAVAILABLE` | Python 进程退出 / 启动失败 / 插件已卸载；附 stderr 尾部便于排错 |

失败处理约定：

- Python 进程**不因单条动作失败而退出**，响应里带 `ok:false` 与 `error`，进程继续服务；
- 未捕获异常统一转成 `ok:false` + `code:"INTERNAL"`，完整 traceback 只写日志；
- Host 路由层 try/catch，内部错误返回 HTTP 500 JSON；`bridge.execute` 把超时/进程错误包成 `ok:false` 信封，
  不把异常抛给路由导致 500；
- agent 工具失败时 `throw Error('[CODE] message')`，错误消息以错误码开头，便于模型识别并重试/换方式。

### 日志文件

| 文件 | 位置 | 内容 |
|---|---|---|
| 运行日志 | `<GAME_CONTROL_HOME>/logs/game-control.log` | 启动信息、动作成功/失败、截图路径、异常摘要；单文件滚动 **2MB × 3** |
| 动作审计 | `<GAME_CONTROL_HOME>/logs/actions.jsonl` | 每行 `{ts, action, ok, code?, durationMs, length?}`；**不记录 `type_text` 原文**（只记长度） |

`GAME_CONTROL_HOME` 默认 `%USERPROFILE%\.dsh\game-control`（Windows）/ `~/.dsh/game-control`（其他平台），
可由环境变量覆盖；截图也在这个目录的 `shots/` 下。

### 排障

| 现象 | 排查 |
|---|---|
| 面板显示「Python 后端当前不可用」 | 看「后端状态」里的 lastError；确认环境变量 `DSH_GAME_CONTROL_PYTHON` 指向的 `python.exe` 可执行 `--version`；或安装 Python 3.10+ |
| 截图报 `MISSING_DEPENDENCY` | `pip install -r requirements.txt`（安装 Pillow） |
| 动作报 `TIMEOUT` | 长文本输入降低 `intervalMs`；确认没有弹窗阻塞；超时后桥会自动重启，可重试 |
| `SendInput` 成功但游戏没反应 | 游戏使用 DirectInput / Raw Input / 独占全屏，或反作弊保护：见[跨平台支持矩阵与限制](#跨平台支持矩阵与限制) |
| 无法操作管理员窗口 | Windows UIPI：普通权限进程不能向提权窗口注入输入，以相同（或更高）权限启动 DSH |
| `capture_window` 报 `WINDOW_NOT_FOUND` | 先 `list_windows`（面板点「刷新窗口列表」）确认窗口标题与 hwnd；`window` 是**标题子串**且大小写不敏感，隐藏窗口需要 `includeInvisible:true` 才列得出来 |
| 后台窗口抓到黑屏 / 报 `WINDOW_CAPTURE_FAILED` | ① 确认窗口没有最小化；② 游戏/播放器可能开启反截图保护；③ Win10 1903+ 才有 WGC；④ 显式 `engine=screen` 只拿可见像素，换 `auto`/`wgc`；⑤ 检查 `python/vendor/windows_capture/` 与本机 `numpy` 是否完整 |
| Win10 上 WGC 报 “Toggling the capture border is not supported” | 实现应传 `draw_border=None`；**不要传 `draw_border=False`**（Win10 不支持该开关）。面板/工具不暴露该参数，正常使用不会触发；改代码时注意 |
| 在 DSH agent 沙箱里 WGC 不可用 | 沙箱可能拿不到图形捕获会话；这是执行环境限制，不是插件 bug。用 DSH 宿主/普通终端或让 Lead 在未受限环境验收（最小验收脚本只测 `engine=screen`） |
| `activate_window` 报 `WINDOW_ACTIVATION_FAILED` | ① 目标窗口可能被 UAC/提权保护或属于其它桌面会话；② Windows 前台锁：先手动点一次目标窗口再重试；③ 确认 `list_windows` 找到的是正确的 hwnd；④ 本动作不注入点击，只切前台 |
| `mouse_circle` 报 `TIMEOUT` / 被提前杀掉 | 确认 Host 超时预算（`15s + leadInMs + durationMs + 5s`，上限 180s）；durationMs 别超过 120000；30s 画圆在普通终端可用，但若 DSH 对单次工具调用有 <35s 外部硬超时，需分段执行或改用技能库编排 |
| 画圆/技能运行时想停止 | 按 **Esc**（`mouse_circle`/`find_image`/`wait_image`/`skill_run` 默认 `abortKey`）：动作会在 ~100ms 内停止，返回 `aborted:true`；等待模板阶段也能立即中止；若 Esc 与游戏冲突可换 `abortKey` 或 `"none"`（不建议） |
| `find_image` / `wait_image` 多模板怎么用 | `templates` 数组（1–8 个，任一命中即返回）与 `template` 二选一；同时给/空数组/超过 8 个 → `INVALID_ARGUMENT`；多模板时结果里的 `template` 是命中的那个 |
| `skill_run` 报 `INVALID_ARGUMENT`（时长前置校验） | 估算时长超过 `timeoutMs`：按 hint 增大 `timeoutMs`，或缩小 `wait_image.timeoutMs` / `mouse_circle.durationMs`；`dryRun:true` 也会返回 `estimatedDurationMs` 便于预估 |
| 长循环报 `LOOP_ALREADY_RUNNING` | 已有一个循环在跑：先 `loop_stop`（或等它完成）再启动；用 `loop_status` 看 `running`/`currentRound` |
| 长循环想立即停 | `loop_stop`（默认等当前轮结束）或 `loop_stop {immediate:true}`（终止 worker，当前轮以 `BACKEND_UNAVAILABLE` 失败）；插件卸载也会停止循环 |
| 面板卡在「运行中」/出现 `Failed to fetch` | v0.6 已修复：worker 退出会把进度标记 `stale:true, abortReason:"backend-stopped"`，面板会显示「后端连接中断」并提供「重置状态」；点重置→刷新→重新运行即可（worker 下次请求自动重启）。若仍卡住，检查 `logs/skill-progress.json` 与 `logs/loop-state.json` |
| 技能运行中界面"卡住"/保存转圈 | 运行期间面板会禁用保存/刷新并轮询 `/exec {action:'loop_status'}`（~700ms，v0.6.1 起统一）；Python worker 是单请求串行，运行中的其他操作会排队，等运行结束再操作。若进度长时间不动，看 `logs/skill-progress.json` 与 `logs/game-control.log` |
| `find_image` / `wait_image` 报 `MISSING_DEPENDENCY` | 安装 numpy：`pip install numpy`；确认 DSH 内置 Python 有 numpy |
| `find_image` 报 `TEMPLATE_NOT_FOUND` / `INVALID_TEMPLATE` | 检查模板路径：相对路径相对 `<GAME_CONTROL_HOME>/templates/`；模板必须 PNG/JPG、≤2MB、宽高 ≤1024；建议只裁按钮文字区域，阈值 0.82–0.88 |
| `skill_run` 某步失败 | 看返回的 `result.failedStep:{index,action,code,message,screenshotPath?}`；`SKILL_VAR_UNRESOLVED` 检查 `$window`/`$saveAs` 引用路径；先 `dryRun:true` 校验变量规划 |
| 技能报 `$xxx.match.center.x` 的 NoneType 变量错误 | v0.6.2 已修复：模板步骤默认 `onMissing:"fail"`，会先以 `WAIT_IMAGE_TIMEOUT`/`TEMPLATE_NOT_FOUND` 失败在该步并附失败截图，不再继续到下一步才报变量错误。旧技能若仍显式 `onMissing:"continue"`，后续变量引用仍会报错（这是显式选择） |
| 模板超时想继续跑循环 | 面板勾选「失败后继续」（=`loop_start.stopOnError:false`），失败轮次会记录 `failedStep`/截图到 `loop_status.results`，循环继续；单轮内仍按 `onMissing` 语义执行 |
| 找不到失败截图 | 模板失败截图在 `<GAME_CONTROL_HOME>/logs/skill-fail-<时间>-step<idx>.png`，路径见 `failedStep.screenshotPath`；优先截失败步骤的 `searchRegion`，其次主屏；截图失败不影响错误返回 |
| `skill_*` 报 `SKILL_NOT_FOUND` / `INVALID_SKILL` | 先 `skill_list` 确认 id；`INVALID_SKILL` 按 [技能库（v0.3）](#技能库v03) 的字段限制检查（id 正则、steps 1–200 等） |
| 技能文件里注释/参数看起来"被改了" | 正常行为：保存时插件会**重新生成中文注释并补全默认参数**（数据不变）。要保留自定义说明请写步骤的 `note` 字段；手工注释会在下次保存时被覆盖（见[技能文件格式](#技能文件格式jsonc--中文注释)） |
| `skill_save` 传 `content` 报 `INVALID_SKILL` | `content` 必须是合法 JSONC（字符串里的 `//` 不会被当注释剥离）；每步只允许 `action/params/saveAs/continueOnError/waitMs/note/onMissing` 字段；`id` 需与 content.id 一致 |
| 旧技能（无注释 JSON）打不开 | v0.4 完全兼容旧纯 JSON；若报错，检查是否被手改成非法 JSON（可用 `skill_get` 看 `content` 原文），或用 `skill_versions`/`skill_restore` 回滚 |
| 面板点击后 404 | 插件 Host 半未加载：确认 bundle 已安装、`cordis.patch.yml` 已合并、DSH 已重启；`GET /api/game-control/health` 验证 |
| 看不到侧栏图标 | Web 端 `client.js` 未加载：刷新页面/重启 DSH；检查 `package.json` 的 `dsh.client.platform === "web"` |
| 想看实时日志 | `GET /api/game-control/logs?lines=200`，或直接打开 `logs/game-control.log` |

---

## 安全边界

- **不联网**：Host 与 Python 后端都不发起任何网络请求；Python 源码不含 `socket` / `urllib` / `http` /
  `requests` / `websocket` 等 import（验收脚本会扫描并断言）。
- **只在显式调用时动作**：没有任何后台线程、定时器或轮询去注入输入；`--serve` 空闲时不做任何输入；
  面板每个动作都需要你点击触发，agent 每个动作都需要一次明确的 `game_control` / `game_skill` 工具调用。
- **长动作可随时中止**：`mouse_circle` / `find_image` / `wait_image` / `skill_run` 默认 **Esc** 中止（`"none"` 可禁用），
  等待模板阶段也能立即停止；`skill_run` 会把中止键透传给内部步骤；退出路径释放按住的鼠标键；
  中止键只在动作执行期间查询，不注册全局热键、不常驻监听。
- **运行进度可观测**：`skill_run` 期间写 `<GAME_CONTROL_HOME>/logs/skill-progress.json` 并提供只读进度接口，
  面板运行期间禁用保存/刷新，避免请求排队假死；进度文件只含技能 id/步骤/耗时等元数据，不含画面内容。
- **长循环由 Host 显式管理**：`loop_start` 只在被显式调用时启动，按轮独立执行技能并受 `loop_stop`/插件卸载控制；
  `immediate:true` 会终止 worker（当前轮失败）以立即停止，属于显式请求的后果；状态写 `logs/loop-state.json`/`loop.jsonl`（元数据）。
- **连接中断不会永久卡状态**：worker 退出时 Host 把进度标记 `stale:true` 并可重置，避免面板永久"运行中"。
- **模板失败证据截图（v0.6.2）**：模板超时时会在 `logs/skill-fail-*.png` 保存失败画面（优先匹配区域，其次主屏），
  截图同样可能含敏感信息；排查后可手动删除，不要把路径随意分享。
- **技能执行 = 组合动作**：`skill_run` 会按步骤执行真实输入（点击/按键/画圆），只在它自己的请求期间动作；
  运行前用 `dryRun:true` 检查变量与步骤，确认技能内容可信后再真实运行。
- **截图/窗口抓取可能包含敏感信息**：截图和后台窗口截图都会把画面（可能含密码、聊天记录、令牌等）保存到本机
  `shots/` 目录，base64 预览还会进入浏览器内存。分享截图或把路径交给 agent/`read_image` 前请自行确认内容；
  不要对不希望被记录的画面截图。模板图（`templates/`）同样是截图裁剪产物，注意别把敏感画面存成模板。
- **日志脱敏**：`type_text` 只记录长度，不记录原文；审计日志只记动作元数据。
- **权限即操作边界**：插件只能操作当前登录会话中、与 DSH 同权限级别的窗口；提权窗口、锁屏、
  UAC 安全桌面无法操作（会失败或无明显效果）。
- **交付/验收零副作用**：`test/acceptance.mjs` 只做静态检查 + 只读动作 + 截图，**不点击、不按键、不输入文本**。

---

## 跨平台支持矩阵与限制

| 能力 | Windows（优先，实测通过） | macOS | Linux |
|---|---|---|---|
| 鼠标/键盘 | `win_native.py`：纯 ctypes `SendInput`（64 位 ULONG_PTR），绝对定位按虚拟桌面归一化 | `input_backend.py`：优先 `pynput`，兜底 `osascript` | `input_backend.py`：优先 `pynput`，兜底 `xdotool` |
| DPI | 启动时 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，失败回退 `SetProcessDPIAware()`；`status.dpiAware` 记录结果 | 系统坐标 | 系统坐标 |
| 截图 | GDI：`GetDC(0)`→`BitBlt`→`GetDIBits`（BI_RGB 32bpp BGRX）→ Pillow 编码 | Pillow `ImageGrab` → `mss` → `screencapture -x -R` | Pillow `ImageGrab`（X11）；Wayland 需合成器授权，否则报权限错误 |
| 窗口枚举 / 后台窗口截图（v0.2） | ✅ `list_windows` + `capture_window`：WGC（Win10 1903+）/ PrintWindow / window-dc / screen 回退链，真后台可抓被遮挡窗口 | ❌ `UNSUPPORTED_PLATFORM`（仅 Windows） | ❌ `UNSUPPORTED_PLATFORM`（仅 Windows） |
| 游戏自动化动作（v0.3） | ✅ `activate_window` / `mouse_circle` / `wait` / `find_image` / `wait_image`（ctypes + numpy FFT 模板匹配 + `GetAsyncKeyState` 中止） | ⚠️ `wait` 可用；窗口激活/圆形轨迹/模板找图返回 `UNSUPPORTED_PLATFORM` | ⚠️ `wait` 可用；窗口激活/圆形轨迹/模板找图返回 `UNSUPPORTED_PLATFORM` |
| 技能库（v0.3） | ✅ `skill_*`（文件存储，跨平台可用；步骤执行受各动作平台支持限制） | ✅ 存储/CRUD 可用；Windows-only 步骤会失败 | ✅ 存储/CRUD 可用；Windows-only 步骤会失败 |
| 多显示器 | 虚拟桌面坐标（允许负坐标），`list_displays` 枚举；混合 DPI 以物理像素为准 | 支持 | X11 支持；Wayland 受限 |

已知限制：

- **游戏输入**：部分游戏（DirectInput / Raw Input / 独占全屏 / 反作弊）会忽略 `SendInput` 注入的合成输入，
  这不是插件 bug；可尝试窗口化/无边框模式。请只在允许自动化的游戏/场景中使用。
- **后台窗口截图（v0.2）**：仅 Windows 10 1903+；最小化窗口、反截图保护的窗口、独占全屏可能抓不到；
  WGC 在受限环境（如 DSH agent 沙箱）可能不可用，DSH 宿主/普通终端不受影响。
- **游戏自动化（v0.3）**：`mouse_circle` 会阻塞 `durationMs`（30s 默认），Host 有单独超时预算（上限 180s）；
  若 DSH 对单次工具调用有外部硬超时，需分段执行或改用技能库编排。
- **模板匹配**：模板随窗口分辨率/DPI/缩放变化可能失效；用 `scales` 多尺度可缓解；模板只匹配画面，不做 OCR。
- **管理员权限**：Windows 下普通权限进程无法向管理员权限窗口注入输入（UIPI）；需要以同等或更高权限启动 DSH。
- **macOS**：需要在「系统设置 → 隐私与安全性 → 辅助功能」授权终端/DSH，否则键鼠动作返回权限错误。
- **Linux**：X11 下需安装 `pynput` 或 `xdotool`；Wayland 默认禁止全局键鼠注入和全屏截图，
  需要合成器显式授权（如 `grim` + 门户），否则给出明确错误。
- **锁屏 / UAC 安全桌面**：任何平台都无法操作，属于系统安全设计。
- 截图默认截**主屏**；需要其他屏幕用 `monitor`（从 1 开始）或 `allScreens`。

---

## 最小验收用例

```powershell
node D:\dsh-chajian\test\acceptance.mjs            # 逐条 PASS / FAIL / SKIP + 汇总
node D:\dsh-chajian\test\acceptance.mjs --verbose  # 额外打印每条 Python 命令与输出摘要
node D:\dsh-chajian\test\acceptance.mjs --keep-tmp # 保留 test/.tmp 下的截图/日志便于排查
```

- 纯 Node（≥20）实现，**零第三方依赖**；退出码：`0` = 无 FAIL（允许 SKIP），`1` = 有 FAIL；
- 严格对应 `SPEC.md` §6 的 8 项验收标准，并包含 v0.2 窗口截图、v0.3 游戏自动化/技能库、v0.4 JSONC 技能文件、
  v0.5 Esc/多模板/进度/结构化编辑、v0.6 长循环/连接修复、v0.6.1 单一轮数语义、v0.6.2 模板超时修复扩展，
  共 **113 项检查**：
  1. 交付文件齐全（含 README 内容、requirements、`python/window_capture.py`、`python/circle_motion.py`、
     `python/template_match.py`、`python/skills.py`、随包 `vendor/windows_capture` + LICENSE、
     `docs/verification-window-background.png` 证据图）；
  2. `node --check index.js` / `client.js`；
  3. `package.json` 字段（name / bundle patch / client platform / exports / **版本 0.6.2**）；
  4. locale JSON、`icon.svg`、`cordis.patch.yml` 内容；
  5. Python 后端：`--selftest`、`status` 几何、全屏/区域截图落盘与尺寸、base64 解码、非法 action/key/region 错误码、
     源码无联网 import；`--version`、`--stdin` JSONL、`--serve` ready/id 回显、审计日志、日志文件；
     v0.2 的 `list_windows` / `capture_window engine=screen` / `WINDOW_NOT_FOUND` / `titleContains` 过滤；
     v0.3 的 `ACTIONS/ACTION_HANDLERS` 注册、`mouse_circle dryRun`（samples 公式、零输入）、
     `find_image` 模板缺失/损坏错误码、`wait_image` 合成模板短超时 `timedOut:true`、
     技能库 `save→list→get→备份→versions→restore→run(dryRun/仅 wait)→软删` 全流程、
     `--selftest` 新能力检查（numpy/模板目录/中止键）；
     v0.4 的 `skills.py` 三函数、结构化保存后磁盘 JSONC 含 `// 第 N 步`/显式 `waitMs`/归一化参数、
     `skill_get` 的 `content`/`stepsView`、`content` 全文保存（`note` 保留/注释重生成）、坏 content → `INVALID_SKILL`、
     旧纯 JSON 兼容、版本恢复保留 JSONC；
     v0.5 的 `mouse_circle` 默认 `abortKey=esc`、`templates` 多模板参数校验（与 template 同给/超过 8 个/空数组）、
     `skill_run dryRun` 返回 `estimatedDurationMs` + 时长前置校验失败 → `INVALID_ARGUMENT`、
     `skill_progress` 动作与 `logs/skill-progress.json` 字段（running=false 等）；
     v0.6.2 的 `skills.py` 含 `onMissing`、`game_control.py` 含 `WAIT_IMAGE_TIMEOUT`/`screenshotPath`/`skill-fail-`；
  6. `index.js` 静态契约（`ctx.tools.register`、`/api/game-control`、`--serve`、`spawn(`、`PYTHON_NOT_FOUND`、
     `list_windows`/`capture_window`/`engine`、**23 个控制 action**、`game_skill` 工具与 **8 个 `skill_*` 动作**、
     `/api/game-control/skill-progress` 路由（返回 `{progress,bridge}`、`stale`）、`templates`/`abortKey` 参数、
     `timeoutFor` 分支（`skill_run` 含 +120000 余量）、v0.6 `loop_start`/`loop_status`/`loop_stop` 与
     `logs/loop-state.json`/`loop.jsonl`、`LOOP_ALREADY_RUNNING`；v0.6.1 `loop_status.currentStep` 合并、
     `game_skill` schema **不含 `repeat` 属性**（deprecated 仅 CLI/内部）；v0.6.2 循环 results 带
     `failedStep`/`screenshotPath` 拷贝）；
  7. `client.js` 静态契约（`sidebar.panellist`、`main`、`/api/game-control/exec`、`ctx.locale.register`、
     v0.2 窗口截图、v0.3 激活/圆形轨迹/模板找图/技能库 UI、v0.4 JSONC 编辑器、
     v0.5 技能库第二项/结构化步骤表格/进度轮询 `/skill-progress`、
     v0.6 循环 UI（`loop_start`/`loop_status`/`loop_stop`、轮数/间隔、立即停止、轮次进度文案）与
     「后端连接中断」/「重置状态」处理；
     v0.6.1 单套控制（**不再有「重复次数 / 运行一次 / 启动循环」**，只有「轮数 + 运行 / 停止 + 立即停止」、
     `loop_status` 轮次+当前步骤显示）；
     v0.6.2「**失败后继续**」复选 + 每轮 `failedStep`/失败截图路径展示）；
  8. 汇总 PASS/FAIL，失败退出码 1。
- **WGC 真后台抓取路径不在脚本内测试**（DSH 沙箱内 WGC 可能不可用，脚本只测 `engine=screen` 落盘链路）；
  WGC 与实机循环由 Lead 在未受限环境单独验收，证据图见 `docs/verification-window-background.png`；
- **v0.3–v0.6.2 动作只做只读/干跑**：`mouse_circle` 只测 `dryRun:true`（不注入、不等待）；`find_image`/`wait_image`
  只做只读截图匹配与多模板参数校验；技能库用 `GAME_CONTROL_HOME` 唯一临时目录做 CRUD，`skill_run` 只测 `dryRun:true`
  与「仅 `wait durationMs:0`」技能（零输入副作用；另保留 CLI 层 `skill_run.repeat` 兼容用例）；v0.6 的 loop 动作仅做
  静态契约检查（实际循环由 Lead 手工验收），技能文件/进度检查同样只写临时目录，不产生真实输入；v0.6.2 的失败截图/
  `onMissing` 由 Python 自测与静态检查覆盖，验收脚本不制造真实模板超时。
### v0.3 实机验收记录（Keep on Mining!）

`docs/v0.3-acceptance.md` 记录了 2026-11-08 在本机的实机验收（已征得用户同意）：`activate_window` 真前台切换、
`find_image` 对真实游戏按钮 score≈1.0、连续 2 轮「点击开始挖矿 → 30s 圆形轨迹 → 挖矿已完成面板」、
`skill_run` 真实执行 6 步技能（32.6s，success）、中止键物理中止（817ms 内停止并释放按键；v0.3 时期中止键为 F12，v0.5 起改为 Esc）、前台变化安全中止。
证据截图：`docs/v0.3-mining-start.png`、`docs/v0.3-mining-done.png`、`docs/v0.3-mining-skill-round.png`。
本机已验证 30.3s 的 `mouse_circle` 与 32.6s 的 `skill_run` 没有被 DSH 工具调用外部超时打断。

- Python 解释器自动发现顺序：`DSH_GAME_CONTROL_PYTHON` / `GAME_CONTROL_PYTHON` →
  `%USERPROFILE%\.dsh\dsh-runtimes\*\dependencies\python\python.exe` → `python3` / `python`；
  都找不到时 Python 相关项记 `SKIP` 并在汇总里说明（这种情况下验收**不完整**，建议装好 Python 后重跑）；
- **零副作用**：只做只读动作（status / list_displays / list_windows / capture_window screen / find_image /
  wait_image / selftest / version / 截图）与干跑动作，绝不点击、按键、拖拽、输入文本、激活窗口或真实画圆；
  截图等临时产物写到 `test/.tmp/` 并在结束时清理；
- 脚本用文件重定向代替 stdio 管道调用子进程，因此在受限的 DSH 沙箱里也能运行。

---

## 参考项目与致谢

本插件的实现思路与部分代码结构参考了以下开源项目，特此致谢：

- [LJL454/dsh-computer-use](https://github.com/LJL454/dsh-computer-use) —— DSH 计算机操作插件参考
  （侧栏面板 / HTTP 路由 / Python 桥的整体思路）。
- [gychen-NJU/dsh-game-daily](https://github.com/gychen-NJU/dsh-game-daily) —— DSH 游戏日常插件参考
  （`cordis.patch.yml` bundle 写法、Client 槽位注册、locale 与工具注册方式）。

参考源码仅用于阅读与对齐实现思路，不随插件安装、也不会被修改（开发期间临时下载，交付目录已清理）。

---

## English summary

`@local/dsh-game-control` is a DSH desktop plugin that adds a left-sidebar "Game Control" panel and a
`game_control` agent tool. A local Python backend (pure `ctypes` `SendInput` + GDI screenshot capture on
Windows; `pynput`/`xdotool` on macOS/Linux) performs mouse moves/clicks/drags/scrolls, key presses, hotkeys,
Unicode text typing, and full-screen / region screenshots (PNG/JPEG, optional base64 preview).

v0.2 adds **window enumeration and background window capture on Windows**: `list_windows` enumerates windows
(hwnd / title / class / process / rect / clientRect / minimized / visible / foreground), and `capture_window` captures a
specific window even when it is **not foreground and is occluded** using Windows Graphics Capture (Win10 1903+,
vendored `windows_capture` 2.0.1 + numpy, MIT, no runtime network). Automatic fallback chain:
`wgc → printwindow → window-dc → screen`, with the actually used engine reported in the result. Verified on
this machine: captured the "Keep on Mining!" main menu while DSH was in the foreground
(see `docs/verification-window-background.png`).

v0.3 adds **game automation actions and an in-plugin skill library**: `activate_window` (bring a window to the
foreground + `clientRect`), `mouse_circle` (smooth timed circular mouse motion, blocking for `durationMs`,
abortable with **Esc**), `wait`, `find_image` / `wait_image` (numpy FFT normalized cross-correlation template
matching with multi-scale + NMS; timeout is a normal result, not an error). The `game_skill` tool plus panel
provide a skill library stored under `<GAME_CONTROL_HOME>/skills/`: readable `skill.json` (Markdown + steps),
view/edit, automatic backups on save, version restore, soft delete to `.trash/`, and `skill_run` with
`dryRun`/`timeoutMs`/`abortKey`/`estimatedDurationMs`, plus `$window.*` / `$saveAs.*` variable substitution
(`skill_run.repeat` is deprecated and only kept for CLI/internal compatibility; use `loop_start.rounds` for N runs).

v0.4 upgrades the skill files to **JSONC with generated Chinese comments**: every step writes explicit
`waitMs`/`continueOnError` and normalized action defaults (e.g. `clicks`, `intervalMs`, `radius`, `periodMs`,
`samplesPerSecond`, drag `steps`), each step is preceded by a `// 第 N 步：…` comment describing what it does and
its key parameters, and the plugin re-renders comments on every save. `note` fields are preserved, old plain-JSON
skills stay compatible, `skill_get` returns `content`/`stepsView`, and `skill_save` accepts a full `content`
(JSONC) for editing; backups and restores keep the original JSONC text.

v0.5 switches the default abort key from F12 to **Esc** (works during `wait_image`/`find_image` polling too, and
`skill_run` passes it through to its steps), adds **`templates` multi-template matching** (1–8 templates, any hit
wins, `template` xor `templates`) so one button with two styles can be matched, and makes skill runs observable and
**queue-safe**: `skill_run` writes `<GAME_CONTROL_HOME>/logs/skill-progress.json` while running, the Host exposes
`GET /api/game-control/skill-progress` (read directly from the file, so it works during a run), the panel polls it
(~700ms) and shows “step N/M · action · elapsed Xs”, disabling save/refresh while running. `skill_run` also does a
pre-flight time-budget check (`estimatedDurationMs`, returned in dry runs) and returns `INVALID_ARGUMENT` when the
estimated worst case exceeds `timeoutMs`. The panel moves the skill library to the **second card** and adds a
**structured step table** editor (key-parameter cells, wait/notes, move up/down, delete) with the JSONC editor as an
advanced view; the example skill now waits for the “开始挖矿！” button (templates, 20 s) → clicks it → waits 2 s →
draws a 30 s circle (radius 360, Esc to abort), instead of waiting for a 120 s panel.

v0.6 adds **Host-level long loops and connection-loss repair**: `loop_start` / `loop_status` / `loop_stop` run a skill
for `rounds` 1–10000 by invoking **one independent `skill_run` per round** (with `intervalMs`, `stopOnError`,
`roundTimeoutMs`, `abortKey`, and `immediate` stop), persist state to `logs/loop-state.json` + per-round
`logs/loop.jsonl`, and reject a second concurrent loop with `LOOP_ALREADY_RUNNING`. Because each round has its own
request, many rounds no longer hit the Host's ~310 s single-request SIGTERM. In addition the Host gives `skill_run`
`timeoutMs + 120000` of headroom and Python aborts gracefully at its own total deadline (`aborted:true,
abortReason:"timeout"`), so long actions stop cleanly before being killed. If the worker exits anyway, the progress
file is marked `stale:true, abortReason:"backend-stopped"`, `GET /api/game-control/skill-progress` returns
`{progress, bridge}`, and the panel shows “backend connection lost” with a **Reset state** button instead of hanging
on a raw `Failed to fetch`. The panel skill card also gains loop controls (rounds / interval / run once / start loop /
stop loop with immediate stop) and a round-progress line.

v0.6.1 merges the duplicated “repeat” and “rounds” controls into a **single `rounds` concept**: `rounds = 1` is a
single run, `rounds > 1` is a loop, and every round is still an independent `skill_run` call. `loop_start` is the
unified N-round entry point; `skill_run` remains the one-round low-level primitive and its `repeat` parameter is
**deprecated** (removed from the agent tool schema and panel, kept only for CLI/internal compatibility).
`loop_status` now includes `currentStep:{index,total,action,elapsedMs}` so the panel can show the current step, and
the panel exposes just “rounds + interval (>1) + immediate stop + Run / Stop” instead of separate
“repeat / run once / start loop” controls.

v0.6.2 fixes **template-wait timeouts**: instead of returning `found:false, match:null` and letting the next step
fail with a misleading `NoneType` variable error (`$btn.match.center.x`), a skill step now fails **at the template
step** by default. Skill steps gain `onMissing` (`"fail"` default / `"continue"`, only for `find_image`/`wait_image`,
auto-normalized to `"fail"`), the failed step returns `WAIT_IMAGE_TIMEOUT` (wait_image) or `TEMPLATE_NOT_FOUND`
(find_image) and a failure screenshot at `<GAME_CONTROL_HOME>/logs/skill-fail-<ts>-step<idx>.png` exposed as
`failedStep.screenshotPath`, and variable-resolution errors now hint that the previous template step may have missed.
The loop settings gain a **“continue after failure”** checkbox (`loop_start.stopOnError=false`) so an occasional
template timeout does not abort a long loop; each round entry in `loop_status.results` now carries
`failedStep`/`screenshotPath` for the panel. The example skill uses `timeoutMs: 30000` and an explicit
`onMissing:"fail"`.

- Install the bundle via `plugin_manager action=install_bundle target=D:\dsh-chajian\dsh-game-control`,
  then refresh/restart DSH; the panel appears in the left sidebar.
- Python ≥ 3.10 with Pillow and numpy is required; the DSH-bundled Python usually already has both. Set
  `DSH_GAME_CONTROL_PYTHON` to override the interpreter.
- No network access, no background input, every action is explicitly triggered. Logs and audit files live in
  `<GAME_CONTROL_HOME>/logs/` (`skill-progress.json`, `loop-state.json`, `loop.jsonl` included); screenshots in
  `<GAME_CONTROL_HOME>/shots/`; templates in `templates/`; skills in `skills/`.
- Window capture, window activation, circular motion and template matching are Windows-only; other platforms
  return `UNSUPPORTED_PLATFORM`. The skill library storage/CRUD itself is cross-platform.
- Acceptance: `node test/acceptance.mjs` (no third-party deps, no side effects, exit code 0 = no FAIL; long
  actions and loops are only tested via static/dry checks, and the WGC live path + real mining/loop scenarios are
  verified separately by the maintainer in an unrestricted environment).
- References: [LJL454/dsh-computer-use](https://github.com/LJL454/dsh-computer-use),
  [gychen-NJU/dsh-game-daily](https://github.com/gychen-NJU/dsh-game-daily).

---

## License

MIT（见 `dsh-game-control/package.json`）。随包分发第三方组件：

- `windows-capture` 2.0.1（`dsh-game-control/python/vendor/windows_capture/`）——MIT 许可，LICENSE 随包附上。
