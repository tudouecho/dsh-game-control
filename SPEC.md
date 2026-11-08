# SPEC — DSH 游戏操作插件（dsh-game-control）

本文件是团队唯一契约。所有实现必须与本文一致；如需偏离，先在群聊说明并由 Lead 更新本文件。
工作目录：`D:\dsh-chajian`。DSH 版本：`0.2.2-rc.1`（桌面端，profile=`desktop`）。

## 0. 交付目标（用户原话归纳）

1. DSH 插件，带**左侧栏入口**（`sidebar.panellist`）和对应主面板（`main`），可在面板里直接操作键鼠、随时截图。
2. 实际动作由 **Python 跨平台后端**（Windows 优先）执行：鼠标移动/点击/拖拽/滚轮、键盘按键/组合键/输入文本、
   全屏与区域截图（保存文件并返回路径，可选返回 base64）。
3. Host 侧同时注册 **agent 工具**（`game_control`），与面板共用同一套动作实现。
4. 交付：可运行代码、`requirements.txt`、`README.md`、最小验收用例。
5. 不联网；敏感操作只在被显式调用时执行（无后台自动输入、无定时点击、无网络请求）。

## 1. 目录结构与文件归属（严格，不要越界写文件）

```
D:\dsh-chajian\
├── SPEC.md                          # Lead
├── README.md                        # verifier
├── requirements.txt                 # py-engineer（根级，用户第一眼看到）
├── test\
│   └── acceptance.mjs               # verifier（最小验收用例）
├── dsh-game-control\                # DSH bundle 包（安装入口）
│   ├── package.json                 # Lead
│   ├── cordis.patch.yml             # Lead
│   ├── index.js                     # Lead（Host：路由 + 工具 + Python 桥）
│   ├── client.js                    # ui-engineer（Client：侧栏图标 + 主面板）
│   ├── icon.svg                     # Lead
│   ├── locale\en.json, zh.json      # Lead（插件管理器显示元数据）
│   └── python\
│       ├── game_control.py          # py-engineer（入口：CLI/JSONL/分发/校验/日志）
│       ├── win_native.py            # py-engineer（Windows ctypes：SendInput + GDI 截图 + DPI）
│       ├── screenshot.py            # py-engineer（跨平台截图 + 保存 + base64）
│       ├── input_backend.py         # py-engineer（非 Windows：pynput/xdotool 后端）
│       ├── test_backend.py          # py-engineer（Python 单元/冒烟测试）
│       └── requirements.txt         # py-engineer（运行依赖）
└── _refs\                           # 参考源码（只读，不修改，不交付说明）
```

`_refs/dsh-computer-use-src`、`_refs/dsh-game-daily-src` 是已下载的参考实现，**只读**。
关键参考位置：
- 路由注册：`_refs/dsh-game-daily-src/index.js` 第 727–755 行（`ctx.webServer.register({kind:'prefix', path, handler})`）。
- 客户端：`_refs/dsh-game-daily-src/client.js` 第 1–260 行（`window.__ModuleLoader__.load`、`fetch('/api/...')`、CSS、
  locale）与第 876–901 行（`apply(ctx)`、`ctx.slots.inject`、`ctx.locale.register`）。
- 侧栏 + 主面板注册：`C:\Users\<用户名>\.dsh\profiles\desktop\node_modules\dsh-hyperframes\lib\client.js` 第 136–137 行：
  ```js
  ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: id }, component));
  ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({ name: 'sidebar.panellist', id, order, label }, Icon));
  ```
- 工具定义（含 `output.render`）：`C:\Users\<用户名>\.dsh\profiles\desktop\node_modules\dsh-game-material-master\lib\tools.js`
  第 954–1045 行（`ctx.tools.register({name,description,parameters,output:{schema,render},execute})`）。
  注意：参数 schema 里每个 `object` 必须显式写 `additionalProperties: false`（或 true），否则宿主拒绝注册。
- 工具结果文本块写法：同文件 `textBlocks(...)`（返回 `[{type:'text', text}]`）。

## 2. Python 后端契约

入口：`python game_control.py ...`（工作目录＝`dsh-game-control/python`，也支持任意 cwd，用绝对路径定位自身目录）。

### 2.1 运行模式

| 模式 | 命令 | 说明 |
|---|---|---|
| 单次 CLI | `game_control.py --json '{"action":"status"}'` | 打印一行 JSON 到 stdout，然后退出 |
| stdin 流 | `game_control.py --stdin` | 每行一个请求 JSON，每行一个响应 JSON，读到 EOF 退出 |
| 常驻 worker | `game_control.py --serve` | 启动时先输出 `{"ok":true,"event":"ready",...}`，之后每行请求→每行响应；Host 使用此模式 |
| 自检 | `game_control.py --selftest` | 只做无副作用检查（不移动/点击/按键），输出 JSON，退出码 0=通过 |
| 版本 | `game_control.py --version` | 输出版本 JSON |

所有 JSON 必须单行（`ensure_ascii=False` 可选，但必须一行）、`flush=True`。worker 模式下 stdout 只允许 JSON 行，
日志/诊断一律走 stderr 或文件。

### 2.2 请求/响应信封

请求（CLI/流/worker 通用）：
```json
{ "id": 1, "action": "mouse_click", "params": { "x": 100, "y": 200, "button": "left" } }
```
`id` 可省略；worker 模式下必须回显同一 `id`。未知字段：顶层 `id/action/params` 之外的多余字段忽略；
`params` 内未知字段 → 错误 `UNKNOWN_PARAM`。

成功响应：
```json
{ "id": 1, "ok": true, "action": "mouse_click",
  "result": { "x": 100, "y": 200, "button": "left" },
  "durationMs": 13, "backend": "win32-sendinput", "ts": "2026-11-08T08:00:00.123Z" }
```
失败响应（进程仍继续工作）：
```json
{ "id": 1, "ok": false, "action": "mouse_click", "durationMs": 2,
  "error": { "code": "INVALID_ARGUMENT", "message": "x 必须是数字", "hint": "传 x/y 为物理像素坐标" } }
```
- CLI 模式即使动作失败也打印信封并以退出码 0 结束；只有**启动阶段致命错误**（找不到 Pillow 且需要截图等）才用
  退出码 3。`--selftest` 失败退出码 1。
- 未捕获异常必须转成 `ok:false` + `code:"INTERNAL"`（含 traceback 摘要到日志，不把整段 traceback 返回给调用方）。

### 2.3 动作表（action 名与参数必须逐字一致）

坐标系：**物理像素**，原点为主屏左上角；多屏虚拟桌面允许负坐标。`status` 返回当前几何。

| action | 参数（* 必需） | 返回 result |
|---|---|---|
| `status` | 无 | `{platform, pythonVersion, backend, dpiAware, virtualScreen:{x,y,width,height}, primary:{x,y,width,height}, displays:[...], pointer:{x,y}, shotDir, logDir}` |
| `mouse_move` | `x*`,`y*`, `durationMs?`(0–5000, 默认 0) | `{x,y}`（移动后的实际位置） |
| `mouse_move_rel` | `dx*`,`dy*` | `{x,y}` |
| `mouse_click` | `x?`,`y?`,`button?`(left/right/middle/x1/x2，默认 left), `clicks?`(1–10，默认 1), `intervalMs?`(0–1000，默认 60) | `{x,y,button,clicks}` |
| `mouse_down` / `mouse_up` | `button?`，`x?`,`y?`（给了先移动） | `{x,y,button}` |
| `mouse_drag` | `x1*`,`y1*`,`x2*`,`y2*`, `button?`, `durationMs?`(0–60000，默认 300), `steps?`(2–200) | `{x1,y1,x2,y2,button,durationMs}` |
| `mouse_scroll` | `amount*`(-1000..1000，正=上/左), `x?`,`y?`, `horizontal?`(bool) | `{amount,horizontal,x,y}` |
| `mouse_position` | 无 | `{x,y}` |
| `key_press` | `key*`, `presses?`(1–20，默认 1), `intervalMs?`(0–1000) | `{key,presses}` |
| `key_down` / `key_up` | `key*` | `{key}` |
| `hotkey` | `keys*`（数组 1–4 个键名，如 `["ctrl","shift","s"]`，按下顺序、反向释放） | `{keys}` |
| `type_text` | `text*`（≤10000 字符）, `intervalMs?`(0–200，默认 12) | `{length}`（**不返回原文**） |
| `screenshot` | `region?`{x,y,width,height}（坐标可为负；`width/height` 必须为正整数）, `monitor?`(int，**1 起**，与 `list_displays` 的 index 一致), `allScreens?`(bool), `path?`(绝对/相对), `format?`("png"/"jpeg"，默认 png), `quality?`(1–100，jpeg 用), `base64?`(bool，默认 false) | `{path, absolutePath, width, height, format, bytes, base64?, base64Length?}` |
| `list_displays` | 无 | `{displays:[...]}` |
| `list_windows` | `titleContains?`(str), `includeInvisible?`(bool 默认 false), `limit?`(1–100 默认 50) | `{count, windows:[{hwnd,title,class,pid,process,rect:{x,y,width,height},minimized,visible,foreground,cloaked}]}`（z 序：前台/最上层在前） |
| `capture_window` | `window?`(标题子串) 或 `hwnd?`(int)（二选一，至少一个），`engine?`("auto"/"wgc"/"printwindow"/"screen"，默认 auto), `includeCursor?`(bool 默认 false), `timeoutMs?`(500–10000，默认 3000), `path?`, `format?`, `quality?`, `base64?` | `{window:{hwnd,title,class,pid,process,rect}, engine:"wgc"\|"printwindow"\|"window-dc"\|"screen", path, absolutePath, width, height, format, bytes, warnings?:[str], fallbackReason?:str, base64?, base64Length?}` |

`region` 语义：`width/height` 非数字或 ≤0 → `INVALID_REGION`；与虚拟桌面**完全没有交集** → `OUT_OF_BOUNDS`；
**部分越界**则裁剪到虚拟桌面并返回裁剪后的 `width/height`（不报错）。`base64:true` 时 `base64` 是不含
`data:` 前缀的 base64 字符串；`bytes` 是落盘文件字节数。**agent 工具**里的 screenshot 会强制 `base64=false`
（避免把数 MB base64 塞进对话上下文），面板预览才请求 base64。

### 2.3.1 窗口枚举与后台窗口截图（v0.2 新增）

目标：抓取**指定窗口**的内容；窗口可以**不是前台、可以被其它窗口遮挡**，用于「游戏窗口在后台，agent 也能看到画面」。

实现要求（Windows）：
1. 窗口枚举：`EnumWindows` + `GetWindowTextW`/`GetClassNameW`/`GetWindowThreadProcessId`/`IsWindowVisible`/`IsIconic`/
   `GetForegroundWindow`/`DwmGetWindowAttribute(EXTENDED_FRAME_BOUNDS, CLOAKED)`；进程名用
   `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `QueryFullProcessImageNameW`（不要用 tasklist）。
2. 选窗口：`hwnd` 优先精确命中；否则 `titleContains`（大小写不敏感）匹配，优先 visible 且非 minimized、
   面积最大的窗口；找不到 → `WINDOW_NOT_FOUND`（hint 提示用 `list_windows`）。
3. **WGC（Windows Graphics Capture）为真后台抓取主路径**：
   - 依赖随插件分发的 `python/vendor/windows_capture/`（`windows-capture` 2.0.1 wheel 解包，MIT，附 LICENSE）
     + 本机 Python 的 `numpy`；**运行时不联网**。
   - `windows_capture/__init__.py` 顶层 `import cv2`，本插件不用 `cv2.imwrite`；导入前若 `cv2` 缺失，向
     `sys.modules['cv2']` 注入最小 stub（仅 `imwrite` 抛错），避免为截图安装 40MB OpenCV。
   - **Win10 兼容**：必须传 `cursor_capture=None, draw_border=None`——Win10 不支持
     `GraphicsCaptureSession.IsBorderRequired`，传 `False` 会抛 "Toggling the capture border is not supported"。
   - 用 `start_free_threaded()` 拿 `CaptureControl`，等首帧最多 `timeoutMs`，然后 `control.stop()`；
     帧缓冲 BGRA → RGB，交给 `screenshot.save_image` 编码。
   - 窗口最小化 / 受保护窗口 / 拿不到帧 → `WINDOW_CAPTURE_FAILED`（hint：确认窗口未最小化、未被反截图保护）。
4. 回退链（engine=auto）：`wgc` → `printwindow`（`PrintWindow` flags 0 与 `PW_RENDERFULLCONTENT(2)`；
   黑帧判定：blackRatio>0.98 或唯一色<3）→ `window-dc`（`GetWindowDC`+`BitBlt`，对 GDI 窗口后台可用）
   → `screen`（按窗口矩形截屏幕，只能拿到可见像素，返回 `warnings:["window may be occluded"]`）。
   显式 `engine` 时只走指定路径，失败返回错误。
5. 结果中的 `engine` 必须如实反映实际路径；回退时填 `fallbackReason`。
6. 现有 `screenshot` 语义不变。`capture_window` 与 `list_windows` 都不注入任何输入。
7. 跨平台：`list_windows`/`capture_window` 在非 Windows 返回 `UNSUPPORTED_PLATFORM`（hint 说明仅 Windows）。


键名（canonical，大小写不敏感）：单字符 `a`–`z`、`0`–`9`、`- = [ ] ; ' , . / \``；
`enter, esc, tab, space, backspace, delete, insert, home, end, pageup, pagedown, up, down, left, right,
f1..f24, ctrl, control, alt, shift, win, meta, caps_lock, num_lock, scroll_lock, print_screen, pause, menu`。
别名：`escape→esc`, `return→enter`, `del→delete`, `pgup→pageup`, `pgdn→pagedown`, `cmd/command/super→win`。
未知键 → `INVALID_KEY`。

默认截图目录：`<GAME_CONTROL_HOME>/shots/`，`GAME_CONTROL_HOME` 默认 `%USERPROFILE%\.dsh\game-control`（非 Windows 用 `~/.dsh/game-control`）。
文件名：`shot_YYYYMMDD_HHMMSS_fff.(png|jpg)`。`path` 为相对路径时相对该目录；父目录不存在则创建。

### 2.4 错误码（`error.code`）

`INVALID_ARGUMENT`（类型/范围）、`UNKNOWN_ACTION`、`UNKNOWN_PARAM`、`INVALID_KEY`、`INVALID_REGION`、
`OUT_OF_BOUNDS`、`WINDOW_NOT_FOUND`、`WINDOW_CAPTURE_FAILED`、`MISSING_DEPENDENCY`（附 pip 安装提示）、
`UNSUPPORTED_PLATFORM`、`PERMISSION_DENIED`、`FILE_IO_ERROR`、`BACKEND_ERROR`、`INTERNAL`。
Host 侧另有 `PYTHON_NOT_FOUND`、`TIMEOUT`、`BACKEND_UNAVAILABLE`。

### 2.5 平台实现要求

- **Windows（必须真机跑通，最高优先级）**：
  - `win_native.py` 纯 `ctypes`，不依赖 pyautogui/pynput：
    - DPI：进程启动时 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，失败回退 `SetProcessDPIAware()`；
      `status.dpiAware` 记录结果。
    - 输入：`SendInput`（`INPUT`/`MOUSEINPUT`/`KEYBDINPUT` 结构；64 位下用 `ctypes.c_size_t` 作 ULONG_PTR）。
      鼠标绝对定位用 `MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK`，按虚拟桌面归一化到 0..65535。
      键盘用 VK；文本用 `KEYEVENTF_UNICODE`（`\n`→VK_RETURN，`\t`→VK_TAB）。
      每次 SendInput 后检查返回条数，不足 → `BACKEND_ERROR`。
    - 截图：GDI 路线（`GetDC(0)`→`CreateCompatibleDC`→`CreateCompatibleBitmap`→`BitBlt`，可选 `CAPTUREBLT`；
      `GetDIBits` 取 BI_RGB 32bpp BGRX 原始像素，行序自底向上需翻转）→ Pillow `Image.frombytes` 保存。
      单显示器场景必须实测通过；多显示器失败时返回 `BACKEND_ERROR` 并提示，不得静默截错屏。
- **macOS/Linux**：`input_backend.py` 优先 `pynput`（没有则 `xdotool`/`osascript` 兜底），缺依赖时返回
  `MISSING_DEPENDENCY` 且 hint 给出 `pip install pynput`。截图：Pillow `ImageGrab`（Windows/macOS）→ `mss` →
  平台命令（`screencapture -x -R`、`grim`、`scrot`、`import`）。Wayland/权限不足给出明确错误。
- Pillow 为**必需**（PNG/JPEG 编码）；缺失时截图返回 `MISSING_DEPENDENCY` 并 hint `pip install pillow`，
  其他动作仍可用。不得 import 任何联网库（socket/urllib/http/requests/websocket）。

### 2.6 日志与安全

- 文件日志：`<GAME_CONTROL_HOME>/logs/game-control.log`（`logging`，单文件滚动 2MB×3）。
- 动作审计：`<GAME_CONTROL_HOME>/logs/actions.jsonl`，每行 `{ts,action,ok,code?,durationMs}`；
  **不得记录 `type_text` 原文**（只记 `length` 与前 8 字符预览可选）。
- 无后台动作：除 `--serve` 读 stdin 外不得有线程/定时器主动注入输入。
- 所有动作都必须由一条请求显式触发；`--serve` 空闲时不做任何输入。

### 2.7 Python 自检与测试（`test_backend.py`，仅 stdlib + Pillow）

`python test_backend.py` 必须覆盖（**不得产生真实点击/按键**）：
1. 信封/参数校验：未知 action、缺参数、非法 key、非法 region 均返回对应错误码。
2. `status` 返回合法几何（宽高 > 0）。
3. 全屏截图到临时目录 → 文件存在、PNG 魔数、宽高 > 0；区域截图 200×120 → 宽高精确匹配（或按交集裁剪）。
4. `base64:true` → 返回 base64 且长度 > 0、可被 `base64.b64decode` 解出 PNG 魔数。
5. 源码扫描：`python/*.py` 不含联网 import。
6. `--selftest` 与 `--json '{"action":"status"}'` 子进程退出码 0 且 JSON 可解析。
测试打印清晰 PASS/FAIL 汇总，失败退出码 1。

## 3. Host 插件契约（`dsh-game-control/index.js`）

- 导出：`export const name = '@local/dsh-game-control'`（Loader 行 id 用 `game-control`，package name 用包名）；
  `export const inject = ['tools']`；`export function apply(ctx) {}`。
- 可选服务 `webServer` 用 `ctx.get('webServer')` 获取，**不要**放进 inject（无 web 的 profile 也要能注册工具）。
- 所有注册都放进 `ctx.effect(...)` 并返回清理函数；插件卸载必须杀掉 Python 子进程。

### 3.1 路由（前缀 `/api/game-control`，kind: 'prefix'）

| 方法 路径 | 请求 | 响应 |
|---|---|---|
| `GET /api/game-control/health` | — | `{ok:true, version, bridge:{ready,pythonPath?,lastError?}}` |
| `GET /api/game-control/status` | — | `{ok:true, bridge:{ready,pythonPath,running,lastError}, backend:<python status 信封>}`；Python 不可用时 `backend` 是 `{ok:false,error:{...}}` 的错误信封（路由本身仍返回 200 + `ok:true`，面板据此显示原因） |
| `POST /api/game-control/exec` | `{"action":"...","params":{...}}`（≤256KB） | Python 信封原样返回（`ok` 可能是 false）；JSON 解析失败 → 400 `{ok:false,error:{code:'INVALID_ARGUMENT',message:...}}` |
| `GET /api/game-control/logs?lines=80` | — | `{ok:true, logTail:string}`（读 `<logDir>/game-control.log` 尾部；读不到返回空串） |

响应头：`content-type: application/json; charset=utf-8`、`cache-control: no-store`。
路由 handler 用 `new URL(req.url, 'http://127.0.0.1')` 解析子路径；必须 try/catch，内部错误返回 500 JSON。

### 3.2 Python 解析与桥接（`index.js` 内实现）

解析顺序（找到第一个能跑 `--version` 的）：
1. `process.env.DSH_GAME_CONTROL_PYTHON`
2. `<pluginDir>/python/.venv/Scripts/python.exe`（win）/`python/.venv/bin/python3`（posix）
3. Windows：扫描 `path.join(os.homedir(),'.dsh','dsh-runtimes','*','dependencies','python','python.exe')`
4. PATH：`python3` → `python` → Windows 额外 `py -3`
5. 找不到：`PYTHON_NOT_FOUND`（提示 `GAME_CONTROL_PYTHON=...` 与 `pip install -r requirements.txt`）

桥接行为：
- 常驻 worker：`spawn(python, [backendScript, '--serve'], {windowsHide:true, stdio:['pipe','pipe','pipe']})`，
  用 readline 读 stdout，逐行 JSON；请求带自增 id；单请求超时（默认 15s，截图 30s，`type_text` 按长度放宽到
  15s+20ms/字符）；超时 → kill + 拒绝并清空 in-flight，下次调用自动重启；stderr 保留最后 8KB 便于排错。
- 进程退出：拒绝所有 in-flight（`BACKEND_UNAVAILABLE`，附 stderr 尾部）；`ctx.effect` 清理时 `kill()`。
- `bridge.execute(action, params)` 返回 Python 信封；网络/超时错误包成 `ok:false` 信封（不抛异常交给路由层 500）。

### 3.3 Agent 工具 `game_control`

单个工具，扁平行参（不是嵌套 params），action 枚举与 Python 动作表一致：

```js
ctx.tools.register({
  name: 'game_control',
  description: '控制本机鼠标/键盘并截图（游戏辅助）。所有动作都立即执行、只影响当前聚焦窗口；'
    + '截图返回文件路径（可再配合 read_image 查看）。action 见枚举。',
  parameters: {
    type: 'object', additionalProperties: false,
    properties: {
      action: { type: 'string', enum: [...], description: '动作名' },
      x: { type: 'number' }, y: { type: 'number' }, x1: ..., // 见动作表，全部可选字段都列出
      button: { type: 'string', enum: ['left','right','middle','x1','x2'] },
      clicks: { type: 'number' }, amount: { type: 'number' }, horizontal: { type: 'boolean' },
      key: { type: 'string' }, keys: { type: 'array', items: { type: 'string' } },
      presses: { type: 'number' }, text: { type: 'string' },
      region: { type: 'object', additionalProperties: false, properties: {...} },
      monitor: { type: 'number' }, allScreens: { type: 'boolean' },
      path: { type: 'string' }, format: { type: 'string', enum: ['png','jpeg'] },
      quality: { type: 'number' }, base64: { type: 'boolean' },
      durationMs: { type: 'number' }, intervalMs: { type: 'number' }, steps: { type: 'number' },
      titleContains: { type: 'string' }, includeInvisible: { type: 'boolean' },
      window: { type: 'string' }, hwnd: { type: 'number' },
      engine: { type: 'string', enum: ['auto','wgc','printwindow','screen'] },
      includeCursor: { type: 'boolean' }, timeoutMs: { type: 'number' },
    },
    required: ['action'],
  },
  output: {
    schema: { type: 'object', additionalProperties: true },
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
  },
  async execute(args) {
    const { action, ...params } = args ?? {};
    const envelope = await bridge.execute(action, params);
    if (!envelope.ok) {
      const e = envelope.error ?? {};
      const error = new Error(`[${e.code ?? 'BACKEND_ERROR'}] ${e.message ?? '执行失败'}`);
      error.code = e.code; throw error;
    }
    // 返回 JSON-safe：{ok, action, result, durationMs}
    return JSON.parse(JSON.stringify({ ok: true, action, result: envelope.result, durationMs: envelope.durationMs }));
  },
});
```
框架要求：**所有输出必须是 JSON 安全值**（不能有 undefined、函数、BigInt），失败用 throw Error。
注册要逐个 try/catch 并记录 `ctx.logger?.warn`，不能让一个工具失败拖垮整个插件。

### 3.4 其他 Host 要求

- 版本号从 `package.json` 读取（不要写两遍）。
- 日志用 `ctx.logger?.info/warn/error`；启动时打一条 `game-control: ready (python=..., version=...)`。
- 不得读写 profile 的 `cordis.patch.yml`/`package.json`；不得联网。
- 代码是 ESM，可直接 `node --check index.js`（本机 Node 22 在 `node.exe`）。
- **不要 import 任何 `@deepseek-ai/*` 包**（本地 link 安装时解析不到）；只用 `node:` 内置模块与全局 fetch 等。

## 4. Client 插件契约（`dsh-game-control/client.js`）

必须完全模仿参考文件的加载方式（`window.__ModuleLoader__.load`，`id` = `@local/dsh-game-control`），
`factory(require)` 里 `require('react')`，返回 `{ inject: ['slots','locale'], apply(ctx) }`。

### 4.1 槽位注册

```js
const ID = 'game-control';
const NS = 'gameControl.panel';
// 侧栏入口（左侧栏，全局面板图标）
ctx.slots.inject('sidebar.panellist', () => ctx.slots.register(
  { name: 'sidebar.panellist', id: ID, order: 72, label: () => t('nav') },
  SidebarIcon,       // 接收 ownerProps {size, active}，返回 SVG
));
// 主面板（点侧栏图标后显示；id 必须与侧栏 id 相同，owner 按 id 派发）
ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: ID }, Panel));
```
- 图标：自绘 SVG（手柄/鼠标），用 `currentColor` 或 `active` 换色，`size` 生效；不要外部图片。
- 样式：`document.createElement('style')` + `ctx.effect` 清理；只使用 `--dsw-alias-*` 主题 token（可带 fallback）。
  类名前缀 `dgc-`。
- 文案：`ctx.locale.register(NS, { zh: {...}, en: {...} })`，`const t = ctx.locale.bind(NS)`。
- 面板里**不要** import 任何 Harness Client 包；不要改 document.body、不要 iframe。

### 4.2 面板 UI（满足用户「可直接操作」）

一个页面内分区，紧凑、可滚动：

1. **状态区**：后端是否就绪、Python 路径/版本、屏幕尺寸、截图目录；「刷新」按钮；错误红字显示。
2. **鼠标区**：X/Y 输入框 + 按钮「移动」「左键单击」「右键单击」「双击」「按下/抬起」「拖拽（起止坐标）」
   「滚轮上/下」；「读取当前位置」。操作后显示结果/错误。
3. **键盘区**：单键输入 +「按键」；组合键输入（`ctrl+shift+s`）+「发送」；文本框 +「输入文本」；
   快捷按钮：W A S D 空格 Shift Esc Enter F Q E R Tab 1–5（点击= key_press，长按= key_down/key_up）。
4. **截图区**：按钮「全屏截图」「区域截图」（区域 x/y/w/h 输入）；勾选「返回 base64（用于预览）」默认开；
   截图后显示 `<img src="data:image/png;base64,...">` 预览与「保存路径」文本（可复制）；显示尺寸/字节数。
   其下增加 **「窗口截图（后台）」卡片**（对应 `list_windows` / `capture_window`）：
   「刷新窗口列表」按钮（`includeInvisible:false`）→ 下拉选择窗口（`标题 — 进程 (hwnd, W×H)`，前台/最小化标记）；
   引擎下拉 `auto/wgc/printwindow/screen`（默认 auto）；「抓取窗口」按钮 → `capture_window`（hwnd、engine、base64:true、
   timeoutMs:5000）；成功后展示 base64 预览、保存路径、尺寸/字节、实际 `engine` 与 `warnings/fallbackReason`；
   错误（WINDOW_NOT_FOUND / WINDOW_CAPTURE_FAILED）中文展示。窗口列表加载失败要可重试。
5. **最近操作日志**：最近 30 条 `{time, action, ok, message}`，失败红色。
6. 所有按钮 `disabled={busy}`，有 busy 指示；fetch 用相对路径 `/api/game-control/exec`（`credentials:'same-origin'`）。
7. 前端也要校验明显错误（空 key、非数字坐标）并给出提示，不把请求打到后端。
8. **不要**在组件里用 `setInterval` 做动作或轮询截图；只在挂载时 `GET /status` 一次。

### 4.3 面板视觉

- 结构参考 `_refs/dsh-game-daily-src/client.js` 的卡片/按钮/输入框 CSS（第 171–222 行），改名 `dgc-`。
- 用 `--dsw-alias-label-primary / label-secondary / label-tertiary / border-l2 / bg-overlay /
  interactive-bg-hover` 等 token。

## 5. 安装与验证（Lead 负责，不进 teammates 写入范围）

- 安装：`plugin_manager action=install_bundle target=<绝对包目录>`（Lead 执行，需审批）。
- 注册验证：`cordis_inspect_query client Slots.listSubTree` 查 `sidebar.panellist` 与 `main` occupants 是否含
  `game-control`；`Tool.listTools` 是否出现 `game_control`。
- 路由验证：本机 `curl http://127.0.0.1:5748/api/game-control/health` 与 `/status`。
- 视觉验证：无浏览器控制能力，只能报告「已注册、未做像素级视觉验证」。

## 6. 验收标准（verifier 的 `test/acceptance.mjs` 必须全部通过）

1. 文件齐全：`dsh-game-control/{package.json,index.js,client.js,cordis.patch.yml,icon.svg,locale/zh.json,
   locale/en.json,python/game_control.py,python/requirements.txt}`、根 `README.md`、根 `requirements.txt`。
2. `node --check dsh-game-control/index.js` 与 `node --check dsh-game-control/client.js` 通过。
3. `package.json`：name=`@local/dsh-game-control`，`dsh.bundle.patch` 指向存在的文件，`dsh.client.platform==='web'`，
   `exports['./client']` 存在。
4. locale 两个 JSON 可解析且 `meta.title`/`meta.description` 非空；`icon.svg` 以 `<svg` 开头且含 `viewBox`。
5. Python：`--selftest` 通过；`status` 返回合法几何；截图 PNG 落盘+尺寸正确；区域截图尺寸正确；
   base64 可解码；非法 action/key/region 返回正确错误码；源码不含联网 import。
6. `index.js` 静态检查：包含 `ctx.tools.register`、`/api/game-control`、`--serve`、`spawn(`、`PYTHON_NOT_FOUND`。
7. `client.js` 静态检查：包含 `sidebar.panellist`、`main`、`/api/game-control/exec`、`ctx.locale.register`。
8. 汇总输出 PASS/FAIL，失败退出码 1；截图等产物写到 `D:\dsh-chajian\test\.tmp\` 并清理。

## 7. 协作约定

- 先读本文件与 `_refs` 参考，再动手；不要修改别人的文件；遇到契约歧义立即在消息里问 Lead。
- 每完成一个文件用对应语言做语法自检：`node --check xxx.js`、`python -m py_compile xxx.py`。
- 禁止真实点击/按键的自动化测试（验收里不点、不按）；截图允许。
- 不联网；GitHub 参考已下载到 `_refs`，不要再下载。

---

# 8. v0.3（版本 0.3.0）：游戏自动化动作 + 技能库

> 依据：`docs/v0.3-开发提示词.md` + 用户追加需求「插件内增加类似现有技能的模块，可人工查看/修改/备份/重复调用已适配技能」。
> 必须保持 v0.2 全部 18 个动作、错误码、HTTP 路由、侧栏面板、日志、测试不回归。

## 8.1 目标

1. agent 能用 `game_control` 完成《Keep on Mining!》挖矿循环：激活窗口 → 找图点击「开始游戏」→ 等 2s →
   找图点击「开始挖矿」→ 窗口内 30s 大圆轨迹 → 等「挖矿已完成」→ 点击「开始挖矿！」进入下一轮（可重复）。
2. 插件内新增「技能库」：把已适配的游戏流程保存为可读的 skill（Markdown 说明 + 步骤 JSON），
   支持人工查看、编辑、自动备份/版本恢复、删除（软删到回收目录）、复用执行（agent 工具 + 面板 + HTTP）。

## 8.2 新增控制动作（5 个，字段名逐字一致）

### 8.2.1 `activate_window`
参数：`window?`(标题子串)/`hwnd?`(number) 二选一；`restore?`(bool 默认 true)；`timeoutMs?`(100–5000 默认 2000)。
行为：复用 `window_capture.resolve_window()`；最小化且 restore 时 `ShowWindow(SW_RESTORE)`；依次尝试
`SetForegroundWindow` → `AttachThreadInput+BringWindowToTop+SetForegroundWindow` → 轻点 `VK_MENU` 解锁后再
`SetForegroundWindow`；轮询 `GetForegroundWindow()` 最多 `timeoutMs`；**不得注入点击**。
返回：`{window:{hwnd,title,class,pid,process,rect,clientRect}, foreground:true, attempts}`。
失败：找不到→`WINDOW_NOT_FOUND`；最终未前台→`WINDOW_ACTIVATION_FAILED`（hint 提 UAC/其他桌面/手动点击）。

### 8.2.2 `mouse_circle`
参数：`centerX*`,`centerY*`；`radius*`(1–5000)；`periodMs?`(300–20000 默认 2500)；`durationMs?`(100–120000 默认 30000)；
`direction?`("cw"默认/"ccw")；`startAngleDeg?`(默认 -90)；`samplesPerSecond?`(30–240 默认 120)；
`leadInMs?`(0–2000 默认 300)；`button?`("none"默认/left/right/middle，画圆期间按住、退出必释放)；
`abortKey?`(键名，默认 "f12"，"none" 禁用)；`hwnd?`(画圆期间校验前台，变化即停)；`dryRun?`(默认 false)。
实现：`circle_motion.points()` 纯函数生成轨迹（`n=int(durationMs/1000*sps)+1`，θ 由 `perf_counter` 绝对时间计算，
不用累加 sleep）；`move_to()` SendInput 绝对坐标；圆包围盒必须完全在虚拟桌面内，否则 `INVALID_ARGUMENT`；
每帧查中止键（`GetAsyncKeyState`）、每 ~100ms 查前台；`finally` 释放鼠标键。
返回：`{centerX,centerY,radius,periodMs,durationMs,direction,samples,loops,start,end,actualDurationMs,aborted,abortReason}`
（中止仍 `ok:true`；`abortReason` 为 `"abort-key"` / `"foreground-changed"`）。`dryRun:true` 不注入、不等待。

### 8.2.3 `wait`
参数：`durationMs*`(0–600000)。同步等待、零输入。返回 `{waitedMs}`。

### 8.2.4 `find_image`
参数：`template*`(PNG/JPG；绝对路径或相对 `<GAME_CONTROL_HOME>/templates/`，≤2MB、宽高≤1024)；
`region?`{x,y,width,height}；`window?`/`hwnd?`(可选，用 clientRect 作搜索区域)；`threshold?`(0–1 默认 0.85)；
`scales?`(1–5 个比例，默认 [1.0])；`grayscale?`(默认 true)；`maxResults?`(1–20 默认 3)；`debugPath?`。
实现：`template_match`：Pillow 截图 → numpy float32 灰度 → FFT 归一化互相关（NCC）+ 积分图分母，多尺度粗搜细比，
NMS 中心距 `max(8, min(tw,th)/4)`。只读，不注入、不激活窗口。
返回：`{found, source, searchRegion, template:{path,width,height}, matches:[{x,y,width,height,center:{x,y},score,scale}], elapsedMs}`；
找不到不是错误（`ok:true, found:false`）。错误：`TEMPLATE_NOT_FOUND` / `INVALID_TEMPLATE` / `MISSING_DEPENDENCY`(numpy)。

### 8.2.5 `wait_image`
继承 find_image 参数，另加：`timeoutMs?`(100–600000 默认 15000)、`intervalMs?`(50–2000 默认 250)、
`mode?`("appear"默认/"disappear")。轮询 find_image；超时返回 `ok:true,{found:false,timedOut:true,attempts,elapsedMs}`。
返回：`{found,timedOut,match,attempts,elapsedMs}`（多命中时 match 取最高分）。

### 8.2.6 `list_windows` 增加 `clientRect`
每个窗口增加 `clientRect:{x,y,width,height}`（`GetClientRect`+`ClientToScreen(0,0)`，物理像素、与 `rect` 同坐标系）。
向后兼容，原有字段不变。

## 8.3 技能库（新模块）

### 8.3.1 存储
根目录：`<GAME_CONTROL_HOME>/skills/`（默认 `%USERPROFILE%\.dsh\game-control\skills`）。
```
skills/<id>/skill.json              # 当前版本（原子写：tmp + os.replace）
skills/<id>/backups/<ts>.json       # 每次保存/恢复/删除前的旧版本，最多保留 50 份（新的在前）
skills/.trash/<id>-<ts>/            # 软删除
```
`skill.json`：
```json
{"schema":1,"id":"keep-on-mining-loop","title":"Keep on Mining 挖矿循环","description":"...",
 "game":"Keep on Mining!","tags":["mining"],"markdown":"## 说明\n...",
 "steps":[{"action":"activate_window","params":{"window":"Keep on Mining"},"saveAs":"win"},
          {"action":"find_image","params":{"template":"keep-on-mining/start_game.png","window":"Keep on Mining"},"saveAs":"start"},
          {"action":"mouse_click","params":{"x":"$start.center.x","y":"$start.center.y"}}],
 "createdAt":"ISO8601","updatedAt":"ISO8601"}
```
限制：`id` 正则 `^[a-z0-9][a-z0-9._-]{0,63}$`；title 1–200；description ≤2000；game ≤100；
tags ≤20 个且每个 ≤40；markdown ≤200000；steps 1–200；每步 `params` JSON ≤32768；`saveAs` 正则
`^[A-Za-z_][A-Za-z0-9_]{0,31}$`；单步允许字段 `action`、`params`、`saveAs?`、`continueOnError?`、`waitMs?`(0–60000)。

### 8.3.2 技能动作（HTTP `/exec` 与 `game_skill` 工具共用）
| action | 参数 | 返回 |
|---|---|---|
| `skill_list` | 无 | `{count, skills:[{id,title,description,game,tags,stepCount,updatedAt,backupCount}]}` |
| `skill_get` | `id*` | `{skill:{...完整 skill.json...}}`；不存在→`SKILL_NOT_FOUND` |
| `skill_save` | `title*`,`description?`,`game?`,`tags?`,`markdown?`,`steps*`,`id?`（缺省=新建按 title 生成 slug；给了存在的 id=更新） | `{id, created, backupPath?, updatedAt, backupCount}`；更新/覆盖前自动备份旧版本 |
| `skill_versions` | `id*` | `{id, count, versions:[{name,savedAt,bytes}]}`（新→旧） |
| `skill_restore` | `id*`,`backup*`（文件名或 `"latest"`） | `{id, restoredFrom, backupOfCurrent?, updatedAt}`；恢复前先备份当前 |
| `skill_delete` | `id*`,`hard?`(默认 false) | `{id, deleted:true, trashPath, hardDeleted}`；默认移动到 `.trash/`，hard 才真删 |
| `skill_run` | `id*`,`dryRun?`(默认 false),`repeat?`(1–100 默认 1),`timeoutMs?`(1000–600000 默认 300000),`abortKey?`(默认 "f12"，"none" 禁用),`stopOnError?`(默认 true) | 见下 |

`skill_run` 语义：
- 步骤 action 白名单：除 `skill_*` 外的全部控制动作（含 `wait`/`activate_window`/`mouse_circle`/`find_image`/`wait_image`）。
- 变量替换：字符串以 `$` 开头时解析。`$window.*` 来自最近一次 `activate_window`/`list_windows` 解析出的窗口
  （字段 `hwnd,title,class,pid,process,rect,clientRect`，另加计算值 `clientCenterX`、`clientCenterY`、
  `quarterWidth`=round(clientRect.width/4)、`halfWidth`、`halfHeight`）；`$<saveAs>.*` 来自该步返回结果的对象路径
  （支持数组数字下标，如 `$start.matches.0.center.x`）。无法解析→该步失败 `SKILL_VAR_UNRESOLVED`。
- 执行到每步之间检查中止键与 `timeoutMs` 截止时间；`abortKey` 同时会传入内部 `mouse_circle`（若步内未显式给）。
- 执行失败：参数/加载错误走普通错误信封；运行期某步失败时返回 `ok:true`，`result.success=false`，
  `result.failedStep:{index,action,code,message}`（`stopOnError:true` 默认立即停止）。
- 返回：`{skillId, dryRun, repeat, repeatsDone, success, aborted, abortReason, durationMs,
  steps:[{index,action,ok,code?,message?,durationMs?}], failedStep?}`；`dryRun:true` 只做静态校验与变量规划，不执行任何动作、不等待。

### 8.3.3 新错误码
`WINDOW_ACTIVATION_FAILED`、`TEMPLATE_NOT_FOUND`、`INVALID_TEMPLATE`、`SKILL_NOT_FOUND`、`INVALID_SKILL`、
`SKILL_VAR_UNRESOLVED`。中止不是错误（`ok:true` + `aborted:true`）。

## 8.4 Host / 工具 / 面板

- `ACTIONS` 追加：`activate_window, mouse_circle, wait, find_image, wait_image`；`ACTION_LABELS` 中文补齐。
- 新增第二个 agent 工具 **`game_skill`**：动作 `skill_list/skill_get/skill_save/skill_versions/skill_restore/skill_delete/skill_run`，
  参数 schema 逐字段补全（每个 object 显式 `additionalProperties`），`steps` 用
  `{type:'array', items:{type:'object', additionalProperties:true}}` + 描述字段。
- `timeoutFor()`：
  - `activate_window`：`min(10000, (timeoutMs??2000)+5000)`
  - `mouse_circle`：`min(180000, 15000+(leadInMs??300)+(durationMs??30000)+5000)`
  - `wait`：`min(620000, durationMs+5000)`
  - `find_image`：30000
  - `wait_image`：`min(620000, (timeoutMs??15000)+5000)`
  - `skill_run`：`min(620000, (timeoutMs??300000)+10000)`；其余 skill_* 15000
  - 其余动作保持 v0.2 原值。
- HTTP `/exec` 必须能调用全部控制动作 + skill_* 动作（bridge 校验用并集，不要破坏 v0.2 动作）。
- 工具描述补充「游戏自动化推荐流程」：`list_windows → activate_window → find_image/mouse_click → mouse_circle → wait_image`，
  以及 F12 中止说明。
- 面板 `client.js`（用户明确要求技能库 UI）：
  1. 窗口截图卡片增加「激活/前置窗口」按钮（`activate_window`）。
  2. 新增「圆形轨迹」卡片：centerX/centerY/radius/periodMs/durationMs/direction/samplesPerSecond/button/abortKey +「开始（会阻塞）」+「干跑」，
     提示 F12 中止、按当前窗口 clientRect 可一键填圆心/半径。
  3. 新增「模板找图」小组件：模板路径/阈值/查找（可用当前窗口 clientRect 作为区域），显示 center/score，可一键点击命中点。
  4. 新增「技能库」卡片：刷新列表 + 新建；编辑器（title/description/game/markdown 多行/步骤 JSON 多行，`保存`自动备份）；
     版本下拉 +「恢复」；「运行」（dryRun 勾选、repeat 输入）；运行结果按步骤显示成功/失败/错误码；删除（软删）；
     所有请求走 `/api/game-control/exec`，错误中文展示；新增 zh/en locale key 且两边数量一致。
- 面板样式继续只用 `--dsw-alias-*` token、`dgc-` 前缀、`ctx.effect` 清理、无 setInterval、不改 document.body。

## 8.5 测试要求（零真实输入）

`python/test_backend.py` 新增：
1. `circle_points()` 纯函数：采样数、startAngleDeg=-90 首点、顺时针 1/4/1/2/3/4 位置、半径误差≤1、ccw 反向、越界 INVALID_ARGUMENT。
2. `mouse_circle dryRun:true`：`samples>0`、`aborted:false`，且不发生输入（可 monkeypatch `move_to` 断言未调用）。
3. `find_image` 纯函数/合成图：400×300 合成图 + 60×20 亮块模板 → 命中中心误差≤2、score>0.95；空白图 → found:false；
   不存在路径 → TEMPLATE_NOT_FOUND；损坏图 → INVALID_TEMPLATE。
4. `wait_image` 合成图 + 短超时 → `timedOut:true`。
5. `activate_window` 参数校验：window/hwnd 同时给/都不给 → INVALID_ARGUMENT；不存在 → WINDOW_NOT_FOUND；不做真实激活。
6. `list_windows` 每项有 `clientRect` 且宽高>0。
7. 技能库：save→list→get→update 产生 backup→versions→restore→delete(软删)；非法 id/超限 steps → INVALID_SKILL/SKILL_NOT_FOUND；
   `skill_run dryRun:true` 返回步骤计划；`skill_run` 只含 `wait durationMs:0` 的 skill 可真实执行且零输入副作用。
8. `--selftest` 增加：numpy 可用、模板目录可创建、`GetAsyncKeyState` 可用。

`test/acceptance.mjs` 新增静态/只读检查：
- index.js `ACTIONS` 含 5 个新动作与 `game_skill` 工具；`timeoutFor` 含 `mouse_circle`/`wait_image`/`skill_run` 分支；
  toolDefinition 含 `centerX,centerY,radius,periodMs,direction,startAngleDeg,samplesPerSecond,abortKey,template,threshold,scales`；
- game_control.py `ACTION_PARAMS` 含 5 个新动作 + 7 个 skill_* 动作；README 含新动作表、F12 中止、技能库章节；
- CLI：`mouse_circle dryRun:true` 校验 samples；`find_image` 缺失模板 → TEMPLATE_NOT_FOUND；
  `wait_image` 用合成小模板+极小区域+短超时验证 `timedOut:true`（只读截图允许，否则 SKIP）；
  `skill_save/get/list/versions/restore/delete` 与 `skill_run dryRun` 用 `GAME_CONTROL_HOME=test/.tmp/...` 全流程。
- 原有 60 项不回归；产物写 `test/.tmp/` 并清理。

## 8.6 实机验收（Lead，需用户在场；输入操作前征得同意）

按 `docs/v0.3-开发提示词.md` §7：动态读取窗口/clientRect；从游戏截图裁剪 4 个文字模板到
`<GAME_CONTROL_HOME>/templates/keep-on-mining/`；`activate_window → find_image/click → wait(2000) →
wait_image(start_mining)/click → mouse_circle(30s, radius=quarterWidth) → wait_image(mining_done) →
find_image(next_round)/click → 第二轮`；F12 中止测试；证据存 `docs/v0.3-mining-*.png`，日志核对实际时长。
若 DSH 单次工具调用存在外部硬超时，则先如实报告并降级为「分段执行 + 技能库编排」。

## 8.7 DoD（v0.3）
- [ ] `package.json` 版本 `0.3.0`；
- [ ] `node --check index.js/client.js`、`python -m py_compile python/*.py` 通过；
- [ ] `python test_backend.py` 全 PASS（零真实输入）；
- [ ] `node test/acceptance.mjs` 无 FAIL（允许 SKIP，SKIP 必须说明原因）；
- [ ] `--selftest` 通过且含新能力检查；
- [ ] 实机 ≥2 轮挖矿 + F12 中止通过（或如实报告外部硬超时并给降级方案）；
- [ ] README/SPEC 与实现一致；技能库可从面板人工查看/编辑/备份/恢复/运行；
- [ ] 最终报告：改动文件、命令、测试结果、实机证据、剩余风险（SendInput/DirectInput、DSH 超时、模板随分辨率失效）。

---

# 9. v0.4（版本 0.4.0）：技能配置文件增强（JSONC + 中文注释 + 参数显式化）

> 用户需求：① 能查看/修改技能配置文件里**完整执行步骤**，每步等待时间、执行动作、动作的全部参数（点击次数、
> 按住时间、拖动轨迹、旋转半径与时间等）都要在配置文件里体现；② README 要求生成技能文件时补充**中文注释**，
> 在文件内用注释标明每一步的具体内容。

## 9.1 文件格式：JSONC（带 // 注释的 JSON）

- 技能文件仍为 `skills/<id>/skill.json`，但**允许 `//` 行注释与 `/* */` 块注释**（JSONC）。
- 读取：先剥离注释再 `json.loads`（去除字符串内的 `//` 不能误伤；实现 `strip_jsonc(text)`）；旧的无注释 JSON 文件完全兼容。
- 写入：**统一由插件重新渲染为带中文注释的 JSONC**（`render_skill_jsonc(skill)`），原子写（tmp + os.replace）。
- **参数显式化**：保存前对每步做归一化（`normalize_skill`），把动作默认参数补全写进文件，确保文件可独立读懂：
  - 步骤字段固定：`action`（必填）、`params`（对象，可空）、`waitMs`（默认 0，显式写）、`continueOnError`（默认 false，显式写）、`saveAs?`、`note?`（自定义中文备注，≤200，保留）。
  - 每步参数默认值（必须显式写入）：见 9.2 表。
- **中文注释自动生成**（每次保存/生成时根据步骤重新生成；手工修改注释会被覆盖，但 `note` 字段内容会保留并拼进注释）：
  - 文件头注释：插件名、标题、适用游戏、说明、生成时间、「保存时自动补全参数并重生成注释」提示。
  - 每步前一行：`// 第 N 步：<动作中文名> —— <参数摘要>；<等待/保存说明>`，摘要包含关键参数，如：
    `// 第 6 步：圆形轨迹 —— 以 (1920,1097) 为圆心、半径 480px 画圆 30000ms（周期 2500ms，顺时针，120Hz，按住 none，中止键 f12）；不注入点击。`
  - 支持的动作摘要至少覆盖：`activate_window`、`wait`、`mouse_move`、`mouse_move_rel`、`mouse_click`、`mouse_down`、`mouse_up`、`mouse_drag`、`mouse_scroll`、`mouse_circle`、`key_press`、`key_down`、`key_up`、`hotkey`、`type_text`、`find_image`、`wait_image`、`screenshot`、`capture_window`、`list_windows`、`status` 等。

## 9.2 归一化默认参数表（写入文件的最小显式集合）

| action | 自动补全的默认参数 |
|---|---|
| `activate_window` | `restore:true`, `timeoutMs:2000` |
| `mouse_move` | `durationMs:0` |
| `mouse_click` | `button:"left"`, `clicks:1`, `intervalMs:60` |
| `mouse_down` / `mouse_up` | `button:"left"` |
| `mouse_drag` | `button:"left"`, `durationMs:300`, `steps:20` |
| `mouse_scroll` | `horizontal:false` |
| `mouse_circle` | `periodMs:2500`, `durationMs:30000`, `direction:"cw"`, `startAngleDeg:-90`, `samplesPerSecond:120`, `leadInMs:300`, `button:"none"`, `abortKey:"f12"` |
| `key_press` | `presses:1`, `intervalMs:60` |
| `type_text` | `intervalMs:12` |
| `wait` | （`durationMs` 必填） |
| `find_image` | `threshold:0.85`, `grayscale:true`, `maxResults:3`, `scales:[1.0]` |
| `wait_image` | 继承 find_image + `timeoutMs:15000`, `intervalMs:250`, `mode:"appear"` |
| `screenshot` | `format:"png"`, `base64:false`, `allScreens:false` |
| `capture_window` | `engine:"auto"`, `includeCursor:false`, `timeoutMs:3000` |

未列出的参数不补全。`params` 值为 `$window.*` / `$saveAs.*` 的变量保持原样。

## 9.3 技能动作接口调整

- `skill_get`：返回 `{skill, content, stepsView}`：
  - `skill`：解析/归一化后的对象（含 `steps`，每步含显式 `waitMs`/`continueOnError`）；
  - `content`：磁盘上原始文件文本（JSONC，含中文注释）——面板「完整配置文件」编辑器直接展示/编辑它；
  - `stepsView`：`[{index, action, comment, waitMs, saveAs?}]`，给面板展示只读步骤列表（`comment` 与生成注释同文）。
- `skill_save`：新增参数 `content?`（string，≤1,000,000 字符，整份 JSONC 文本）。
  - 传了 `content`：以它为准解析（剥离注释→json→校验→归一化），忽略其他内容字段（`id` 若同时给出必须与 content.id 一致，或 content 无 id 时用参数 id）；
  - 未传 `content`：维持原有结构化字段（title/description/game/tags/markdown/steps），同样归一化后渲染 JSONC 保存。
  - 返回追加 `content`（保存后重新渲染的 JSONC 文本，UI 用它刷新编辑器）与 `stepsView`。
  - 保存前照旧自动备份旧文件到 `backups/<ts>.json`（备份保留原始 JSONC 文本；`skill_restore` 原样恢复备份文本）。
- `skill_run`：读取时同样剥离注释并归一化（默认参数显式化后再执行）；`note` 字段不参与执行。
- `skill_versions`/`skill_restore`/`skill_delete` 行为不变；备份/恢复保留注释文本。

## 9.4 面板（client.js）

- 「技能库」编辑器改为：**「完整配置文件（skill.json，JSONC，含中文注释）」多行等宽编辑器**，内容来自 `skill_get.content`；
  保存时发送 `{id, content}`，成功后用返回的 `content` 刷新编辑器（显示补全参数/重新生成注释后的版本）。
- 编辑器上方展示只读「步骤列表」：序号 + 动作名 + 中文摘要注释 + `waitMs`（来自 `stepsView`），方便快速核对。
- 保留：技能列表/刷新、运行/干跑（dryRun/repeat/abortKey）、版本下拉+恢复、软删除；移除或收起原先分离的
  title/description/game/markdown/步骤 JSON 输入（这些都在完整配置文件里编辑）。
- 增加提示文案：「保存时自动补全每步参数并重新生成中文注释；手工注释会被覆盖，自定义备注请写 `note` 字段」。
- 新增 zh/en locale key 且数量一致；`node --check` 通过；样式/清理约定不变。

## 9.5 README 要求（必须写清）

- 新增/更新「技能文件格式（JSONC + 中文注释）」章节：给出一份带 `//` 中文注释的完整示例（含每步 `waitMs`、
  `action`、完整参数、`saveAs`、`note`），说明注释由插件生成/更新、`note` 自定义备注会保留、旧 JSON 兼容。
- 明确要求：**生成技能文件时必须补充中文内容注释**，在文件内用注释标明每一步的具体执行内容与关键参数
  （点击次数、按住/等待时间、拖动起止与步数、圆形半径/周期/时长等）。
- 说明查看/修改方式：面板「技能库 → 完整配置文件」直接编辑保存（自动备份），或直接编辑
  `<GAME_CONTROL_HOME>/skills/<id>/skill.json` 后下次运行生效；版本恢复保留历史文件。
- 动作表/技能库章节同步 `content`/`stepsView`/`note`/归一化默认参数表；版本号 0.4.0。

## 9.6 测试要求

`python/test_backend.py` 新增：
1. `strip_jsonc`：字符串内的 `//` 不被误删、行/块注释剥离、注释后 JSON 可解析；无注释 JSON 兼容。
2. 保存结构化技能 → 读取磁盘原文，断言含 `// 第 1 步：`、`"waitMs": 0`、归一化参数（如 `mouse_click` 的
   `clicks/button/intervalMs`；`mouse_circle` 的 `periodMs/direction/...`）；`skill_get.content`/`stepsView` 一致。
3. 用 `content`（JSONC，含注释与 `note`）保存 → 解析成功；`note` 出现在重新生成的注释中；注释被重生成但数据不变。
4. 非法 content（坏 JSON/未知步骤字段/超限）→ `INVALID_SKILL`；保存前旧版本进 backups。
5. `skill_run` 对 JSONC 技能可用（仅 `wait durationMs:0` 技能真实执行，零输入）。

`test/acceptance.mjs` 新增静态/CLI 检查：README 含 JSONC/中文注释要求与示例关键字；`skills.py` 含 `strip_jsonc`/
`render_skill_jsonc`/`normalize_skill`；`game_skill` 工具 schema 含 `content`；`skill_get` 返回 `content`/`stepsView`；
CLI 保存技能后 grep 文件含 `// 第 ` 与显式 `waitMs`；原 77 项不回归。

## 9.7 DoD（v0.4）
- [ ] `package.json` 版本 `0.4.0`；`node --check` / `py_compile` 全绿；
- [ ] `python test_backend.py`、`node test/acceptance.mjs` 全 PASS；
- [ ] 已保存技能 `keep-on-mining-30s` 迁移为新 JSONC（含中文注释、每步显式参数），面板可查看/编辑/保存/恢复/运行；
- [ ] README/SPEC 与实现一致；不改游戏输入行为，不新增真实输入测试；
- [ ] 最终报告：文件清单、测试结果、面板/文件截图或文本样例。

---

# 10. v0.5（版本 0.5.0）：中止键改 Esc + 技能库前置 + 结构化步骤编辑 + 运行防卡死

## 10.1 中止键：F12 → Esc（全部默认值）

- `mouse_circle.abortKey` 默认 `"esc"`（仍可传其他键名或 `"none"` 禁用）。
- `skill_run.abortKey` 默认 `"esc"`；面板「圆形轨迹」「技能运行」默认值同步为 esc；提示文案改为「Esc 中止」。
- **`find_image` / `wait_image` 新增 `abortKey?`**（默认 `"esc"`，`"none"` 禁用）：在每次轮询/扫描前后用 `GetAsyncKeyState` 检查，按下立即停止。
- `skill_run` 自动把自身 `abortKey` 透传给步骤里未显式设置的 `find_image`/`wait_image`/`mouse_circle`；中止时返回
  `aborted:true, abortReason:"abort-key"`，并确保鼠标键释放。
- **关键：** 等待模板阶段按 Esc 必须能立即停止（不再等满 timeout）。兼容键名 `escape`/`esc`。
- 文档/示例技能/面板 locale/测试全部把 F12 改为 Esc。

## 10.2 面板：技能库移到第二项

面板卡片顺序固定为：**① 后端状态 → ② 技能库 → ③ 鼠标 → ④ 键盘 → ⑤ 截图 → ⑥ 窗口截图 → ⑦ 圆形轨迹 → ⑧ 模板找图 → ⑨ 最近操作日志**（技能库从末尾移到第 2 位）。

## 10.3 技能库：结构化步骤编辑（关键条件单元格）

在「完整配置文件（JSONC）」之外，增加**结构化步骤表格**（默认展示，JSONC 作为高级编辑）：

- 每步一行，列：`#`、`操作（action）`、**关键条件/参数单元格**（按 action 显示可编辑输入）、`等待(ms)`、`备注(note)`、`操作（上移/下移/删除）`。
- 关键参数单元格（可新增/编辑，保存时写回 steps）：
  | action | 可编辑关键字段 |
  |---|---|
  | `activate_window` | `window` 或 `hwnd`、`timeoutMs` |
  | `wait_image` / `find_image` | `template`（或 `templates` 多个，逗号分隔）、`threshold`、`timeoutMs`(wait_image)、`intervalMs`(wait_image)、`mode`(wait_image)、`window` |
  | `mouse_click` | `x`、`y`、`clicks`、`button`、`intervalMs` |
  | `mouse_circle` | `centerX`、`centerY`、`radius`、`periodMs`、`durationMs`、`direction`、`abortKey` |
  | `wait` | `durationMs` |
  | `mouse_drag` | `x1`、`y1`、`x2`、`y2`、`durationMs`、`steps`、`button` |
  | `key_press`/`key_down`/`key_up`/`hotkey` | `key`/`keys` |
  | `type_text` | `text`（长度提示） |
  | 其它 | 通用 `params` JSON 编辑器 |
- 每行都可编辑 `waitMs`（0–60000）、`note`（≤200）；行操作：上移/下移/删除；「新增步骤」插入默认 `wait` 步骤（可选常用模板）。
- **保存**：UI 用 `skill_get.skill` 的 title/description/game/tags/markdown + 表格编辑后的 steps 调 `skill_save`（结构化字段），后端归一化 + 生成中文注释 JSONC；成功后刷新表格与 JSONC 高级编辑器。
- 只读摘要保留：id/标题/游戏/步骤数/更新时间/备份数。
- 「高级」折叠区保留完整 JSONC 编辑与保存（content 模式），两种方式都可用。
- 用户示例语义：技能应表达「**等待『开始挖矿！』按钮出现 → 点击它**」，而不是必须先等「挖矿已完成」标题；为此：
  - 示例技能 `keep-on-mining-30s` 步骤改为：
    `activate_window → wait_image(templates=[next_round.png, start_mining.png], timeoutMs=20000) → mouse_click(按钮中心) → wait(2000) → mouse_circle(radius=360, durationMs=30000, abortKey=esc)`；
  - 等待时间缩短（按钮 20s；步骤后等待 2s），不再等 120s 的面板；
  - `wait_image` 支持 **`templates` 多模板**（1–8 个，任一出现即命中，`template` 与 `templates` 二选一），以兼容完成面板/准备界面两种「开始挖矿！」样式。

## 10.4 运行防卡死 + 超时前置校验

- **进度文件**：`<GAME_CONTROL_HOME>/logs/skill-progress.json`（原子写）。`skill_run` 开始/每步开始/每步结束/结束时更新：
  `{running, skillId, startedAt, elapsedMs, repeat, repeatsDone, step:{index,total,action,startedAt}, updatedAt}`；结束时 `running:false`。
- **进度接口**：Host `GET /api/game-control/skill-progress` 直接读该文件（不需要 Python worker，因此运行期间也可用）；
  返回 `{ok:true, progress:{...}|null}`。Python 也提供 `skill_progress` 动作（读文件）。
- **面板运行态**：技能运行期间轮询 `/api/game-control/skill-progress`（约 700ms），显示「**正在执行第 N/M 步 · action · 已等待 X 秒**」；
  运行期间**禁用技能库的保存/刷新/新建/删除/恢复/JSONC 保存**（以及其它会排队的操作按钮），结束后恢复并显示结果；
  运行超时/失败时显示 failedStep 与错误码。避免请求排队导致“保存中/运行中”卡死。
- **skill_run 超时前置校验**（dryRun 也执行）：
  - `timeoutMs` 范围 1000–600000（已有）；
  - 估算最坏执行时长 `estimatedDurationMs = Σ(step 最大耗时) + ΣwaitMs + 500ms×steps`，其中
    `wait→durationMs`、`mouse_circle→leadInMs+durationMs`、`wait_image→timeoutMs`、`find_image→8000`、其它→1000；
  - 若 `estimatedDurationMs > timeoutMs` → `INVALID_ARGUMENT`，hint 给出“至少需要 X ms，可增大 timeoutMs 或缩小 wait_image/mouse_circle 参数”；
  - 返回/ dryRun 结果附 `estimatedDurationMs`。
- 面板运行/干跑按钮文案与提示同步 Esc。

## 10.5 测试要求

`python/test_backend.py` 新增：
1. `mouse_circle`/`skill_run` 默认 abortKey 为 `"esc"`（dryRun 结果/参数表断言）。
2. `find_image`/`wait_image` 的 `abortKey` 参数：注入截图 + monkeypatch `key_is_down` 返回按下 → `aborted:true, abortReason:"abort-key"`（不产生真实输入）；`"none"` 不检查。
3. `wait_image` 多模板 `templates`：合成两张图，任一命中返回对应 `template` 路径；`template` 与 `templates` 同时给 → INVALID_ARGUMENT；数量 1–8 校验。
4. `skill_run` 透传 abortKey：含 `wait_image` 步骤的技能在注入按键后中止，返回 aborted:true。
5. 进度文件：运行 `wait(0)` 技能过程中/结束后 `skill-progress.json` 字段合法，最终 `running:false`；`skill_progress` 动作可读。
6. 超时前置校验：估算超 `timeoutMs` → INVALID_ARGUMENT（hint）；dryRun 返回 `estimatedDurationMs`；估算合法时通过。
7. Esc 键名解析（`esc`/`escape`）。

`test/acceptance.mjs` 新增：README/工具 schema/面板静态检查（Esc、技能库第二项、结构化步骤表格、进度路由、templates、前校验关键字）；
CLI 只读用例：默认 abortKey=esc；`templates` 参数错误；`skill_run dryRun` 返回 `estimatedDurationMs`；进度文件存在且 running=false；原 87 项不回归（预期约 97+）。

## 10.6 DoD（v0.5）

- [ ] 版本 `0.5.0`；`node --check`/`py_compile` 全绿；`test_backend.py`、`acceptance.mjs` 全 PASS；
- [ ] 面板技能库位于「后端状态」与「鼠标」之间；结构化步骤表格可改关键条件（含 templates/等待/备注）并保存为 JSONC；
- [ ] 技能运行期间面板显示「第 N/M 步 · action · 已等待 Xs」且保存/刷新禁用；不会出现请求排队卡死；
- [ ] 等待模板阶段按 Esc 可立即停止；`skill_run`/`mouse_circle` 默认 Esc；README/工具描述/示例技能同步；
- [ ] 示例技能改为「等『开始挖矿！』按钮 → 点击 → 圆形 30s」，等待缩短；用户已有 radius=360 保留；
- [ ] 最终报告：文件清单、测试结果、面板/文件样例、未做项。

---

# 11. v0.6（版本 0.6.0）：长循环（逐轮独立调用）+ 后端中断修复 + 总 deadline 优雅中止

## 11.1 背景问题（已实测）

`repeat=99` 的单次 `skill_run` 会撞上 Host 单请求超时（`timeoutMs+10s` ≈ 310s）被 SIGTERM 强杀：
日志无 `mouse_circle end`、`skill-progress.json` 停在 `running:true`、面板永久“运行中”、浏览器出现
`Failed to fetch`（连接中断）。本版本必须修掉，并支持**大量轮数、每轮独立调用**的循环。

## 11.2 长循环：Host 侧 LoopRunner（新增 Host 动作，不进 Python worker）

- 新增 Host 级动作 `loop_start` / `loop_status` / `loop_stop`，由 Host 进程直接处理（不需要 worker），
  经 `/api/game-control/exec` 与 `game_skill` 工具均可调用。
- `loop_start` 参数：
  | 参数 | 说明 |
  |---|---|
  | `skillId*` | 要循环执行的技能 id |
  | `rounds*` | 1–10000 |
  | `intervalMs?` | 0–60000，默认 500 |
  | `stopOnError?` | 默认 true：某轮失败/中止即停止 |
  | `abortKey?` | 默认 `"esc"`，透传给每轮 skill_run |
  | `roundTimeoutMs?` | 1000–600000；省略时先做一次 skill_run dryRun 取 `estimatedDurationMs`，取 `estimated+30000`（clamp） |
- 行为：先 dryRun 校验（失败直接返回错误）；立即返回
  `{jobId, running:true, skillId, rounds, intervalMs, perRoundTimeoutMs, estimatedDurationMs, startedAt}`；
  随后后台按轮执行：**每轮单独调用一次** `bridge.execute('skill_run', {id, repeat:1, timeoutMs:perRoundTimeoutMs, abortKey, stopOnError})`，
  记录每轮结果（success/aborted/error/durationMs）后等 `intervalMs` 再进入下一轮；`stopRequested` 或
  `stopOnError` 命中失败/中止时停止；结束后写 `running:false, completedRounds, failedRounds, finishedAt`。
- `loop_status` 返回：
  `{running, jobId, skillId, rounds, completedRounds, failedRounds, currentRound, stopRequested, startedAt,
   finishedAt?, perRoundTimeoutMs, estimatedDurationMs, lastError?, results:[最近 20 轮 {round,ok,aborted,success,durationMs,errorCode?,message?}]}`
- `loop_stop` 参数 `{immediate?}`（默认 false）：置 `stopRequested`，当前轮结束后停止；
  `immediate:true` 时终止 worker 使当前轮立即失败并停止循环（当前轮结果 error `BACKEND_UNAVAILABLE`）。
- 同一时间只允许一个循环，重复启动 → `LOOP_ALREADY_RUNNING`；插件卸载/进程退出时停止循环并清理定时器。
- 状态持久化：`<GAME_CONTROL_HOME>/logs/loop-state.json`（原子写，每次状态变化）与
  `<GAME_CONTROL_HOME>/logs/loop.jsonl`（每轮一行，便于事后核对）。
- **每轮独立**：每轮是独立的 skill_run 调用与独立进度更新，单轮仍受自身 timeout 约束；大量轮数不再受单请求 310s 限制。

## 11.3 Python：总 deadline 优雅中止（不再被 Host 杀）

- `skill_run` 开始设置 `runtime.skill_deadline = monotonic() + timeoutMs/1000`，结束/异常都清除。
- 步骤前后检查 deadline；到点 → 停止执行，返回 `{success:false, aborted:true, abortReason:"timeout"}`，
  进度文件写 `running:false, aborted:true, abortReason:"timeout", finishedAt`。
- `mouse_circle`：有效时长 = `min(durationMs, 剩余时间)`；循环内每帧检查 deadline，到点提前停止并
  `aborted:true, abortReason:"timeout"`；仍保证释放鼠标键。
- `wait`：睡眠截断到剩余时间，到点后 skill_run 按超时中止。
- `wait_image`：有效 `timeoutMs = min(timeoutMs, 剩余时间)`，轮询内检查 deadline，到点返回
  `{found:false, aborted:true, abortReason:"timeout", attempts, elapsedMs}`。
- `find_image`：扫描前后检查 deadline，到点返回 aborted。
- skill_run 收到步骤 `aborted:true` 时整体中止（reason 透传），不再执行后续步骤/重复轮。

## 11.4 Host 超时余量

- `timeoutFor('skill_run')` 改为 `min(620000, (timeoutMs ?? 300000) + 120000)`（原 +10000），
  确保 Python 的总 deadline 与单步收尾先于 Host 强杀生效。
- `loop_*` 动作：`loop_start` 15000；`loop_status` 15000；`loop_stop` 30000（immediate 需等 worker 终止）。

## 11.5 进度 stale 与连接中断（Failed to fetch）修复

- Python worker 退出（正常/超时/被杀）时，Host 立即把 `skill-progress.json` 标记为
  `running:false, stale:true, abortReason:"backend-stopped", finishedAt`（保留原 skillId/step），原子写。
- `GET /api/game-control/skill-progress` 返回 `{ok:true, progress, bridge:{running,lastError}}`；
  若 `progress.running===true` 但 `bridge.running===false` → 返回 `running:false, stale:true` 并顺手写回文件。
- 面板：
  - 进度轮询请求失败（fetch 网络错误/超时）→ 停止轮询，显示「后端连接中断（可能因超时被杀/被停止）」，
    提供「重置状态」按钮（清除本地 running、恢复按钮），不再显示原始 `TypeError: Failed to fetch`；
  - 收到 `stale:true` 或 `bridge.running===false` 而本地仍在跑 → 同样停止轮询 + 提示 + 重置；
  - 面板所有 fetch 错误统一为中文可读提示。

## 11.6 面板循环 UI

- 技能运行区增加：`轮数 rounds`（默认 10，1–10000）、`轮间隔 intervalMs`（默认 500）、按钮
  「运行一次」「启动循环」「停止循环」（停止带「立即停止」复选）。
- 循环运行期间显示：「循环 第 N/总轮数 · 已完成 X · 失败 Y · 当前轮已等待 Zs」，数据来自
  `/exec {action:'loop_status'}` 轮询（700ms）；单次运行进度继续用 `/skill-progress`。
- 循环运行期间禁用保存/刷新/新建/删除/恢复/单次运行；结束后显示汇总（completed/failed/lastError）。
- 保留 dryRun 干跑；无 setInterval，定时器在停止/卸载时清理。

## 11.7 测试与验收

- `python/test_backend.py` 新增：
  1. skill_run deadline：`timeoutMs=1000` + `wait(5000)` → `aborted:true, abortReason:"timeout"`，耗时 <3s；
  2. 进度文件结束为 `running:false, abortReason:"timeout"`（超时场景）；
  3. `wait_image` deadline（timeoutMs 大但 skill 总 timeout 小）→ 返回 aborted timeout；
  4. 无 deadline 时行为不变（回归）。
- `test/acceptance.mjs` 新增静态/只读检查：index.js 含 `loop_start/loop_status/loop_stop`、`loop-state.json`、
  `stale`、`skill_run` 超时余量 120000、`/skill-progress` 返回 bridge；client.js 含循环 UI/重置/连接中断文案；
  README 含 v0.6 章节；原 97 项不回归。
- 手工/实机验由 Lead：创建 `wait` 步骤临时技能 → `loop_start rounds=5 intervalMs=200` → `loop_status` 依次更新、
  最终 `running:false, completedRounds=5`；`loop_stop immediate` 验证 worker 终止 + progress stale；
  不产生真实鼠标/键盘输入。

## 11.8 DoD（v0.6）

- [ ] 版本 `0.6.0`；`node --check`/`py_compile` 全绿；test_backend/acceptance 全 PASS；
- [ ] `loop_start` 可大量轮数逐轮调用，状态/每轮结果可查询，可停止；单轮独立的 skill_run 调用有独立结果；
- [ ] 长循环不再因 Host 310s 被强杀；Python deadline 优雅中止并写正确进度；
- [ ] worker 退出/连接中断后面板不再永久“运行中/保存中”，提示「后端连接中断」并可重置；
- [ ] README/SPEC/工具描述同步；示例技能不变（可继续单轮/循环）；
- [ ] 最终报告：文件清单、测试结果、loop 实测截图/日志、剩余风险。

---

# 12. v0.6.1：融合「重复次数 repeat」与「轮数 rounds」为单一执行轮数

## 12.1 问题

v0.6 面板里同时存在 `skill_run.repeat`（重复次数）与 `loop_start.rounds`（轮数）两个控制，二者本质都是
“同一技能执行 N 次”，逻辑重复、容易混淆（且只有 loop 的逐轮独立调用才不会撞单请求超时）。

## 12.2 融合后的唯一语义

- **只有一个控制：`轮数`（rounds）**，范围 1–10000：
  - `rounds = 1` → 单次运行（等价于原来的“运行一次 / repeat=1”）；
  - `rounds > 1` → 循环执行，每轮仍是一次独立 `skill_run` 调用（Host LoopRunner）。
- 面板：删除「重复次数」输入与「运行一次 / 启动循环」两个按钮，改为：
  - `轮数`（默认 1）、`轮间隔 intervalMs`（默认 500，仅轮数>1 时生效）、`立即停止` 复选；
  - 唯一按钮「运行」→ 统一调用 `loop_start`（rounds 传输入值，rounds=1 也走 LoopRunner），
    「停止」→ `loop_stop`；
  - 进度统一用 `loop_status` 轮询：「第 N/M 轮 · 已完成 X · 失败 Y · 当前轮已等待 Zs」+ 当前步骤（来自
    `loop_status.currentStep`，Host 从 skill-progress 合并）；单轮的步骤进度不再单独维护一套按钮。
  - dryRun 干跑保留（单次、不计数）。
- Agent 工具：**`loop_start` 是统一的 N 轮入口**（rounds=1 即单次）；`skill_run` 保留为“只执行 1 轮的底层原语”
  （内部由 LoopRunner 每轮调用），工具 schema **移除 `repeat` 参数**，文档说明“多轮请用 loop_start.rounds”。
  Python 后端仍兼容 `skill_run.repeat`（仅内部/CLI 调试用，文档标注 deprecated），面板与工具不再暴露。
- `loop_status` 增加 `currentStep:{index,total,action,elapsedMs}`（运行中且 skill-progress 属于同一技能时），
  面板据此显示当前步骤；worker 退出时仍由 stale 机制收口。
- 版本 `0.6.1`；README/验收更新为单一轮数语义（删除重复次数相关文案与断言；保留 CLI 层 skill_run.repeat 兼容说明）。

## 12.3 测试

- acceptance：更新静态断言——client.js 不再出现「重复次数 / 运行一次 / 启动循环」双控制，`轮数`/`运行`/`停止`/
  「立即停止」存在且统一走 loop_start；index.js game_skill schema 无 `repeat`（或标注 deprecated）；
  `loop_status.currentStep` 存在；README 有「轮数 1=单次、>1 循环」说明；原 105 项相应调整且不回归。
- `python test_backend.py` 不变（19 类）。
- 手工：面板 轮数=1 运行一次 → loop_status 完成 1 轮；轮数=5 → 5 轮；停止/立即停止同 v0.6。

---

# 13. v0.6.2：模板等待超时导致 `$btn.match.center.x` NoneType 的修复

## 13.1 问题（实测报错）

循环运行中偶发：`第 3 步 mouse_click: [SKILL_VAR_UNRESOLVED] 无法继续解析变量 '$btn.match.center.x'（当前值类型 NoneType）`。
根因：`wait_image` 在 20s 内没找到「开始挖矿！」按钮，按旧语义超时返回 `ok:true, found:false,
match:null, timedOut:true`，skill_run 没有把它当失败继续执行下一步 `mouse_click`，展开变量时才发现
`$btn.match` 是 `null`，报出与真实原因无关的变量错误，并导致整个循环意外停止（7 成功 + 1 失败）。

## 13.2 修复

### 13.2.1 步骤级 `onMissing`（默认 fail）

- 技能步骤新增可选字段 `onMissing`（仅 `find_image` / `wait_image` 步骤有效）：`"fail"`（默认）|`"continue"`。
- 保存/归一化时：`find_image`/`wait_image` 步骤自动写显式 `"onMissing": "fail"`；生成注释追加「找不到时：失败/继续」。
- `skill_run` 执行到 `wait_image`/`find_image` 且 **未找到模板** 时：
  - `onMissing="fail"`（默认）→ 该步立即记为失败，**不再执行后续步骤**（受 `stopOnError`/`continueOnError` 控制），
    `failedStep.code = "WAIT_IMAGE_TIMEOUT"`（wait_image）或 `"TEMPLATE_NOT_FOUND"`（find_image），
    message 写明“等待模板超时（X ms）：模板 A, B；请确认游戏画面/提高 timeoutMs/补充模板”。
  - `onMissing="continue"` → 继续后续步骤（保存的 match 为 null，后续变量引用会报变量错误——这是显式选择）。
- **失败证据截图**：模板失败时自动截图（优先失败步骤的 searchRegion，其次主屏）保存到
  `<GAME_CONTROL_HOME>/logs/skill-fail-<yyyymmdd_hhmmss>-step<idx>.png`，路径写入
  `failedStep.screenshotPath`，供人工排查为什么没匹配到；截图失败不影响错误返回。
- **变量错误信息增强**：解析到 `None`/空值时给出明确 hint，如
  「`$btn.match.center.x` 中 `match` 为空：上一步 wait_image 可能未找到模板（见 failedStep.screenshotPath）」。
- 示例技能 `keep-on-mining-30s`：`wait_image` 的 `timeoutMs` 由 20000 调为 30000，并显式 `onMissing:"fail"`。

### 13.2.2 循环失败策略可配置

- 面板循环设置新增「失败后继续」复选（默认不勾=遇失败停止）：对应 `loop_start.stopOnError=false`，
  这样偶发一轮模板超时不会中断整个长循环；失败的轮次仍记录在 results/lastError。
- `loop_status.results` 每轮条目追加 `failedStep`（index/action/code/message/screenshotPath）与
  `aborted/abortReason`；面板每轮结果/最后错误区域显示失败步骤、错误码与截图路径。
- Host LoopRunner 把每轮 result.failedStep 拷进 entry，`lastError` 保持人类可读。

## 13.3 测试

- `python/test_backend.py` 新增：
  1. `wait_image` 步骤超时默认 fail：技能 = [wait_image(不存在模板, timeoutMs=300), mouse_click(引用该结果)]
     → 失败停在 **第 1 步**，`failedStep.code="WAIT_IMAGE_TIMEOUT"`，不出现变量错误；`screenshotPath` 存在且为 png；
  2. `onMissing:"continue"` 时继续到后续步骤并出现 SKILL_VAR_UNRESOLVED（带 None/模板未找到 hint）；
  3. `onMissing` 非法值 → INVALID_SKILL；
  4. 归一化后 find/wait_image 步骤显式含 `onMissing:"fail"`，注释含「找不到时」。
- acceptance 新增静态/只读检查：README v0.6.2 章节；skills.py 含 onMissing；game_control 含 WAIT_IMAGE_TIMEOUT 与
  screenshotPath；client 含「失败后继续」；示例技能 JSONC 含 onMissing/timeoutMs 30000（profile 内文件只读检查可 SKIP）。
- 手工：循环中人为制造一次模板超时（或临时改模板名），验证循环按「失败后继续」继续、failedStep/截图可见。

## 13.4 DoD（v0.6.2）

- [ ] 版本 `0.6.2`；acceptance/test_backend 全 PASS；`node --check`/`py_compile` 全绿；
- [ ] 模板超时先失败在该步（WAIT_IMAGE_TIMEOUT）并附失败截图，不再报 NoneType 变量错误；
- [ ] 面板「失败后继续」可用；loop results 显示 failedStep/截图；
- [ ] 示例技能更新（onMissing/timeoutMs）；README/SPEC/测试同步；目录清理。






