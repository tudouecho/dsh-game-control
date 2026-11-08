#!/usr/bin/env python3
"""DSH 游戏操作插件 —— Python 后端入口。

契约见 ``D:\\dsh-chajian\\SPEC.md`` 第 2 节。本文件负责：

1. 运行模式：``--json`` / ``--stdin`` / ``--serve`` / ``--selftest`` / ``--version``；
2. JSON 行协议：请求 ``{"id":1,"action":"...","params":{...}}``，响应信封见 SPEC 2.2；
3. 动作分发（SPEC 2.3）、参数校验、错误码（SPEC 2.4）；
4. 日志与审计：``<GAME_CONTROL_HOME>/logs/game-control.log``（滚动 2MB×3）+
   ``<GAME_CONTROL_HOME>/logs/actions.jsonl``（不记录 type_text 原文）；
5. 环境变量：``GAME_CONTROL_HOME`` / ``SHOT_DIR`` / ``LOG_DIR``。

worker（``--serve``）模式下 stdout **只输出单行 JSON**；日志与诊断一律走 stderr 或文件。
本文件不 import 任何联网库（socket/urllib/http/requests/websocket）。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# 基础常量 / 自身目录
# ---------------------------------------------------------------------------

BACKEND_VERSION = "1.0.0"
PROTOCOL_VERSION = 1

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

JSON_SEPARATORS = (",", ":")
LOG_MAX_BYTES = 2 * 1024 * 1024  # 单文件 2MB
LOG_BACKUP_COUNT = 3

# 发送给调用方的错误码白名单（SPEC 2.4）
ERROR_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "UNKNOWN_ACTION",
        "UNKNOWN_PARAM",
        "INVALID_KEY",
        "INVALID_REGION",
        "OUT_OF_BOUNDS",
        "WINDOW_NOT_FOUND",
        "WINDOW_CAPTURE_FAILED",
        "WINDOW_ACTIVATION_FAILED",
        "TEMPLATE_NOT_FOUND",
        "INVALID_TEMPLATE",
        "SKILL_NOT_FOUND",
        "INVALID_SKILL",
        "SKILL_VAR_UNRESOLVED",
        "MISSING_DEPENDENCY",
        "UNSUPPORTED_PLATFORM",
        "PERMISSION_DENIED",
        "FILE_IO_ERROR",
        "BACKEND_ERROR",
        "INTERNAL",
    }
)

# 按键规范名（不含单字符键）；别名解析见 ALIASES
NAMED_KEYS = frozenset(
    "enter esc tab space backspace delete insert home end pageup pagedown "
    "up down left right ctrl control alt shift win meta caps_lock num_lock "
    "scroll_lock print_screen pause menu".split()
) | frozenset("f%d" % i for i in range(1, 25))

KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "cmd": "win",
    "command": "win",
    "super": "win",
}

LETTER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz")
DIGIT_CHARS = frozenset("0123456789")
PUNCT_CHARS = frozenset("-=[];',./`")
SINGLE_CHARS = LETTER_CHARS | DIGIT_CHARS | PUNCT_CHARS

BUTTONS = ("left", "right", "middle", "x1", "x2")
FORMATS = ("png", "jpeg")
WINDOW_ENGINES = ("auto", "wgc", "printwindow", "screen")
CIRCLE_DIRECTIONS = ("cw", "ccw")
CIRCLE_BUTTONS = ("none", "left", "right", "middle")
WAIT_IMAGE_MODES = ("appear", "disappear")


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------


class GCError(Exception):
    """后端业务错误；``code``/``hint`` 会进入响应信封。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


class UsageError(Exception):
    """命令行参数错误（退出码 2）。"""


# ---------------------------------------------------------------------------
# 路径配置（GAME_CONTROL_HOME / SHOT_DIR / LOG_DIR）
# ---------------------------------------------------------------------------


def _expand(raw: str) -> Path:
    """展开 ``~`` / 环境变量后转成绝对路径。"""
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _default_home() -> Path:
    """``GAME_CONTROL_HOME`` 默认值：Windows ``%USERPROFILE%\\.dsh\\game-control``。"""
    if IS_WINDOWS:
        base = os.environ.get("USERPROFILE") or str(Path.home())
    else:
        base = os.environ.get("HOME") or str(Path.home())
    return Path(os.path.expanduser(base)) / ".dsh" / "game-control"


class Config:
    """运行期目录配置。目录创建失败不致命（日志降级到 stderr）。"""

    def __init__(self) -> None:
        home_env = os.environ.get("GAME_CONTROL_HOME", "").strip()
        self.home = _expand(home_env) if home_env else _default_home()
        shot_env = os.environ.get("SHOT_DIR", "").strip()
        log_env = os.environ.get("LOG_DIR", "").strip()
        self.shot_dir = _expand(shot_env) if shot_env else self.home / "shots"
        self.log_dir = _expand(log_env) if log_env else self.home / "logs"
        # v0.3：模板库 / 技能库（SPEC 8.2.4 / 8.3.1）
        self.templates_dir = self.home / "templates"
        self.skills_dir = self.home / "skills"
        self.log_file = self.log_dir / "game-control.log"
        self.audit_file = self.log_dir / "actions.jsonl"

    def as_dict(self) -> dict:
        return {
            "home": str(self.home),
            "shotDir": str(self.shot_dir),
            "logDir": str(self.log_dir),
            "templatesDir": str(self.templates_dir),
            "skillsDir": str(self.skills_dir),
        }


# ---------------------------------------------------------------------------
# 日志（单文件 2MB×3 滚动，自行实现以避免引入 logging.handlers 的 socket 依赖）
# ---------------------------------------------------------------------------


class _RotatingFileHandler(logging.FileHandler):
    """极简按大小滚动：写前检查文件大小，超过阈值时把 .1→.2→.3 依次后移。"""

    def __init__(self, filename, max_bytes=LOG_MAX_BYTES, backup_count=LOG_BACKUP_COUNT, encoding="utf-8"):
        self.max_bytes = int(max_bytes)
        self.backup_count = int(backup_count)
        super().__init__(filename, mode="a", encoding=encoding, delay=False)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename) and os.path.getsize(self.baseFilename) >= self.max_bytes:
                self._rotate()
        except OSError:
            pass
        super().emit(record)

    def _rotate(self):
        """关闭 → 滚动备份 → 重新打开。"""
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
        except OSError:
            pass
        base = self.baseFilename
        for index in range(self.backup_count - 1, 0, -1):
            src = "%s.%d" % (base, index)
            dst = "%s.%d" % (base, index + 1)
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        if os.path.exists(base):
            try:
                os.replace(base, base + ".1")
            except OSError:
                pass
        try:
            self.stream = self._open()
        except OSError:
            self.stream = None


def setup_logging(config: Config, mode: str) -> logging.Logger:
    """初始化日志：文件 handler 必选（失败降级），stderr 按模式选择级别。"""
    logger = logging.getLogger("game_control")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stderr_level = logging.WARNING if mode in ("serve", "stdin") else logging.INFO
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = _RotatingFileHandler(str(config.log_file))
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("日志文件不可写（%s），仅输出到 stderr: %s", config.log_file, exc)
    return logger


class Audit:
    """动作审计日志：每行一个 JSON，不记录 type_text 原文（只记 length）。"""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def record(self, action: str, ok: bool, duration_ms: float, code: str | None = None, **extra) -> None:
        entry = {
            "ts": iso_timestamp(),
            "action": action,
            "ok": bool(ok),
            "durationMs": int(round(duration_ms)),
        }
        if not ok and code:
            entry["code"] = code
        for key, value in extra.items():
            if value is not None:
                entry[key] = value
        try:
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config.audit_file, "a", encoding="utf-8") as handle:
                handle.write(_json_line(entry))
                handle.write("\n")
        except OSError as exc:  # 审计失败不影响动作本身
            self.logger.warning("写入 actions.jsonl 失败: %s", exc)


# ---------------------------------------------------------------------------
# JSON / 时间工具
# ---------------------------------------------------------------------------


def iso_timestamp(now: datetime | None = None) -> str:
    """UTC ISO8601 毫秒精度，形如 ``2026-11-08T08:00:00.123Z``。"""
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_line(value: dict) -> str:
    """序列化为单行 JSON（worker stdout 的唯一允许格式）。"""
    return json.dumps(value, ensure_ascii=False, separators=JSON_SEPARATORS)


class Emitter:
    """stdout 单行 JSON 输出器；UTF-8 + 立即 flush。"""

    def __init__(self) -> None:
        # Host 以 UTF-8 读取 stdout/stderr，统一改成 UTF-8，避免中文日志在管道里乱码
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

    def emit(self, value: dict) -> None:
        sys.stdout.write(_json_line(value) + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# 参数规格与校验
# ---------------------------------------------------------------------------

_UNSET = object()


def P(
    kind: str,
    required: bool = False,
    default=_UNSET,
    minimum=None,
    maximum=None,
    enum=None,
    min_items=None,
    max_items=None,
    max_length=None,
) -> dict:
    """构造一个参数规则。"""
    return {
        "kind": kind,
        "required": required,
        "default": default,
        "min": minimum,
        "max": maximum,
        "enum": enum,
        "minItems": min_items,
        "maxItems": max_items,
        "maxLength": max_length,
    }


# 动作参数表：与 SPEC 2.3 逐字对齐。未列出的 params 字段一律 UNKNOWN_PARAM。
ACTION_PARAMS = {
    "status": {},
    "mouse_move": {
        "x": P("coord", required=True),
        "y": P("coord", required=True),
        "durationMs": P("int", default=0, minimum=0, maximum=5000),
    },
    "mouse_move_rel": {
        "dx": P("coord", required=True),
        "dy": P("coord", required=True),
    },
    "mouse_click": {
        "x": P("coord"),
        "y": P("coord"),
        "button": P("enum", default="left", enum=BUTTONS),
        "clicks": P("int", default=1, minimum=1, maximum=10),
        "intervalMs": P("int", default=60, minimum=0, maximum=1000),
    },
    "mouse_down": {
        "button": P("enum", default="left", enum=BUTTONS),
        "x": P("coord"),
        "y": P("coord"),
    },
    "mouse_up": {
        "button": P("enum", default="left", enum=BUTTONS),
        "x": P("coord"),
        "y": P("coord"),
    },
    "mouse_drag": {
        "x1": P("coord", required=True),
        "y1": P("coord", required=True),
        "x2": P("coord", required=True),
        "y2": P("coord", required=True),
        "button": P("enum", default="left", enum=BUTTONS),
        "durationMs": P("int", default=300, minimum=0, maximum=60000),
        "steps": P("int", default=20, minimum=2, maximum=200),
    },
    "mouse_scroll": {
        "amount": P("int", required=True, minimum=-1000, maximum=1000),
        "x": P("coord"),
        "y": P("coord"),
        "horizontal": P("bool", default=False),
    },
    "mouse_position": {},
    "key_press": {
        "key": P("key", required=True),
        "presses": P("int", default=1, minimum=1, maximum=20),
        "intervalMs": P("int", default=60, minimum=0, maximum=1000),
    },
    "key_down": {"key": P("key", required=True)},
    "key_up": {"key": P("key", required=True)},
    "hotkey": {"keys": P("keys", required=True, min_items=1, max_items=4)},
    "type_text": {
        "text": P("str", required=True, max_length=10000),
        "intervalMs": P("int", default=12, minimum=0, maximum=200),
    },
    "screenshot": {
        "region": P("region"),
        "monitor": P("int", minimum=1),
        "allScreens": P("bool", default=False),
        "path": P("str", max_length=4096),
        "format": P("format", default="png"),
        "quality": P("int", minimum=1, maximum=100),
        "base64": P("bool", default=False),
    },
    "list_displays": {},
    "list_windows": {
        "titleContains": P("str", max_length=512),
        "includeInvisible": P("bool", default=False),
        "limit": P("int", default=50, minimum=1, maximum=100),
    },
    "capture_window": {
        "window": P("str", max_length=512),
        "hwnd": P("int"),
        "engine": P("enum", default="auto", enum=WINDOW_ENGINES),
        "includeCursor": P("bool", default=False),
        "timeoutMs": P("int", default=3000, minimum=500, maximum=10000),
        "path": P("str", max_length=4096),
        "format": P("format", default="png"),
        "quality": P("int", minimum=1, maximum=100),
        "base64": P("bool", default=False),
    },
    # ---- v0.3 游戏自动化动作（SPEC 8.2） ----
    "activate_window": {
        "window": P("str", max_length=512),
        "hwnd": P("int"),
        "restore": P("bool", default=True),
        "timeoutMs": P("int", default=2000, minimum=100, maximum=5000),
    },
    "mouse_circle": {
        "centerX": P("coord", required=True),
        "centerY": P("coord", required=True),
        "radius": P("int", required=True, minimum=1, maximum=5000),
        "periodMs": P("int", default=2500, minimum=300, maximum=20000),
        "durationMs": P("int", default=30000, minimum=100, maximum=120000),
        "direction": P("enum", default="cw", enum=CIRCLE_DIRECTIONS),
        "startAngleDeg": P("number", default=-90.0),
        "samplesPerSecond": P("int", default=120, minimum=30, maximum=240),
        "leadInMs": P("int", default=300, minimum=0, maximum=2000),
        "button": P("enum", default="none", enum=CIRCLE_BUTTONS),
        "abortKey": P("str", default="esc", max_length=64),
        "hwnd": P("int"),
        "dryRun": P("bool", default=False),
    },
    "wait": {
        "durationMs": P("int", required=True, minimum=0, maximum=600000),
    },
    "find_image": {
        "template": P("str", max_length=4096),
        "templates": P("templates"),
        "region": P("region"),
        "window": P("str", max_length=512),
        "hwnd": P("int"),
        "threshold": P("number", default=0.85, minimum=0.0, maximum=1.0),
        "scales": P("scales", default=None),
        "grayscale": P("bool", default=True),
        "maxResults": P("int", default=3, minimum=1, maximum=20),
        "debugPath": P("str", max_length=4096),
        "abortKey": P("str", default="esc", max_length=64),
    },
    "wait_image": {
        "template": P("str", max_length=4096),
        "templates": P("templates"),
        "region": P("region"),
        "window": P("str", max_length=512),
        "hwnd": P("int"),
        "threshold": P("number", default=0.85, minimum=0.0, maximum=1.0),
        "scales": P("scales", default=None),
        "grayscale": P("bool", default=True),
        "maxResults": P("int", default=3, minimum=1, maximum=20),
        "debugPath": P("str", max_length=4096),
        "timeoutMs": P("int", default=15000, minimum=100, maximum=600000),
        "intervalMs": P("int", default=250, minimum=50, maximum=2000),
        "mode": P("enum", default="appear", enum=WAIT_IMAGE_MODES),
        "abortKey": P("str", default="esc", max_length=64),
    },
    # ---- v0.3 技能库（SPEC 8.3.2） ----
    "skill_list": {},
    "skill_get": {
        "id": P("str", required=True, max_length=64),
    },
    "skill_save": {
        "title": P("str", max_length=200),
        "description": P("str", max_length=2000),
        "game": P("str", max_length=100),
        "tags": P("tags"),
        "markdown": P("str", max_length=200000),
        "steps": P("steps"),
        "id": P("str", max_length=64),
        # v0.4（SPEC 9.3）：整份 JSONC 文本（含中文注释）优先于结构化字段
        "content": P("str", max_length=1_000_000),
    },
    "skill_versions": {
        "id": P("str", required=True, max_length=64),
    },
    "skill_restore": {
        "id": P("str", required=True, max_length=64),
        "backup": P("str", required=True, max_length=128),
    },
    "skill_delete": {
        "id": P("str", required=True, max_length=64),
        "hard": P("bool", default=False),
    },
    "skill_run": {
        "id": P("str", required=True, max_length=64),
        "dryRun": P("bool", default=False),
        "repeat": P("int", default=1, minimum=1, maximum=100),
        "timeoutMs": P("int", default=300000, minimum=1000, maximum=600000),
        "abortKey": P("str", default="esc", max_length=64),
        "stopOnError": P("bool", default=True),
    },
    # v0.5：读取 skill_run 进度文件（Host /exec 运行期间也可查）
    "skill_progress": {},
}

# 技能单步允许的控制动作：除 skill_* 外的全部动作（SPEC 8.3.2）
SKILL_STEP_ACTIONS = tuple(sorted(name for name in ACTION_PARAMS if not name.startswith("skill_")))


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_value(name: str, value, rule: dict):
    """按规则校验并归一化单个参数；失败抛 INVALID_ARGUMENT。"""
    kind = rule["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是布尔值" % name, hint="传 true / false")
        return value
    if kind in ("coord", "int"):
        if not _is_number(value):
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 必须是数字" % name,
                hint="坐标请传物理像素整数，例如 100",
            )
        if isinstance(value, float) and value != value:  # NaN
            raise GCError("INVALID_ARGUMENT", "参数 %s 不能是 NaN" % name)
        number = int(round(value))
        if kind == "int":
            if rule.get("min") is not None and number < rule["min"]:
                raise GCError(
                    "INVALID_ARGUMENT",
                    "参数 %s 不能小于 %s（收到 %s）" % (name, rule["min"], number),
                )
            if rule.get("max") is not None and number > rule["max"]:
                raise GCError(
                    "INVALID_ARGUMENT",
                    "参数 %s 不能大于 %s（收到 %s）" % (name, rule["max"], number),
                )
        return number
    if kind == "str":
        if not isinstance(value, str):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是字符串" % name)
        if rule.get("maxLength") is not None and len(value) > rule["maxLength"]:
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 长度 %d 超过上限 %d" % (name, len(value), rule["maxLength"]),
            )
        return value
    if kind == "key":
        if not isinstance(value, str):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是字符串（键名）" % name)
        return value
    if kind == "keys":
        if not isinstance(value, list):
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 必须是数组，例如 [\"ctrl\",\"shift\",\"s\"]" % name,
            )
        if rule.get("minItems") is not None and len(value) < rule["minItems"]:
            raise GCError("INVALID_ARGUMENT", "参数 %s 至少需要 %d 个键" % (name, rule["minItems"]))
        if rule.get("maxItems") is not None and len(value) > rule["maxItems"]:
            raise GCError("INVALID_ARGUMENT", "参数 %s 最多 %d 个键" % (name, rule["maxItems"]))
        for index, item in enumerate(value):
            if not isinstance(item, str) or item.strip() == "":
                raise GCError(
                    "INVALID_ARGUMENT",
                    "参数 %s[%d] 必须是非空字符串键名" % (name, index),
                )
        return list(value)
    if kind == "enum":
        allowed = rule.get("enum") or ()
        if not isinstance(value, str) or value not in allowed:
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 取值非法: %r" % (name, value),
                hint="可选值: %s" % ", ".join(allowed),
            )
        return value
    if kind == "format":
        normalized = str(value).strip().lower()
        if normalized not in FORMATS:
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 取值非法: %r" % (name, value),
                hint="可选值: png, jpeg",
            )
        return normalized
    if kind == "region":
        if not isinstance(value, dict):
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 必须是对象 {x,y,width,height}" % name,
                hint='例如 {"x":0,"y":0,"width":800,"height":600}',
            )
        return value
    if kind == "number":
        if not _is_number(value):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是数字" % name)
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise GCError("INVALID_ARGUMENT", "参数 %s 不能是 NaN/Infinity" % name)
        if rule.get("min") is not None and number < rule["min"]:
            raise GCError("INVALID_ARGUMENT", "参数 %s 不能小于 %s（收到 %s）" % (name, rule["min"], number))
        if rule.get("max") is not None and number > rule["max"]:
            raise GCError("INVALID_ARGUMENT", "参数 %s 不能大于 %s（收到 %s）" % (name, rule["max"], number))
        return number
    if kind == "scales":
        # find_image / wait_image 的多尺度比例：1–5 个正数
        if not isinstance(value, list):
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 必须是数组，例如 [0.8, 1.0, 1.25]" % name,
            )
        if len(value) < 1 or len(value) > 5:
            raise GCError("INVALID_ARGUMENT", "参数 %s 需要 1–5 个缩放比例" % name)
        normalized = []
        for index, item in enumerate(value):
            if not _is_number(item):
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 必须是数字" % (name, index))
            scale = float(item)
            if scale != scale or scale <= 0.05 or scale > 10:
                raise GCError(
                    "INVALID_ARGUMENT",
                    "参数 %s[%d] 必须在 (0.05, 10] 内（收到 %s）" % (name, index, item),
                )
            normalized.append(scale)
        return normalized
    if kind == "templates":
        # find_image / wait_image 的多模板路径列表（1–8 个非空字符串，SPEC 10.1/10.3）
        if not isinstance(value, list):
            raise GCError(
                "INVALID_ARGUMENT",
                "参数 %s 必须是数组（模板路径列表）" % name,
                hint='例如 "templates": ["a.png","b.png"]',
            )
        if len(value) < 1 or len(value) > 8:
            raise GCError("INVALID_ARGUMENT", "参数 %s 需要 1–8 个模板路径（收到 %d）" % (name, len(value)))
        normalized = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or item.strip() == "":
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 必须是非空字符串路径" % (name, index))
            if len(item) > 4096:
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 路径过长" % (name, index))
            normalized.append(item)
        return normalized
    if kind == "tags":
        if not isinstance(value, list):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是数组" % name)
        if len(value) > 20:
            raise GCError("INVALID_ARGUMENT", "参数 %s 最多 20 个标签" % name)
        normalized = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 必须是字符串" % (name, index))
            if len(item) > 40:
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 长度不能超过 40" % (name, index))
            normalized.append(item)
        return normalized
    if kind == "steps":
        # 仅做类型层校验；数量/字段/体积等细节在 skills.SkillsStore 里统一报 INVALID_SKILL
        if not isinstance(value, list):
            raise GCError("INVALID_ARGUMENT", "参数 %s 必须是数组（步骤列表）" % name)
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise GCError("INVALID_ARGUMENT", "参数 %s[%d] 必须是对象" % (name, index))
        return value
    raise GCError("INTERNAL", "未实现的参数类型: %s" % kind)


def validate_params(action: str, params: dict) -> dict:
    """校验并补齐默认值；未知字段返回 UNKNOWN_PARAM。"""
    spec = ACTION_PARAMS.get(action)
    if spec is None:
        raise GCError("UNKNOWN_ACTION", "未知 action: %s" % action)
    if not isinstance(params, dict):
        raise GCError("INVALID_ARGUMENT", "params 必须是 JSON 对象")
    unknown = [key for key in params if key not in spec]
    if unknown:
        raise GCError(
            "UNKNOWN_PARAM",
            "action %s 不支持参数: %s" % (action, ", ".join(sorted(unknown))),
            hint="可用参数: %s" % (", ".join(sorted(spec)) or "（无）"),
        )
    validated = {}
    for name, rule in spec.items():
        if name in params and params[name] is None and not rule["required"]:
            # 显式 null 视为“未传”，避免前端/工具把可选参数传成 null 时报错
            pass
        elif name in params:
            validated[name] = _coerce_value(name, params[name], rule)
            continue
        if rule["required"] and name not in params:
            raise GCError(
                "INVALID_ARGUMENT",
                "缺少必需参数: %s" % name,
                hint="action %s 需要参数: %s"
                % (action, ", ".join(sorted(k for k, r in spec.items() if r["required"]))),
            )
        if rule["default"] is not _UNSET:
            validated[name] = rule["default"]
    return validated


def normalize_key(raw) -> str:
    """键名规范化：大小写不敏感、别名映射；非法返回 INVALID_KEY。"""
    if not isinstance(raw, str):
        raise GCError("INVALID_ARGUMENT", "键名必须是字符串")
    key = raw.strip()
    if not key:
        raise GCError("INVALID_KEY", "键名不能为空", hint="例如 a / enter / ctrl")
    lowered = key.lower()
    if lowered in KEY_ALIASES:
        lowered = KEY_ALIASES[lowered]
    if lowered in NAMED_KEYS:
        return lowered
    if len(key) == 1:
        char = key
        if char in SINGLE_CHARS:
            return char
        if char.lower() in LETTER_CHARS:
            return char.lower()
    raise GCError(
        "INVALID_KEY",
        "未知键名: %r" % raw,
        hint="可用键名见 SPEC 2.3（a-z、0-9、enter/esc/tab/…、f1-f24、ctrl/alt/shift/win 等）",
    )


# ---------------------------------------------------------------------------
# 运行时
# ---------------------------------------------------------------------------


class Runtime:
    """一次进程内的后端运行时：目录配置、日志、DPI、平台输入/几何实现。"""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.audit = Audit(config, logger)
        # 平台实现：Windows 用 win_native，其它平台用 input_backend（接口同构）
        if IS_WINDOWS:
            import win_native as input_impl  # 延迟导入，避免非 Windows 加载 DLL
        else:
            import input_backend as input_impl
        self.input_impl = input_impl
        self.dpi_state = {"aware": False, "mode": "none"}
        try:
            self.dpi_state = input_impl.setup_dpi_awareness() if IS_WINDOWS else input_impl.setup()
        except Exception as exc:  # noqa: BLE001 - DPI 失败不致命
            logger.warning("设置 DPI 感知失败: %s", exc)
        try:
            self.backend_name = input_impl.backend_name()
        except Exception:  # noqa: BLE001
            self.backend_name = "unavailable"
        self.platform_name = _platform_name()
        self._skills_store = None
        # v0.6：skill_run 的总 deadline（time.monotonic() 秒）；None 表示不在技能运行中
        self.skill_deadline = None
        # 启动时打印一次配置，排障用
        logger.info(
            "runtime ready: platform=%s python=%s backend=%s dpi=%s shotDir=%s logDir=%s templatesDir=%s skillsDir=%s",
            self.platform_name,
            platform.python_version(),
            self.backend_name,
            self.dpi_state.get("mode"),
            self.config.shot_dir,
            self.config.log_dir,
            self.config.templates_dir,
            self.config.skills_dir,
        )

    # -- 平台几何 -------------------------------------------------------
    def get_geometry(self) -> dict:
        """虚拟桌面 / 主屏 / 显示器 / 指针（Windows 走 win_native，其它平台尽力而为）。"""
        return self.input_impl.get_geometry()

    def get_pointer(self) -> dict:
        return self.input_impl.get_pointer()

    # -- 技能库（v0.3） -------------------------------------------------
    def get_skills_store(self):
        """懒加载技能库读写对象（``skills.SkillsStore``）。"""
        if self._skills_store is None:
            import skills

            self._skills_store = skills.SkillsStore(self.config.skills_dir, SKILL_STEP_ACTIONS)
        return self._skills_store


def _platform_name() -> str:
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def _wrap_exception(exc: BaseException, action: str, logger: logging.Logger) -> GCError:
    """把任意异常转换成 GCError；未识别异常记日志并返回 INTERNAL。"""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in ERROR_CODES:
        return GCError(code, str(exc) or code, getattr(exc, "hint", None))
    if isinstance(exc, PermissionError):
        return GCError(
            "PERMISSION_DENIED",
            "系统拒绝访问: %s" % exc,
            "检查文件权限 / 屏幕录制授权 / 是否以管理员身份运行",
        )
    if isinstance(exc, GCError):
        return exc
    logger.error("action %s 未捕获异常:\n%s", action, traceback.format_exc())
    summary = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return GCError(
        "INTERNAL",
        "内部错误（%s）: %s" % (type(exc).__name__, summary[:300]),
        "查看日志 <GAME_CONTROL_HOME>/logs/game-control.log",
    )


# ---------------------------------------------------------------------------
# 动作实现
# ---------------------------------------------------------------------------


def _move_before_if_given(params: dict, runtime: Runtime) -> None:
    """mouse_click / down / up / scroll 的可选 x,y：必须成对出现，先移动。"""
    has_x = "x" in params
    has_y = "y" in params
    if has_x != has_y:
        raise GCError(
            "INVALID_ARGUMENT",
            "x 与 y 必须同时提供",
            hint="只移动一个轴请用 mouse_move_rel",
        )
    if has_x:
        runtime.input_impl.move_to(params["x"], params["y"])


def _window_capture_capability() -> dict:
    """status 的 windowCapture 能力字段（v0.2）；异常时降级而不是让 status 失败。"""
    try:
        import window_capture

        return window_capture.capability()
    except Exception as exc:  # noqa: BLE001
        return {
            "wgcAvailable": False,
            "vendor": False,
            "printWindow": False,
            "note": "窗口捕获模块不可用: %s" % str(exc)[:300],
        }


def _numpy_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("numpy") is not None
    except Exception:  # noqa: BLE001
        return False


def _automation_capability() -> dict:
    """status.automation（v0.3 可选能力字段）。"""
    return {
        "mouseCircle": IS_WINDOWS,
        "findImage": _numpy_available(),
        "skills": True,
    }


def action_status(params: dict, runtime: Runtime) -> dict:
    geometry = runtime.get_geometry()
    return {
        "platform": runtime.platform_name,
        "pythonVersion": platform.python_version(),
        "backend": runtime.backend_name,
        "dpiAware": bool(runtime.dpi_state.get("aware")),
        "virtualScreen": geometry["virtualScreen"],
        "primary": geometry["primary"],
        "displays": geometry["displays"],
        "pointer": geometry["pointer"],
        "shotDir": str(runtime.config.shot_dir),
        "logDir": str(runtime.config.log_dir),
        # v0.2：窗口捕获能力（WGC vendor 是否可用、PrintWindow 是否支持）
        "windowCapture": _window_capture_capability(),
        # v0.3：游戏自动化能力（圆形轨迹 / 模板匹配 / 技能库）
        "automation": _automation_capability(),
    }


def action_mouse_move(params: dict, runtime: Runtime) -> dict:
    impl = runtime.input_impl
    x, y = params["x"], params["y"]
    duration = params["durationMs"]
    if duration <= 0:
        impl.move_to(x, y)
    else:
        start = impl.get_pointer()
        x0, y0 = int(start.get("x", x)), int(start.get("y", y))
        steps = max(2, min(120, int(duration / 10)))
        began = time.perf_counter()
        for index in range(1, steps + 1):
            t = index / steps
            impl.move_to(round(x0 + (x - x0) * t), round(y0 + (y - y0) * t))
            due = began + duration / 1000.0 * t
            remain = due - time.perf_counter()
            if remain > 0:
                time.sleep(remain)
    return impl.get_pointer()


def action_mouse_move_rel(params: dict, runtime: Runtime) -> dict:
    runtime.input_impl.move_rel(params["dx"], params["dy"])
    return runtime.input_impl.get_pointer()


def action_mouse_click(params: dict, runtime: Runtime) -> dict:
    impl = runtime.input_impl
    _move_before_if_given(params, runtime)
    impl.click(params["button"], params["clicks"], params["intervalMs"])
    pointer = impl.get_pointer()
    return {
        "x": pointer["x"],
        "y": pointer["y"],
        "button": params["button"],
        "clicks": params["clicks"],
    }


def action_mouse_down(params: dict, runtime: Runtime) -> dict:
    impl = runtime.input_impl
    _move_before_if_given(params, runtime)
    impl.button_down(params["button"])
    pointer = impl.get_pointer()
    return {"x": pointer["x"], "y": pointer["y"], "button": params["button"]}


def action_mouse_up(params: dict, runtime: Runtime) -> dict:
    impl = runtime.input_impl
    _move_before_if_given(params, runtime)
    impl.button_up(params["button"])
    pointer = impl.get_pointer()
    return {"x": pointer["x"], "y": pointer["y"], "button": params["button"]}


def action_mouse_drag(params: dict, runtime: Runtime) -> dict:
    runtime.input_impl.drag(
        params["x1"],
        params["y1"],
        params["x2"],
        params["y2"],
        params["button"],
        params["durationMs"],
        params["steps"],
    )
    return {
        "x1": params["x1"],
        "y1": params["y1"],
        "x2": params["x2"],
        "y2": params["y2"],
        "button": params["button"],
        "durationMs": params["durationMs"],
    }


def action_mouse_scroll(params: dict, runtime: Runtime) -> dict:
    impl = runtime.input_impl
    _move_before_if_given(params, runtime)
    impl.scroll(params["amount"], params["horizontal"])
    pointer = impl.get_pointer()
    return {
        "amount": params["amount"],
        "horizontal": params["horizontal"],
        "x": pointer["x"],
        "y": pointer["y"],
    }


def action_mouse_position(params: dict, runtime: Runtime) -> dict:
    return runtime.input_impl.get_pointer()


def action_key_press(params: dict, runtime: Runtime) -> dict:
    key = normalize_key(params["key"])
    runtime.input_impl.key_press(key, params["presses"], params["intervalMs"])
    return {"key": key, "presses": params["presses"]}


def action_key_down(params: dict, runtime: Runtime) -> dict:
    key = normalize_key(params["key"])
    runtime.input_impl.key_down(key)
    return {"key": key}


def action_key_up(params: dict, runtime: Runtime) -> dict:
    key = normalize_key(params["key"])
    runtime.input_impl.key_up(key)
    return {"key": key}


def action_hotkey(params: dict, runtime: Runtime) -> dict:
    keys = [normalize_key(item) for item in params["keys"]]
    runtime.input_impl.hotkey(keys)
    return {"keys": keys}


def action_type_text(params: dict, runtime: Runtime) -> dict:
    text = params["text"]
    runtime.input_impl.type_text(text, params["intervalMs"])
    # 故意不返回原文（SPEC 2.6：审计日志也不记录 type_text 原文）
    return {"length": len(text)}


def action_screenshot(params: dict, runtime: Runtime) -> dict:
    import screenshot  # 延迟导入：Pillow 缺失只影响截图动作

    geometry = runtime.get_geometry()
    virtual_screen = geometry["virtualScreen"]
    primary = geometry["primary"]
    displays = geometry["displays"]

    region = params.get("region")
    monitor = params.get("monitor")
    all_screens = bool(params.get("allScreens", False))

    if region is not None and (monitor is not None or all_screens):
        raise GCError(
            "INVALID_ARGUMENT",
            "region 不能与 monitor / allScreens 同时使用",
            hint="三选一：region（区域）、monitor（单个显示器）、allScreens（整个虚拟桌面）",
        )
    if monitor is not None and all_screens:
        raise GCError(
            "INVALID_ARGUMENT",
            "monitor 不能与 allScreens 同时使用",
            hint="选择单个显示器请用 monitor，全部显示器请用 allScreens",
        )

    if region is not None:
        rect = screenshot.validate_region(region, virtual_screen)
    elif all_screens:
        rect = {
            "x": virtual_screen["x"],
            "y": virtual_screen["y"],
            "width": virtual_screen["width"],
            "height": virtual_screen["height"],
        }
    elif monitor is not None:
        if monitor < 1 or monitor > len(displays):
            raise GCError(
                "INVALID_ARGUMENT",
                "monitor 超出范围: %d（共 %d 个显示器）" % (monitor, len(displays)),
                hint="monitor 从 1 开始，用 list_displays 查看可用编号",
            )
        display = displays[monitor - 1]
        rect = {
            "x": display["x"],
            "y": display["y"],
            "width": display["width"],
            "height": display["height"],
        }
    else:
        rect = {
            "x": primary["x"],
            "y": primary["y"],
            "width": primary["width"],
            "height": primary["height"],
        }

    if rect["width"] <= 0 or rect["height"] <= 0:
        raise GCError("INVALID_REGION", "截图区域尺寸非法: %dx%d" % (rect["width"], rect["height"]))

    fmt = params.get("format", "png")
    data, width, height = screenshot.capture(rect["x"], rect["y"], rect["width"], rect["height"])
    display_path, absolute_path = screenshot.resolve_output_path(
        params.get("path"), runtime.config.shot_dir, fmt
    )
    saved = screenshot.save_image(
        data,
        width,
        height,
        absolute_path,
        fmt=fmt,
        quality=params.get("quality"),
    )
    result = {
        "path": display_path,
        "absolutePath": saved["absolutePath"],
        "width": saved["width"],
        "height": saved["height"],
        "format": saved["format"],
        "bytes": saved["bytes"],
    }
    if params.get("base64", False):
        encoded = screenshot.file_base64(saved["absolutePath"])
        result["base64"] = encoded
        result["base64Length"] = len(encoded)
    runtime.logger.info(
        "screenshot -> %s (%dx%d, %s, %d bytes)",
        saved["absolutePath"],
        saved["width"],
        saved["height"],
        saved["format"],
        saved["bytes"],
    )
    return result


def action_list_displays(params: dict, runtime: Runtime) -> dict:
    geometry = runtime.get_geometry()
    return {"displays": geometry["displays"]}


# ---------------------------------------------------------------------------
# v0.2 窗口枚举 / 后台窗口截图（SPEC 2.3.1）
# ---------------------------------------------------------------------------


def _select_window_params(params: dict, runtime: Runtime) -> dict:
    """校验 ``window`` / ``hwnd`` 二选一并解析出窗口信息。"""
    import window_capture

    window_text = params.get("window")
    hwnd = params.get("hwnd")
    has_window = isinstance(window_text, str) and window_text.strip() != ""
    has_hwnd = hwnd is not None
    if has_window and has_hwnd:
        raise GCError(
            "INVALID_ARGUMENT",
            "window 与 hwnd 必须二选一，不能同时传",
            hint='例如 {"hwnd":12345} 或 {"window":"游戏标题子串"}',
        )
    if not has_window and not has_hwnd:
        raise GCError(
            "INVALID_ARGUMENT",
            "需要 window（标题子串）或 hwnd（窗口句柄）之一",
            hint="先用 list_windows 查看窗口列表",
        )
    if has_hwnd:
        handle = int(hwnd)
        if handle <= 0:
            raise GCError(
                "INVALID_ARGUMENT",
                "hwnd 必须是正整数（收到 %s）" % hwnd,
                hint="用 list_windows 查看窗口的 hwnd",
            )
        return window_capture.resolve_window(hwnd=handle)
    return window_capture.resolve_window(window=window_text.strip())


def action_list_windows(params: dict, runtime: Runtime) -> dict:
    import window_capture

    listing = window_capture.list_windows(
        title_contains=params.get("titleContains"),
        include_invisible=bool(params.get("includeInvisible", False)),
        limit=int(params.get("limit", 50)),
    )
    runtime.logger.debug(
        "list_windows count=%d titleContains=%r includeInvisible=%s",
        listing["count"],
        params.get("titleContains"),
        params.get("includeInvisible", False),
    )
    return {"count": listing["count"], "windows": listing["windows"]}


def action_capture_window(params: dict, runtime: Runtime) -> dict:
    import screenshot
    import window_capture

    window = _select_window_params(params, runtime)
    captured = window_capture.capture_window(
        window,
        engine=params.get("engine", "auto"),
        timeout_ms=int(params.get("timeoutMs", 3000)),
        include_cursor=bool(params.get("includeCursor", False)),
    )

    fmt = params.get("format", "png")
    display_path, absolute_path = screenshot.resolve_output_path(
        params.get("path"), runtime.config.shot_dir, fmt
    )
    saved = screenshot.save_image(
        captured["data"],
        captured["width"],
        captured["height"],
        absolute_path,
        fmt=fmt,
        quality=params.get("quality"),
    )
    # 不返回/记录窗口内容本身；window 只含元数据（标题可能敏感，日志里也不打印标题）
    result = {
        "window": {
            "hwnd": window["hwnd"],
            "title": window["title"],
            "class": window["class"],
            "pid": window["pid"],
            "process": window["process"],
            "rect": window["rect"],
        },
        "engine": captured["engine"],
        "path": display_path,
        "absolutePath": saved["absolutePath"],
        "width": saved["width"],
        "height": saved["height"],
        "format": saved["format"],
        "bytes": saved["bytes"],
    }
    warnings = captured.get("warnings") or []
    if warnings:
        result["warnings"] = list(warnings)
    if captured.get("fallbackReason"):
        result["fallbackReason"] = captured["fallbackReason"]
    if params.get("base64", False):
        encoded = screenshot.file_base64(saved["absolutePath"])
        result["base64"] = encoded
        result["base64Length"] = len(encoded)
    runtime.logger.info(
        "capture_window hwnd=%d engine=%s -> %s (%dx%d, %s, %d bytes)%s",
        window["hwnd"],
        captured["engine"],
        saved["absolutePath"],
        saved["width"],
        saved["height"],
        saved["format"],
        saved["bytes"],
        (" fallback=" + captured["fallbackReason"][:200]) if captured.get("fallbackReason") else "",
    )
    return result


# ---------------------------------------------------------------------------
# v0.3：游戏自动化动作（activate_window / mouse_circle / wait / find_image / wait_image）
# ---------------------------------------------------------------------------


def _require_windows(action: str) -> None:
    """v0.3 新动作仅 Windows 支持（见 SPEC 8 与开发提示词第 3 节）。"""
    if not IS_WINDOWS:
        raise GCError(
            "UNSUPPORTED_PLATFORM",
            "%s 仅支持 Windows" % action,
            hint="当前平台可继续使用 screenshot / wait / 技能库（skill_*）等跨平台动作",
        )


def _sleep_until(due: float) -> None:
    """睡到 perf_counter 绝对时刻 due（不累积 sleep 漂移）。"""
    remain = due - time.perf_counter()
    if remain > 0:
        # 大间隔用 sleep，末尾 <2ms 用短睡避免明显过冲
        if remain > 0.002:
            time.sleep(remain - 0.001)
        while time.perf_counter() < due:
            time.sleep(0)


def action_wait(params: dict, runtime: Runtime) -> dict:
    """同步等待，零输入（SPEC 8.2.3）。

    v0.6：若处于 skill_run 总 deadline 内，睡眠截断到剩余时间；到点返回 aborted:timeout，
    由 skill_run 整体中止。
    """
    duration = int(params["durationMs"])
    remaining = _deadline_remaining_seconds(runtime)
    if remaining is not None:
        effective = max(0, min(duration, int(remaining * 1000.0)))
        truncated = effective < duration
        if effective > 0:
            time.sleep(effective / 1000.0)
        # 预算不足以完成请求的等待（被截断）或已到点 → 视为总 deadline 超时，由 skill_run 整体中止
        if truncated or _deadline_expired(runtime):
            return {"waitedMs": effective, "aborted": True, "abortReason": "timeout"}
        return {"waitedMs": effective}
    if duration > 0:
        time.sleep(duration / 1000.0)
    return {"waitedMs": duration}


def action_activate_window(params: dict, runtime: Runtime) -> dict:
    """恢复并前置窗口（SPEC 8.2.1，不注入点击）。"""
    _require_windows("activate_window")
    import window_capture

    window_text = params.get("window")
    hwnd = params.get("hwnd")
    has_window = isinstance(window_text, str) and window_text.strip() != ""
    has_hwnd = hwnd is not None
    if has_window and has_hwnd:
        raise GCError(
            "INVALID_ARGUMENT",
            "window 与 hwnd 必须二选一，不能同时传",
            hint='例如 {"hwnd":12345} 或 {"window":"游戏标题子串"}',
        )
    if not has_window and not has_hwnd:
        raise GCError(
            "INVALID_ARGUMENT",
            "需要 window（标题子串）或 hwnd（窗口句柄）之一",
            hint="先用 list_windows 查看窗口列表",
        )
    if has_hwnd:
        handle = int(hwnd)
        if handle <= 0:
            raise GCError("INVALID_ARGUMENT", "hwnd 必须是正整数（收到 %s）" % hwnd)
        window = window_capture.resolve_window(hwnd=handle)
    else:
        window = window_capture.resolve_window(window=window_text.strip())
    result = window_capture.activate_window(
        window,
        restore=bool(params.get("restore", True)),
        timeout_ms=int(params.get("timeoutMs", 2000)),
    )
    runtime.logger.info(
        "activate_window hwnd=%d attempts=%d foreground=%s",
        result["window"]["hwnd"],
        result["attempts"],
        result["foreground"],
    )
    return result


def action_mouse_circle(params: dict, runtime: Runtime) -> dict:
    """连续圆形鼠标移动（SPEC 8.2.2）：绝对时间调度、Esc 中止、前台保护、释放按键。"""
    _require_windows("mouse_circle")
    import circle_motion

    impl = runtime.input_impl
    virtual = impl.get_virtual_screen()
    stats = circle_motion.circle_points(
        center_x=params["centerX"],
        center_y=params["centerY"],
        radius=params["radius"],
        period_ms=params["periodMs"],
        duration_ms=params["durationMs"],
        direction=params["direction"],
        start_angle_deg=params["startAngleDeg"],
        samples_per_second=params["samplesPerSecond"],
        lead_in_ms=params["leadInMs"],
        virtual_screen=virtual,
    )
    abort_key_raw = str(params.get("abortKey", "esc")).strip()
    abort_enabled = abort_key_raw.lower() not in ("none", "off", "disabled", "")
    abort_name = normalize_key(abort_key_raw) if abort_enabled else None
    hwnd = params.get("hwnd")
    target_hwnd = int(hwnd) if hwnd is not None else None
    if target_hwnd is not None:
        import window_capture

        window_capture.resolve_window(hwnd=target_hwnd)  # 不存在 → WINDOW_NOT_FOUND

    # v0.6：有效时长 = min(durationMs, 总 deadline 剩余时间)
    requested_duration = int(stats["durationMs"])
    remaining_ms = _deadline_remaining_ms(runtime)
    effective_duration = requested_duration
    if remaining_ms is not None:
        effective_duration = max(0, min(requested_duration, remaining_ms))
    effective_stats = stats
    if effective_duration != requested_duration and effective_duration >= 100:
        effective_stats = circle_motion.circle_points(
            center_x=stats["centerX"],
            center_y=stats["centerY"],
            radius=stats["radius"],
            period_ms=stats["periodMs"],
            duration_ms=effective_duration,
            direction=stats["direction"],
            start_angle_deg=stats["startAngleDeg"],
            samples_per_second=stats["samplesPerSecond"],
            lead_in_ms=stats["leadInMs"],
            virtual_screen=virtual,
        )
    elif effective_duration < 100 and remaining_ms is not None:
        effective_stats = dict(stats)
        effective_stats["samples"] = 0
        effective_stats["loops"] = 0.0
    common = {
        "centerX": stats["centerX"],
        "centerY": stats["centerY"],
        "radius": stats["radius"],
        "periodMs": stats["periodMs"],
        "durationMs": requested_duration,
        "direction": stats["direction"],
        "samples": effective_stats["samples"],
        "loops": effective_stats["loops"],
        "start": effective_stats["start"],
        "end": effective_stats["end"],
        "effectiveDurationMs": int(effective_duration),
        # v0.5：显式回显中止键（默认 esc；dryRun 也返回，便于面板/测试核对）
        "abortKey": abort_name or "none",
    }
    if bool(params.get("dryRun", False)):
        # 只算不注入：不移动、不等待、不按键（dryRun 返回完整请求时长的统计）
        return {**common, "samples": stats["samples"], "loops": stats["loops"], "effectiveDurationMs": requested_duration,
                "actualDurationMs": 0, "aborted": False, "abortReason": None}

    button = params.get("button", "none")
    pressed = False
    abort_reason = None
    circle_start = None
    runtime.logger.info(
        "mouse_circle start center=(%.0f,%.0f) radius=%.0f periodMs=%.0f durationMs=%.0f effectiveMs=%.0f direction=%s button=%s abortKey=%s hwnd=%s",
        stats["centerX"],
        stats["centerY"],
        stats["radius"],
        stats["periodMs"],
        stats["durationMs"],
        effective_duration,
        stats["direction"],
        button,
        abort_name or "none",
        target_hwnd,
    )
    try:
        if remaining_ms is not None and (remaining_ms <= 0 or effective_duration < 100):
            # 总 deadline 剩余不足以画任何一段圆（最短 100ms）→ 立即按 timeout 中止，不注入输入
            abort_reason = "timeout"
        if abort_reason is None and button != "none":
            impl.button_down(button)
            pressed = True
        # 起始前台保护：目标窗口不在前台则立即中止，避免把输入打到别的窗口
        if abort_reason is None and target_hwnd is not None and int(impl.get_foreground_hwnd() or 0) != target_hwnd:
            abort_reason = "foreground-changed"
        # lead-in：从当前指针平滑移动到起点
        if abort_reason is None and stats["leadInMs"] > 0:
            current = impl.get_pointer()
            start_x, start_y = effective_stats["start"]["x"], effective_stats["start"]["y"]
            from_x = int(current.get("x", start_x))
            from_y = int(current.get("y", start_y))
            lead_start = time.perf_counter()
            lead_steps = max(2, int(stats["leadInMs"] / 10.0))
            for index in range(1, lead_steps + 1):
                if _deadline_expired(runtime):
                    abort_reason = "timeout"
                    break
                ratio = index / lead_steps
                impl.move_to(
                    int(round(from_x + (start_x - from_x) * ratio)),
                    int(round(from_y + (start_y - from_y) * ratio)),
                )
                if abort_enabled and impl.key_is_down(abort_name):
                    abort_reason = "abort-key"
                    break
                if target_hwnd is not None and int(impl.get_foreground_hwnd() or 0) != target_hwnd:
                    abort_reason = "foreground-changed"
                    break
                _sleep_until(lead_start + stats["leadInMs"] / 1000.0 * ratio)
        # 画圆：每帧按 perf_counter 绝对时间计算，不累加 sleep；每帧检查 Esc 与总 deadline
        if abort_reason is None:
            circle_start = time.perf_counter()
            last_foreground_check = circle_start
            period = effective_stats["periodMs"]
            sps = effective_stats["samplesPerSecond"]
            for index in range(effective_stats["samples"]):
                if _deadline_expired(runtime):
                    abort_reason = "timeout"
                    break
                elapsed_ms = index / sps * 1000.0
                x, y = circle_motion.point_at_elapsed(
                    effective_stats["centerX"],
                    effective_stats["centerY"],
                    effective_stats["radius"],
                    period,
                    effective_stats["direction"],
                    effective_stats["startAngleDeg"],
                    elapsed_ms,
                )
                impl.move_to(int(round(x)), int(round(y)))
                if abort_enabled and impl.key_is_down(abort_name):
                    abort_reason = "abort-key"
                    break
                now = time.perf_counter()
                if target_hwnd is not None and (now - last_foreground_check) >= 0.1:
                    last_foreground_check = now
                    if int(impl.get_foreground_hwnd() or 0) != target_hwnd:
                        abort_reason = "foreground-changed"
                        break
                _sleep_until(circle_start + index / sps)
    finally:
        if pressed:
            try:
                impl.button_up(button)
            except Exception as exc:  # noqa: BLE001 - 释放失败只警告，不能掩盖主流程
                runtime.logger.warning("mouse_circle 释放鼠标键失败: %s", exc)
        # actualDurationMs 只统计“画圆阶段”（不含 lead-in/按键准备），与 SPEC 8.2.2 示例一致
        if circle_start is not None:
            actual_duration_ms = int(round((time.perf_counter() - circle_start) * 1000.0))
        else:
            actual_duration_ms = 0
        runtime.logger.info(
            "mouse_circle end samples=%d actualDurationMs=%d aborted=%s abortReason=%s",
            effective_stats["samples"],
            actual_duration_ms,
            bool(abort_reason),
            abort_reason,
        )
    return {
        **common,
        "actualDurationMs": actual_duration_ms,
        "aborted": bool(abort_reason),
        "abortReason": abort_reason,
    }


def _resolve_optional_window(params: dict, action: str) -> dict | None:
    """find_image / wait_image 的可选 window/hwnd 解析（都不给返回 None）。"""
    import window_capture

    window_text = params.get("window")
    hwnd = params.get("hwnd")
    has_window = isinstance(window_text, str) and window_text.strip() != ""
    has_hwnd = hwnd is not None
    if has_window and has_hwnd:
        raise GCError(
            "INVALID_ARGUMENT",
            "window 与 hwnd 必须二选一，不能同时传",
            hint="也可以改用 region 指定搜索区域",
        )
    if has_hwnd:
        handle = int(hwnd)
        if handle <= 0:
            raise GCError("INVALID_ARGUMENT", "hwnd 必须是正整数（收到 %s）" % hwnd)
        return window_capture.resolve_window(hwnd=handle)
    if has_window:
        return window_capture.resolve_window(window=window_text.strip())
    return None


def _image_search_region(params: dict, runtime: Runtime) -> tuple:
    """计算 find_image 的搜索区域，返回 ``(region, source)``（屏幕坐标）。"""
    import screenshot

    window_text = params.get("window")
    hwnd = params.get("hwnd")
    has_window = (isinstance(window_text, str) and window_text.strip() != "") or hwnd is not None
    region = params.get("region")
    if has_window and region is not None:
        raise GCError(
            "INVALID_ARGUMENT",
            "region 与 window/hwnd 不能同时使用",
            hint="window/hwnd 会用窗口 clientRect 作为搜索区域；二选一即可",
        )
    virtual = runtime.input_impl.get_virtual_screen()
    if has_window:
        window = _resolve_optional_window(params, "find_image")
        client = window.get("clientRect") or {}
        if int(client.get("width") or 0) <= 0 or int(client.get("height") or 0) <= 0:
            raise GCError(
                "INVALID_REGION",
                "窗口客户区尺寸非法（窗口可能已最小化）: %r" % client,
                hint="先 activate_window（restore=true）再 find_image，或显式传 region",
            )
        checked = screenshot.validate_region(
            {
                "x": int(client["x"]),
                "y": int(client["y"]),
                "width": int(client["width"]),
                "height": int(client["height"]),
            },
            virtual,
        )
        return {"x": checked["x"], "y": checked["y"], "width": checked["width"], "height": checked["height"]}, "window"
    if region is not None:
        checked = screenshot.validate_region(region, virtual)
        return {"x": checked["x"], "y": checked["y"], "width": checked["width"], "height": checked["height"]}, "region"
    primary = runtime.input_impl.get_primary_screen()
    return {
        "x": int(primary["x"]),
        "y": int(primary["y"]),
        "width": int(primary["width"]),
        "height": int(primary["height"]),
    }, "screen"


def _save_find_image_debug(data, width, height, region, matches, debug_path, runtime) -> str:
    """可选排障图：在截图里画出搜索区域与匹配框（不影响匹配结果）。"""
    import screenshot

    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # pragma: no cover
        raise GCError("MISSING_DEPENDENCY", "缺少 Pillow，无法输出 debugPath: %s" % exc)
    display_path, absolute_path = screenshot.resolve_output_path(debug_path, runtime.config.shot_dir, "png")
    image = Image.frombytes("RGB", (int(width), int(height)), data)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, int(width) - 1, int(height) - 1], outline=(0, 255, 0), width=2)
    for match in matches:
        local_x = int(match["x"]) - int(region["x"])
        local_y = int(match["y"]) - int(region["y"])
        draw.rectangle(
            [local_x, local_y, local_x + int(match["width"]) - 1, local_y + int(match["height"]) - 1],
            outline=(255, 0, 0),
            width=2,
        )
        draw.text((local_x + 2, max(0, local_y - 12)), "%.3f" % float(match.get("score", 0)), fill=(255, 255, 0))
    try:
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(absolute_path), "PNG")
    except OSError as exc:
        raise GCError("FILE_IO_ERROR", "保存 debugPath 失败: %s (%s)" % (absolute_path, exc)) from exc
    return display_path


def _abort_key_for_image(params: dict) -> tuple:
    """解析 find_image / wait_image 的 abortKey（默认 esc，"none" 禁用）。"""
    raw = params.get("abortKey", "esc")
    text = str(raw if raw is not None else "esc").strip()
    if text.lower() in ("none", "off", "disabled", ""):
        return False, None
    return True, normalize_key(text)


def _abort_pressed(runtime: Runtime, abort_name: str | None) -> bool:
    """只读检查中止键是否按下（GetAsyncKeyState；查询失败不阻塞截图）。"""
    if not abort_name:
        return False
    try:
        return bool(runtime.input_impl.key_is_down(abort_name))
    except Exception:  # noqa: BLE001
        return False


def _resolve_image_templates(params: dict) -> list:
    """解析 template / templates（二选一，templates 1–8 个）。"""
    template = params.get("template")
    templates = params.get("templates")
    has_template = isinstance(template, str) and template.strip() != ""
    has_templates = isinstance(templates, list) and len(templates) > 0
    if has_template and has_templates:
        raise GCError(
            "INVALID_ARGUMENT",
            "template 与 templates 必须二选一，不能同时传",
            hint='单个模板用 "template"，多模板用 "templates": ["a.png","b.png"]',
        )
    if has_templates:
        return [str(item) for item in templates]
    if has_template:
        return [str(template).strip()]
    raise GCError(
        "INVALID_ARGUMENT",
        "需要 template（单个）或 templates（1–8 个）之一",
        hint='例如 "template": "keep-on-mining/start_game.png"',
    )


def _abort_image_result(
    started: float,
    attempts: int,
    template_meta: dict | None = None,
    candidate_templates: list | None = None,
    source: str | None = None,
    region: dict | None = None,
    reason: str = "abort-key",
) -> dict:
    """find_image / wait_image 被 Esc 或总 deadline 中止时的返回结构（零副作用）。"""
    result = {
        "found": False,
        "aborted": True,
        "abortReason": reason,
        "attempts": int(attempts),
        "elapsedMs": int(round((time.perf_counter() - started) * 1000.0)),
        "source": source,
        "searchRegion": region,
        "template": template_meta,
        "matches": [],
    }
    if candidate_templates and len(candidate_templates) > 1:
        result["candidateTemplates"] = candidate_templates
    return result


def _find_image_once(params: dict, runtime: Runtime) -> dict:
    """执行一次模板匹配（find_image 与 wait_image 共用；不注入输入）。

    v0.5：支持 ``templates`` 多模板（依次匹配、任一命中即返回）与 ``abortKey``
    （默认 esc；在扫描前/每个模板前/扫描后检查，按下立即返回 aborted:true）。
    """
    import screenshot
    import template_match

    started = time.perf_counter()
    templates_root = runtime.config.templates_dir
    abort_enabled, abort_name = _abort_key_for_image(params)
    # v0.6：扫描前检查总 deadline（技能整体超时）
    if _deadline_expired(runtime):
        return _abort_image_result(started, 0, reason="timeout")
    template_paths = _resolve_image_templates(params)
    # Esc 检查 1：开始扫描前
    if abort_enabled and _abort_pressed(runtime, abort_name):
        return _abort_image_result(started, 0)
    loaded_templates = []
    for path in template_paths:
        loaded_templates.append(template_match.load_template(path, templates_root=templates_root))
    candidate_meta = [
        {"path": item["path"], "width": item["width"], "height": item["height"]} for item in loaded_templates
    ]
    region, source = _image_search_region(params, runtime)
    # v0.6：加载模板/解析区域后再次检查总 deadline
    if _deadline_expired(runtime):
        return _abort_image_result(
            started,
            0,
            template_meta=candidate_meta[0],
            candidate_templates=candidate_meta,
            source=source,
            region=region,
            reason="timeout",
        )
    # Esc 检查 2：截图前
    if abort_enabled and _abort_pressed(runtime, abort_name):
        return _abort_image_result(
            started,
            0,
            template_meta=candidate_meta[0],
            candidate_templates=candidate_meta,
            source=source,
            region=region,
        )
    data, width, height = screenshot.capture(region["x"], region["y"], region["width"], region["height"])
    matched_result = None
    matched_template = None
    for loaded in loaded_templates:
        # v0.6：每个模板扫描前检查总 deadline
        if _deadline_expired(runtime):
            return _abort_image_result(
                started,
                1,
                template_meta=candidate_meta[0],
                candidate_templates=candidate_meta,
                source=source,
                region=region,
                reason="timeout",
            )
        # Esc 检查 3：每个模板扫描前
        if abort_enabled and _abort_pressed(runtime, abort_name):
            return _abort_image_result(
                started,
                1,
                template_meta=candidate_meta[0],
                candidate_templates=candidate_meta,
                source=source,
                region=region,
            )
        candidate = template_match.find_template(
            data,
            width,
            height,
            loaded,
            region=region,
            threshold=float(params.get("threshold", 0.85)),
            scales=params.get("scales") or None,
            grayscale=bool(params.get("grayscale", True)),
            max_results=int(params.get("maxResults", 3)),
            templates_root=templates_root,
        )
        if candidate["found"]:
            for match in candidate["matches"]:
                match["template"] = loaded["path"]
            matched_result = candidate
            matched_template = loaded
            break
    # v0.6：扫描后检查总 deadline（优先于 Esc 检查）
    if _deadline_expired(runtime):
        return _abort_image_result(
            started,
            1,
            template_meta=candidate_meta[0],
            candidate_templates=candidate_meta,
            source=source,
            region=region,
            reason="timeout",
        )
    # Esc 检查 4：扫描后（未命中时也检查，保证 Esc 能及时中止）
    if abort_enabled and _abort_pressed(runtime, abort_name):
        return _abort_image_result(
            started,
            1,
            template_meta=candidate_meta[0],
            candidate_templates=candidate_meta,
            source=source,
            region=region,
        )
    elapsed_ms = int(round((time.perf_counter() - started) * 1000.0))
    if matched_result is not None and matched_template is not None:
        output = {
            "found": True,
            "source": source,
            "searchRegion": region,
            "template": {
                "path": matched_template["path"],
                "width": matched_template["width"],
                "height": matched_template["height"],
            },
            "matches": matched_result["matches"],
            "elapsedMs": elapsed_ms,
        }
        if len(candidate_meta) > 1:
            output["candidateTemplates"] = candidate_meta
        if params.get("debugPath"):
            output["debugPath"] = _save_find_image_debug(
                data, width, height, region, matched_result["matches"], params["debugPath"], runtime
            )
        runtime.logger.info(
            "find_image source=%s region=%s template=%s templates=%d matches=%d elapsedMs=%d",
            source,
            region,
            matched_template["path"],
            len(candidate_meta),
            len(matched_result["matches"]),
            elapsed_ms,
        )
        return output
    output = {
        "found": False,
        "source": source,
        "searchRegion": region,
        "template": candidate_meta[0],
        "matches": [],
        "elapsedMs": elapsed_ms,
    }
    if len(candidate_meta) > 1:
        output["candidateTemplates"] = candidate_meta
    if params.get("debugPath"):
        output["debugPath"] = _save_find_image_debug(data, width, height, region, [], params["debugPath"], runtime)
    runtime.logger.info(
        "find_image source=%s region=%s templates=%s matches=0 elapsedMs=%d",
        source,
        region,
        [item["path"] for item in candidate_meta],
        elapsed_ms,
    )
    return output


def action_find_image(params: dict, runtime: Runtime) -> dict:
    """在当前屏幕/窗口客户区做模板匹配（SPEC 8.2.4 / 10.1，只读）。"""
    _require_windows("find_image")
    return _find_image_once(params, runtime)


def _interruptible_sleep(runtime: Runtime, abort_name: str | None, seconds: float, abort_enabled: bool):
    """可被 Esc / 总 deadline 打断的 sleep；返回中止原因（"abort-key"/"timeout"/None）。"""
    sleep_deadline = time.perf_counter() + max(0.0, float(seconds))
    while True:
        if abort_enabled and _abort_pressed(runtime, abort_name):
            return "abort-key"
        if _deadline_expired(runtime):
            return "timeout"
        remain = sleep_deadline - time.perf_counter()
        if remain <= 0:
            return None
        time.sleep(min(0.05, remain))


def action_wait_image(params: dict, runtime: Runtime) -> dict:
    """轮询模板匹配直到出现/消失、超时、Esc 或总 deadline 中止（SPEC 8.2.5 / 10.1 / 11.3）。"""
    _require_windows("wait_image")
    requested_timeout_ms = int(params["timeoutMs"])
    interval_ms = int(params["intervalMs"])
    mode = params.get("mode", "appear")
    abort_enabled, abort_name = _abort_key_for_image(params)
    started = time.perf_counter()
    # v0.6：有效 timeout = min(timeoutMs, 总 deadline 剩余时间)
    remaining_budget_ms = _deadline_remaining_ms(runtime)
    if remaining_budget_ms is None:
        effective_timeout_ms = requested_timeout_ms
    else:
        effective_timeout_ms = max(0, min(requested_timeout_ms, remaining_budget_ms))
    deadline = started + effective_timeout_ms / 1000.0
    attempts = 0
    last_snapshot = None

    def _aborted(attempt_count: int, reason: str = "abort-key") -> dict:
        return {
            "found": False,
            "timedOut": False,
            "aborted": True,
            "abortReason": reason,
            "match": None,
            "attempts": int(attempt_count),
            "elapsedMs": int(round((time.perf_counter() - started) * 1000.0)),
            # SPEC 13.2.1：失败截图优先使用 searchRegion
            "searchRegion": (last_snapshot or {}).get("searchRegion"),
        }

    if _deadline_expired(runtime):
        return _aborted(0, "timeout")
    if abort_enabled and _abort_pressed(runtime, abort_name):
        return _aborted(0, "abort-key")
    while True:
        if _deadline_expired(runtime):
            return _aborted(attempts, "timeout")
        attempts += 1
        snapshot = _find_image_once(params, runtime)
        last_snapshot = snapshot
        if snapshot.get("aborted"):
            reason = str(snapshot.get("abortReason") or "abort-key")
            return _aborted(attempts, "timeout" if reason == "timeout" else "abort-key")
        found = bool(snapshot["found"])
        elapsed_ms = int(round((time.perf_counter() - started) * 1000.0))
        if mode == "appear" and found:
            match = snapshot["matches"][0] if snapshot["matches"] else None
            return {
                "found": True,
                "timedOut": False,
                "match": match,
                "attempts": attempts,
                "elapsedMs": elapsed_ms,
                "searchRegion": snapshot.get("searchRegion"),
            }
        if mode == "disappear" and not found:
            return {
                "found": False,
                "timedOut": False,
                "match": None,
                "attempts": attempts,
                "elapsedMs": elapsed_ms,
                "searchRegion": snapshot.get("searchRegion"),
            }
        if abort_enabled and _abort_pressed(runtime, abort_name):
            return _aborted(attempts, "abort-key")
        if _deadline_expired(runtime):
            return _aborted(attempts, "timeout")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            # 总 deadline 先到 → 技能整体超时中止；否则是 wait_image 自身超时
            if _deadline_expired(runtime):
                return _aborted(attempts, "timeout")
            return {
                "found": False,
                "timedOut": True,
                "match": None,
                "attempts": attempts,
                "elapsedMs": elapsed_ms,
                "searchRegion": snapshot.get("searchRegion"),
            }
        interrupted = _interruptible_sleep(runtime, abort_name, min(interval_ms / 1000.0, remaining), abort_enabled)
        if interrupted:
            return _aborted(attempts, interrupted)


# ---------------------------------------------------------------------------
# v0.3：技能库动作（storage 在 skills.py；skill_run 执行器在本文件）
# ---------------------------------------------------------------------------


def _skill_window_context(window: dict | None) -> dict | None:
    """把窗口信息扩成技能变量上下文（含 clientRect 计算值，SPEC 8.3.2）。"""
    if not isinstance(window, dict) or not window:
        return None
    context = {
        "hwnd": window.get("hwnd"),
        "title": window.get("title"),
        "class": window.get("class"),
        "pid": window.get("pid"),
        "process": window.get("process"),
        "rect": window.get("rect"),
        "clientRect": window.get("clientRect"),
    }
    client = window.get("clientRect") or {}
    try:
        left = float(client.get("x") or 0)
        top = float(client.get("y") or 0)
        width = float(client.get("width") or 0)
        height = float(client.get("height") or 0)
    except (TypeError, ValueError):
        left = top = width = height = 0.0
    context.update(
        {
            "clientCenterX": int(round(left + width / 2.0)),
            "clientCenterY": int(round(top + height / 2.0)),
            "quarterWidth": int(round(width / 4.0)),
            "halfWidth": int(round(width / 2.0)),
            "halfHeight": int(round(height / 2.0)),
        }
    )
    return context


def _resolve_skill_reference(expression: str, window_context: dict | None, contexts: dict):
    """解析 ``$window.*`` / ``$<saveAs>.*``（支持数组数字下标）。"""
    if not isinstance(expression, str) or not expression.startswith("$"):
        return expression
    parts = expression[1:].split(".")
    if not parts or not parts[0]:
        raise GCError(
            "SKILL_VAR_UNRESOLVED",
            "无法解析变量: %r" % expression,
            hint="变量写法如 $window.clientCenterX / $start.matches.0.center.x",
        )
    root_name = parts[0]
    if root_name == "window":
        if window_context is None:
            raise GCError(
                "SKILL_VAR_UNRESOLVED",
                "$window 尚未解析：需要先执行 activate_window 或 list_windows 步骤",
                hint="在技能步骤里先 activate_window（可 saveAs）再使用 $window.*",
            )
        current = window_context
    elif root_name in contexts:
        current = contexts[root_name]
    else:
        available = sorted(set(contexts) | ({"window"} if window_context is not None else set()))
        raise GCError(
            "SKILL_VAR_UNRESOLVED",
            "未知变量根: $%s（可用: %s）" % (root_name, ", ".join(available) or "无"),
            hint="saveAs 步骤返回值后才能引用该变量",
        )
    for part in parts[1:]:
        if current is None:
            raise GCError(
                "SKILL_VAR_UNRESOLVED",
                "变量 %r 的中间值 %r 为空（None）：上一步结果通常表示模板未找到" % (expression, parts[0]),
                hint="上一步结果为空，通常是模板未找到（见 failedStep.screenshotPath / 提高 timeoutMs / 补充模板）",
            )
        if current == "":
            raise GCError(
                "SKILL_VAR_UNRESOLVED",
                "变量 %r 的中间值为空字符串" % expression,
                hint="上一步结果为空，通常是模板未找到（见 failedStep.screenshotPath / 提高 timeoutMs / 补充模板）",
            )
        if isinstance(current, list):
            if not part.isdigit():
                raise GCError("SKILL_VAR_UNRESOLVED", "数组变量 %r 期望数字下标，收到 %r" % (expression, part))
            index = int(part)
            if index < 0 or index >= len(current):
                raise GCError(
                    "SKILL_VAR_UNRESOLVED",
                    "数组下标越界: %r（长度 %d）" % (expression, len(current)),
                )
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise GCError(
                    "SKILL_VAR_UNRESOLVED",
                    "变量路径不存在: %r（缺少字段 %r）" % (expression, part),
                )
            current = current[part]
        else:
            raise GCError(
                "SKILL_VAR_UNRESOLVED",
                "无法继续解析变量 %r（当前值类型 %s）" % (expression, type(current).__name__),
                hint="若上一步是 wait_image/find_image，可能未找到模板（见 failedStep.screenshotPath）",
            )
    return current


def _expand_skill_value(value, window_context: dict | None, contexts: dict):
    """递归展开步骤参数中的 ``$...`` 变量。"""
    if isinstance(value, str):
        if value.startswith("$"):
            return _resolve_skill_reference(value, window_context, contexts)
        return value
    if isinstance(value, dict):
        return {key: _expand_skill_value(item, window_context, contexts) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_skill_value(item, window_context, contexts) for item in value]
    return value


def _count_skill_refs(value) -> int:
    if isinstance(value, str):
        return 1 if value.startswith("$") else 0
    if isinstance(value, dict):
        return sum(_count_skill_refs(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_skill_refs(item) for item in value)
    return 0


def _abort_key_enabled(raw) -> tuple:
    """解析 abortKey，返回 ``(enabled, canonical_name_or_None)``（v0.5 默认 esc）。"""
    text = str(raw if raw is not None else "esc").strip()
    if text.lower() in ("none", "off", "disabled", ""):
        return False, None
    return True, normalize_key(text)


def _key_down_safe(runtime: Runtime, name: str) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(runtime.input_impl.key_is_down(name))
    except Exception:  # noqa: BLE001 - 查询失败不应中断技能
        return False


# ---------------------------------------------------------------------------
# v0.6：skill_run 总 deadline（time.monotonic 秒；动作内部据此优雅提前停止）
# ---------------------------------------------------------------------------


def _runtime_deadline(runtime: Runtime):
    """当前 skill_run 的总 deadline（monotonic 秒）；不在技能运行中返回 None。"""
    return getattr(runtime, "skill_deadline", None)


def _deadline_remaining_seconds(runtime: Runtime):
    """距离总 deadline 的剩余秒数（>=0）；无 deadline 返回 None。"""
    deadline = _runtime_deadline(runtime)
    if deadline is None:
        return None
    return max(0.0, float(deadline) - time.monotonic())


def _deadline_expired(runtime: Runtime) -> bool:
    """总 deadline 是否已经到点（无 deadline 返回 False）。"""
    remaining = _deadline_remaining_seconds(runtime)
    return remaining is not None and remaining <= 0.0


def _deadline_remaining_ms(runtime: Runtime):
    remaining = _deadline_remaining_seconds(runtime)
    if remaining is None:
        return None
    return int(remaining * 1000.0)


def _skill_progress_path(runtime: Runtime):
    """技能运行进度文件：``<GAME_CONTROL_HOME>/logs/skill-progress.json``（SPEC 10.4）。"""
    return runtime.config.log_dir / "skill-progress.json"


def _write_skill_progress(runtime: Runtime, payload: dict) -> None:
    """原子写进度文件；运行中 Host 会直接读该文件，因此每次都要及时落盘。"""
    path = _skill_progress_path(runtime)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp-%d" % os.getpid())
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:  # 进度写失败不影响技能执行
        try:
            runtime.logger.warning("写入 skill-progress.json 失败: %s", exc)
        except Exception:  # noqa: BLE001
            pass


def action_skill_progress(params: dict, runtime: Runtime) -> dict:
    """读取技能运行进度（SPEC 10.4；无文件返回 progress:null）。"""
    path = _skill_progress_path(runtime)
    if not path.exists():
        return {"progress": None}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"progress": None}
    if not isinstance(data, dict):
        return {"progress": None}
    return {"progress": data}


def action_skill_list(params: dict, runtime: Runtime) -> dict:
    return runtime.get_skills_store().list()


def action_skill_get(params: dict, runtime: Runtime) -> dict:
    return runtime.get_skills_store().get(params["id"])


def action_skill_save(params: dict, runtime: Runtime) -> dict:
    store = runtime.get_skills_store()
    payload = {
        key: params[key]
        for key in ("title", "description", "game", "tags", "markdown", "steps", "id", "content")
        if key in params
    }
    result = store.save(payload)
    runtime.logger.info(
        "skill_save id=%s created=%s backup=%s mode=%s",
        result["id"],
        result["created"],
        result.get("backupPath"),
        "content" if "content" in params else "structured",
    )
    return result


def action_skill_versions(params: dict, runtime: Runtime) -> dict:
    return runtime.get_skills_store().versions(params["id"])


def action_skill_restore(params: dict, runtime: Runtime) -> dict:
    result = runtime.get_skills_store().restore(params["id"], params["backup"])
    runtime.logger.info("skill_restore id=%s from=%s", result["id"], result["restoredFrom"])
    return result


def action_skill_delete(params: dict, runtime: Runtime) -> dict:
    result = runtime.get_skills_store().delete(params["id"], hard=bool(params.get("hard", False)))
    runtime.logger.info("skill_delete id=%s hard=%s trash=%s", result["id"], result["hardDeleted"], result.get("trashPath"))
    return result


def _skill_run_dry(params: dict, skill: dict, abort_enabled: bool, abort_name: str | None) -> dict:
    """dryRun：只做静态校验与变量规划，不执行任何动作、不等待。"""
    steps = skill.get("steps") or []
    allowed = set(SKILL_STEP_ACTIONS)
    plan = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise GCError("INVALID_SKILL", "steps[%d] 不是对象" % index)
        action = step.get("action")
        if action not in allowed:
            raise GCError(
                "INVALID_SKILL",
                "steps[%d].action 不允许: %r" % (index, action),
                hint="技能步骤不能调用 skill_* 或未知动作",
            )
        plan.append(
            {
                "index": index,
                "action": action,
                "ok": True,
                "planned": True,
                "saveAs": step.get("saveAs"),
                "waitMs": int(step.get("waitMs") or 0),
                "varRefs": _count_skill_refs(step.get("params") or {}),
            }
        )
    return {
        "skillId": skill["id"],
        "dryRun": True,
        "repeat": int(params.get("repeat", 1)),
        "repeatsDone": 0,
        "success": True,
        "aborted": False,
        "abortReason": None,
        "durationMs": 0,
        "steps": plan,
    }


SKILL_IMAGE_ACTIONS = ("find_image", "wait_image")


def _skill_template_missing(action: str, result: dict) -> bool:
    """模板类步骤是否“未找到”（wait_image 超时/未命中，find_image 未命中）。"""
    if not isinstance(result, dict):
        return True
    if action == "wait_image":
        return bool(result.get("timedOut")) or result.get("found") is not True
    return result.get("found") is not True


def _skill_missing_messages(action: str, validated: dict) -> tuple:
    """生成模板未找到的 failedStep.message / hint（SPEC 13.2.1）。"""
    templates = validated.get("templates") if isinstance(validated, dict) else None
    if not isinstance(templates, list) or not templates:
        template = validated.get("template") if isinstance(validated, dict) else None
        templates = [template] if template else []
    template_text = ", ".join(str(item) for item in templates if item) or "（未指定）"
    if action == "wait_image":
        timeout_ms = int(validated.get("timeoutMs", 0) or 0) if isinstance(validated, dict) else 0
        message = "等待模板超时（%d ms）：%s；请确认游戏画面/提高 timeoutMs/补充模板" % (timeout_ms, template_text)
        hint = "确认游戏窗口当前画面是否含该按钮；可提高 timeoutMs、把更多样式加入 templates、或降低阈值"
    else:
        message = "未找到模板：%s；请确认游戏画面/增大搜索区域/降低阈值/补充模板" % template_text
        hint = "确认模板截图与当前画面一致；可降低 threshold、增大 region/window 搜索区域或补充模板"
    return message, hint


def _skill_failure_screenshot(runtime: Runtime, region, step_index: int) -> str | None:
    """模板失败证据截图（优先失败步骤 searchRegion，其次主屏），失败仅 warning。"""
    try:
        import screenshot

        rect = None
        if isinstance(region, dict):
            try:
                width = int(region.get("width") or 0)
                height = int(region.get("height") or 0)
                if width > 0 and height > 0:
                    rect = {
                        "x": int(region.get("x") or 0),
                        "y": int(region.get("y") or 0),
                        "width": width,
                        "height": height,
                    }
            except (TypeError, ValueError):
                rect = None
        if rect is None:
            primary = runtime.input_impl.get_primary_screen()
            rect = {
                "x": int(primary["x"]),
                "y": int(primary["y"]),
                "width": int(primary["width"]),
                "height": int(primary["height"]),
            }
        data, width, height = screenshot.capture(rect["x"], rect["y"], rect["width"], rect["height"])
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = runtime.config.log_dir / ("skill-fail-%s-step%d.png" % (stamp, int(step_index)))
        saved = screenshot.save_image(data, width, height, path, fmt="png")
        return saved["absolutePath"]
    except Exception as exc:  # noqa: BLE001 - 截图失败不影响错误返回
        try:
            runtime.logger.warning("模板失败截图失败: %s", exc)
        except Exception:  # noqa: BLE001
            pass
        return None


def _skill_run_execute(
    params: dict,
    skill: dict,
    runtime: Runtime,
    abort_enabled: bool,
    abort_name: str | None,
    estimated_ms: int = 0,
) -> dict:
    """真实执行技能步骤（可 repeat；运行期失败返回 ok:true + success:false）。

    v0.5：每步开始/结束写 ``skill-progress.json``（SPEC 10.4），让 Host 运行期间可查询进度。
    """
    steps = skill.get("steps") or []
    repeat = int(params.get("repeat", 1))
    timeout_ms = int(params.get("timeoutMs", 300000))
    stop_on_error = bool(params.get("stopOnError", True))
    started = time.perf_counter()
    # v0.6：优先使用 action_skill_run 设置的总 deadline（monotonic 秒），兼容直接调用时回退
    runtime_deadline = getattr(runtime, "skill_deadline", None)
    if runtime_deadline is not None:
        deadline = float(runtime_deadline)
    else:
        deadline = time.monotonic() + timeout_ms / 1000.0
    contexts: dict = {}
    window_context: dict | None = None
    steps_log: list = []
    failed_step = None
    aborted = False
    abort_reason = None
    repeats_done = 0
    stop_outer = False
    total_steps = len(steps)
    run_started_at = iso_timestamp()
    progress_state = {"repeats_done": 0, "step": None}

    def _progress(
        running: bool,
        step: dict | None = None,
        repeat_count: int | None = None,
        finish: bool = False,
        success: bool | None = None,
        aborted_flag: bool | None = None,
        abort_reason_value: str | None = None,
    ) -> None:
        payload = {
            "running": bool(running),
            "skillId": skill["id"],
            "startedAt": run_started_at,
            "elapsedMs": int(round((time.perf_counter() - started) * 1000.0)),
            "repeat": repeat,
            "repeatsDone": progress_state["repeats_done"] if repeat_count is None else int(repeat_count),
            "step": step if step is not None else progress_state["step"],
            "updatedAt": iso_timestamp(),
        }
        if finish:
            payload["finishedAt"] = iso_timestamp()
            payload["success"] = bool(success)
            payload["aborted"] = bool(aborted_flag)
            payload["estimatedDurationMs"] = int(estimated_ms)
            if abort_reason_value:
                payload["abortReason"] = abort_reason_value
            if failed_step is not None:
                payload["failedStep"] = failed_step
        _write_skill_progress(runtime, payload)

    if total_steps > 0:
        progress_state["step"] = {
            "index": 0,
            "total": total_steps,
            "action": steps[0].get("action"),
            "startedAt": iso_timestamp(),
        }
        _progress(True, repeat_count=0)
    for _repeat_index in range(repeat):
        for index, step in enumerate(steps):
            if time.monotonic() > deadline:
                aborted = True
                abort_reason = "timeout"
                stop_outer = True
                break
            if abort_enabled and _key_down_safe(runtime, abort_name):
                aborted = True
                abort_reason = "abort-key"
                stop_outer = True
                break
            action = step["action"]
            step_wall = iso_timestamp()
            progress_state["step"] = {
                "index": index,
                "total": total_steps,
                "action": action,
                "startedAt": step_wall,
            }
            _progress(True, repeat_count=repeats_done)
            step_started = time.perf_counter()

            def _step_end(ok: bool) -> None:
                progress_state["step"] = {
                    "index": index,
                    "total": total_steps,
                    "action": action,
                    "startedAt": step_wall,
                    "elapsedMs": int(round((time.perf_counter() - step_started) * 1000.0)),
                    "ok": bool(ok),
                }
                progress_state["repeats_done"] = repeats_done
                _progress(True, repeat_count=repeats_done)

            try:
                expanded = _expand_skill_value(step.get("params") or {}, window_context, contexts)
                if not isinstance(expanded, dict):
                    raise GCError("INVALID_SKILL", "steps[%d].params 展开后不是对象" % index)
                # v0.5：把 skill_run 的 abortKey 透传给步骤内未显式设置的鼠标/找图动作
                if action in ("mouse_circle", "find_image", "wait_image") and abort_enabled and "abortKey" not in expanded:
                    expanded = dict(expanded, abortKey=abort_name)
                validated = validate_params(action, expanded)
                result = ACTION_HANDLERS[action](validated, runtime)
                step_duration = int(round((time.perf_counter() - step_started) * 1000.0))
                # 1) deadline / Esc / 动作内部中止优先：整体中止并透传 reason
                if isinstance(result, dict) and result.get("aborted"):
                    steps_log.append({"index": index, "action": action, "ok": True, "durationMs": step_duration})
                    aborted = True
                    abort_reason = str(result.get("abortReason") or "abort-key")
                    _step_end(False)
                    stop_outer = True
                    break
                # 2) v0.6.2：模板未找到 → 默认 fail（WAIT_IMAGE_TIMEOUT / TEMPLATE_NOT_FOUND + 失败截图）
                if action in SKILL_IMAGE_ACTIONS and _skill_template_missing(action, result):
                    on_missing = str(step.get("onMissing") or "fail")
                    if on_missing == "fail":
                        message, hint = _skill_missing_messages(action, validated if isinstance(validated, dict) else {})
                        code = "WAIT_IMAGE_TIMEOUT" if action == "wait_image" else "TEMPLATE_NOT_FOUND"
                        search_region = result.get("searchRegion") if isinstance(result, dict) else None
                        screenshot_path = _skill_failure_screenshot(runtime, search_region, index)
                        failed_entry = {
                            "index": index,
                            "action": action,
                            "code": code,
                            "message": message,
                            "hint": hint,
                        }
                        step_entry = {
                            "index": index,
                            "action": action,
                            "ok": False,
                            "code": code,
                            "message": message,
                            "durationMs": step_duration,
                        }
                        if screenshot_path:
                            failed_entry["screenshotPath"] = screenshot_path
                            step_entry["screenshotPath"] = screenshot_path
                        failed_step = failed_entry
                        steps_log.append(step_entry)
                        _step_end(False)
                        if not stop_on_error or bool(step.get("continueOnError")):
                            continue
                        stop_outer = True
                        break
                    # onMissing=continue：继续后续步骤（saveAs 得到 match=null，后续变量引用会给出明确提示）
                steps_log.append({"index": index, "action": action, "ok": True, "durationMs": step_duration})
                if step.get("saveAs"):
                    contexts[step["saveAs"]] = result
                if action == "activate_window" and isinstance(result, dict):
                    window_context = _skill_window_context(result.get("window")) or window_context
                elif action == "list_windows" and isinstance(result, dict):
                    windows = result.get("windows") or []
                    if windows:
                        window_context = _skill_window_context(windows[0]) or window_context
                wait_ms = int(step.get("waitMs") or 0)
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000.0)
                _step_end(True)
            except BaseException as exc:  # noqa: BLE001 - 单步失败按技能语义处理
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                error = _wrap_exception(exc, action, runtime.logger)
                step_duration = int(round((time.perf_counter() - step_started) * 1000.0))
                steps_log.append(
                    {
                        "index": index,
                        "action": action,
                        "ok": False,
                        "code": error.code,
                        "message": str(error),
                        "durationMs": step_duration,
                    }
                )
                failed_step = {
                    "index": index,
                    "action": action,
                    "code": error.code,
                    "message": str(error),
                }
                _step_end(False)
                if not stop_on_error or bool(step.get("continueOnError")):
                    continue
                stop_outer = True
                break
            if time.monotonic() > deadline:
                aborted = True
                abort_reason = "timeout"
                stop_outer = True
                break
        if stop_outer:
            break
        repeats_done += 1
        progress_state["repeats_done"] = repeats_done
        _progress(True, repeat_count=repeats_done)
    duration_ms = int(round((time.perf_counter() - started) * 1000.0))
    result_success = bool(failed_step is None and not aborted)
    _progress(
        False,
        repeat_count=repeats_done,
        finish=True,
        success=result_success,
        aborted_flag=bool(aborted),
        abort_reason_value=abort_reason,
    )
    result = {
        "skillId": skill["id"],
        "dryRun": False,
        "repeat": repeat,
        "repeatsDone": repeats_done,
        "success": result_success,
        "aborted": bool(aborted),
        "abortReason": abort_reason,
        "durationMs": duration_ms,
        "estimatedDurationMs": int(estimated_ms),
        "steps": steps_log,
    }
    if failed_step is not None:
        result["failedStep"] = failed_step
    return result


STEP_FIXED_OVERHEAD_MS = 500
FIND_IMAGE_MAX_MS = 8000
OTHER_STEP_MAX_MS = 1000


def _as_int_param(value, default: int) -> int:
    """从步骤参数里取整数（变量 ``$...``/非法值用默认值，估算不抛错）。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _estimate_skill_duration(steps: list) -> int:
    """估算技能最坏执行时长（SPEC 10.4）。

    ``Σ(step 最大耗时) + ΣwaitMs + 500ms×steps``；wait=durationMs、
    mouse_circle=leadInMs+durationMs、wait_image=timeoutMs、find_image=8000，其它=1000。
    """
    total = 0
    for step in steps:
        action = step.get("action")
        params = step.get("params") or {}
        wait_ms = _as_int_param(step.get("waitMs"), 0)
        if action == "wait":
            step_max = _as_int_param(params.get("durationMs"), 0)
        elif action == "mouse_circle":
            step_max = _as_int_param(params.get("leadInMs"), 300) + _as_int_param(params.get("durationMs"), 30000)
        elif action == "wait_image":
            step_max = _as_int_param(params.get("timeoutMs"), 15000)
        elif action == "find_image":
            step_max = FIND_IMAGE_MAX_MS
        else:
            step_max = OTHER_STEP_MAX_MS
        total += max(0, step_max) + max(0, wait_ms) + STEP_FIXED_OVERHEAD_MS
    return int(total)


def _static_validate_skill_steps(steps: list) -> None:
    """前置校验每步参数类型/范围（可复用 validate_params；含 ``$...`` 变量的步骤跳过）。"""
    for index, step in enumerate(steps):
        action = step.get("action")
        if action not in ACTION_PARAMS:
            raise GCError(
                "INVALID_SKILL",
                "steps[%d].action 不允许: %r" % (index, action),
                hint="技能步骤不能调用 skill_* 或未知动作",
            )
        params = step.get("params") or {}
        if not isinstance(params, dict):
            raise GCError("INVALID_SKILL", "steps[%d].params 必须是对象" % index)
        if _count_skill_refs(params) > 0:
            continue  # 变量值无法静态确定类型，跳过深度校验（运行期再校验）
        try:
            validate_params(action, params)
        except GCError as exc:
            raise GCError(
                exc.code,
                "steps[%d].action=%s 参数校验失败: %s" % (index, action, exc),
                hint=exc.hint,
            ) from exc


def action_skill_run(params: dict, runtime: Runtime) -> dict:
    """执行技能（SPEC 8.3.2 + v0.5 10.4 + v0.6 11.3）：变量替换 / repeat / timeout / abortKey / dryRun。

    v0.6：整个 skill_run 设置 ``runtime.skill_deadline``（time.monotonic 秒），动作内部据此
    优雅提前停止（不再被 Host 强杀）；结束/异常 finally 清除 deadline。
    """
    store = runtime.get_skills_store()
    skill = store.get(params["id"])["skill"]
    steps = skill.get("steps") or []
    if not steps:
        raise GCError("INVALID_SKILL", "技能没有可执行步骤: %s" % skill.get("id"))
    abort_enabled, abort_name = _abort_key_enabled(params.get("abortKey", "esc"))
    dry_run = bool(params.get("dryRun", False))
    timeout_ms = int(params.get("timeoutMs", 300000))
    # v0.6：设置总 deadline（结束/异常都清除）
    runtime.skill_deadline = time.monotonic() + timeout_ms / 1000.0
    try:
        # 前置校验（dryRun 也执行）
        _static_validate_skill_steps(steps)
        estimated_ms = _estimate_skill_duration(steps)
        if dry_run:
            # dryRun 严格拒绝：估算超过 timeoutMs 时无法保证真实运行能完成
            if estimated_ms > timeout_ms:
                raise GCError(
                    "INVALID_ARGUMENT",
                    "技能预计最坏耗时 %dms 超过 timeoutMs=%dms" % (estimated_ms, timeout_ms),
                    hint="至少需要 %d ms，可增大 timeoutMs 或缩小 wait_image/mouse_circle 参数，或减少步骤"
                    % estimated_ms,
                )
            result = _skill_run_dry(params, skill, abort_enabled, abort_name)
            result["estimatedDurationMs"] = int(estimated_ms)
            return result
        if estimated_ms > timeout_ms:
            # v0.6：真实运行不直接拒绝，交由总 deadline 优雅中止（SPEC 11.3/11.7）
            runtime.logger.warning(
                "skill_run estimatedMs=%d 超过 timeoutMs=%d，将在总 deadline 到时优雅中止",
                estimated_ms,
                timeout_ms,
            )
        runtime.logger.info(
            "skill_run start id=%s steps=%d repeat=%d timeoutMs=%d estimatedMs=%d stopOnError=%s abortKey=%s",
            skill["id"],
            len(steps),
            int(params.get("repeat", 1)),
            timeout_ms,
            estimated_ms,
            bool(params.get("stopOnError", True)),
            abort_name or "none",
        )
        result = _skill_run_execute(params, skill, runtime, abort_enabled, abort_name, estimated_ms=estimated_ms)
        runtime.logger.info(
            "skill_run end id=%s success=%s repeatsDone=%d aborted=%s reason=%s durationMs=%d",
            result["skillId"],
            result["success"],
            result["repeatsDone"],
            result["aborted"],
            result["abortReason"],
            result["durationMs"],
        )
        return result
    finally:
        runtime.skill_deadline = None


# ---------------------------------------------------------------------------
# 主流程


ACTION_HANDLERS = {
    "status": action_status,
    "mouse_move": action_mouse_move,
    "mouse_move_rel": action_mouse_move_rel,
    "mouse_click": action_mouse_click,
    "mouse_down": action_mouse_down,
    "mouse_up": action_mouse_up,
    "mouse_drag": action_mouse_drag,
    "mouse_scroll": action_mouse_scroll,
    "mouse_position": action_mouse_position,
    "key_press": action_key_press,
    "key_down": action_key_down,
    "key_up": action_key_up,
    "hotkey": action_hotkey,
    "type_text": action_type_text,
    "screenshot": action_screenshot,
    "list_displays": action_list_displays,
    "list_windows": action_list_windows,
    "capture_window": action_capture_window,
    # v0.3 游戏自动化
    "activate_window": action_activate_window,
    "mouse_circle": action_mouse_circle,
    "wait": action_wait,
    "find_image": action_find_image,
    "wait_image": action_wait_image,
    # v0.3 技能库
    "skill_list": action_skill_list,
    "skill_get": action_skill_get,
    "skill_save": action_skill_save,
    "skill_versions": action_skill_versions,
    "skill_restore": action_skill_restore,
    "skill_delete": action_skill_delete,
    "skill_run": action_skill_run,
    # v0.5：技能运行进度（读 skill-progress.json）
    "skill_progress": action_skill_progress,
}


# ---------------------------------------------------------------------------
# 请求解析 / 响应信封
# ---------------------------------------------------------------------------


def parse_request(raw) -> tuple:
    """解析一条请求，返回 ``(request_id_or_UNSET, action, params)``。"""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise GCError("INVALID_ARGUMENT", "请求为空", hint="每行发送一个 JSON 对象")
        try:
            request = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GCError(
                "INVALID_ARGUMENT",
                "请求不是合法 JSON: %s" % exc.msg,
                hint="每行发送一个 JSON 对象，例如 {\"action\":\"status\"}",
            )
    elif isinstance(raw, dict):
        request = raw
    else:
        raise GCError("INVALID_ARGUMENT", "请求必须是 JSON 对象")
    if not isinstance(request, dict):
        raise GCError("INVALID_ARGUMENT", "请求必须是 JSON 对象")
    request_id = request["id"] if "id" in request else _UNSET
    action = request.get("action")
    if not isinstance(action, str) or not action.strip():
        raise GCError(
            "INVALID_ARGUMENT",
            "缺少 action 或 action 不是非空字符串",
            hint="例如 {\"action\":\"status\"}",
        )
    action = action.strip()
    if action not in ACTION_PARAMS:
        raise GCError(
            "UNKNOWN_ACTION",
            "未知 action: %s" % action,
            hint="可用动作: %s" % ", ".join(sorted(ACTION_PARAMS)),
        )
    params = request.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise GCError("INVALID_ARGUMENT", "params 必须是 JSON 对象")
    return request_id, action, params


def _peek_request_id(raw) -> object:
    """尽量从请求里取出 id（即使 action 非法也要在响应里回显）。

    worker 协议要求回显同一 id；解析失败时返回 ``_UNSET``。
    """
    return _peek_field(raw, "id")


def _peek_field(raw, field: str) -> object:
    """从原始请求里尽量取出某个字段；解析失败返回 ``_UNSET``。"""
    try:
        request = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError, ValueError):
        return _UNSET
    if isinstance(request, dict) and field in request:
        return request[field]
    return _UNSET


def execute_request(raw, runtime: Runtime) -> dict:
    """执行一条请求并返回响应信封；任何异常都转成 ``ok:false``（进程继续工作）。"""
    started = time.perf_counter()
    request_id = _peek_request_id(raw)
    raw_action = _peek_field(raw, "action")
    action = None
    try:
        request_id, action, params = parse_request(raw)
        validated = validate_params(action, params)
        result = ACTION_HANDLERS[action](validated, runtime)
        duration_ms = (time.perf_counter() - started) * 1000.0
        extra = {}
        if action == "type_text":
            # 审计只记长度，不记原文
            extra["length"] = len(validated.get("text") or "")
        runtime.audit.record(action, True, duration_ms, **extra)
        runtime.logger.debug("action=%s ok durationMs=%.1f", action, duration_ms)
        response = {}
        if request_id is not _UNSET:
            response["id"] = request_id
        response.update(
            {
                "ok": True,
                "action": action,
                "result": result,
                "durationMs": int(round(duration_ms)),
                "backend": runtime.backend_name,
                "ts": iso_timestamp(),
            }
        )
        return response
    except BaseException as exc:  # noqa: BLE001 - 协议层必须吞掉一切可恢复异常
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        duration_ms = (time.perf_counter() - started) * 1000.0
        error = _wrap_exception(exc, action or "(invalid)", runtime.logger)
        if isinstance(action, str) and action:
            display_action = action
        elif isinstance(raw_action, str) and raw_action.strip():
            display_action = raw_action.strip()
        else:
            display_action = None
        try:
            runtime.audit.record(
                display_action or "(invalid)",
                False,
                duration_ms,
                code=error.code,
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响错误响应
            pass
        runtime.logger.warning(
            "action=%s failed code=%s message=%s", display_action or "(invalid)", error.code, error
        )
        response = {}
        if request_id is not _UNSET:
            response["id"] = request_id
        response.update(
            {
                "ok": False,
                "durationMs": int(round(duration_ms)),
                "error": {
                    "code": error.code,
                    "message": str(error),
                },
            }
        )
        if display_action:
            response["action"] = display_action
        if error.hint:
            response["error"]["hint"] = error.hint
        return response


# ---------------------------------------------------------------------------
# 自检（--selftest，只读、无输入副作用）
# ---------------------------------------------------------------------------


def _network_import_scan() -> list:
    """扫描 python/*.py 是否 import 了联网库；返回违规文件名列表。"""
    banned_roots = {"socket", "urllib", "http", "requests", "websocket"}
    offenders = []
    pattern = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
    for path in sorted(SCRIPT_DIR.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for root in pattern.findall(text):
            if root.split(".")[0] in banned_roots:
                offenders.append("%s: %s" % (path.name, root))
    return offenders


def _selftest_checks(runtime: Runtime) -> list:
    checks: list = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    add("Python 版本", sys.version_info >= (3, 9), platform.python_version())
    add("平台", True, "%s (%s)" % (runtime.platform_name, sys.platform))

    # 平台原生层只读自检（Windows 会检查 INPUT 结构大小、DPI、几何、鼠标位置、键名解析）
    try:
        native_report = runtime.input_impl.selftest()
        for item in native_report.get("checks", []):
            add("native: %s" % item.get("name", "?"), item.get("ok", False), item.get("detail", ""))
    except Exception as exc:  # noqa: BLE001
        add("native 自检", False, str(exc))

    # 截图后端可用性（不实际截图）
    try:
        import screenshot

        backend = screenshot.available_backend()
        add(
            "截图后端",
            bool(backend.get("ok")),
            "%s (%s)" % (backend.get("backend"), backend.get("detail")),
        )
        has_pillow = bool(screenshot._has_pillow())
        pillow_detail = "缺失，需要 pip install pillow"
        if has_pillow:
            try:
                import PIL

                pillow_detail = "Pillow %s" % getattr(PIL, "__version__", "已安装")
            except Exception:  # noqa: BLE001
                pillow_detail = "已安装"
        add("Pillow 可用（PNG/JPEG 编码）", has_pillow, pillow_detail)
    except Exception as exc:  # noqa: BLE001
        add("截图后端", False, str(exc))

    # v0.2：窗口枚举 / 窗口捕获能力（只读，不截图、不注入输入）
    if IS_WINDOWS:
        try:
            import window_capture

            window_report = window_capture.selftest()
            for item in window_report.get("checks", []):
                add(item.get("name", "窗口自检"), item.get("ok", False), item.get("detail", ""))
        except Exception as exc:  # noqa: BLE001
            add("窗口枚举可用", False, str(exc))
    else:
        add("窗口枚举可用", True, "非 Windows 平台跳过（UNSUPPORTED_PLATFORM）")

    # v0.3：numpy / 模板目录 / 技能库目录 / GetAsyncKeyState / 纯函数模块
    add("numpy 可用（find_image）", _numpy_available(), "缺失时 find_image 返回 MISSING_DEPENDENCY")
    try:
        import circle_motion

        stats = circle_motion.circle_points(center_x=100, center_y=100, radius=10, duration_ms=1000)
        add(
            "circle_motion 纯函数",
            stats["samples"] > 0 and len(stats["start"]) == 2,
            "samples=%d loops=%.3f" % (stats["samples"], stats["loops"]),
        )
    except Exception as exc:  # noqa: BLE001
        add("circle_motion 纯函数", False, str(exc))
    try:
        import template_match

        add(
            "template_match 模块",
            bool(hasattr(template_match, "find_template")),
            "numpy FFT NCC + NMS",
        )
    except Exception as exc:  # noqa: BLE001
        add("template_match 模块", False, str(exc))
    # v0.4：JSONC 解析 / 归一化 / 中文注释渲染
    try:
        import skills as skills_module

        sample = "// 行注释\n{\n  /* 块注释 */\n  \"a\": 1,\n  \"b\": \"字符串内 // 不是注释\"\n}\n"
        parsed = skills_module.parse_jsonc(sample)
        normalized = skills_module.normalize_skill(
            {
                "schema": 1,
                "id": "selftest-jsonc",
                "title": "自检",
                "steps": [{"action": "mouse_click", "params": {"x": 10, "y": 20}}],
            }
        )
        rendered = skills_module.render_skill_jsonc(normalized)
        step_params = normalized["steps"][0]["params"]
        ok = (
            parsed.get("a") == 1
            and parsed.get("b") == "字符串内 // 不是注释"
            and step_params.get("clicks") == 1
            and step_params.get("button") == "left"
            and "// 第 1 步：" in rendered
            and '"waitMs": 0' in rendered
        )
        add("JSONC 解析/渲染", ok, "strip_jsonc + normalize_skill + render_skill_jsonc")
    except Exception as exc:  # noqa: BLE001
        add("JSONC 解析/渲染", False, str(exc))
    for label, directory in (
        ("模板目录可创建", runtime.config.templates_dir),
        ("技能库目录可创建", runtime.config.skills_dir),
    ):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".selftest-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(label, True, str(directory))
        except OSError as exc:
            # 与日志目录检查一致：只读环境（如 DSH 沙箱）下退回系统临时目录验证写能力
            try:
                import tempfile as _tempfile

                with _tempfile.NamedTemporaryFile(prefix="dgc-selftest-", delete=True) as handle:
                    handle.write(b"ok")
                add(label, True, "配置目录不可写（%s），系统临时目录可创建" % exc)
            except OSError as temp_exc:
                add(label, False, "不可写（%s）: %s；临时目录也不可用: %s" % (directory, exc, temp_exc))
    if IS_WINDOWS:
        try:
            import win_native

            add(
                "GetAsyncKeyState 可用",
                bool(win_native.key_state_available()),
                "Esc 等中止键轮询可用",
            )
        except Exception as exc:  # noqa: BLE001
            add("GetAsyncKeyState 可用", False, str(exc))
    else:
        add("GetAsyncKeyState 可用", True, "非 Windows 平台跳过（中止键不可用）")

    # 目录可写检查：优先配置目录，失败时退到系统临时目录（不因只读环境影响自检）
    import tempfile

    writable = False
    detail = ""
    try:
        runtime.config.log_dir.mkdir(parents=True, exist_ok=True)
        probe = runtime.config.log_dir / ".selftest-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
        detail = str(runtime.config.log_dir)
    except OSError as exc:
        detail = "配置目录不可写（%s），尝试系统临时目录" % exc
        try:
            with tempfile.NamedTemporaryFile(prefix="dgc-selftest-", delete=True) as handle:
                handle.write(b"ok")
            writable = True
            detail += "；临时目录可用"
        except OSError as temp_exc:
            detail += "；临时目录也不可用: %s" % temp_exc
    add("日志目录可写", writable, detail)

    # 键名解析覆盖（Windows 直接问 win_native，不发送事件）
    if IS_WINDOWS:
        try:
            import win_native

            bad_named = [name for name in sorted(NAMED_KEYS) if win_native.key_info(name)[0] is None]
            bad_chars = [ch for ch in "abcdefghijklmnopqrstuvwxyz0123456789-=[];',./`" if win_native.vk_for_char(ch)[0] is None]
            add("键名解析", not bad_named and not bad_chars, "失败: %s %s" % (bad_named, bad_chars))
        except Exception as exc:  # noqa: BLE001
            add("键名解析", False, str(exc))
    else:
        add("键名解析", True, "非 Windows 平台由 pynput/xdotool 解析")

    # 源码无联网 import
    offenders = _network_import_scan()
    add("源码无联网 import", not offenders, "; ".join(offenders) or "socket/urllib/http/requests/websocket 均未使用")

    return checks


def run_selftest(runtime: Runtime, emitter: Emitter) -> int:
    started = time.perf_counter()
    checks = _selftest_checks(runtime)
    failures = [item["name"] for item in checks if not item["ok"]]
    report = {
        "ok": not failures,
        "event": "selftest",
        "action": "selftest",
        "version": BACKEND_VERSION,
        "platform": runtime.platform_name,
        "pythonVersion": platform.python_version(),
        "backend": runtime.backend_name,
        "checks": checks,
        "failures": failures,
        "durationMs": int(round((time.perf_counter() - started) * 1000.0)),
        "ts": iso_timestamp(),
    }
    emitter.emit(report)
    runtime.logger.info("selftest ok=%s failures=%s", report["ok"], failures)
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------------------
# 运行模式
# ---------------------------------------------------------------------------


def version_payload() -> dict:
    """``--version``：Host 用它探测 Python 是否可用。

    额外提供 ``pythonVersionText``（形如 ``Python 3.12.14``）：部分探测脚本用
    ``/python\\s+(\\d+\\.\\d+\\.\\d+)/`` 从 stdout+stderr 里提取解释器版本，
    纯 JSON 的 ``pythonVersion`` 字段没有空格，可能导致探测失败。
    """
    version = platform.python_version()
    return {
        "ok": True,
        "event": "version",
        "name": "dsh-game-control-python",
        "version": BACKEND_VERSION,
        "protocol": PROTOCOL_VERSION,
        "platform": _platform_name(),
        "pythonVersion": version,
        "pythonVersionText": "Python %s" % version,
        "executable": sys.executable,
        "scriptDir": str(SCRIPT_DIR),
        "backend": "win32-sendinput" if IS_WINDOWS else "auto",
    }


def ready_payload(runtime: Runtime) -> dict:
    """``--serve`` 启动后的第一行事件。"""
    return {
        "ok": True,
        "event": "ready",
        "version": BACKEND_VERSION,
        "protocol": PROTOCOL_VERSION,
        "platform": runtime.platform_name,
        "pythonVersion": platform.python_version(),
        "backend": runtime.backend_name,
        "dpiAware": bool(runtime.dpi_state.get("aware")),
        "pid": os.getpid(),
        "shotDir": str(runtime.config.shot_dir),
        "logDir": str(runtime.config.log_dir),
        "ts": iso_timestamp(),
    }


def run_stdin(runtime: Runtime, emitter: Emitter, send_ready: bool) -> int:
    """stdin 流 / worker 模式：每行一个请求，每行一个响应，EOF 退出。"""
    if send_ready:
        emitter.emit(ready_payload(runtime))
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        line = line.strip()
        if not line:
            continue
        response = execute_request(line, runtime)
        emitter.emit(response)
    return 0


def run_json(raw: str, runtime: Runtime, emitter: Emitter) -> int:
    """单次 CLI：打印一行 JSON 后退出；动作失败也返回退出码 0。"""
    emitter.emit(execute_request(raw, runtime))
    return 0


USAGE = """用法: python game_control.py <模式>

模式（互斥）:
  --json '<JSON>'   执行单条请求，stdout 打印一行 JSON 后退出
  --stdin           逐行读取 stdin 的请求，逐行输出响应（读到 EOF 退出）
  --serve           常驻 worker：先输出 {"ok":true,"event":"ready",...}，再逐行请求/响应
  --selftest        只读自检（不移动/点击/按键），通过退出码 0
  --version         输出版本 JSON

环境变量:
  GAME_CONTROL_HOME  数据根目录（默认 %USERPROFILE%\\.dsh\\game-control 或 ~/.dsh/game-control）
  SHOT_DIR           截图输出目录（默认 <HOME>/shots）
  LOG_DIR            日志目录（默认 <HOME>/logs）

示例:
  python game_control.py --json '{"action":"status"}'
  python game_control.py --json '{"action":"screenshot","params":{"region":{"x":0,"y":0,"width":200,"height":120}}}'
"""


FLAG_TO_OPTION = {
    "--stdin": "stdin",
    "--serve": "serve",
    "--selftest": "selftest",
    "--version": "version",
    "--help": "help",
    "-h": "help",
}


def parse_args(argv: list) -> SimpleNamespace:
    """极简参数解析：避免 argparse 在未知参数时直接退出码 2 并打印非 JSON 文本。"""
    options = {
        "json": None,
        "stdin": False,
        "serve": False,
        "selftest": False,
        "version": False,
        "help": False,
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--json":
            if index + 1 >= len(argv):
                raise UsageError("--json 需要一个 JSON 字符串参数")
            options["json"] = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--json="):
            options["json"] = arg[len("--json=") :]
            index += 1
            continue
        if arg in FLAG_TO_OPTION:
            options[FLAG_TO_OPTION[arg]] = True
            index += 1
            continue
        raise UsageError("未知参数: %s" % arg)
    if options["help"]:
        return SimpleNamespace(mode="help")
    modes = [
        name
        for name in ("json", "stdin", "serve", "selftest", "version")
        if options[name] not in (None, False)
    ]
    if len(modes) > 1:
        raise UsageError("运行模式互斥，不能同时使用: %s" % ", ".join("--" + m for m in modes))
    if not modes:
        raise UsageError("未指定运行模式")
    return SimpleNamespace(mode=modes[0], raw=options["json"])


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    emitter = Emitter()
    try:
        options = parse_args(argv)
    except UsageError as exc:
        sys.stderr.write("参数错误: %s\n\n%s" % (exc, USAGE))
        emitter.emit(
            {
                "ok": False,
                "error": {"code": "INVALID_ARGUMENT", "message": "命令行参数错误: %s" % exc, "hint": USAGE},
            }
        )
        return 2
    if options.mode == "help":
        sys.stdout.write(USAGE)
        return 0
    if options.mode == "version":
        # 版本探测必须轻量：不创建日志目录、不加载截图依赖
        emitter.emit(version_payload())
        return 0

    config = Config()
    logger = setup_logging(config, options.mode)
    logger.info(
        "game-control backend start: mode=%s version=%s python=%s cwd=%s",
        options.mode,
        BACKEND_VERSION,
        platform.python_version(),
        os.getcwd(),
    )
    try:
        runtime = Runtime(config, logger)
    except BaseException as exc:  # noqa: BLE001 - 启动阶段致命错误 → 退出码 3
        logger.exception("启动失败")
        sys.stderr.write("game_control 启动失败: %s\n" % exc)
        return 3

    try:
        if options.mode == "selftest":
            return run_selftest(runtime, emitter)
        if options.mode == "json":
            return run_json(options.raw or "", runtime, emitter)
        return run_stdin(runtime, emitter, send_ready=options.mode == "serve")
    except BrokenPipeError:
        # Host 关闭管道：安静退出
        try:
            sys.stderr.close()
        except Exception:  # noqa: BLE001
            pass
        return 0
    except KeyboardInterrupt:
        logger.info("收到中断信号，退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
