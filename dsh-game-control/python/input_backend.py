"""非 Windows 平台的输入后端：``pynput`` 优先，``xdotool`` / ``osascript`` 兜底。

SPEC 2.5 要求 macOS/Linux 优先 pynput，没有则用平台命令兜底；缺依赖时返回
``MISSING_DEPENDENCY`` 且 hint 给出 ``pip install pynput``。本模块被 game_control.py
在非 Windows 平台导入，接口与 win_native.py 保持同构：

    backend_name() / available() / setup() / get_geometry() / get_pointer()
    move_to / move_rel / click / button_down / button_up / scroll / drag
    key_down / key_up / key_press / hotkey / type_text

说明：本文件在 Windows 上不会被加载，也没有条件在真机验证 macOS/Linux；
实现以“尽力可用 + 错误信息明确”为目标，未验证项已在交付汇报中标注。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 规范键名 → pynput 特殊键属性名
_PYNPUT_KEY_ATTR = {
    "enter": "enter",
    "esc": "esc",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "cmd",
    "meta": "cmd",
    "caps_lock": "caps_lock",
    "num_lock": "num_lock",
    "scroll_lock": "scroll_lock",
    "print_screen": "print_screen",
    "pause": "pause",
    "menu": "menu",
}
for _i in range(1, 25):
    _PYNPUT_KEY_ATTR["f%d" % _i] = "f%d" % _i

# 规范键名 → xdotool keysym
_XDOTOOL_KEYSYM = {
    "enter": "Return",
    "esc": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "super",
    "meta": "super",
    "caps_lock": "Caps_Lock",
    "num_lock": "Num_Lock",
    "scroll_lock": "Scroll_Lock",
    "print_screen": "Print",
    "pause": "Pause",
    "menu": "Menu",
}
for _i in range(1, 25):
    _XDOTOOL_KEYSYM["f%d" % _i] = "F%d" % _i

# 规范键名 → macOS AppleScript key code（仅 ANSI 键盘常用键）
_APPLE_KEYCODE = {
    "enter": 36,
    "esc": 53,
    "tab": 48,
    "space": 49,
    "backspace": 51,
    "delete": 117,
    "insert": 114,
    "home": 115,
    "end": 119,
    "pageup": 116,
    "pagedown": 121,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}
_APPLE_MODIFIER = {
    "ctrl": "control down",
    "control": "control down",
    "alt": "option down",
    "shift": "shift down",
    "win": "command down",
    "meta": "command down",
}


class BackendError(Exception):
    """输入后端错误；``code``/``hint`` 直接进入响应信封。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


def _pynput_hint() -> str:
    return "pip install pynput（macOS 还需在 系统设置→隐私与安全性→辅助功能 中授权；Linux 需 X11 会话）"


def _is_wayland() -> bool:
    return (
        IS_LINUX
        and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    ) or bool(os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------------------
# 后端探测
# ---------------------------------------------------------------------------

_state = {"detected": None, "backend": None, "detail": ""}


def _detect() -> str | None:
    """探测可用后端，结果缓存。返回 ``'pynput'|'xdotool'|'osascript'|None``。"""
    if _state["detected"]:
        return _state["backend"]
    _state["detected"] = True
    # 1) pynput（需要能 import 且能取到 Controller，X/Windows/mac 权限不足时构造会抛异常）
    try:
        from pynput.keyboard import Controller as KeyboardController  # noqa: F401
        from pynput.mouse import Controller as MouseController  # noqa: F401

        MouseController()
        KeyboardController()
        _state["backend"] = "pynput"
        _state["detail"] = "pynput Controller 可用"
        return _state["backend"]
    except Exception as exc:  # noqa: BLE001 - 缺库/无显示/权限不足都走兜底
        _state["detail"] = "pynput 不可用: %s" % exc
    # 2) Linux：xdotool（X11）
    if IS_LINUX and shutil.which("xdotool"):
        _state["backend"] = "xdotool"
        _state["detail"] = "xdotool 可用"
        return _state["backend"]
    # 3) macOS：osascript（System Events）
    if IS_MAC and shutil.which("osascript"):
        _state["backend"] = "osascript"
        _state["detail"] = "osascript 可用（基础动作）"
        return _state["backend"]
    _state["backend"] = None
    return _state["backend"]


def backend_name() -> str:
    """响应信封里的 backend 字段。"""
    return _detect() or "unavailable"


def available() -> bool:
    return _detect() is not None


def setup() -> dict:
    """非 Windows 无需 DPI 特殊处理；返回与 win_native 同构的状态。"""
    return {"aware": False, "mode": "none"}


def get_dpi_state() -> dict:
    return {"aware": False, "mode": "none"}


def _require() -> str:
    """确保存在可用后端，否则抛 MISSING_DEPENDENCY / UNSUPPORTED_PLATFORM。"""
    backend = _detect()
    if backend:
        return backend
    if _is_wayland():
        raise BackendError(
            "UNSUPPORTED_PLATFORM",
            "检测到 Wayland 会话：pynput / xdotool 均依赖 X11，无法注入输入",
            "切换到 X11 会话，或安装 ydotool 后改用其它后端；截图可用 grim",
        )
    raise BackendError(
        "MISSING_DEPENDENCY",
        "没有可用的键鼠后端（pynput 未安装，且没有 xdotool/osascript）: %s"
        % (_state["detail"] or "未知原因"),
        _pynput_hint(),
    )


# ---------------------------------------------------------------------------
# 平台命令执行辅助
# ---------------------------------------------------------------------------


def _run(args: list, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """执行平台命令；超时/缺失/权限错误转成明确错误码。"""
    try:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackendError(
            "MISSING_DEPENDENCY",
            "找不到命令: %s" % args[0],
            "安装对应工具（Linux: apt install xdotool；macOS 使用 pynput）",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError("BACKEND_ERROR", "命令执行超时: %s" % " ".join(args)) from exc
    except PermissionError as exc:
        raise BackendError(
            "PERMISSION_DENIED",
            "没有执行命令的权限: %s" % args[0],
            "检查系统辅助功能/输入监控授权",
        ) from exc
    except OSError as exc:
        raise BackendError("BACKEND_ERROR", "命令执行失败: %s (%s)" % (args[0], exc)) from exc


def _run_checked(args: list, timeout: float = 10.0) -> str:
    result = _run(args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()[:300]
        raise BackendError(
            "BACKEND_ERROR",
            "%s 退出码 %d: %s" % (args[0], result.returncode, detail or "无输出"),
            "macOS 检查 辅助功能/输入监控 授权；Linux 检查 X11 会话",
        )
    return (result.stdout or b"").decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 键名解析
# ---------------------------------------------------------------------------


def _validate_key_char(ch: str, backend: str) -> None:
    if len(ch) != 1:
        raise BackendError("INVALID_KEY", "非法的单字符键: %r" % ch)


def _pynput_key(name: str):
    """规范键名 → pynput 键对象。"""
    from pynput.keyboard import Key, KeyCode

    if len(name) == 1:
        return KeyCode.from_char(name)
    attr = _PYNPUT_KEY_ATTR.get(name.lower())
    if attr is None:
        raise BackendError("INVALID_KEY", "pynput 后端不支持键名: %s" % name)
    key = getattr(Key, attr, None)
    if key is None:
        raise BackendError(
            "INVALID_KEY",
            "pynput 版本不支持键名: %s" % name,
            "升级 pynput：pip install -U pynput",
        )
    return key


def _xdotool_key(name: str) -> str:
    if len(name) == 1:
        return name
    keysym = _XDOTOOL_KEYSYM.get(name.lower())
    if keysym is None:
        raise BackendError("INVALID_KEY", "xdotool 后端不支持键名: %s" % name)
    return keysym


def _apple_key(name: str):
    """返回 ("keycode", int) 或 ("char", str)。"""
    if len(name) == 1:
        return "char", name
    code = _APPLE_KEYCODE.get(name.lower())
    if code is None:
        raise BackendError(
            "INVALID_KEY",
            "osascript 后端不支持键名: %s（仅支持常用键与 F1-F12）" % name,
            "安装 pynput 获得完整键名支持：pip install pynput",
        )
    return "keycode", code


def _apple_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# pynput 实现
# ---------------------------------------------------------------------------


def _pynput_mouse():
    from pynput.mouse import Controller

    return Controller()


def _pynput_keyboard():
    from pynput.keyboard import Controller

    return Controller()


def _pynput_button(button: str):
    from pynput.mouse import Button

    mapping = {"left": "left", "right": "right", "middle": "middle", "x1": "x1", "x2": "x2"}
    attr = mapping.get(button)
    if attr is None:
        raise BackendError("INVALID_ARGUMENT", "未知鼠标按键: %s" % button)
    value = getattr(Button, attr, None)
    if value is None:
        raise BackendError(
            "BACKEND_ERROR",
            "当前 pynput 版本不支持鼠标按键: %s" % button,
            "升级 pynput：pip install -U pynput",
        )
    return value


def _pynput_move_to(x: int, y: int) -> None:
    _pynput_mouse().position = (int(x), int(y))


def _pynput_move_rel(dx: int, dy: int) -> None:
    _pynput_mouse().move(int(dx), int(dy))


def _pynput_click(button: str, clicks: int, interval_ms: int) -> None:
    mouse = _pynput_mouse()
    btn = _pynput_button(button)
    for i in range(max(1, int(clicks))):
        mouse.click(btn, 1)
        if i + 1 < int(clicks) and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)


def _pynput_button_down(button: str) -> None:
    _pynput_mouse().press(_pynput_button(button))


def _pynput_button_up(button: str) -> None:
    _pynput_mouse().release(_pynput_button(button))


def _pynput_scroll(amount: int, horizontal: bool) -> None:
    mouse = _pynput_mouse()
    if horizontal:
        # SPEC：正数 = 左；pynput dx 正数 = 右
        mouse.scroll(-int(amount), 0)
    else:
        mouse.scroll(0, int(amount))


def _pynput_drag(x1, y1, x2, y2, button, duration_ms, steps) -> None:
    steps = max(2, int(steps))
    mouse = _pynput_mouse()
    btn = _pynput_button(button)
    mouse.position = (int(x1), int(y1))
    mouse.press(btn)
    started = time.perf_counter()
    try:
        for i in range(1, steps + 1):
            t = i / steps
            mouse.position = (
                round(x1 + (x2 - x1) * t),
                round(y1 + (y2 - y1) * t),
            )
            due = started + max(0, int(duration_ms)) / 1000.0 * t
            remain = due - time.perf_counter()
            if remain > 0:
                time.sleep(remain)
    finally:
        mouse.release(btn)


def _pynput_key_down(name: str) -> None:
    _pynput_keyboard().press(_pynput_key(name))


def _pynput_key_up(name: str) -> None:
    _pynput_keyboard().release(_pynput_key(name))


def _pynput_key_press(name: str, presses: int, interval_ms: int) -> None:
    keyboard = _pynput_keyboard()
    key = _pynput_key(name)
    count = max(1, int(presses))
    for i in range(count):
        keyboard.press(key)
        keyboard.release(key)
        if i + 1 < count and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)


def _pynput_hotkey(names: list) -> None:
    keyboard = _pynput_keyboard()
    keys = [_pynput_key(name) for name in names]
    pressed = []
    try:
        for key in keys:
            keyboard.press(key)
            pressed.append(key)
    finally:
        for key in reversed(pressed):
            try:
                keyboard.release(key)
            except Exception:  # noqa: BLE001 - 释放阶段尽力而为
                pass


def _pynput_type_text(text: str, interval_ms: int) -> int:
    from pynput.keyboard import Key, KeyCode

    keyboard = _pynput_keyboard()
    delay = max(0, int(interval_ms)) / 1000.0
    chars = list(text)
    for index, ch in enumerate(chars):
        if ch == "\r":
            continue
        if ch == "\n":
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)
        elif ch == "\t":
            keyboard.press(Key.tab)
            keyboard.release(Key.tab)
        else:
            key = KeyCode.from_char(ch)
            keyboard.press(key)
            keyboard.release(key)
        if delay and index + 1 < len(chars):
            time.sleep(delay)
    return len(chars)


# ---------------------------------------------------------------------------
# xdotool 实现（Linux X11 兜底）
# ---------------------------------------------------------------------------


def _xdo(*args: str, timeout: float = 10.0) -> str:
    return _run_checked(["xdotool", *args], timeout=timeout)


def _xdotool_move_to(x: int, y: int) -> None:
    _xdo("mousemove", "--sync", str(int(x)), str(int(y)))


def _xdotool_move_rel(dx: int, dy: int) -> None:
    _xdo("mousemove_relative", "--", str(int(dx)), str(int(dy)))


def _xdotool_button_number(button: str) -> int:
    mapping = {"left": 1, "middle": 2, "right": 3, "x1": 8, "x2": 9}
    number = mapping.get(button)
    if number is None:
        raise BackendError("INVALID_ARGUMENT", "未知鼠标按键: %s" % button)
    return number


def _xdotool_click(button: str, clicks: int, interval_ms: int) -> None:
    number = _xdotool_button_number(button)
    _xdo(
        "click",
        "--repeat",
        str(max(1, int(clicks))),
        "--delay",
        str(max(0, int(interval_ms))),
        str(number),
    )


def _xdotool_button_down(button: str) -> None:
    _xdo("mousedown", str(_xdotool_button_number(button)))


def _xdotool_button_up(button: str) -> None:
    _xdo("mouseup", str(_xdotool_button_number(button)))


def _xdotool_scroll(amount: int, horizontal: bool) -> None:
    steps = abs(int(amount))
    if steps == 0:
        return
    if horizontal:
        button = 6 if int(amount) > 0 else 7  # 6=左 7=右
    else:
        button = 4 if int(amount) > 0 else 5  # 4=上 5=下
    _xdo("click", "--repeat", str(steps), "--delay", "12", str(button))


def _xdotool_drag(x1, y1, x2, y2, button, duration_ms, steps) -> None:
    steps = max(2, int(steps))
    delay = max(1, int(int(duration_ms) / steps))
    _xdotool_move_to(x1, y1)
    _xdotool_button_down(button)
    try:
        for i in range(1, steps + 1):
            t = i / steps
            _xdotool_move_to(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t))
            time.sleep(delay / 1000.0)
    finally:
        _xdotool_button_up(button)


def _xdotool_key_down(name: str) -> None:
    _xdo("keydown", _xdotool_key(name))


def _xdotool_key_up(name: str) -> None:
    _xdo("keyup", _xdotool_key(name))


def _xdotool_key_press(name: str, presses: int, interval_ms: int) -> None:
    _xdo(
        "key",
        "--clearmodifiers",
        "--repeat",
        str(max(1, int(presses))),
        "--delay",
        str(max(0, int(interval_ms))),
        _xdotool_key(name),
    )


def _xdotool_hotkey(names: list) -> None:
    combo = "+".join(_xdotool_key(name) for name in names)
    _xdo("key", "--clearmodifiers", combo)


def _xdotool_type_text(text: str, interval_ms: int) -> int:
    delay = max(0, int(interval_ms))
    for index, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if index:
            _xdo("key", "--clearmodifiers", "Return")
        if line:
            _xdo("type", "--delay", str(delay), "--", line)
    return len(text)


# ---------------------------------------------------------------------------
# osascript 实现（macOS 兜底，基础动作）
# ---------------------------------------------------------------------------


def _osascript(script: str, timeout: float = 10.0) -> str:
    return _run_checked(["osascript", "-e", script], timeout=timeout)


def _osascript_move_to(x: int, y: int) -> None:
    # System Events 没有直接移动鼠标的 AppleScript 接口：用 cliclick（若安装）兜底。
    cliclick = shutil.which("cliclick")
    if cliclick:
        _run_checked([cliclick, "m:%d,%d" % (int(x), int(y))])
        return
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端无法移动鼠标（未安装 cliclick）",
        "pip install pynput 或 brew install cliclick",
    )


def _osascript_move_rel(dx: int, dy: int) -> None:
    cliclick = shutil.which("cliclick")
    if cliclick:
        _run_checked([cliclick, "m:%+d,%+d" % (int(dx), int(dy))])
        return
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端无法相对移动鼠标（未安装 cliclick）",
        "pip install pynput 或 brew install cliclick",
    )


def _osascript_click(button: str, clicks: int, interval_ms: int) -> None:
    if button not in ("left", "right"):
        raise BackendError(
            "MISSING_DEPENDENCY",
            "osascript 后端只支持左键/右键点击；%s 键需要 pynput/cliclick" % button,
            "pip install pynput",
        )
    cliclick = shutil.which("cliclick")
    if not cliclick:
        raise BackendError(
            "MISSING_DEPENDENCY",
            "osascript 后端无法在当前位置点击（未安装 cliclick）",
            "pip install pynput 或 brew install cliclick",
        )
    # cliclick：c:. 在当前位置单击；rc:. 右键单击；重复调用实现多击与间隔
    command = "c:." if button == "left" else "rc:."
    count = max(1, int(clicks))
    for i in range(count):
        _run_checked([cliclick, command])
        if i + 1 < count and int(interval_ms) > 0:
            time.sleep(int(interval_ms) / 1000.0)


def _osascript_button_down(button: str) -> None:
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端不支持鼠标按住（%s）" % button,
        "pip install pynput",
    )


def _osascript_button_up(button: str) -> None:
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端不支持鼠标释放（%s）" % button,
        "pip install pynput",
    )


def _osascript_scroll(amount: int, horizontal: bool) -> None:
    # AppleScript 没有可靠的滚轮接口，明确报错而不是静默失效
    direction = "上/左" if int(amount) > 0 else "下/右"
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端不支持滚轮（%s %d 格）" % (direction, abs(int(amount))),
        "pip install pynput 或 brew install cliclick",
    )


def _osascript_drag(x1, y1, x2, y2, button, duration_ms, steps) -> None:
    raise BackendError(
        "MISSING_DEPENDENCY",
        "osascript 后端不支持拖拽",
        "pip install pynput 或 brew install cliclick",
    )


def _osascript_key(name: str):
    kind, value = _apple_key(name)
    if kind == "char":
        return 'keystroke "%s"' % _apple_quote(value)
    return "key code %d" % value


def _osascript_key_down(name: str) -> None:
    kind, value = _apple_key(name)
    if kind != "char":
        # 没有“按住”语义；用一次 key down 近似不可靠，直接明确报错
        raise BackendError(
            "MISSING_DEPENDENCY",
            "osascript 后端不支持按住特殊键: %s" % name,
            "pip install pynput",
        )
    _osascript('tell application "System Events" to key down "%s"' % _apple_quote(value))


def _osascript_key_up(name: str) -> None:
    kind, value = _apple_key(name)
    if kind != "char":
        raise BackendError(
            "MISSING_DEPENDENCY",
            "osascript 后端不支持释放特殊键: %s" % name,
            "pip install pynput",
        )
    _osascript('tell application "System Events" to key up "%s"' % _apple_quote(value))


def _osascript_key_press(name: str, presses: int, interval_ms: int) -> None:
    count = max(1, int(presses))
    command = _osascript_key(name)
    for i in range(count):
        _osascript('tell application "System Events" to %s' % command)
        if i + 1 < count and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)


def _osascript_hotkey(names: list) -> None:
    modifiers = []
    final = None
    for name in names:
        lowered = name.lower()
        if lowered in _APPLE_MODIFIER:
            modifier = _APPLE_MODIFIER[lowered]
            if modifier not in modifiers:
                modifiers.append(modifier)
        else:
            if final is not None:
                raise BackendError(
                    "INVALID_ARGUMENT",
                    "osascript 后端组合键只允许一个非修饰键: %s" % names,
                )
            final = name
    if final is None:
        raise BackendError("INVALID_ARGUMENT", "组合键至少需要一个非修饰键: %s" % names)
    using = (" using {%s}" % ", ".join(modifiers)) if modifiers else ""
    _osascript('tell application "System Events" to %s%s' % (_osascript_key(final), using))


def _osascript_type_text(text: str, interval_ms: int) -> int:
    delay = max(0, int(interval_ms))
    for index, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if index:
            _osascript('tell application "System Events" to key code 36')
            if delay:
                time.sleep(delay / 1000.0)
        if line:
            _osascript('tell application "System Events" to keystroke "%s"' % _apple_quote(line))
            if delay:
                time.sleep(delay / 1000.0)
    return len(text)


# ---------------------------------------------------------------------------
# 统一分发
# ---------------------------------------------------------------------------


def _dispatch(pynput_fn, xdo_fn, osa_fn, *args) -> None:
    backend = _require()
    try:
        if backend == "pynput":
            pynput_fn(*args)
        elif backend == "xdotool":
            xdo_fn(*args)
        else:
            osa_fn(*args)
    except BackendError:
        raise
    except Exception as exc:  # noqa: BLE001 - pynput 的底层异常统一包装
        code = "PERMISSION_DENIED" if IS_MAC else "BACKEND_ERROR"
        hint = (
            "macOS：系统设置→隐私与安全性→辅助功能/输入监控 中授权终端或 DSH"
            if IS_MAC
            else "检查 X11 DISPLAY 与输入权限"
        )
        raise BackendError(code, "输入后端执行失败: %s" % exc, hint) from exc


def move_to(x: int, y: int) -> None:
    _dispatch(_pynput_move_to, _xdotool_move_to, _osascript_move_to, int(x), int(y))


def move_rel(dx: int, dy: int) -> None:
    _dispatch(_pynput_move_rel, _xdotool_move_rel, _osascript_move_rel, int(dx), int(dy))


def click(button: str = "left", clicks: int = 1, interval_ms: int = 60) -> None:
    _dispatch(_pynput_click, _xdotool_click, _osascript_click, button, int(clicks), int(interval_ms))


def button_down(button: str = "left") -> None:
    _dispatch(_pynput_button_down, _xdotool_button_down, _osascript_button_down, button)


def button_up(button: str = "left") -> None:
    _dispatch(_pynput_button_up, _xdotool_button_up, _osascript_button_up, button)


def scroll(amount: int, horizontal: bool = False) -> None:
    _dispatch(_pynput_scroll, _xdotool_scroll, _osascript_scroll, int(amount), bool(horizontal))


def drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    button: str = "left",
    duration_ms: int = 300,
    steps: int = 20,
) -> None:
    _dispatch(
        _pynput_drag,
        _xdotool_drag,
        _osascript_drag,
        int(x1),
        int(y1),
        int(x2),
        int(y2),
        button,
        int(duration_ms),
        int(steps),
    )


def key_down(name: str) -> None:
    _dispatch(_pynput_key_down, _xdotool_key_down, _osascript_key_down, name)


def key_up(name: str) -> None:
    _dispatch(_pynput_key_up, _xdotool_key_up, _osascript_key_up, name)


def key_press(name: str, presses: int = 1, interval_ms: int = 60) -> None:
    _dispatch(_pynput_key_press, _xdotool_key_press, _osascript_key_press, name, int(presses), int(interval_ms))


def hotkey(names: list) -> None:
    _dispatch(_pynput_hotkey, _xdotool_hotkey, _osascript_hotkey, list(names))


def type_text(text: str, interval_ms: int = 12) -> int:
    backend = _require()
    try:
        if backend == "pynput":
            return _pynput_type_text(text, int(interval_ms))
        if backend == "xdotool":
            return _xdotool_type_text(text, int(interval_ms))
        return _osascript_type_text(text, int(interval_ms))
    except BackendError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BackendError("BACKEND_ERROR", "文本输入失败: %s" % exc) from exc


# ---------------------------------------------------------------------------
# 屏幕几何（非 Windows 尽力而为）
# ---------------------------------------------------------------------------


def _screens_from_xrandr() -> tuple:
    """解析 ``xrandr --current``，返回 (virtualScreen, displays)。"""
    if not shutil.which("xrandr"):
        return None, None
    try:
        result = _run(["xrandr", "--current"], timeout=5)
    except BackendError:
        return None, None
    if result.returncode != 0:
        return None, None
    text = (result.stdout or b"").decode("utf-8", "replace")
    displays = []
    pattern = re.compile(
        r"^(\S+) connected (primary )?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", re.MULTILINE
    )
    for match in pattern.finditer(text):
        name, primary, width, height, x, y = match.groups()
        displays.append(
            {
                "name": name,
                "primary": bool(primary),
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
            }
        )
    if not displays:
        return None, None
    left = min(item["x"] for item in displays)
    top = min(item["y"] for item in displays)
    right = max(item["x"] + item["width"] for item in displays)
    bottom = max(item["y"] + item["height"] for item in displays)
    virtual = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    primary = next((item for item in displays if item["primary"]), displays[0])
    primary_screen = {
        "x": primary["x"],
        "y": primary["y"],
        "width": primary["width"],
        "height": primary["height"],
    }
    return virtual, (displays, primary_screen)


def _screens_from_pillow() -> tuple:
    """用 Pillow ImageGrab 取虚拟屏幕尺寸（单显示器退化）。"""
    try:
        from PIL import ImageGrab

        try:
            image = ImageGrab.grab(all_screens=True)
        except TypeError:
            image = ImageGrab.grab()
        width, height = image.size
        virtual = {"x": 0, "y": 0, "width": int(width), "height": int(height)}
        display = {
            "name": "screen-1",
            "primary": True,
            **virtual,
        }
        return virtual, ([display], dict(virtual))
    except Exception:  # noqa: BLE001 - 无显示/无 Pillow 都退化
        return None, None


def _detect_screens() -> tuple:
    if IS_LINUX:
        virtual, rest = _screens_from_xrandr()
        if virtual and rest:
            return virtual, rest
    virtual, rest = _screens_from_pillow()
    if virtual and rest:
        return virtual, rest
    empty = {"x": 0, "y": 0, "width": 0, "height": 0}
    return empty, ([], empty)


def get_pointer() -> dict:
    """当前鼠标位置；无后端时退化为 (0,0)。"""
    backend = _detect()
    try:
        if backend == "pynput":
            position = _pynput_mouse().position
            return {"x": int(position[0]), "y": int(position[1])}
        if backend == "xdotool":
            output = _xdo("getmouselocation", "--shell")
            values = dict(
                line.split("=", 1) for line in output.splitlines() if "=" in line
            )
            return {"x": int(values.get("X", "0")), "y": int(values.get("Y", "0"))}
    except Exception:  # noqa: BLE001 - 只读探测失败不应让 status 失败
        pass
    return {"x": 0, "y": 0}


def get_geometry() -> dict:
    """status 用几何信息；非 Windows 为尽力而为。"""
    virtual, (displays, primary) = _detect_screens()
    normalized = []
    ordered = sorted(displays, key=lambda item: (not item.get("primary"), item.get("name", "")))
    for index, item in enumerate(ordered, 1):
        normalized.append(
            {
                "index": index,
                "name": item.get("name", "screen-%d" % index),
                "id": index,
                "primary": bool(item.get("primary")),
                "x": int(item.get("x", 0)),
                "y": int(item.get("y", 0)),
                "width": int(item.get("width", 0)),
                "height": int(item.get("height", 0)),
            }
        )
    return {
        "virtualScreen": virtual,
        "primary": primary,
        "displays": normalized,
        "pointer": get_pointer(),
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def selftest() -> dict:
    """只读自检：后端探测 + 几何读取，不注入任何输入。"""
    checks = [
        {
            "name": "输入后端可用",
            "ok": available(),
            "detail": "%s（%s）" % (backend_name(), _state["detail"] or "无"),
        }
    ]
    try:
        geometry = get_geometry()
        screen = geometry["virtualScreen"]
        checks.append(
            {
                "name": "屏幕几何可读",
                "ok": screen["width"] > 0 and screen["height"] > 0,
                "detail": "%(x)d,%(y)d %(width)dx%(height)d" % screen,
            }
        )
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "屏幕几何可读", "ok": False, "detail": str(exc)})
    checks.append(
        {
            "name": "平台",
            "ok": True,
            "detail": "macOS" if IS_MAC else ("Linux" if IS_LINUX else sys.platform),
        }
    )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}
