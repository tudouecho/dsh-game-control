"""Windows 原生键鼠输入 / 屏幕截图实现（纯 ctypes，不依赖 pyautogui/pynput）。

本模块只在 Windows 上可用；在其它平台上可以 import（用于语法检查 / 打包扫描），
但调用任何公开函数都会抛出 ``NativeError(code="UNSUPPORTED_PLATFORM")``。

实现要点（对应 SPEC 2.5）：
- DPI 感知：进程启动时优先 ``SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)``，
  失败依次回退 ``SetProcessDpiAwareness(2)``、``SetProcessDPIAware()``；结果记录在
  :func:`get_dpi_state` 中，供 ``status.dpiAware`` 使用。
- 输入：``SendInput``。``INPUT`` / ``MOUSEINPUT`` / ``KEYBDINPUT`` 结构在 64 位下
  ULONG_PTR 用 ``ctypes.c_size_t``，保证结构体大小与 ABI 一致（x64 下 sizeof(INPUT)=40）。
  鼠标绝对定位使用 ``MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK``，按虚拟桌面
  归一化到 0..65535；键盘按 VK 注入；文本用 ``KEYEVENTF_UNICODE``。
- 截图：GDI 路线 ``GetDC(0)`` → ``CreateCompatibleDC`` → ``CreateCompatibleBitmap``
  → ``BitBlt``（带 ``CAPTUREBLT`` 以尽量捕获分层窗口）→ ``GetDIBits`` 取 BI_RGB
  32bpp BGRX 原始像素（行序自底向上，这里显式翻转）→ 去掉 X 通道得到 RGB bytes。
  返回 ``(rgb_bytes, width, height)``，由 :mod:`screenshot` 交给 Pillow 编码落盘。

坐标约定：全部是**物理像素**，原点为主屏左上角，多屏虚拟桌面允许负坐标。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# 鼠标事件标志
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# 键盘事件标志
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008  # 本实现按 SPEC 使用 VK，保留常量说明用

# GDI
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0

# GetSystemMetrics 索引
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

MONITORINFOF_PRIMARY = 0x0001

# v0.3：窗口激活
SW_RESTORE = 9
VK_MENU = 0x12
VK_LMENU = 0xA4

# 需要 KEYEVENTF_EXTENDEDKEY 的 VK（方向键 / 编辑键 / 小键盘锁等）
EXTENDED_VKS = frozenset(
    {
        0x21,  # VK_PRIOR / pageup
        0x22,  # VK_NEXT / pagedown
        0x23,  # VK_END
        0x24,  # VK_HOME
        0x25,  # VK_LEFT
        0x26,  # VK_UP
        0x27,  # VK_RIGHT
        0x28,  # VK_DOWN
        0x2D,  # VK_INSERT
        0x2E,  # VK_DELETE
        0x5D,  # VK_APPS / menu
        0x90,  # VK_NUMLOCK
    }
)

# 规范键名 → VK。键名的规范化 / 别名解析在 game_control.py 里完成，这里只认规范名。
NAMED_KEYS = {
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "meta": 0x5B,
    "caps_lock": 0x14,
    "num_lock": 0x90,
    "scroll_lock": 0x91,
    "print_screen": 0x2C,
    "pause": 0x13,
    "menu": 0x5D,
}
for _i in range(1, 25):
    NAMED_KEYS["f%d" % _i] = 0x6F + _i  # F1=0x70 ... F24=0x87

# 极少数键盘布局下 VkKeyScanW 不可用时，标点字符的 US 布局兜底 VK
_PUNCT_FALLBACK = {
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
}


class NativeError(Exception):
    """原生层错误。``code`` 对应 SPEC 2.4 的错误码，``hint`` 给调用方安装提示。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


# ---------------------------------------------------------------------------
# 结构体（注意 64 位下 ULONG_PTR = ctypes.c_size_t）
# ---------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUTUNION),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    ctypes.c_void_p,  # HMONITOR
    ctypes.c_void_p,  # HDC
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


# ---------------------------------------------------------------------------
# DLL 懒加载与函数原型
# ---------------------------------------------------------------------------

_AVAILABLE = sys.platform == "win32"
_LOAD_ERROR: str | None = None
_user32 = None
_gdi32 = None
_kernel32 = None
_loaded = False


def _load_libraries() -> None:
    """加载 user32/gdi32/kernel32 并声明函数原型；失败记录到 ``_LOAD_ERROR``。"""
    global _user32, _gdi32, _kernel32, _loaded, _LOAD_ERROR
    if _loaded:
        return
    _loaded = True
    if not _AVAILABLE:
        _LOAD_ERROR = "当前平台不是 Windows"
        return
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError as exc:  # pragma: no cover - 正常 Windows 不会发生
        _LOAD_ERROR = "加载系统 DLL 失败: %s" % exc
        _user32 = _gdi32 = _kernel32 = None
        return

    # -- user32 --
    _user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    _user32.SetProcessDPIAware.argtypes = []
    _user32.SetProcessDPIAware.restype = wintypes.BOOL
    _user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetDC.argtypes = [ctypes.c_void_p]
    _user32.GetDC.restype = ctypes.c_void_p
    _user32.GetWindowDC.argtypes = [ctypes.c_void_p]
    _user32.GetWindowDC.restype = ctypes.c_void_p
    _user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    _user32.PrintWindow.restype = wintypes.BOOL
    # v0.3：窗口客户区 / 激活 / 按键状态
    _user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [ctypes.c_void_p]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.EnumDisplayMonitors.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        MONITORENUMPROC,
        wintypes.LPARAM,
    ]
    _user32.EnumDisplayMonitors.restype = wintypes.BOOL
    _user32.GetMonitorInfoW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(MONITORINFOEXW),
    ]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    _user32.VkKeyScanW.restype = ctypes.c_short

    # -- gdi32 --
    _gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    _gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    _gdi32.CreateCompatibleBitmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    _gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    _gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _gdi32.SelectObject.restype = ctypes.c_void_p
    _gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    _gdi32.BitBlt.restype = wintypes.BOOL
    _gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    ]
    _gdi32.GetDIBits.restype = ctypes.c_int
    _gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _gdi32.DeleteObject.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    _gdi32.DeleteDC.restype = wintypes.BOOL


def _require() -> None:
    """确保原生库可用，否则抛 ``UNSUPPORTED_PLATFORM`` / ``BACKEND_ERROR``。"""
    _load_libraries()
    if _user32 is None or _gdi32 is None:
        raise NativeError(
            "UNSUPPORTED_PLATFORM",
            "Windows 原生后端不可用：%s" % (_LOAD_ERROR or "原因未知"),
            hint="本插件目前在 Windows 上功能完整；其它平台请使用 pynput/xdotool 后端",
        )


def backend_name() -> str:
    """返回后端标识，用于响应信封的 ``backend`` 字段。"""
    return "win32-sendinput"


def available() -> bool:
    """原生库是否可用（供 selftest 使用，不产生副作用）。"""
    _load_libraries()
    return _user32 is not None and _gdi32 is not None


def window_gdi_available() -> bool:
    """窗口级 GDI 截图入口是否可用（PrintWindow / GetWindowDC，供 selftest 使用）。"""
    _load_libraries()
    return (
        _user32 is not None
        and _gdi32 is not None
        and hasattr(_user32, "PrintWindow")
        and hasattr(_user32, "GetWindowDC")
    )


def last_error() -> str | None:
    return _LOAD_ERROR


# ---------------------------------------------------------------------------
# DPI 感知
# ---------------------------------------------------------------------------

_dpi_state = {"done": False, "aware": False, "mode": "none"}


def setup_dpi_awareness() -> dict:
    """设置进程 DPI 感知，重复调用幂等。

    返回 ``{"aware": bool, "mode": "per-monitor-v2"|"per-monitor"|"system"|"none"}``。
    """
    global _dpi_state
    if _dpi_state["done"]:
        return dict(_dpi_state)
    _dpi_state["done"] = True
    try:
        _load_libraries()
    except Exception:  # pragma: no cover - 极端环境
        return dict(_dpi_state)
    if _user32 is None:
        return dict(_dpi_state)
    # 1) Win10 1703+：PER_MONITOR_AWARE_V2（-4）；成功返回非 0
    try:
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _dpi_state.update(aware=True, mode="per-monitor-v2")
            return dict(_dpi_state)
    except (AttributeError, OSError, TypeError):
        pass
    # 2) Win8.1+：Shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE=2)
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:
            _dpi_state.update(aware=True, mode="per-monitor")
            return dict(_dpi_state)
    except (OSError, AttributeError):
        pass
    # 3) Vista+：系统级 DPI 感知
    try:
        if _user32.SetProcessDPIAware():
            _dpi_state.update(aware=True, mode="system")
    except (AttributeError, OSError):
        pass
    return dict(_dpi_state)


def get_dpi_state() -> dict:
    """返回 DPI 感知状态；若尚未初始化则先尝试初始化。"""
    if not _dpi_state["done"]:
        setup_dpi_awareness()
    return dict(_dpi_state)


# ---------------------------------------------------------------------------
# 屏幕几何
# ---------------------------------------------------------------------------


def get_virtual_screen() -> dict:
    """整个虚拟桌面的边界（物理像素，可能出现负坐标）。"""
    _require()
    return {
        "x": int(_user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        "y": int(_user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        "width": int(_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        "height": int(_user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    }


def get_primary_screen() -> dict:
    """主显示器边界；主屏原点规定为主屏左上角（Windows 上主屏恒为 0,0）。"""
    _require()
    return {
        "x": 0,
        "y": 0,
        "width": int(_user32.GetSystemMetrics(SM_CXSCREEN)),
        "height": int(_user32.GetSystemMetrics(SM_CYSCREEN)),
    }


def enumerate_displays() -> list:
    """枚举显示器，返回按“主屏优先、名称排序”排列的列表，``index`` 从 1 开始。

    每项：``{index, name, primary, x, y, width, height, workArea:{...}}``。
    """
    _require()
    found = []

    def _callback(hmonitor, hdc, lprect, lparam):  # noqa: ANN001 - ctypes 回调
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if _user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcMonitor
            work = info.rcWork
            found.append(
                {
                    "name": str(info.szDevice),
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    "x": int(rect.left),
                    "y": int(rect.top),
                    "width": int(rect.right - rect.left),
                    "height": int(rect.bottom - rect.top),
                    "workArea": {
                        "x": int(work.left),
                        "y": int(work.top),
                        "width": int(work.right - work.left),
                        "height": int(work.bottom - work.top),
                    },
                }
            )
        return True

    proc = MONITORENUMPROC(_callback)
    _user32.EnumDisplayMonitors(None, None, proc, 0)
    if not found:
        # 极端情况（无桌面会话）：退化为单显示器
        vs = get_virtual_screen()
        return [
            {
                "index": 1,
                "name": "\\\\.\\DISPLAY1",
                "primary": True,
                "x": vs["x"],
                "y": vs["y"],
                "width": vs["width"],
                "height": vs["height"],
                "workArea": dict(vs),
            }
        ]
    found.sort(key=lambda d: (not d["primary"], d["name"]))
    for i, disp in enumerate(found, 1):
        disp["index"] = i
    return found


def get_pointer() -> dict:
    """当前鼠标位置（物理像素）。"""
    _require()
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        raise NativeError("BACKEND_ERROR", "GetCursorPos 读取鼠标位置失败")
    return {"x": int(point.x), "y": int(point.y)}


def get_geometry() -> dict:
    """一次性取回 status 需要的几何信息。"""
    vs = get_virtual_screen()
    primary = get_primary_screen()
    return {
        "virtualScreen": vs,
        "primary": primary,
        "displays": enumerate_displays(),
        "pointer": get_pointer(),
    }


# ---------------------------------------------------------------------------
# SendInput 基础
# ---------------------------------------------------------------------------


def _send_inputs(events: list) -> None:
    """发送一批 INPUT 事件；返回条数不足即视为后端失败（SPEC 2.5）。"""
    _require()
    if not events:
        return
    array = (INPUT * len(events))(*events)
    sent = int(_user32.SendInput(len(events), array, ctypes.sizeof(INPUT)))
    if sent != len(events):
        err = ctypes.get_last_error()
        if not err:
            try:
                err = int(_kernel32.GetLastError())
            except Exception:  # pragma: no cover
                err = 0
        raise NativeError(
            "BACKEND_ERROR",
            "SendInput 仅注入 %d/%d 条输入事件（WinError=%d）" % (sent, len(events), err),
            hint="若目标程序以管理员权限运行，请用同等权限启动 DSH",
        )


def _make_mouse(dx: int, dy: int, mouse_data: int, flags: int) -> INPUT:
    event = INPUT()
    event.type = INPUT_MOUSE
    event.mi = MOUSEINPUT(
        dx=int(dx),
        dy=int(dy),
        mouseData=int(mouse_data) & 0xFFFFFFFF,
        dwFlags=int(flags),
        time=0,
        dwExtraInfo=0,
    )
    return event


def _make_key(vk: int, scan: int = 0, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(
        wVk=int(vk) & 0xFFFF,
        wScan=int(scan) & 0xFFFF,
        dwFlags=int(flags),
        time=0,
        dwExtraInfo=0,
    )
    return event


# ---------------------------------------------------------------------------
# 鼠标
# ---------------------------------------------------------------------------


def _normalized_abs(x: int, y: int) -> tuple:
    """把虚拟桌面物理坐标归一化到 0..65535（SendInput 绝对坐标要求）。"""
    vs = get_virtual_screen()
    width = max(vs["width"] - 1, 1)
    height = max(vs["height"] - 1, 1)
    nx = int(round((x - vs["x"]) * 65535.0 / width))
    ny = int(round((y - vs["y"]) * 65535.0 / height))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    return nx, ny


def move_to(x: int, y: int) -> None:
    """把鼠标移动到虚拟桌面坐标 (x, y)。"""
    nx, ny = _normalized_abs(int(x), int(y))
    _send_inputs(
        [
            _make_mouse(
                nx,
                ny,
                0,
                MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            )
        ]
    )


def move_rel(dx: int, dy: int) -> None:
    """相对移动鼠标。"""
    _send_inputs([_make_mouse(int(dx), int(dy), 0, MOUSEEVENTF_MOVE)])


_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    "x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}


def _button_flags(button: str) -> tuple:
    if button not in _BUTTON_FLAGS:
        raise NativeError("INVALID_ARGUMENT", "未知鼠标按键: %s" % button)
    return _BUTTON_FLAGS[button]


def button_down(button: str = "left") -> None:
    down, _up, data = _button_flags(button)
    _send_inputs([_make_mouse(0, 0, data, down)])


def button_up(button: str = "left") -> None:
    _down, up, data = _button_flags(button)
    _send_inputs([_make_mouse(0, 0, data, up)])


def click(button: str = "left", clicks: int = 1, interval_ms: int = 60) -> None:
    """点击若干次；每次点击 = down + up，两次之间等待 interval_ms。"""
    down, up, data = _button_flags(button)
    for i in range(max(1, int(clicks))):
        _send_inputs([_make_mouse(0, 0, data, down), _make_mouse(0, 0, data, up)])
        if i + 1 < int(clicks) and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)


def scroll(amount: int, horizontal: bool = False) -> None:
    """滚轮滚动。``amount`` 正数 = 上（垂直）/ 左（水平），每格 120 单位。

    注意 Windows 的 HWHEEL 正方向是“右”，而 SPEC 规定正数 = 左，所以水平时取反。
    """
    steps = int(amount)
    if steps == 0:
        return
    if horizontal:
        delta = -steps * 120
        flags = MOUSEEVENTF_HWHEEL
    else:
        delta = steps * 120
        flags = MOUSEEVENTF_WHEEL
    _send_inputs([_make_mouse(0, 0, delta, flags)])


def drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    button: str = "left",
    duration_ms: int = 300,
    steps: int = 20,
) -> None:
    """按下 → 平滑移动 → 抬起。无论中途是否异常都会尽量释放按键。"""
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    steps = max(2, int(steps))
    duration_ms = max(0, int(duration_ms))
    move_to(x1, y1)
    button_down(button)
    started = time.perf_counter()
    try:
        for i in range(1, steps + 1):
            t = i / steps
            move_to(
                round(x1 + (x2 - x1) * t),
                round(y1 + (y2 - y1) * t),
            )
            if duration_ms > 0:
                due = started + duration_ms / 1000.0 * t
                remain = due - time.perf_counter()
                if remain > 0:
                    time.sleep(remain)
    finally:
        button_up(button)


# ---------------------------------------------------------------------------
# 键盘
# ---------------------------------------------------------------------------


def vk_for_char(ch: str) -> tuple:
    """单字符 → (vk, 是否需要 shift)。

    ``VkKeyScanW`` 依赖当前键盘布局；中文/美式布局下标点、字母、数字都是非 shift 键。
    无法解析时返回 ``(None, False)``。
    """
    _require()
    if len(ch) != 1:
        return None, False
    try:
        scan = int(_user32.VkKeyScanW(ch))
    except (OSError, TypeError, ValueError):
        scan = -1
    if scan == -1:
        fallback = _PUNCT_FALLBACK.get(ch)
        return (fallback, False) if fallback else (None, False)
    vk = scan & 0xFF
    shift_state = (scan >> 8) & 0xFF
    return vk, bool(shift_state & 0x01)


def key_info(name: str) -> tuple:
    """规范键名 → (vk, 是否需要 shift)；未知键抛 ``INVALID_KEY``。"""
    key = str(name)
    if len(key) == 1:
        vk, need_shift = vk_for_char(key)
        if vk is None:
            raise NativeError("INVALID_KEY", "当前键盘布局无法识别字符键: %r" % key)
        return vk, need_shift
    vk = NAMED_KEYS.get(key.lower())
    if vk is None:
        raise NativeError("INVALID_KEY", "未知键名: %s" % key)
    return vk, False


def _key_event(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _make_key(vk, 0, flags)


def key_down(name: str) -> None:
    """按下命名键（按住不放）。需要 shift 的字符会先按下 shift。"""
    vk, need_shift = key_info(name)
    events = []
    if need_shift:
        events.append(_key_event(NAMED_KEYS["shift"], False))
    events.append(_key_event(vk, False))
    _send_inputs(events)


def key_up(name: str) -> None:
    """抬起命名键（需要 shift 的字符最后抬起 shift）。"""
    vk, need_shift = key_info(name)
    events = [_key_event(vk, True)]
    if need_shift:
        events.append(_key_event(NAMED_KEYS["shift"], True))
    _send_inputs(events)


def key_press(name: str, presses: int = 1, interval_ms: int = 60) -> None:
    """按键若干次（down+up），间隔 interval_ms。"""
    vk, need_shift = key_info(name)
    prefix = [_key_event(NAMED_KEYS["shift"], False)] if need_shift else []
    suffix = [_key_event(NAMED_KEYS["shift"], True)] if need_shift else []
    down = prefix + [_key_event(vk, False)]
    up = [_key_event(vk, True)] + suffix
    count = max(1, int(presses))
    for i in range(count):
        _send_inputs(down + up)
        if i + 1 < count and interval_ms > 0:
            time.sleep(interval_ms / 1000.0)


def hotkey(names: list) -> None:
    """组合键：按给定顺序按下，再反向抬起；异常时尽量释放已按下的键。"""
    resolved = [key_info(name)[0] for name in names]
    pressed: list = []
    try:
        for vk in resolved:
            _send_inputs([_key_event(vk, False)])
            pressed.append(vk)
    finally:
        for vk in reversed(pressed):
            try:
                _send_inputs([_key_event(vk, True)])
            except NativeError:
                pass


def type_text(text: str, interval_ms: int = 12) -> int:
    """用 KEYEVENTF_UNICODE 输入文本；``\\n``→回车、``\\t``→Tab。

    返回按 Unicode 码点计的字符数（与请求里的 ``len(text)`` 一致）。
    """
    delay = max(0, int(interval_ms)) / 1000.0
    chars = list(text)
    for index, ch in enumerate(chars):
        if ch == "\r":
            continue
        if ch == "\n":
            _send_inputs([_key_event(NAMED_KEYS["enter"], False), _key_event(NAMED_KEYS["enter"], True)])
        elif ch == "\t":
            _send_inputs([_key_event(NAMED_KEYS["tab"], False), _key_event(NAMED_KEYS["tab"], True)])
        else:
            # 非 BMP 字符（如 emoji）需要按 UTF-16 代理对逐个注入
            units = ch.encode("utf-16-le")
            events = []
            for off in range(0, len(units), 2):
                unit = units[off] | (units[off + 1] << 8)
                events.append(_make_key(0, unit, KEYEVENTF_UNICODE))
                events.append(_make_key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            _send_inputs(events)
        if delay and index + 1 < len(chars):
            time.sleep(delay)
    return len(chars)


# ---------------------------------------------------------------------------
# v0.3：窗口客户区 / 激活窗口 / 按键按下状态
# ---------------------------------------------------------------------------


def get_client_rect_screen(hwnd: int) -> dict:
    """窗口客户区在虚拟桌面中的物理像素矩形（``GetClientRect`` + ``ClientToScreen``）。

    返回 ``{x, y, width, height}``；用于计算圆形轨迹圆心/半径与点击坐标。
    """
    _require()
    handle = ctypes.c_void_p(int(hwnd))
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes

    client = _wintypes.RECT()
    if not _user32.GetClientRect(handle, _ctypes.byref(client)):
        raise NativeError("WINDOW_CAPTURE_FAILED", "GetClientRect 失败（hwnd=%s）" % hwnd)
    width = int(client.right - client.left)
    height = int(client.bottom - client.top)
    if width <= 0 or height <= 0:
        raise NativeError(
            "WINDOW_CAPTURE_FAILED",
            "窗口客户区尺寸非法：%dx%d（hwnd=%s）" % (width, height, hwnd),
            hint="窗口可能已最小化或正在销毁",
        )
    point = _wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(handle, _ctypes.byref(point)):
        raise NativeError("WINDOW_CAPTURE_FAILED", "ClientToScreen 失败（hwnd=%s）" % hwnd)
    return {"x": int(point.x), "y": int(point.y), "width": width, "height": height}


def get_foreground_hwnd() -> int:
    """当前前台窗口句柄（无前台窗口时为 0）。"""
    _require()
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _wait_for_foreground(hwnd: int, seconds: float) -> bool:
    """在 ``seconds`` 内轮询前台窗口，命中返回 True。"""
    deadline = time.perf_counter() + max(0.0, float(seconds))
    handle = int(hwnd)
    while time.perf_counter() < deadline:
        try:
            if int(_user32.GetForegroundWindow() or 0) == handle:
                return True
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.01)
    try:
        return int(_user32.GetForegroundWindow() or 0) == handle
    except Exception:  # noqa: BLE001
        return False


def _wait_foreground_until(hwnd: int, deadline: float) -> bool:
    while time.perf_counter() < deadline:
        try:
            if int(_user32.GetForegroundWindow() or 0) == int(hwnd):
                return True
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.01)
    return False


def activate_window_native(hwnd: int, restore: bool = True, timeout_ms: int = 2000) -> dict:
    """把窗口恢复到前台（**不注入点击**）。

    依次尝试：``SetForegroundWindow`` → ``AttachThreadInput+BringWindowToTop+SetForegroundWindow``
    → 轻点 ``VK_MENU`` 解锁后再 ``SetForegroundWindow``；轮询 ``GetForegroundWindow`` 至超时。

    返回 ``{hwnd, foreground, attempts, minimized, restored}``；失败时 ``foreground=False``，
    由调用方（game_control）转成 ``WINDOW_ACTIVATION_FAILED``。
    """
    _require()
    hwnd = int(hwnd)
    if hwnd <= 0:
        raise NativeError("INVALID_ARGUMENT", "hwnd 必须是正整数")
    timeout_ms = max(100, min(5000, int(timeout_ms)))
    deadline = time.perf_counter() + timeout_ms / 1000.0
    handle = ctypes.c_void_p(hwnd)
    attempts = 0
    restored = False

    # 1) 最小化时恢复窗口
    try:
        minimized = bool(_user32.IsIconic(handle))
    except Exception:  # noqa: BLE001
        minimized = False
    if minimized and restore:
        try:
            restored = bool(_user32.ShowWindow(handle, SW_RESTORE))
        except Exception:  # noqa: BLE001
            restored = False
        # 等窗口退出最小化（不超过总超时的一半或 1s）
        restore_deadline = min(deadline, time.perf_counter() + min(1.0, timeout_ms / 2000.0))
        while time.perf_counter() < restore_deadline:
            try:
                if not _user32.IsIconic(handle):
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.01)
        minimized = bool(_user32.IsIconic(handle))

    if _wait_foreground_until(hwnd, min(deadline, time.perf_counter() + 0.02)):
        return {"hwnd": hwnd, "foreground": True, "attempts": 0, "minimized": minimized, "restored": restored}

    # 2) SetForegroundWindow
    attempts += 1
    try:
        _user32.SetForegroundWindow(handle)
    except Exception:  # noqa: BLE001
        pass
    if _wait_for_foreground(hwnd, min(0.15, max(0.02, (deadline - time.perf_counter())))):
        return {"hwnd": hwnd, "foreground": True, "attempts": attempts, "minimized": minimized, "restored": restored}

    # 3) AttachThreadInput + BringWindowToTop + SetForegroundWindow
    attempts += 1
    current_thread = int(_kernel32.GetCurrentThreadId())
    foreground = 0
    try:
        foreground = int(_user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        foreground = 0
    foreground_thread = 0
    if foreground:
        try:
            foreground_thread = int(_user32.GetWindowThreadProcessId(foreground, None))
        except Exception:  # noqa: BLE001
            foreground_thread = 0
    attached = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(_user32.AttachThreadInput(current_thread, foreground_thread, True))
        _user32.BringWindowToTop(handle)
        _user32.SetForegroundWindow(handle)
    except Exception:  # noqa: BLE001
        pass
    finally:
        if attached:
            try:
                _user32.AttachThreadInput(current_thread, foreground_thread, False)
            except Exception:  # noqa: BLE001
                pass
    if _wait_for_foreground(hwnd, min(0.2, max(0.02, (deadline - time.perf_counter())))):
        return {"hwnd": hwnd, "foreground": True, "attempts": attempts, "minimized": minimized, "restored": restored}

    # 4) 轻点 VK_MENU 解锁前台锁，再 SetForegroundWindow（不产生字符、不是点击）
    attempts += 1
    try:
        _send_inputs([_make_key(VK_MENU, 0, 0), _make_key(VK_MENU, 0, KEYEVENTF_KEYUP)])
    except NativeError:
        pass
    try:
        _user32.SetForegroundWindow(handle)
    except Exception:  # noqa: BLE001
        pass
    foreground_ok = _wait_foreground_until(hwnd, deadline)
    try:
        minimized = bool(_user32.IsIconic(handle))
    except Exception:  # noqa: BLE001
        pass
    return {
        "hwnd": hwnd,
        "foreground": bool(foreground_ok),
        "attempts": attempts,
        "minimized": minimized,
        "restored": restored,
    }


def key_is_down(name: str) -> bool:
    """``GetAsyncKeyState`` 查询某键当前是否按下（用于中止键检测）。"""
    _require()
    vk, _need_shift = key_info(name)
    try:
        state = int(_user32.GetAsyncKeyState(int(vk)))
    except Exception as exc:  # noqa: BLE001
        raise NativeError("BACKEND_ERROR", "GetAsyncKeyState 失败: %s" % exc) from exc
    return bool(state & 0x8000)


def key_state_available() -> bool:
    """``GetAsyncKeyState`` 是否可用（供 selftest 只读检查）。"""
    _load_libraries()
    return _user32 is not None and hasattr(_user32, "GetAsyncKeyState")


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------


def _dib_buffer(width: int, height: int) -> tuple:
    """构造 32bpp BI_RGB 的 BITMAPINFO；返回 (bitmapinfo, buffer)。

    用正数 biHeight（自底向上）取图，再翻转，符合 SPEC 对行序的说明。
    """
    info = BITMAPINFO()
    header = info.bmiHeader
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = int(width)
    header.biHeight = int(height)
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB
    header.biSizeImage = int(width) * int(height) * 4
    buffer = ctypes.create_string_buffer(int(width) * int(height) * 4)
    return info, buffer


def capture_rgb(x: int, y: int, width: int, height: int) -> tuple:
    """用 GDI 截取虚拟桌面区域，返回 ``(rgb_bytes, width, height)``。

    ``rgb_bytes`` 为紧密排列的 RGB（每像素 3 字节，行优先、自顶向下）。
    区域越界由调用方（screenshot.py）负责裁剪；这里只做基本参数校验。
    """
    _require()
    x, y, width, height = int(x), int(y), int(width), int(height)
    if width <= 0 or height <= 0:
        raise NativeError("INVALID_REGION", "截图宽高必须为正数: %dx%d" % (width, height))
    screen_dc = None
    mem_dc = None
    bitmap = None
    try:
        screen_dc = _user32.GetDC(None)
        if not screen_dc:
            raise NativeError("BACKEND_ERROR", "GetDC(0) 获取屏幕 DC 失败")
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            raise NativeError("BACKEND_ERROR", "CreateCompatibleDC 失败")
        bitmap = _gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            raise NativeError("BACKEND_ERROR", "CreateCompatibleBitmap 失败（%dx%d）" % (width, height))
        old_obj = _gdi32.SelectObject(mem_dc, bitmap)
        blt_ok = _gdi32.BitBlt(
            mem_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            x,
            y,
            SRCCOPY | CAPTUREBLT,
        )
        _gdi32.SelectObject(mem_dc, old_obj)
        if not blt_ok:
            err = ctypes.get_last_error()
            raise NativeError(
                "BACKEND_ERROR",
                "BitBlt 截图失败（区域 %dx%d @ (%d,%d)，WinError=%d）" % (width, height, x, y, err),
                hint="多显示器 / 受保护窗口（DRM 内容）可能无法截取，请确认会话未锁定",
            )
        return _bitmap_dib_rgb(mem_dc, bitmap, width, height), width, height
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        if screen_dc:
            _user32.ReleaseDC(None, screen_dc)


def _bitmap_dib_rgb(mem_dc, bitmap, width: int, height: int) -> bytes:
    """把已绘制好的兼容位图读成 RGB bytes（BI_RGB 32bpp BGRX → 翻转 → RGB）。"""
    info, buffer = _dib_buffer(width, height)
    lines = _gdi32.GetDIBits(
        mem_dc,
        bitmap,
        0,
        height,
        ctypes.byref(buffer),
        ctypes.byref(info),
        DIB_RGB_COLORS,
    )
    if int(lines) != height:
        err = ctypes.get_last_error()
        raise NativeError(
            "BACKEND_ERROR",
            "GetDIBits 只返回 %s/%d 行像素（WinError=%d）" % (lines, height, err),
        )
    stride = width * 4
    raw = buffer.raw
    # BI_RGB 32bpp 内存布局：B,G,R,X（X 未定义）；行序自底向上
    flipped = bytearray(len(raw))
    for row in range(height):
        src = (height - 1 - row) * stride
        dst = row * stride
        flipped[dst : dst + stride] = raw[src : src + stride]
    rgb = bytearray(width * height * 3)
    rgb[0::3] = flipped[2::4]  # R
    rgb[1::3] = flipped[1::4]  # G
    rgb[2::3] = flipped[0::4]  # B
    return bytes(rgb)


def _capture_window_via_gdi(hwnd: int, width: int, height: int, paint) -> bytes | None:
    """通用窗口 GDI 截图：取窗口 DC → 兼容位图 → paint(mem_dc, window_dc) → RGB。

    ``paint`` 返回假值表示本次绘制失败（例如 ``PrintWindow`` 返回 0），函数返回 None；
    GDI 资源创建失败则抛 ``NativeError(BACKEND_ERROR)``。
    """
    _require()
    hwnd = int(hwnd)
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise NativeError("WINDOW_CAPTURE_FAILED", "窗口尺寸非法: %dx%d" % (width, height))
    hwnd_handle = ctypes.c_void_p(hwnd)
    hdc_window = _user32.GetWindowDC(hwnd_handle)
    if not hdc_window:
        raise NativeError(
            "WINDOW_CAPTURE_FAILED",
            "GetWindowDC 失败（hwnd=%d）" % hwnd,
            hint="窗口可能已关闭或权限不足",
        )
    mem_dc = None
    bitmap = None
    try:
        mem_dc = _gdi32.CreateCompatibleDC(hdc_window)
        if not mem_dc:
            raise NativeError("BACKEND_ERROR", "CreateCompatibleDC 失败（窗口截图）")
        bitmap = _gdi32.CreateCompatibleBitmap(hdc_window, width, height)
        if not bitmap:
            raise NativeError("BACKEND_ERROR", "CreateCompatibleBitmap 失败（%dx%d）" % (width, height))
        old_obj = _gdi32.SelectObject(mem_dc, bitmap)
        try:
            painted = paint(mem_dc, hdc_window)
        finally:
            _gdi32.SelectObject(mem_dc, old_obj)
        if not painted:
            return None
        return _bitmap_dib_rgb(mem_dc, bitmap, width, height)
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd_handle, hdc_window)


def capture_print_window_rgb(hwnd: int, width: int, height: int, flags: int = 0) -> bytes | None:
    """用 ``PrintWindow`` 抓取窗口画面；返回 RGB bytes，PrintWindow 返回 0 时返回 None。

    ``flags``：0 或 ``PW_RENDERFULLCONTENT(2)``（Win8.1+，可抓更多 DWM/硬件合成内容）。
    """

    def _paint(mem_dc, _window_dc):
        return bool(_user32.PrintWindow(ctypes.c_void_p(int(hwnd)), mem_dc, int(flags)))

    return _capture_window_via_gdi(hwnd, width, height, _paint)


def capture_window_dc_rgb(hwnd: int, width: int, height: int) -> bytes | None:
    """用 ``GetWindowDC`` + ``BitBlt`` 抓取窗口画面（对 GDI 窗口后台可用）。"""

    def _paint(mem_dc, window_dc):
        return bool(
            _gdi32.BitBlt(mem_dc, 0, 0, int(width), int(height), window_dc, 0, 0, SRCCOPY | CAPTUREBLT)
        )

    return _capture_window_via_gdi(hwnd, width, height, _paint)


# ---------------------------------------------------------------------------
# 自检辅助（只读，无输入副作用）
# ---------------------------------------------------------------------------


def selftest() -> dict:
    """返回原生层只读自检信息；不发送任何输入事件。"""
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    _load_libraries()
    add("win_native 可用", available(), _LOAD_ERROR or "user32/gdi32 已加载")
    add("INPUT 结构大小(x64=40)", ctypes.sizeof(INPUT) == 40, "sizeof(INPUT)=%d" % ctypes.sizeof(INPUT))
    add("窗口 GDI 截图入口", window_gdi_available(), "PrintWindow + GetWindowDC")
    dpi = setup_dpi_awareness()
    add("DPI 感知", bool(dpi["aware"]), dpi["mode"])
    try:
        vs = get_virtual_screen()
        add(
            "虚拟桌面几何",
            vs["width"] > 0 and vs["height"] > 0,
            "%(x)d,%(y)d %(width)dx%(height)d" % vs,
        )
    except NativeError as exc:
        add("虚拟桌面几何", False, str(exc))
    try:
        displays = enumerate_displays()
        add("显示器枚举", len(displays) > 0, "共 %d 个显示器" % len(displays))
    except NativeError as exc:
        add("显示器枚举", False, str(exc))
    try:
        pointer = get_pointer()
        add("鼠标位置可读", isinstance(pointer.get("x"), int), "(%d,%d)" % (pointer["x"], pointer["y"]))
    except NativeError as exc:
        add("鼠标位置可读", False, str(exc))
    # v0.3：GetAsyncKeyState / 前台窗口 / 客户区（只读）
    try:
        _ = key_is_down("esc")
        add("GetAsyncKeyState 可用", hasattr(_user32, "GetAsyncKeyState"), "中止键轮询可用")
    except NativeError as exc:
        add("GetAsyncKeyState 可用", False, str(exc))
    try:
        foreground = get_foreground_hwnd()
        add("前台窗口可读", foreground > 0, "hwnd=%d" % foreground)
        if foreground > 0:
            client = get_client_rect_screen(foreground)
            add(
                "窗口客户区可读",
                client["width"] > 0 and client["height"] > 0,
                "client=%dx%d @ (%d,%d)" % (client["width"], client["height"], client["x"], client["y"]),
            )
        else:
            add("窗口客户区可读", True, "无前台窗口，跳过")
    except NativeError as exc:
        add("前台窗口/客户区", False, str(exc))
    # 键名解析覆盖检查（不发送事件）
    try:
        bad = [name for name in list(NAMED_KEYS) if key_info(name)[0] is None]
        add("命名键解析", not bad, "解析失败: %s" % (bad or "无"))
        char_bad = [ch for ch in "abcdefghijklmnopqrstuvwxyz0123456789-=[];',./`" if vk_for_char(ch)[0] is None]
        add("字符键解析", not char_bad, "解析失败: %s" % (char_bad or "无"))
    except NativeError as exc:
        add("键名解析", False, str(exc))
    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }
