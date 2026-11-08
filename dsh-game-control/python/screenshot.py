"""跨平台截图封装：采集 RGB 像素、PNG/JPEG 落盘、可选 base64。

设计：
- 所有采集函数统一返回 ``(rgb_bytes, width, height)``，RGB 紧密排列（每像素 3 字节）。
- Windows 走 :mod:`win_native` 的 GDI 实现（唯一能保证物理像素精确、多屏偏移正确的路线）。
- 其它平台按 Pillow ``ImageGrab`` → ``mss`` → 平台命令（``screencapture`` / ``grim`` /
  ``scrot`` / ``import``）的顺序兜底，全部失败时给出可操作错误码与 hint。
- Pillow 只在真正需要编码/解码时懒加载：缺 Pillow 只影响截图动作，不影响 status 等只读动作。

错误码遵循 SPEC 2.4：``MISSING_DEPENDENCY`` / ``INVALID_REGION`` / ``OUT_OF_BOUNDS`` /
``PERMISSION_DENIED`` / ``FILE_IO_ERROR`` / ``BACKEND_ERROR``。
"""

from __future__ import annotations

import base64
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"


class ScreenshotError(Exception):
    """截图/编码/落盘错误；``code``/``hint`` 会被信封层原样使用。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


# ---------------------------------------------------------------------------
# 依赖与后端探测
# ---------------------------------------------------------------------------


def _pillow():
    """懒加载 Pillow；缺失时返回 MISSING_DEPENDENCY（SPEC 2.5）。"""
    try:
        from PIL import Image  # noqa: F401

        return Image
    except Exception as exc:  # pragma: no cover - 本机已安装
        raise ScreenshotError(
            "MISSING_DEPENDENCY",
            "缺少 Pillow，无法保存/编码截图（其它动作仍可用）",
            "pip install pillow",
        ) from exc


def _has_pillow() -> bool:
    try:
        import PIL.Image  # noqa: F401
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def available_backend() -> dict:
    """探测当前平台可用于截图的路线（只读，不截图）。"""
    if WINDOWS:
        try:
            import win_native

            ok = win_native.available()
            return {
                "ok": bool(ok),
                "backend": "win32-gdi",
                "detail": win_native.last_error() or "GDI BitBlt/GetDIBits",
            }
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "backend": "win32-gdi", "detail": str(exc)}
    if _has_pillow():
        return {"ok": True, "backend": "pillow-imagegrab", "detail": "Pillow ImageGrab"}
    try:
        import mss  # noqa: F401

        return {"ok": True, "backend": "mss", "detail": "mss 库"}
    except Exception:
        pass
    command = _command_backend()
    if command:
        return {"ok": True, "backend": command, "detail": "平台命令兜底"}
    return {
        "ok": False,
        "backend": "none",
        "detail": "没有可用的截图后端",
    }


def _command_backend() -> str | None:
    """返回可用的平台截图命令名。"""
    if MACOS and shutil.which("screencapture"):
        return "screencapture"
    if not MACOS:
        for name in ("grim", "scrot", "import"):
            if shutil.which(name):
                return name
    return None


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def capture(x: int, y: int, width: int, height: int) -> tuple:
    """截取物理像素区域，返回 ``(rgb_bytes, width, height)``。

    调用方（game_control）负责用 :func:`validate_region` 先做区域与虚拟桌面校验。
    """
    x, y, width, height = int(x), int(y), int(width), int(height)
    if width <= 0 or height <= 0:
        raise ScreenshotError(
            "INVALID_REGION",
            "截图宽高必须为正整数：%dx%d" % (width, height),
            "region.width / region.height 需大于 0",
        )
    if WINDOWS:
        try:
            import win_native
        except Exception as exc:  # pragma: no cover
            raise ScreenshotError(
                "UNSUPPORTED_PLATFORM", "Windows 原生截图模块不可用: %s" % exc
            ) from exc
        try:
            return win_native.capture_rgb(x, y, width, height)
        except Exception as exc:
            code = getattr(exc, "code", None) or "BACKEND_ERROR"
            raise ScreenshotError(code, str(exc), getattr(exc, "hint", None)) from exc
    return _capture_non_windows(x, y, width, height)


def _capture_non_windows(x: int, y: int, width: int, height: int) -> tuple:
    """非 Windows：Pillow ImageGrab → mss → 平台命令。"""
    errors = []
    # 1) Pillow ImageGrab（macOS / 有 X11 的 Linux）
    if _has_pillow():
        try:
            from PIL import ImageGrab

            try:
                image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
            except TypeError:
                image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
            image = image.convert("RGB")
            return image.tobytes(), image.width, image.height
        except (OSError, RuntimeError, ImportError) as exc:
            errors.append("ImageGrab: %s" % exc)
    # 2) mss
    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            shot = sct.grab({"left": x, "top": y, "width": width, "height": height})
            Image = _pillow()
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            return image.tobytes(), image.width, image.height
    except Exception as exc:  # noqa: BLE001 - 逐个兜底
        errors.append("mss: %s" % exc)
    # 3) 平台命令
    command = _command_backend()
    if command:
        try:
            return _capture_with_command(command, x, y, width, height)
        except ScreenshotError as exc:
            errors.append("%s: %s" % (command, exc))
    detail = "；".join(errors) if errors else "无可用后端"
    hint = (
        "macOS 请在 系统设置→隐私与安全性→屏幕录制 中授权终端/DSH；"
        "Linux Wayland 需要 grim；X11 可安装 scrot 或 mss：pip install mss"
    )
    if not _has_pillow() and not errors:
        hint = "pip install pillow"
    raise ScreenshotError("BACKEND_ERROR", "非 Windows 截图失败: %s" % detail, hint)


def _capture_with_command(command: str, x: int, y: int, width: int, height: int) -> tuple:
    """调用平台截图命令写入临时文件，再用 Pillow 读回 RGB。"""
    Image = _pillow()
    tmp_dir = tempfile.mkdtemp(prefix="dgc-shot-")
    tmp_path = Path(tmp_dir) / "capture.png"
    try:
        if command == "screencapture":
            args = [
                "screencapture",
                "-x",
                "-R",
                "%d,%d,%d,%d" % (x, y, width, height),
                "-t",
                "png",
                str(tmp_path),
            ]
        elif command == "grim":
            args = [
                "grim",
                "-g",
                "%d,%d %dx%d" % (x, y, width, height),
                str(tmp_path),
            ]
        elif command == "scrot":
            args = ["scrot", "-a", "%d,%d,%d,%d" % (x, y, width, height), str(tmp_path)]
        else:  # ImageMagick import
            args = [
                "import",
                "-window",
                "root",
                "-crop",
                "%dx%d+%d+%d" % (width, height, x, y),
                str(tmp_path),
            ]
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()[:300]
            raise ScreenshotError(
                "BACKEND_ERROR",
                "%s 退出码 %d: %s" % (command, result.returncode, detail or "无 stderr"),
            )
        if not tmp_path.exists():
            raise ScreenshotError("BACKEND_ERROR", "%s 未生成截图文件" % command)
        with Image.open(str(tmp_path)) as image:
            rgb = image.convert("RGB")
            return rgb.tobytes(), rgb.width, rgb.height
    except FileNotFoundError as exc:
        raise ScreenshotError("MISSING_DEPENDENCY", "截图命令不存在: %s" % command) from exc
    except subprocess.TimeoutExpired as exc:
        raise ScreenshotError("BACKEND_ERROR", "%s 截图超时" % command) from exc
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 区域校验
# ---------------------------------------------------------------------------


def validate_region(region: dict, virtual_screen: dict) -> dict:
    """校验区域参数并与虚拟桌面求交集；返回裁剪后的区域。

    返回 ``{x, y, width, height, clipped}``；``clipped`` 表示原始区域超出虚拟桌面。
    """
    if not isinstance(region, dict):
        raise ScreenshotError("INVALID_ARGUMENT", "region 必须是对象 {x,y,width,height}")
    missing = [key for key in ("x", "y", "width", "height") if key not in region]
    if missing:
        raise ScreenshotError(
            "INVALID_ARGUMENT",
            "region 缺少字段: %s" % ", ".join(missing),
            "region 需要 {x, y, width, height}",
        )
    unknown = sorted(set(region) - {"x", "y", "width", "height"})
    if unknown:
        raise ScreenshotError(
            "UNKNOWN_PARAM",
            "region 内未知字段: %s" % ", ".join(unknown),
            "region 只允许 x/y/width/height",
        )
    values = {}
    for key in ("x", "y", "width", "height"):
        value = region[key]
        # SPEC 2.3：region 的 width/height 非数字或 ≤0 → INVALID_REGION；
        # x/y 非数字同属 region 非法，同样按 INVALID_REGION 处理。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScreenshotError(
                "INVALID_REGION",
                "region.%s 必须是数字（收到 %r）" % (key, value),
                "region 需要 {x, y, width, height} 物理像素整数",
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ScreenshotError(
                "INVALID_REGION",
                "region.%s 不能是 NaN/Infinity" % key,
                "region 需要有限的物理像素整数",
            )
        values[key] = int(round(value))
    if values["width"] <= 0 or values["height"] <= 0:
        raise ScreenshotError(
            "INVALID_REGION",
            "区域宽高必须为正数：%dx%d" % (values["width"], values["height"]),
            "width/height 需大于 0（物理像素）",
        )
    try:
        vs = {
            "x": int(virtual_screen["x"]),
            "y": int(virtual_screen["y"]),
            "width": int(virtual_screen["width"]),
            "height": int(virtual_screen["height"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ScreenshotError("INTERNAL", "虚拟桌面几何不可用: %s" % exc) from exc
    left = max(values["x"], vs["x"])
    top = max(values["y"], vs["y"])
    right = min(values["x"] + values["width"], vs["x"] + vs["width"])
    bottom = min(values["y"] + values["height"], vs["y"] + vs["height"])
    if left >= right or top >= bottom:
        raise ScreenshotError(
            "OUT_OF_BOUNDS",
            "区域不在虚拟桌面内：region=(%d,%d %dx%d)，虚拟桌面=(%d,%d %dx%d)"
            % (
                values["x"],
                values["y"],
                values["width"],
                values["height"],
                vs["x"],
                vs["y"],
                vs["width"],
                vs["height"],
            ),
            "多屏时允许负坐标，请用 status 查看 virtualScreen",
        )
    clipped = (left, top, right - left, bottom - top) != (
        values["x"],
        values["y"],
        values["width"],
        values["height"],
    )
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
        "clipped": clipped,
    }


# ---------------------------------------------------------------------------
# 输出路径 / 保存 / base64
# ---------------------------------------------------------------------------


def default_filename(fmt: str = "png", now: datetime | None = None) -> str:
    """``shot_YYYYMMDD_HHMMSS_fff.(png|jpg)``。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return "shot_%s.%s" % (stamp, "jpg" if fmt == "jpeg" else "png")


def normalize_format(fmt: str | None) -> str:
    """把 format 归一化为 ``png`` / ``jpeg``；未知值抛 INVALID_ARGUMENT。"""
    value = (fmt or "png").strip().lower()
    if value in ("png",):
        return "png"
    if value in ("jpeg", "jpg"):
        return "jpeg"
    raise ScreenshotError(
        "INVALID_ARGUMENT",
        "不支持的截图格式: %r" % fmt,
        "format 只能是 png 或 jpeg",
    )


def resolve_output_path(path: str | None, shot_dir: str | os.PathLike, fmt: str) -> tuple:
    """解析输出路径，返回 ``(path_display, absolute_path)``。

    - 未给 path：在截图目录生成默认文件名；
    - 相对 path：相对截图目录；
    - 绝对 path：原样使用；
    - 无扩展名时按 format 补 ``.png`` / ``.jpg``。
    """
    suffix = ".jpg" if fmt == "jpeg" else ".png"
    if path is None or str(path).strip() == "":
        name = default_filename(fmt)
        absolute = (Path(shot_dir) / name).resolve()
        return name, absolute
    raw = os.path.expandvars(os.path.expanduser(str(path).strip()))
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(shot_dir) / candidate
        display = str(path).strip()
    else:
        display = str(candidate)
    if candidate.suffix == "":
        candidate = candidate.with_suffix(suffix)
        display = display + suffix
    return display, candidate.resolve()


def save_image(
    data: bytes,
    width: int,
    height: int,
    absolute_path: str | os.PathLike,
    fmt: str = "png",
    quality: int | None = None,
) -> dict:
    """把 RGB 原始像素编码为 PNG/JPEG 并落盘。

    返回 ``{absolutePath, width, height, format, bytes}``。
    """
    Image = _pillow()
    fmt = normalize_format(fmt)
    expected = width * height * 3
    if len(data) != expected:
        raise ScreenshotError(
            "INTERNAL",
            "截图原始数据长度异常：%d != %dx%dx3" % (len(data), width, height),
        )
    path = Path(absolute_path)
    try:
        image = Image.frombytes("RGB", (width, height), data)
    except Exception as exc:  # pragma: no cover - 正常不会发生
        raise ScreenshotError("INTERNAL", "Pillow 解码原始像素失败: %s" % exc) from exc
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "jpeg":
            # JPEG 不支持 alpha；quality 越界时钳制到 1..100
            q = 90 if quality is None else max(1, min(100, int(quality)))
            image.save(str(path), "JPEG", quality=q)
        else:
            image.save(str(path), "PNG")
    except PermissionError as exc:
        raise ScreenshotError(
            "PERMISSION_DENIED",
            "没有写入截图文件的权限: %s" % path,
            "换一个有写权限的目录，或检查文件是否被占用",
        ) from exc
    except OSError as exc:
        raise ScreenshotError(
            "FILE_IO_ERROR",
            "保存截图失败: %s (%s)" % (path, exc),
            "确认父目录可创建、磁盘空间充足",
        ) from exc
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ScreenshotError("FILE_IO_ERROR", "截图已写入但无法读取文件大小: %s" % exc) from exc
    return {
        "absolutePath": str(path.resolve()),
        "width": int(width),
        "height": int(height),
        "format": fmt,
        "bytes": int(size),
    }


def file_base64(absolute_path: str | os.PathLike) -> str:
    """读取已落盘截图并返回 base64 字符串（不含 data: 前缀）。"""
    try:
        raw = Path(absolute_path).read_bytes()
    except PermissionError as exc:
        raise ScreenshotError("PERMISSION_DENIED", "读取截图文件被拒绝: %s" % absolute_path) from exc
    except OSError as exc:
        raise ScreenshotError("FILE_IO_ERROR", "读取截图文件失败: %s" % exc) from exc
    return base64.b64encode(raw).decode("ascii")
