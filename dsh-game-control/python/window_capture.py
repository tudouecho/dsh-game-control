"""Windows 窗口枚举 + 后台窗口截图（SPEC 2.3.1，v0.2 新增）。

能力：
- ``list_windows``：``EnumWindows`` 枚举顶层窗口（z 序，前台在前），字段包含
  title/class/pid/process/rect(DWM extended frame)/minimized/visible/foreground/cloaked；
- ``capture_window``：按 hwnd 或标题子串选窗，抓取窗口画面（可被遮挡、可非前台）。

截图引擎（``engine=auto`` 时按顺序回退）：
1. **wgc**：Windows Graphics Capture（真后台抓取主路径）。依赖随插件分发的
   ``python/vendor/windows_capture/``（windows-capture 2.0.1，MIT，见
   ``vendor/windows_capture_LICENSE.txt``）+ 本机 numpy。该包 ``__init__.py`` 顶层
   ``import cv2``，本插件不用 cv2 落盘，导入前若 cv2 缺失则向 ``sys.modules['cv2']``
   注入最小 stub（``imwrite`` 抛错），避免为了截图安装 40MB OpenCV。
   **Win10 兼容**：``WindowsCapture(cursor_capture=None, draw_border=None, window_hwnd=hwnd)``，
   传 ``draw_border=False`` 会抛 "Toggling the capture border is not supported"。
   用 ``start_free_threaded()`` 拿 ``CaptureControl``，等首帧最多 ``timeoutMs``，然后 ``stop()``。
2. **printwindow**：``PrintWindow`` flags ``PW_RENDERFULLCONTENT(2)`` 与 ``0`` 都尝试；
   黑帧判定（blackRatio>0.98 或唯一色<3）后视为失败继续回退。
3. **window-dc**：``GetWindowDC`` + ``BitBlt``（对 GDI 窗口后台可用），同样做黑帧判定。
4. **screen**：按窗口矩形裁剪屏幕（只能拿到可见像素），返回
   ``warnings:["window may be occluded"]``。

显式指定 ``engine`` 时只走指定路径，失败返回 ``WINDOW_CAPTURE_FAILED``；
``list_windows``/``capture_window`` 在非 Windows 返回 ``UNSUPPORTED_PLATFORM``。
两个动作都**不注入任何输入**，不联网。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import types
from pathlib import Path

IS_WINDOWS = os.name == "nt"
SCRIPT_DIR = Path(__file__).resolve().parent
VENDOR_DIR = SCRIPT_DIR / "vendor"
VENDOR_PACKAGE = VENDOR_DIR / "windows_capture"

# DWM 属性
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
# PrintWindow 的 PW_RENDERFULLCONTENT（Win8.1+）
PW_RENDERFULLCONTENT = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# capture_window 的 engine 枚举（SPEC 2.3.1）；window-dc 只作为 auto 的中间回退
ENGINE_VALUES = ("auto", "wgc", "printwindow", "screen")
AUTO_ENGINES = ("wgc", "printwindow", "window-dc", "screen")

# 黑帧判定阈值（SPEC 2.3.1）：纯黑占比 > 98% 或采样唯一色 < 3 种
BLACK_RATIO_THRESHOLD = 0.98
UNIQUE_COLOR_THRESHOLD = 3
BLACK_PIXEL_LEVEL = 8  # 每通道都 < 8 视为黑色像素


class WindowCaptureError(Exception):
    """窗口枚举/截图错误；``code``/``hint`` 直接进入响应信封。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


# ---------------------------------------------------------------------------
# Win32 绑定（懒加载：非 Windows 上模块仍可导入，动作返回 UNSUPPORTED_PLATFORM）
# ---------------------------------------------------------------------------

_win32 = {
    "loaded": False,
    "error": None,
    "user32": None,
    "kernel32": None,
    "dwmapi": None,
    "wintypes": None,
    "enum_proc": None,
}


def _load_win32() -> None:
    """加载 user32/kernel32/dwmapi 并声明函数原型（仅 Windows）。"""
    if _win32["loaded"]:
        return
    _win32["loaded"] = True
    if not IS_WINDOWS:
        _win32["error"] = "当前平台不是 Windows"
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

        # -- user32 --
        user32.EnumWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL

        # -- dwmapi --
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

        # -- kernel32 --
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        _win32.update(
            {
                "user32": user32,
                "kernel32": kernel32,
                "dwmapi": dwmapi,
                "wintypes": wintypes,
                "enum_proc": ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            }
        )
    except OSError as exc:  # pragma: no cover - 正常 Windows 不会发生
        _win32["error"] = "加载 Win32 窗口 API 失败: %s" % exc


def _require() -> None:
    """确保当前平台/原生 API 可用。"""
    _load_win32()
    if not IS_WINDOWS:
        raise WindowCaptureError(
            "UNSUPPORTED_PLATFORM",
            "窗口枚举与后台窗口截图仅支持 Windows",
            "macOS/Linux 请使用 screenshot 的整屏/区域截图",
        )
    if _win32["error"]:
        raise WindowCaptureError(
            "UNSUPPORTED_PLATFORM",
            "Windows 窗口 API 不可用：%s" % _win32["error"],
            "确认当前为交互式桌面会话",
        )


def _user32():
    return _win32["user32"]


def _kernel32():
    return _win32["kernel32"]


def _dwmapi():
    return _win32["dwmapi"]


# ---------------------------------------------------------------------------
# 窗口信息读取
# ---------------------------------------------------------------------------


def _window_title(hwnd: int) -> str:
    user32 = _user32()
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = _create_unicode_buffer(length + 2)
    copied = int(user32.GetWindowTextW(hwnd, buffer, length + 2))
    return buffer.value[:copied] if copied > 0 else buffer.value


def _window_class(hwnd: int) -> str:
    buffer = _create_unicode_buffer(256)
    copied = int(_user32().GetClassNameW(hwnd, buffer, 256))
    return buffer.value[:copied] if copied > 0 else buffer.value


def _create_unicode_buffer(size: int):
    import ctypes

    return ctypes.create_unicode_buffer(size)


def _window_pid(hwnd: int) -> int:
    import ctypes

    pid = _win32["wintypes"].DWORD(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_name(pid: int) -> str:
    """``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``QueryFullProcessImageNameW``。"""
    if not pid:
        return ""
    import ctypes

    wintypes = _win32["wintypes"]
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_rect(hwnd: int) -> dict:
    """DWM extended frame bounds（可见边框），失败回退 GetWindowRect。"""
    import ctypes

    wintypes = _win32["wintypes"]
    rect = wintypes.RECT()
    try:
        status = int(
            _dwmapi().DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
        )
    except Exception:  # noqa: BLE001 - DWM 不可用时回退
        status = -1
    if status != 0 or rect.right <= rect.left or rect.bottom <= rect.top:
        if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
            return {"x": 0, "y": 0, "width": 0, "height": 0}
    return {
        "x": int(rect.left),
        "y": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
    }


def _window_rect_raw(hwnd: int) -> dict:
    """``GetWindowRect``（含不可见的 DWM 边框），供 PrintWindow/BitBlt 使用。"""
    import ctypes

    wintypes = _win32["wintypes"]
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    return {
        "x": int(rect.left),
        "y": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
    }


def _window_cloaked(hwnd: int) -> bool:
    """DWM cloaked（UWP 隐藏窗口/虚拟桌面不可见窗口）。"""
    import ctypes

    value = _win32["wintypes"].DWORD(0)
    try:
        status = int(
            _dwmapi().DwmGetWindowAttribute(
                hwnd,
                DWMWA_CLOAKED,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        )
    except Exception:  # noqa: BLE001
        return False
    return status == 0 and int(value.value) != 0


def _window_client_rect(hwnd: int) -> dict:
    """客户区屏幕矩形（SPEC 8.2.6）；失败/零尺寸时退化为窗口矩形，保证宽高 > 0。"""
    try:
        import win_native

        client = win_native.get_client_rect_screen(hwnd)
        if int(client.get("width") or 0) > 0 and int(client.get("height") or 0) > 0:
            return client
    except Exception:  # noqa: BLE001 - 单个窗口读不到不影响列表
        pass
    # 退化：部分最小化/工具窗口 GetClientRect 返回 0，用窗口矩形兜底（仍保证 >0）
    try:
        rect = _window_rect_raw(hwnd)
    except Exception:  # noqa: BLE001
        rect = {"x": 0, "y": 0, "width": 0, "height": 0}
    return {
        "x": int(rect.get("x") or 0),
        "y": int(rect.get("y") or 0),
        "width": max(1, int(rect.get("width") or 0)),
        "height": max(1, int(rect.get("height") or 0)),
    }


def _window_info(hwnd: int, foreground: bool, visible: bool | None = None, title: str | None = None) -> dict:
    """组装 SPEC 2.3.1 规定的窗口字段（list_windows 每项；v0.3 增加 clientRect）。"""
    hwnd = int(hwnd)
    rect = _window_rect(hwnd)
    pid = _window_pid(hwnd)
    return {
        "hwnd": hwnd,
        "title": _window_title(hwnd) if title is None else title,
        "class": _window_class(hwnd),
        "pid": pid,
        "process": _process_name(pid),
        "rect": rect,
        # v0.3（SPEC 8.2.6）：客户区在虚拟桌面中的物理像素矩形
        "clientRect": _window_client_rect(hwnd),
        "minimized": bool(_user32().IsIconic(hwnd)),
        "visible": bool(_user32().IsWindowVisible(hwnd)) if visible is None else bool(visible),
        "foreground": bool(foreground),
        "cloaked": _window_cloaked(hwnd),
    }


def _foreground_hwnd() -> int:
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _is_window(hwnd: int) -> bool:
    try:
        return bool(_user32().IsWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


def _is_minimized(hwnd: int) -> bool:
    try:
        return bool(_user32().IsIconic(hwnd))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 窗口枚举
# ---------------------------------------------------------------------------


def _enumerate_infos() -> list:
    """枚举所有顶层窗口，返回按 EnumWindows 顺序（z 序，最上层在前）的 info 列表。"""
    user32 = _user32()
    enum_proc = _win32["enum_proc"]
    foreground = _foreground_hwnd()
    collected: list = []

    def _callback(hwnd, _lparam):  # noqa: ANN001 - ctypes 回调
        try:
            handle = int(hwnd or 0)
            if handle:
                collected.append(_window_info(handle, handle == foreground))
        except Exception:  # noqa: BLE001 - 单个窗口读取失败不影响整体
            pass
        return True

    callback = enum_proc(_callback)
    user32.EnumWindows(callback, 0)
    # 前台窗口提到最前（EnumWindows 顺序本身已是 z 序，这里只保证“前台在前”）
    for index, item in enumerate(collected):
        if item["foreground"] and index > 0:
            collected.insert(0, collected.pop(index))
            break
    return collected


def list_windows(title_contains: str | None = None, include_invisible: bool = False, limit: int = 50) -> dict:
    """枚举窗口（SPEC 2.3.1）。返回 ``{count, windows:[...]}``。"""
    _require()
    limit = max(1, min(100, int(limit)))
    needle = str(title_contains or "").strip().lower()
    windows = []
    for item in _enumerate_infos():
        if not include_invisible and not item["visible"]:
            continue
        if needle and needle not in item["title"].lower():
            continue
        windows.append(item)
    windows = windows[:limit]
    return {"count": len(windows), "windows": windows}


# ---------------------------------------------------------------------------
# 选窗口
# ---------------------------------------------------------------------------


def resolve_window(window: str | None = None, hwnd: int | None = None) -> dict:
    """按 hwnd（精确）或标题子串选窗；找不到抛 ``WINDOW_NOT_FOUND``。"""
    _require()
    foreground = _foreground_hwnd()
    if hwnd is not None:
        handle = int(hwnd)
        if handle <= 0 or not _is_window(handle):
            raise WindowCaptureError(
                "WINDOW_NOT_FOUND",
                "窗口不存在或已关闭: hwnd=%s" % hwnd,
                "先用 list_windows 查看当前窗口的 hwnd",
            )
        return _window_info(handle, handle == foreground)
    needle = str(window or "").strip()
    if not needle:
        raise WindowCaptureError(
            "WINDOW_NOT_FOUND",
            "window 标题子串为空",
            "传 window（标题子串）或 hwnd（数值句柄）",
        )
    lowered = needle.lower()
    matches = [item for item in _enumerate_infos() if lowered in item["title"].lower()]
    if not matches:
        raise WindowCaptureError(
            "WINDOW_NOT_FOUND",
            "没有标题包含 %r 的窗口" % needle,
            "先用 list_windows 查看当前窗口标题；标题匹配不区分大小写",
        )
    preferred = [item for item in matches if item["visible"] and not item["minimized"]]
    pool = preferred or [item for item in matches if item["visible"]] or matches
    # 优先 visible 且非 minimized 中面积最大的窗口
    return max(pool, key=lambda item: item["rect"]["width"] * item["rect"]["height"])


def activate_window(window: dict, restore: bool = True, timeout_ms: int = 2000) -> dict:
    """把窗口切到前台（SPEC 8.2.1，**不注入点击**）。

    复用 :func:`resolve_window` 解析出的窗口信息；原生激活由 ``win_native.activate_window_native``
    完成（SetForegroundWindow → AttachThreadInput → 轻点 VK_MENU）。失败抛
    ``WINDOW_ACTIVATION_FAILED``。
    """
    _require()
    import win_native

    hwnd = int(window["hwnd"])
    result = win_native.activate_window_native(
        hwnd,
        restore=bool(restore),
        timeout_ms=int(timeout_ms),
    )
    if not result.get("foreground"):
        raise WindowCaptureError(
            "WINDOW_ACTIVATION_FAILED",
            "窗口未能切到前台: hwnd=%d（尝试 %d 次，minimized=%s）"
            % (hwnd, int(result.get("attempts", 0)), bool(result.get("minimized"))),
            hint="窗口可能被 UAC/提权保护、属于其他桌面会话，或需用户手动点击一次；可传 restore=true 先恢复最小化",
        )
    # 激活/恢复后窗口矩形可能变化，重新读取一次
    info = _window_info(hwnd, foreground=True)
    return {
        "window": {
            "hwnd": info["hwnd"],
            "title": info["title"],
            "class": info["class"],
            "pid": info["pid"],
            "process": info["process"],
            "rect": info["rect"],
            "clientRect": info["clientRect"],
        },
        "foreground": True,
        "attempts": int(result.get("attempts", 0)),
    }


# ---------------------------------------------------------------------------
# 帧质量判定（黑帧）
# ---------------------------------------------------------------------------


def frame_stats(data: bytes) -> tuple:
    """对 RGB bytes 抽样，返回 ``(black_ratio, unique_colors_capped)``。"""
    total = len(data) // 3 if data else 0
    if total <= 0:
        return 1.0, 0
    # 均匀抽样最多 ~4096 个像素，避免大窗口逐像素遍历
    step = max(1, total // 4096)
    black = 0
    sampled = 0
    colors: set = set()
    for index in range(0, total, step):
        offset = index * 3
        r = data[offset]
        g = data[offset + 1]
        b = data[offset + 2]
        sampled += 1
        if r < BLACK_PIXEL_LEVEL and g < BLACK_PIXEL_LEVEL and b < BLACK_PIXEL_LEVEL:
            black += 1
        if len(colors) < UNIQUE_COLOR_THRESHOLD + 1:
            colors.add((r >> 3, g >> 3, b >> 3))
    ratio = black / sampled if sampled else 1.0
    return ratio, len(colors)


def is_black_frame(data: bytes) -> bool:
    """黑帧判定：blackRatio>0.98 或唯一色<3（SPEC 2.3.1）。"""
    ratio, unique = frame_stats(data)
    return ratio > BLACK_RATIO_THRESHOLD or unique < UNIQUE_COLOR_THRESHOLD


# ---------------------------------------------------------------------------
# 引擎 1：Windows Graphics Capture（真后台抓取主路径）
# ---------------------------------------------------------------------------

_wgc_module = None
_wgc_status = {"checked": False, "available": False, "detail": "", "vendor": False}


def _vendor_present() -> bool:
    return (VENDOR_PACKAGE / "__init__.py").exists() and (VENDOR_PACKAGE / "windows_capture.pyd").exists()


def _ensure_cv2_stub() -> None:
    """windows_capture 顶层 ``import cv2``；cv2 缺失时注入最小 stub（不安装 OpenCV）。"""
    if "cv2" in sys.modules:
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("cv2")
    except (ImportError, ValueError):
        spec = None
    if spec is not None:
        return
    stub = types.ModuleType("cv2")

    def _imwrite(*_args, **_kwargs):
        raise RuntimeError("cv2 stub：本插件不使用 cv2.imwrite，请用 screenshot.save_image")

    stub.imwrite = _imwrite
    stub.__version__ = "0.0.0-dsh-stub"
    sys.modules["cv2"] = stub


def _import_windows_capture():
    """先尝试已安装的 windows_capture；失败再把 ``vendor/`` 临时加入 sys.path 重试。"""
    global _wgc_module
    if _wgc_module is not None:
        return _wgc_module
    _ensure_cv2_stub()
    first_error = None
    try:
        import windows_capture as module  # type: ignore

        _wgc_module = module
        return module
    except Exception as exc:  # noqa: BLE001 - 缺依赖/ABI 不匹配都走 vendor 重试
        first_error = exc
    vendor_path = str(VENDOR_DIR)
    added = vendor_path not in sys.path
    if added:
        sys.path.insert(0, vendor_path)
    try:
        import windows_capture as module  # type: ignore

        _wgc_module = module
        return module
    except Exception as exc:  # noqa: BLE001
        hint = "确认 python/vendor/windows_capture/ 存在（含 windows_capture.pyd），且本机 numpy 可用；也可 pip install windows-capture"
        raise WindowCaptureError(
            "MISSING_DEPENDENCY",
            "WGC 依赖不可用：%s" % (str(exc) or first_error or "未知原因"),
            hint,
        ) from exc
    finally:
        if added:
            try:
                sys.path.remove(vendor_path)
            except ValueError:
                pass


def wgc_status(refresh: bool = False) -> dict:
    """探测 WGC 依赖是否可导入（结果缓存，不实际抓帧）。"""
    if _wgc_status["checked"] and not refresh:
        return dict(_wgc_status)
    _wgc_status["checked"] = True
    _wgc_status["vendor"] = _vendor_present()
    if not IS_WINDOWS:
        _wgc_status.update(available=False, detail="仅 Windows 支持 WGC")
        return dict(_wgc_status)
    try:
        module = _import_windows_capture()
        _wgc_status.update(
            available=True,
            detail="windows_capture 已导入（%s）" % getattr(module, "__file__", "vendor"),
        )
    except WindowCaptureError as exc:
        _wgc_status.update(available=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        _wgc_status.update(available=False, detail="WGC 导入失败: %s" % exc)
    return dict(_wgc_status)


def _capture_wgc(hwnd: int, timeout_ms: int, include_cursor: bool) -> dict:
    """WGC 抓首帧。返回 ``{data, width, height}``；失败抛 ``WINDOW_CAPTURE_FAILED``。"""
    module = _import_windows_capture()
    frame_event = threading.Event()
    closed_event = threading.Event()
    state: dict = {"frame": None, "error": None, "closed": False}
    hwnd = int(hwnd)
    timeout_ms = max(500, min(10000, int(timeout_ms)))

    def _build(cursor_value):
        # Win10：draw_border 必须为 None；cursor_capture 为 None 表示不设置（兼容旧系统）
        kwargs = {"draw_border": None, "window_hwnd": hwnd}
        if cursor_value is not None:
            kwargs["cursor_capture"] = cursor_value
        return module.WindowsCapture(**kwargs)

    try:
        capture = _build(bool(include_cursor))
    except Exception as first_exc:  # noqa: BLE001 - Win10 可能不支持 cursor 开关
        try:
            capture = _build(None)
        except Exception:  # noqa: BLE001
            raise WindowCaptureError(
                "WINDOW_CAPTURE_FAILED",
                "创建 WGC 捕获会话失败：%s" % (str(first_exc) or "未知原因"),
                "Win10 需 draw_border=None；也可尝试 engine=printwindow 或 engine=screen",
            ) from first_exc

    @capture.event
    def on_frame_arrived(frame, capture_control):  # noqa: ANN001 - 包的约定回调名
        try:
            buffer = frame.frame_buffer  # numpy (h, w, 4) BGRA
            if buffer is None or buffer.ndim != 3 or buffer.shape[2] < 3:
                raise RuntimeError("WGC 帧格式非 BGRA")
            # 在回调内立即拷贝：底层 mapped frame 生命周期仅限本次回调
            rgb = buffer[:, :, 2::-1]
            data = rgb.tobytes()
            width = int(frame.width)
            height = int(frame.height)
            if len(data) != width * height * 3:
                raise RuntimeError("WGC 帧长度异常: %d != %dx%dx3" % (len(data), width, height))
            state["frame"] = {"data": data, "width": width, "height": height}
        except Exception as exc:  # noqa: BLE001
            state["error"] = exc
        finally:
            try:
                capture_control.stop()
            except Exception:  # noqa: BLE001
                pass
            frame_event.set()

    @capture.event
    def on_closed():  # noqa: ANN001 - 包的约定回调名
        state["closed"] = True
        closed_event.set()
        frame_event.set()

    try:
        control = capture.start_free_threaded()
    except Exception as exc:  # noqa: BLE001 - 受限沙箱/远程会话常见
        raise WindowCaptureError(
            "WINDOW_CAPTURE_FAILED",
            "启动 WGC 捕获失败：%s" % _short_error(exc),
            "受限沙箱/远程桌面可能不支持 WGC；可尝试 engine=printwindow 或 engine=screen",
        ) from exc

    try:
        got_frame = frame_event.wait(timeout_ms / 1000.0)
        if not got_frame:
            raise WindowCaptureError(
                "WINDOW_CAPTURE_FAILED",
                "WGC 在 %dms 内未收到首帧（窗口可能最小化、被保护或捕获会话受限）" % timeout_ms,
                "确认窗口未最小化、未被反截图保护；可尝试 engine=printwindow",
            )
        if state["error"] is not None:
            raise WindowCaptureError(
                "WINDOW_CAPTURE_FAILED",
                "WGC 帧处理失败：%s" % _short_error(state["error"]),
            )
        if state["frame"] is None:
            raise WindowCaptureError(
                "WINDOW_CAPTURE_FAILED",
                "WGC 会话在收到首帧前结束（窗口可能已关闭）",
            )
        return dict(state["frame"])
    finally:
        try:
            control.stop()
        except Exception:  # noqa: BLE001
            pass
        # 等捕获线程退出，但不无限阻塞后端
        waiter = threading.Thread(target=_safe_wait, args=(control,), daemon=True)
        waiter.start()
        waiter.join(2.0)


def _safe_wait(control) -> None:  # noqa: ANN001
    try:
        control.wait()
    except Exception:  # noqa: BLE001
        pass


def _short_error(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().replace("\r", " ").replace("\n", " ")
    return text[:limit] if text else type(exc).__name__


# ---------------------------------------------------------------------------
# 引擎 2/3：PrintWindow / GetWindowDC（GDI）
# ---------------------------------------------------------------------------


def _gdi_window_size(hwnd: int) -> tuple:
    """GDI 截图使用 GetWindowRect 尺寸（含不可见 DWM 边框）。"""
    rect = _window_rect_raw(hwnd)
    width, height = int(rect["width"]), int(rect["height"])
    if width <= 0 or height <= 0:
        raise WindowCaptureError(
            "WINDOW_CAPTURE_FAILED",
            "窗口尺寸非法（%dx%d），无法用 GDI 截图" % (width, height),
            "窗口可能最小化或已销毁",
        )
    return width, height


def _capture_printwindow(hwnd: int, accept_black: bool) -> dict:
    """PrintWindow(2) 与 PrintWindow(0) 都尝试；黑帧视为失败。"""
    import win_native

    width, height = _gdi_window_size(hwnd)
    reasons = []
    for flags in (PW_RENDERFULLCONTENT, 0):
        try:
            data = win_native.capture_print_window_rgb(hwnd, width, height, flags)
        except Exception as exc:  # noqa: BLE001 - 单个 flags 失败继续试
            reasons.append("flags=%d: %s" % (flags, _short_error(exc)))
            continue
        if data is None:
            reasons.append("flags=%d: PrintWindow 返回 0" % flags)
            continue
        if not accept_black and is_black_frame(data):
            reasons.append("flags=%d: 黑帧（blackRatio/唯一色判定）" % flags)
            continue
        return {"data": data, "width": width, "height": height, "warnings": []}
    raise WindowCaptureError(
        "WINDOW_CAPTURE_FAILED",
        "PrintWindow 抓取失败：%s" % ("; ".join(reasons) or "未知原因"),
        "该窗口可能对 WM_PRINT 无响应（游戏/硬件合成窗口常见），可尝试 engine=wgc 或 engine=screen",
    )


def _capture_window_dc(hwnd: int, accept_black: bool) -> dict:
    """GetWindowDC + BitBlt；黑帧视为失败。"""
    import win_native

    width, height = _gdi_window_size(hwnd)
    try:
        data = win_native.capture_window_dc_rgb(hwnd, width, height)
    except Exception as exc:  # noqa: BLE001
        raise WindowCaptureError(
            "WINDOW_CAPTURE_FAILED",
            "GetWindowDC/BitBlt 抓取失败：%s" % _short_error(exc),
        ) from exc
    if data is None:
        raise WindowCaptureError("WINDOW_CAPTURE_FAILED", "GetWindowDC/BitBlt 返回空画面")
    if not accept_black and is_black_frame(data):
        raise WindowCaptureError(
            "WINDOW_CAPTURE_FAILED",
            "GetWindowDC/BitBlt 抓到黑帧（窗口可能被遮挡或为硬件合成窗口）",
        )
    return {"data": data, "width": width, "height": height, "warnings": []}


# ---------------------------------------------------------------------------
# 引擎 4：屏幕裁剪
# ---------------------------------------------------------------------------


def _capture_screen_crop(rect: dict) -> dict:
    """按窗口可见矩形裁剪屏幕（只能拿到可见像素）。"""
    import win_native

    virtual = win_native.get_virtual_screen()
    left = max(int(rect["x"]), int(virtual["x"]))
    top = max(int(rect["y"]), int(virtual["y"]))
    right = min(int(rect["x"]) + int(rect["width"]), int(virtual["x"]) + int(virtual["width"]))
    bottom = min(int(rect["y"]) + int(rect["height"]), int(virtual["y"]) + int(virtual["height"]))
    if left >= right or top >= bottom:
        raise WindowCaptureError(
            "WINDOW_CAPTURE_FAILED",
            "窗口完全在虚拟桌面外，无法用 screen 引擎裁剪",
            "把窗口移到屏幕内，或使用 engine=wgc/printwindow",
        )
    data, width, height = win_native.capture_rgb(left, top, right - left, bottom - top)
    warnings = ["window may be occluded"]
    if (right - left, bottom - top) != (int(rect["width"]), int(rect["height"])):
        warnings.append("window partially off-screen; captured visible part only")
    if is_black_frame(data):
        warnings.append("screen capture appears black; window may be hidden or off-screen")
    return {"data": data, "width": width, "height": height, "warnings": warnings}


# ---------------------------------------------------------------------------
# 统一抓取入口
# ---------------------------------------------------------------------------


def _try_engine(
    name: str,
    hwnd: int,
    rect: dict,
    timeout_ms: int,
    include_cursor: bool,
    accept_black: bool,
) -> dict:
    """执行单个引擎；失败抛 ``WindowCaptureError``。"""
    if name == "wgc":
        result = _capture_wgc(hwnd, timeout_ms, include_cursor)
        if not accept_black and is_black_frame(result["data"]):
            raise WindowCaptureError(
                "WINDOW_CAPTURE_FAILED",
                "WGC 首帧为黑帧（blackRatio/唯一色判定）",
            )
        return result
    if name == "printwindow":
        return _capture_printwindow(hwnd, accept_black)
    if name == "window-dc":
        return _capture_window_dc(hwnd, accept_black)
    if name == "screen":
        return _capture_screen_crop(rect)
    raise WindowCaptureError("INTERNAL", "未知截图引擎: %s" % name)


def _engine_accepts_black(engine_param: str, name: str) -> bool:
    """黑帧是否可接受：显式 wgc/screen 接受（真实画面可能全黑），其余不接受。"""
    if engine_param == "auto":
        return name == "screen"
    return name in ("wgc", "screen")


def capture_window(
    window: dict,
    engine: str = "auto",
    timeout_ms: int = 3000,
    include_cursor: bool = False,
) -> dict:
    """抓取指定窗口画面。

    返回 ``{data(rgb bytes), width, height, engine, warnings, fallbackReason?}``；
    全部引擎失败时抛 ``WINDOW_CAPTURE_FAILED``。
    """
    _require()
    engine = str(engine or "auto").lower()
    if engine not in ENGINE_VALUES:
        raise WindowCaptureError(
            "INVALID_ARGUMENT",
            "engine 取值非法: %r" % engine,
            "可选值: %s" % ", ".join(ENGINE_VALUES),
        )
    hwnd = int(window["hwnd"])
    rect = window.get("rect") or _window_rect(hwnd)
    if not _is_window(hwnd):
        raise WindowCaptureError("WINDOW_NOT_FOUND", "窗口已关闭: hwnd=%d" % hwnd, "重新用 list_windows 选择窗口")
    warnings: list = []
    if _is_minimized(hwnd):
        warnings.append("window is minimized")

    engines = AUTO_ENGINES if engine == "auto" else (engine,)
    reasons: list = []
    for name in engines:
        accept_black = _engine_accepts_black(engine, name)
        try:
            outcome = _try_engine(name, hwnd, rect, timeout_ms, include_cursor, accept_black)
        except WindowCaptureError as exc:
            if engine != "auto":
                # 显式 engine 只走指定路径：保留原始错误码（如 MISSING_DEPENDENCY / WINDOW_CAPTURE_FAILED）
                raise
            reasons.append("%s: %s" % (name, _short_error(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - 单引擎异常不拖垮整条回退链
            if engine != "auto":
                raise WindowCaptureError(
                    "WINDOW_CAPTURE_FAILED",
                    "%s 引擎抓取失败：%s" % (engine, _short_error(exc)),
                ) from exc
            reasons.append("%s: %s" % (name, _short_error(exc)))
            continue
        merged_warnings = list(warnings)
        for item in outcome.get("warnings") or []:
            if item not in merged_warnings:
                merged_warnings.append(item)
        if name == "window-dc" and not window.get("foreground"):
            # window-dc 对硬件合成窗口可能只能拿到当前可见像素（被遮挡时内容不可信），如实提示
            note = "window-dc: window is not foreground; captured content may be occluded"
            if note not in merged_warnings:
                merged_warnings.append(note)
        result = {
            "data": outcome["data"],
            "width": int(outcome["width"]),
            "height": int(outcome["height"]),
            "engine": name,
            "warnings": merged_warnings,
        }
        if reasons:
            result["fallbackReason"] = "; ".join(reasons)
        return result
    raise WindowCaptureError(
        "WINDOW_CAPTURE_FAILED",
        "所有截图引擎均失败：%s" % ("; ".join(reasons) or "未知原因"),
        "确认窗口未最小化、未被反截图保护；可尝试 engine=screen（只能拿可见像素）",
    )


# ---------------------------------------------------------------------------
# 能力探测（status.windowCapture / selftest）
# ---------------------------------------------------------------------------


def capability(refresh: bool = False) -> dict:
    """返回 ``{wgcAvailable, vendor, printWindow, note?}``，供 ``status`` 使用。"""
    if not IS_WINDOWS:
        return {
            "wgcAvailable": False,
            "vendor": False,
            "printWindow": False,
            "note": "窗口枚举与后台窗口截图仅支持 Windows",
        }
    wgc = wgc_status(refresh=refresh)
    result = {
        "wgcAvailable": bool(wgc.get("available")),
        "vendor": bool(wgc.get("vendor")),
        "printWindow": True,
    }
    if not result["wgcAvailable"]:
        result["note"] = str(wgc.get("detail") or "WGC 不可用，将回退 PrintWindow/screen")[:400]
    else:
        result["note"] = "WGC 依赖可导入；受限沙箱/受保护窗口抓取失败时会自动回退"
    return result


def selftest() -> dict:
    """只读自检：窗口枚举数量 + 能力探测，不截图、不注入输入。"""
    checks = []
    try:
        listing = list_windows(limit=5)
        checks.append(
            {
                "name": "窗口枚举",
                "ok": listing["count"] > 0,
                "detail": "枚举到 %d 个窗口（limit=5）" % listing["count"],
            }
        )
    except WindowCaptureError as exc:
        checks.append({"name": "窗口枚举", "ok": False, "detail": str(exc)})
    cap = capability()
    checks.append(
        {
            "name": "窗口捕获能力",
            "ok": True,
            "detail": "wgc=%s vendor=%s printWindow=%s" % (cap["wgcAvailable"], cap["vendor"], cap["printWindow"]),
        }
    )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}
