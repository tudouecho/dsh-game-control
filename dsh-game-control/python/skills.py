"""技能库存储（SPEC 8.3.1 + v0.4 第 9 节：JSONC + 参数显式化 + 中文注释）。

目录结构（``<GAME_CONTROL_HOME>/skills/``）::

    skills/<id>/skill.json              # 当前版本（JSONC：允许 // 与 /* */ 注释；原子写 tmp+os.replace）
    skills/<id>/backups/<ts>.json       # 每次保存/恢复/删除前的旧版本（原始 JSONC 文本），最多 50 份（新的在前）
    skills/.trash/<id>-<ts>/            # 软删除（回收目录）

v0.4 关键行为：
- 读取：``strip_jsonc()`` 剥离注释（字符串内的 ``//`` 不误删）后 ``json.loads``；旧的无注释 JSON 完全兼容；
- 写入：``normalize_skill()`` 按 SPEC 9.2 给每步补齐显式默认参数与 ``waitMs``/``continueOnError``，
  再由 ``render_skill_jsonc()`` 重新渲染带中文步骤注释的 JSONC 文本；
- ``save()`` 支持结构化字段或整份 ``content``（JSONC 文本）两种入口；
- 备份保存**原始文本**，``restore`` 原样恢复备份文本（保留注释）。

错误码：``SKILL_NOT_FOUND`` / ``INVALID_SKILL`` / ``FILE_IO_ERROR``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
MAX_BACKUPS = 50
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SAVE_AS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")

TITLE_MAX = 200
DESCRIPTION_MAX = 2000
GAME_MAX = 100
TAGS_MAX = 20
TAG_MAX = 40
MARKDOWN_MAX = 200000
STEPS_MIN, STEPS_MAX = 1, 200
STEP_PARAMS_MAX_BYTES = 32768
WAIT_MS_MAX = 60000
NOTE_MAX = 200
CONTENT_MAX = 1_000_000

# 单步允许字段（SPEC 9.1：新增 note，自定义中文备注，≤200；v0.6.2 新增 onMissing）
STEP_ALLOWED_FIELDS = frozenset(
    {"action", "params", "saveAs", "continueOnError", "waitMs", "note", "onMissing"}
)

# v0.6.2：模板类动作支持步骤级“找不到时”策略（SPEC 13.2.1）
ON_MISSING_ACTIONS = frozenset({"find_image", "wait_image"})
ON_MISSING_VALUES = ("fail", "continue")
ON_MISSING_LABELS = {"fail": "失败", "continue": "继续"}

# SPEC 9.2：保存时写入文件的最小显式默认参数集合（未列出的参数不补全）
NORMALIZE_DEFAULTS = {
    "activate_window": {"restore": True, "timeoutMs": 2000},
    "mouse_move": {"durationMs": 0},
    "mouse_click": {"button": "left", "clicks": 1, "intervalMs": 60},
    "mouse_down": {"button": "left"},
    "mouse_up": {"button": "left"},
    "mouse_drag": {"button": "left", "durationMs": 300, "steps": 20},
    "mouse_scroll": {"horizontal": False},
    "mouse_circle": {
        "periodMs": 2500,
        "durationMs": 30000,
        "direction": "cw",
        "startAngleDeg": -90,
        "samplesPerSecond": 120,
        "leadInMs": 300,
        "button": "none",
        "abortKey": "esc",
    },
    "key_press": {"presses": 1, "intervalMs": 60},
    "type_text": {"intervalMs": 12},
    "wait": {},
    "find_image": {
        "threshold": 0.85,
        "grayscale": True,
        "maxResults": 3,
        "scales": [1.0],
        "abortKey": "esc",
    },
    "wait_image": {
        "threshold": 0.85,
        "grayscale": True,
        "maxResults": 3,
        "scales": [1.0],
        "timeoutMs": 15000,
        "intervalMs": 250,
        "mode": "appear",
        "abortKey": "esc",
    },
    "screenshot": {"format": "png", "base64": False, "allScreens": False},
    "capture_window": {"engine": "auto", "includeCursor": False, "timeoutMs": 3000},
}

# 动作中文名（用于逐步骤注释）
ACTION_LABELS = {
    "status": "后端状态",
    "list_displays": "显示器列表",
    "list_windows": "窗口列表",
    "activate_window": "激活窗口",
    "wait": "等待",
    "mouse_move": "鼠标移动",
    "mouse_move_rel": "鼠标相对移动",
    "mouse_click": "鼠标点击",
    "mouse_down": "鼠标按下",
    "mouse_up": "鼠标抬起",
    "mouse_drag": "鼠标拖拽",
    "mouse_scroll": "鼠标滚轮",
    "mouse_circle": "圆形轨迹",
    "mouse_position": "读取鼠标位置",
    "key_press": "按键",
    "key_down": "按下键",
    "key_up": "松开键",
    "hotkey": "组合键",
    "type_text": "输入文本",
    "find_image": "模板找图",
    "wait_image": "等待模板",
    "screenshot": "截图",
    "capture_window": "窗口截图",
}

BUTTON_LABELS = {"left": "左键", "right": "右键", "middle": "中键", "x1": "侧键1", "x2": "侧键2", "none": "无"}
DIRECTION_LABELS = {"cw": "顺时针", "ccw": "逆时针"}
MODE_LABELS = {"appear": "出现", "disappear": "消失"}


class SkillError(Exception):
    """技能库错误；``code`` / ``hint`` 直接进入响应信封。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# JSONC 解析（strip_jsonc）
# ---------------------------------------------------------------------------


def strip_jsonc(text: str) -> str:
    """剥离 JSONC 的 ``//`` 行注释与 ``/* */`` 块注释，返回可被 ``json.loads`` 解析的文本。

    逐字符状态机，**字符串内部的 ``//`` / ``/*`` 不会误删**；保留字符串转义与换行。
    纯 JSON（无注释）原样兼容。
    """
    if not isinstance(text, str):
        raise SkillError("INVALID_SKILL", "JSONC 内容必须是字符串")
    output: list = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False
    while index < length:
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "/":
                index += 2
                while index < length and text[index] not in "\r\n":
                    index += 1
                continue
            if nxt == "*":
                index += 2
                closed = False
                while index + 1 < length:
                    if text[index] == "*" and text[index + 1] == "/":
                        index += 2
                        closed = True
                        break
                    index += 1
                if not closed:
                    raise SkillError("INVALID_SKILL", "JSONC 块注释未闭合（缺少 */）")
                continue
        output.append(char)
        index += 1
    if in_string:
        raise SkillError("INVALID_SKILL", "JSONC 字符串未闭合")
    return "".join(output)


def parse_jsonc(text: str, label: str = "技能内容") -> dict:
    """剥离注释并解析为 JSON 对象；解析失败 → INVALID_SKILL。"""
    stripped = strip_jsonc(text)
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SkillError(
            "INVALID_SKILL",
            "%s JSON 解析失败：%s（第 %d 行第 %d 列）" % (label, exc.msg, exc.lineno, exc.colno),
            hint="确认 JSON 结构完整；注释用 // 或 /* */，字符串内的 // 不会当作注释",
        ) from exc
    if not isinstance(document, dict):
        raise SkillError("INVALID_SKILL", "%s 必须是 JSON 对象" % label)
    return document


# ---------------------------------------------------------------------------
# 归一化（normalize_skill）
# ---------------------------------------------------------------------------


def _deep_copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        raise SkillError("INVALID_SKILL", "技能内容不是合法 JSON 值")


def normalize_skill(document: dict) -> dict:
    """按 SPEC 9.2 补全每步显式默认参数，并固定写入 ``waitMs`` / ``continueOnError``。

    纯变换（不校验字段合法性）；``saveAs`` / ``note`` 原样保留；``$window.*`` 等变量值保持原样。
    """
    if not isinstance(document, dict):
        raise SkillError("INVALID_SKILL", "技能文档必须是对象")
    result = _deep_copy(document)
    steps = result.get("steps")
    if not isinstance(steps, list):
        raise SkillError("INVALID_SKILL", "steps 必须是数组")
    normalized_steps = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SkillError("INVALID_SKILL", "steps[%d] 必须是对象" % index)
        action = step.get("action")
        params = step.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise SkillError("INVALID_SKILL", "steps[%d].params 必须是对象" % index)
        merged = _deep_copy(params)
        for key, value in NORMALIZE_DEFAULTS.get(action, {}).items():
            if key not in merged:
                merged[key] = _deep_copy(value)
        normalized_step = {
            "action": action,
            "params": merged,
            "waitMs": int(step.get("waitMs") or 0),
            "continueOnError": bool(step.get("continueOnError", False)),
        }
        if "saveAs" in step:
            normalized_step["saveAs"] = step["saveAs"]
        if "note" in step:
            normalized_step["note"] = step["note"]
        # v0.6.2：find_image / wait_image 显式写 onMissing（默认 fail）
        if action in ON_MISSING_ACTIONS:
            normalized_step["onMissing"] = step.get("onMissing", "fail")
        normalized_steps.append(normalized_step)
    result["steps"] = normalized_steps
    return result


# ---------------------------------------------------------------------------
# 中文摘要 / 注释渲染（render_skill_jsonc）
# ---------------------------------------------------------------------------


def _show(value) -> str:
    """参数值展示：变量保留原文，字符串加引号。"""
    if isinstance(value, str) and value.startswith("$"):
        return value
    if isinstance(value, str):
        return '"%s"' % value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_show(item) for item in value) + "]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _position(params: dict) -> str:
    if "x" in params and "y" in params:
        return "(%s, %s)" % (_show(params["x"]), _show(params["y"]))
    if "x1" in params and "y1" in params and "x2" in params and "y2" in params:
        return "(%s, %s)→(%s, %s)" % (
            _show(params["x1"]),
            _show(params["y1"]),
            _show(params["x2"]),
            _show(params["y2"]),
        )
    return "当前指针位置"


def _region_text(params: dict) -> str:
    region = params.get("region")
    if isinstance(region, dict):
        return "区域(%s,%s %sx%s)" % (
            _show(region.get("x", 0)),
            _show(region.get("y", 0)),
            _show(region.get("width", 0)),
            _show(region.get("height", 0)),
        )
    hwnd = params.get("hwnd")
    window = params.get("window")
    if hwnd is not None:
        return "窗口 hwnd=%s 的 clientRect" % _show(hwnd)
    if window:
        return "窗口“%s”的 clientRect" % window
    return "主屏"


def _template_text(params: dict) -> str:
    """模板描述：单个模板或多模板列表 + 中止键（v0.5）。"""
    templates = params.get("templates")
    if isinstance(templates, list) and templates:
        shown = ", ".join(str(item) for item in templates)
        text = "多模板 [%s]" % shown
    else:
        text = "模板“%s”" % str(params.get("template"))
    return "%s（中止键 %s）" % (text, str(params.get("abortKey", "esc")))


def _summarize_step(action: str, params: dict) -> str:
    """为一步动作生成中文参数摘要（SPEC 9.1 要求覆盖主要动作与关键参数）。"""
    if not isinstance(params, dict):
        params = {}
    if action == "activate_window":
        target = "标题包含“%s”" % params["window"] if params.get("window") else "hwnd=%s" % _show(params.get("hwnd"))
        return "前置窗口（%s，恢复最小化=%s，超时 %sms）" % (
            target,
            _show(bool(params.get("restore", True))),
            _show(params.get("timeoutMs", 2000)),
        )
    if action == "wait":
        return "等待 %sms" % _show(params.get("durationMs", 0))
    if action == "mouse_move":
        return "移动到 %s（耗时 %sms）" % (("(%s, %s)" % (_show(params.get("x")), _show(params.get("y")))), _show(params.get("durationMs", 0)))
    if action == "mouse_move_rel":
        return "相对移动 (%s, %s)" % (_show(params.get("dx")), _show(params.get("dy")))
    if action == "mouse_click":
        return "在 %s 点击 %s 次（%s，间隔 %sms）" % (
            _position(params),
            _show(params.get("clicks", 1)),
            BUTTON_LABELS.get(params.get("button", "left"), params.get("button", "left")),
            _show(params.get("intervalMs", 60)),
        )
    if action == "mouse_down":
        return "在 %s 按下 %s" % (_position(params), BUTTON_LABELS.get(params.get("button", "left"), params.get("button", "left")))
    if action == "mouse_up":
        return "在 %s 抬起 %s" % (_position(params), BUTTON_LABELS.get(params.get("button", "left"), params.get("button", "left")))
    if action == "mouse_drag":
        return "从 (%s, %s) 拖拽到 (%s, %s)（%s，时长 %sms，%s 步）" % (
            _show(params.get("x1")),
            _show(params.get("y1")),
            _show(params.get("x2")),
            _show(params.get("y2")),
            BUTTON_LABELS.get(params.get("button", "left"), params.get("button", "left")),
            _show(params.get("durationMs", 300)),
            _show(params.get("steps", 20)),
        )
    if action == "mouse_scroll":
        return "滚轮 %s 格（%s）" % (
            _show(params.get("amount")),
            "水平" if params.get("horizontal") else "垂直",
        )
    if action == "mouse_circle":
        return "以 (%s, %s) 为圆心、半径 %spx 画圆 %sms（周期 %sms，%s，%sHz，按住 %s，中止键 %s）" % (
            _show(params.get("centerX")),
            _show(params.get("centerY")),
            _show(params.get("radius")),
            _show(params.get("durationMs", 30000)),
            _show(params.get("periodMs", 2500)),
            DIRECTION_LABELS.get(params.get("direction", "cw"), params.get("direction", "cw")),
            _show(params.get("samplesPerSecond", 120)),
            BUTTON_LABELS.get(params.get("button", "none"), params.get("button", "none")),
            str(params.get("abortKey", "esc")),
        )
    if action == "key_press":
        return "按键 %s ×%s（间隔 %sms）" % (
            str(params.get("key")),
            _show(params.get("presses", 1)),
            _show(params.get("intervalMs", 60)),
        )
    if action == "key_down":
        return "按下 %s" % str(params.get("key"))
    if action == "key_up":
        return "松开 %s" % str(params.get("key"))
    if action == "hotkey":
        keys = params.get("keys") or []
        return "组合键 %s" % "+".join(str(item) for item in keys)
    if action == "type_text":
        text = params.get("text")
        length = len(text) if isinstance(text, str) else 0
        return "输入文本（%d 字符，间隔 %sms，不记录原文）" % (length, _show(params.get("intervalMs", 12)))
    if action == "find_image":
        return "在 %s 查找%s（阈值 %s，缩放 %s，最多 %s 个，灰度=%s）" % (
            _region_text(params),
            _template_text(params),
            _show(params.get("threshold", 0.85)),
            _show(params.get("scales", [1.0])),
            _show(params.get("maxResults", 3)),
            _show(bool(params.get("grayscale", True))),
        )
    if action == "wait_image":
        return "在 %s 等待%s %s（超时 %sms，间隔 %sms，阈值 %s，缩小搜索=%s）" % (
            _region_text(params),
            _template_text(params),
            MODE_LABELS.get(params.get("mode", "appear"), params.get("mode", "appear")),
            _show(params.get("timeoutMs", 15000)),
            _show(params.get("intervalMs", 250)),
            _show(params.get("threshold", 0.85)),
            _show(params.get("scales", [1.0])),
        )
    if action == "screenshot":
        target = "全部屏幕" if params.get("allScreens") else (
            "显示器 %s" % params.get("monitor") if params.get("monitor") is not None else _region_text(params)
        )
        return "截图（%s，%s，base64=%s）" % (
            target,
            params.get("format", "png"),
            _show(bool(params.get("base64", False))),
        )
    if action == "capture_window":
        target = "标题包含“%s”" % params["window"] if params.get("window") else "hwnd=%s" % _show(params.get("hwnd"))
        return "截取窗口（%s，引擎 %s，超时 %sms，光标=%s）" % (
            target,
            params.get("engine", "auto"),
            _show(params.get("timeoutMs", 3000)),
            _show(bool(params.get("includeCursor", False))),
        )
    if action == "list_windows":
        return "列出窗口（标题包含“%s”，包含不可见=%s，上限 %s）" % (
            params.get("titleContains", ""),
            _show(bool(params.get("includeInvisible", False))),
            _show(params.get("limit", 50)),
        )
    if action == "list_displays":
        return "列出显示器"
    if action == "mouse_position":
        return "读取鼠标当前位置"
    if action == "status":
        return "读取后端状态"
    tail = json.dumps(params, ensure_ascii=False)
    if len(tail) > 160:
        tail = tail[:157] + "..."
    return "执行 %s（参数 %s）" % (action, tail)


def _one_line(text) -> str:
    """把任意文本压成单行（注释是 // 行注释，遇到换行会破坏 JSONC 结构）。"""
    return str(text).replace("\r", " ").replace("\n", " ")


def step_comment(index: int, step: dict) -> str:
    """生成第 ``index``（0 基）步的中文注释文本（与 stepsView.comment 同文）。"""
    action = step.get("action", "")
    label = ACTION_LABELS.get(action, action)
    params = step.get("params") or {}
    summary = _one_line(_summarize_step(action, params))
    wait_ms = int(step.get("waitMs") or 0)
    tail_parts = []
    tail_parts.append("步骤后等待 %dms" % wait_ms if wait_ms > 0 else "无额外等待")
    if step.get("saveAs"):
        tail_parts.append("结果保存为 %s" % _one_line(step["saveAs"]))
    if step.get("continueOnError"):
        tail_parts.append("失败后继续")
    if step.get("note"):
        tail_parts.append("备注：%s" % _one_line(step["note"]))
    # v0.6.2：模板类动作追加“找不到时”策略说明
    if action in ON_MISSING_ACTIONS:
        on_missing = step.get("onMissing", "fail")
        tail_parts.append("找不到时：%s" % ON_MISSING_LABELS.get(str(on_missing), str(on_missing)))
    return "第 %d 步：%s —— %s；%s" % (index + 1, label, summary, "；".join(tail_parts))


def render_steps_view(skill: dict) -> list:
    """面板只读步骤列表：``[{index, action, comment, waitMs, saveAs?}]``。"""
    view = []
    for index, step in enumerate(skill.get("steps") or []):
        item = {
            "index": index,
            "action": step.get("action", ""),
            "comment": step_comment(index, step),
            "waitMs": int(step.get("waitMs") or 0),
        }
        if "saveAs" in step:
            item["saveAs"] = step["saveAs"]
        view.append(item)
    return view


def render_skill_jsonc(skill: dict) -> str:
    """把（已归一化的）技能文档渲染为带中文注释的 JSONC 文本。

    文件头注释包含插件名/标题/游戏/说明/生成时间/提示；每一步前一行是
    ``// 第 N 步：<动作中文名> —— <参数摘要>；<等待/保存/备注说明>``。
    """
    if not isinstance(skill, dict):
        raise SkillError("INVALID_SKILL", "技能文档必须是对象")
    steps = skill.get("steps") or []
    if not isinstance(steps, list):
        raise SkillError("INVALID_SKILL", "steps 必须是数组")
    generated_at = str(skill.get("updatedAt") or _iso())
    lines: list = [
        "// dsh-game-control 技能配置文件（JSONC：支持 // 行注释与 /* */ 块注释）",
        "// 标题：%s" % _one_line(skill.get("title") or ""),
        "// 适用游戏：%s" % (_one_line(skill.get("game") or "") or "未指定"),
        "// 说明：%s" % (_one_line(skill.get("description") or "") or "（无）"),
        "// 生成时间：%s" % _one_line(generated_at),
        "// 步骤数：%d" % len(steps),
        "// 保存时自动补全每步参数并重新生成中文注释；手工修改注释会被覆盖，自定义备注请写 note 字段。",
        "// 直接编辑本文件后，下一次 skill_get / skill_run 生效（也可在面板「技能库 → 完整配置文件」编辑）。",
        "{",
    ]
    header_fields = ["schema", "id", "title", "description", "game", "tags", "markdown"]
    for field in header_fields:
        if field not in skill:
            continue
        value = skill.get(field)
        if field == "schema" and value is None:
            value = SCHEMA
        lines.append("  %s: %s," % (json.dumps(field, ensure_ascii=False), json.dumps(value, ensure_ascii=False)))
    lines.append('  "steps": [')
    for index, step in enumerate(steps):
        lines.append("    // %s" % step_comment(index, step))
        step_text = json.dumps(step, ensure_ascii=False, indent=2)
        step_lines = ["    " + line for line in step_text.splitlines()]
        if index < len(steps) - 1:
            step_lines[-1] = step_lines[-1] + ","
        lines.extend(step_lines)
    lines.append("  ],")
    footer_fields = ["createdAt", "updatedAt"]
    for position, field in enumerate(footer_fields):
        value = skill.get(field)
        if field == "updatedAt" and value is None:
            value = generated_at
        suffix = "," if position < len(footer_fields) - 1 else ""
        lines.append("  %s: %s%s" % (json.dumps(field, ensure_ascii=False), json.dumps(value, ensure_ascii=False), suffix))
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SkillsStore
# ---------------------------------------------------------------------------


def _require_text(name: str, value, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise SkillError("INVALID_SKILL", "%s 必须是字符串" % name)
    if len(value) < minimum:
        raise SkillError("INVALID_SKILL", "%s 长度不能小于 %d（当前 %d）" % (name, minimum, len(value)))
    if len(value) > maximum:
        raise SkillError("INVALID_SKILL", "%s 长度不能超过 %d（当前 %d）" % (name, maximum, len(value)))
    return value


def validate_id(skill_id) -> str:
    if not isinstance(skill_id, str) or not ID_PATTERN.match(skill_id):
        raise SkillError(
            "INVALID_SKILL",
            "技能 id 非法: %r" % (skill_id,),
            hint="id 必须匹配 ^[a-z0-9][a-z0-9._-]{0,63}$，例如 keep-on-mining-loop",
        )
    return skill_id


def slugify(title: str) -> str:
    """把标题转成合法 id 候选（小写、非字母数字转 -、截断 48）。"""
    lowered = str(title or "").strip().lower()
    lowered = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-._")
    if not lowered:
        lowered = "skill"
    if not re.match(r"^[a-z0-9]", lowered):
        lowered = "s-" + lowered
    return lowered[:48]


def _json_size(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SkillError("INVALID_SKILL", "参数不是合法 JSON: %s" % exc) from exc


class SkillsStore:
    """技能库读写 + 校验；由 game_control.Runtime 持有（懒加载单例）。"""

    def __init__(self, root: Path, allowed_actions):
        self.root = Path(root)
        self.allowed_actions = frozenset(allowed_actions)
        self.backups_rootname = "backups"
        self.trash_rootname = ".trash"

    # -- 路径 -----------------------------------------------------------
    def ensure_root(self) -> Path:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SkillError(
                "FILE_IO_ERROR",
                "无法创建技能库目录: %s (%s)" % (self.root, exc),
                hint="检查 GAME_CONTROL_HOME 写权限",
            ) from exc
        return self.root

    def _dir(self, skill_id: str) -> Path:
        return self.root / validate_id(skill_id)

    def _json_path(self, skill_id: str) -> Path:
        return self._dir(skill_id) / "skill.json"

    def _backups_dir(self, skill_id: str) -> Path:
        return self._dir(skill_id) / self.backups_rootname

    # -- 校验 -----------------------------------------------------------
    def validate_step(self, step, index: int) -> dict:
        if not isinstance(step, dict):
            raise SkillError("INVALID_SKILL", "steps[%d] 必须是对象" % index)
        unknown = sorted(set(step) - STEP_ALLOWED_FIELDS)
        if unknown:
            raise SkillError(
                "INVALID_SKILL",
                "steps[%d] 含不允许字段: %s" % (index, ", ".join(unknown)),
                hint="单步只允许 action/params/saveAs/continueOnError/waitMs/note",
            )
        action = step.get("action")
        if not isinstance(action, str) or not action:
            raise SkillError("INVALID_SKILL", "steps[%d].action 必须是非空字符串" % index)
        if action not in self.allowed_actions:
            raise SkillError(
                "INVALID_SKILL",
                "steps[%d].action 不在允许列表: %s" % (index, action),
                hint="允许的控制动作: %s" % ", ".join(sorted(self.allowed_actions)),
            )
        params = step.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise SkillError("INVALID_SKILL", "steps[%d].params 必须是对象" % index)
        if _json_size(params) > STEP_PARAMS_MAX_BYTES:
            raise SkillError(
                "INVALID_SKILL",
                "steps[%d].params 超过 %d 字节" % (index, STEP_PARAMS_MAX_BYTES),
            )
        normalized = {"action": action, "params": params}
        if "saveAs" in step:
            save_as = step["saveAs"]
            if not isinstance(save_as, str) or not SAVE_AS_PATTERN.match(save_as):
                raise SkillError(
                    "INVALID_SKILL",
                    "steps[%d].saveAs 非法: %r" % (index, save_as),
                    hint="saveAs 必须匹配 ^[A-Za-z_][A-Za-z0-9_]{0,31}$",
                )
            normalized["saveAs"] = save_as
        if "continueOnError" in step:
            if not isinstance(step["continueOnError"], bool):
                raise SkillError("INVALID_SKILL", "steps[%d].continueOnError 必须是布尔" % index)
            normalized["continueOnError"] = step["continueOnError"]
        if "waitMs" in step:
            wait_ms = step["waitMs"]
            if isinstance(wait_ms, bool) or not isinstance(wait_ms, (int, float)):
                raise SkillError("INVALID_SKILL", "steps[%d].waitMs 必须是数字" % index)
            if wait_ms < 0 or wait_ms > WAIT_MS_MAX:
                raise SkillError("INVALID_SKILL", "steps[%d].waitMs 必须在 0–%d 之间" % (index, WAIT_MS_MAX))
            normalized["waitMs"] = int(round(wait_ms))
        if "note" in step:
            note = step["note"]
            if not isinstance(note, str):
                raise SkillError("INVALID_SKILL", "steps[%d].note 必须是字符串" % index)
            if len(note) > NOTE_MAX:
                raise SkillError("INVALID_SKILL", "steps[%d].note 不能超过 %d 字符" % (index, NOTE_MAX))
            normalized["note"] = note
        if "onMissing" in step:
            on_missing = step["onMissing"]
            if action not in ON_MISSING_ACTIONS:
                raise SkillError(
                    "INVALID_SKILL",
                    "steps[%d].onMissing 仅对 find_image / wait_image 有效" % index,
                    hint="把 onMissing 写到 find_image / wait_image 步骤上",
                )
            if not isinstance(on_missing, str) or on_missing not in ON_MISSING_VALUES:
                raise SkillError(
                    "INVALID_SKILL",
                    "steps[%d].onMissing 只能是 fail 或 continue（收到 %r）" % (index, on_missing),
                    hint='默认 "fail"：找不到模板时该步失败；"continue"：继续后续步骤（match 为 null）',
                )
            normalized["onMissing"] = on_missing
        return normalized

    def validate_document(self, payload: dict) -> dict:
        """校验技能文档（结构/长度/单步白名单），返回规范化结构（未补默认参数）。"""
        if not isinstance(payload, dict):
            raise SkillError("INVALID_SKILL", "技能内容必须是对象")
        title = _require_text("title", payload.get("title"), 1, TITLE_MAX)
        description = payload.get("description", "")
        if description is None:
            description = ""
        description = _require_text("description", description, 0, DESCRIPTION_MAX)
        game = payload.get("game", "")
        if game is None:
            game = ""
        game = _require_text("game", game, 0, GAME_MAX)
        markdown = payload.get("markdown", "")
        if markdown is None:
            markdown = ""
        markdown = _require_text("markdown", markdown, 0, MARKDOWN_MAX)
        tags = payload.get("tags", [])
        if tags is None:
            tags = []
        if not isinstance(tags, list):
            raise SkillError("INVALID_SKILL", "tags 必须是数组")
        if len(tags) > TAGS_MAX:
            raise SkillError("INVALID_SKILL", "tags 最多 %d 个" % TAGS_MAX)
        normalized_tags = []
        for index, tag in enumerate(tags):
            if not isinstance(tag, str):
                raise SkillError("INVALID_SKILL", "tags[%d] 必须是字符串" % index)
            if len(tag) > TAG_MAX:
                raise SkillError("INVALID_SKILL", "tags[%d] 长度不能超过 %d" % (index, TAG_MAX))
            normalized_tags.append(tag)
        steps = payload.get("steps")
        if not isinstance(steps, list):
            raise SkillError("INVALID_SKILL", "steps 必须是数组")
        if len(steps) < STEPS_MIN or len(steps) > STEPS_MAX:
            raise SkillError("INVALID_SKILL", "steps 数量必须在 %d–%d 之间（当前 %d）" % (STEPS_MIN, STEPS_MAX, len(steps)))
        normalized_steps = [self.validate_step(step, index) for index, step in enumerate(steps)]
        document = {
            "schema": SCHEMA,
            "title": title,
            "description": description,
            "game": game,
            "tags": normalized_tags,
            "markdown": markdown,
            "steps": normalized_steps,
        }
        if "note" in payload:  # 文档级 note 不在 SPEC 内；忽略而非报错
            pass
        return document

    # -- 渲染 / 原子写 ---------------------------------------------------
    def render(self, document: dict) -> str:
        """归一化 + 渲染 JSONC 文本（保存与测试共用）。"""
        normalized = normalize_skill(document)
        return render_skill_jsonc(normalized)

    def _atomic_write_text(self, path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(path.name + ".tmp-%d" % os.getpid())
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except OSError as exc:
            raise SkillError("FILE_IO_ERROR", "写入技能文件失败: %s (%s)" % (path, exc)) from exc

    def _backup_current(self, skill_id: str) -> str | None:
        """把当前 skill.json（原始 JSONC 文本）复制到 backups/<ts>.json。"""
        current = self._json_path(skill_id)
        if not current.exists():
            return None
        backups = self._backups_dir(skill_id)
        try:
            backups.mkdir(parents=True, exist_ok=True)
            stamp = _now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            name = "%s.json" % stamp
            target = backups / name
            suffix = 1
            while target.exists():
                suffix += 1
                target = backups / ("%s-%d.json" % (stamp, suffix))
            shutil.copy2(current, target)
            self._prune_backups(skill_id)
            return target.name
        except OSError as exc:
            raise SkillError("FILE_IO_ERROR", "备份技能失败: %s (%s)" % (skill_id, exc)) from exc

    def _prune_backups(self, skill_id: str) -> None:
        backups = self._backups_dir(skill_id)
        if not backups.exists():
            return
        files = sorted((item for item in backups.glob("*.json") if item.is_file()), key=lambda item: item.name, reverse=True)
        for stale in files[MAX_BACKUPS:]:
            try:
                stale.unlink()
            except OSError:
                pass

    def _backup_count(self, skill_id: str) -> int:
        backups = self._backups_dir(skill_id)
        if not backups.exists():
            return 0
        try:
            return len([item for item in backups.glob("*.json") if item.is_file()])
        except OSError:
            return 0

    # -- 读取 -----------------------------------------------------------
    def _read_raw(self, skill_id: str, required: bool = True) -> str | None:
        path = self._json_path(skill_id)
        if not path.exists():
            if required:
                raise SkillError(
                    "SKILL_NOT_FOUND",
                    "技能不存在: %s" % skill_id,
                    hint="用 skill_list 查看已有技能，或先 skill_save 创建",
                )
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            if not required:
                return None
            raise SkillError("FILE_IO_ERROR", "读取技能文件失败: %s (%s)" % (path, exc)) from exc

    def _load_normalized(self, skill_id: str, required: bool = True) -> tuple:
        """返回 ``(raw_text, parsed, normalized)``；required=False 时缺失返回 (None, None, None)。"""
        raw = self._read_raw(skill_id, required=required)
        if raw is None:
            return None, None, None
        parsed = parse_jsonc(raw, "技能文件")
        parsed.setdefault("id", skill_id)
        normalized = normalize_skill(parsed)
        normalized["id"] = skill_id
        return raw, parsed, normalized

    def list(self) -> dict:
        self.ensure_root()
        entries = []
        try:
            children = sorted(self.root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise SkillError("FILE_IO_ERROR", "读取技能库失败: %s" % exc) from exc
        for child in children:
            if not child.is_dir() or child.name == self.trash_rootname:
                continue
            if not ID_PATTERN.match(child.name):
                continue
            try:
                _raw, _parsed, normalized = self._load_normalized(child.name, required=False)
            except SkillError:
                continue
            if normalized is None:
                continue
            entries.append(
                {
                    "id": normalized.get("id") or child.name,
                    "title": normalized.get("title", ""),
                    "description": normalized.get("description", ""),
                    "game": normalized.get("game", ""),
                    "tags": normalized.get("tags", []),
                    "stepCount": len(normalized.get("steps") or []),
                    "updatedAt": normalized.get("updatedAt", ""),
                    "backupCount": self._backup_count(normalized.get("id") or child.name),
                }
            )
        entries.sort(key=lambda item: (item.get("updatedAt") or "", item["id"]), reverse=True)
        return {"count": len(entries), "skills": entries}

    def get(self, skill_id: str) -> dict:
        self.ensure_root()
        validate_id(skill_id)
        raw, _parsed, normalized = self._load_normalized(skill_id, required=True)
        return {
            "skill": normalized,
            "content": raw,
            "stepsView": render_steps_view(normalized),
        }

    # -- 保存 -----------------------------------------------------------
    def _unique_slug(self, title: str) -> str:
        base = slugify(title)
        if not ID_PATTERN.match(base):
            base = "skill"
        candidate = base
        suffix = 2
        while self._json_path(candidate).exists():
            suffix_text = "-%d" % suffix
            candidate = (base[: max(1, 64 - len(suffix_text))] + suffix_text)
            suffix += 1
            if suffix > 999:
                raise SkillError("INVALID_SKILL", "无法生成唯一技能 id（slug=%s）" % base)
        return candidate

    def _content_payload(self, content: str, requested_id) -> dict:
        parsed = parse_jsonc(content, "content")
        content_id = parsed.get("id")
        if requested_id not in (None, "") and content_id not in (None, ""):
            if str(requested_id) != str(content_id):
                raise SkillError(
                    "INVALID_SKILL",
                    "参数 id 与 content.id 不一致: %r != %r" % (requested_id, content_id),
                    hint='传 content 时 id 可省略，或保持两者一致',
                )
        chosen_id = content_id if content_id not in (None, "") else requested_id
        payload = {
            "title": parsed.get("title"),
            "description": parsed.get("description", ""),
            "game": parsed.get("game", ""),
            "tags": parsed.get("tags", []),
            "markdown": parsed.get("markdown", ""),
            "steps": parsed.get("steps"),
        }
        if chosen_id not in (None, ""):
            payload["id"] = chosen_id
        return payload

    def save(self, payload: dict) -> dict:
        """保存技能（结构化字段或 ``content`` JSONC 文本）；自动备份 + 渲染 JSONC。"""
        self.ensure_root()
        if not isinstance(payload, dict):
            raise SkillError("INVALID_SKILL", "保存参数必须是对象")
        requested_id = payload.get("id")
        content = payload.get("content")
        if content is not None:
            if not isinstance(content, str) or content.strip() == "":
                raise SkillError("INVALID_SKILL", "content 必须是非空 JSONC 文本")
            if len(content) > CONTENT_MAX:
                raise SkillError("INVALID_SKILL", "content 不能超过 %d 字符" % CONTENT_MAX)
            base = self._content_payload(content, requested_id)
        else:
            base = {key: payload[key] for key in ("title", "description", "game", "tags", "markdown", "steps") if key in payload}
            if requested_id not in (None, ""):
                base["id"] = requested_id
        validated = self.validate_document(base)
        normalized = normalize_skill(validated)
        created = False
        created_at = _iso()
        backup_path = None
        chosen_id = base.get("id")
        if chosen_id not in (None, ""):
            skill_id = validate_id(str(chosen_id).strip())
            created = not self._json_path(skill_id).exists()
        else:
            skill_id = self._unique_slug(normalized.get("title", ""))
            created = True
        if not created:
            existing_raw, existing_parsed, _existing_normalized = self._load_normalized(skill_id, required=False)
            if existing_parsed:
                created_at = existing_parsed.get("createdAt") or created_at
            backup_path = self._backup_current(skill_id)
        updated_at = _iso()
        document = {
            "schema": SCHEMA,
            "id": skill_id,
            **{key: normalized[key] for key in ("title", "description", "game", "tags", "markdown", "steps")},
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
        text = render_skill_jsonc(document)
        self._atomic_write_text(self._json_path(skill_id), text)
        return {
            "id": skill_id,
            "created": created,
            "backupPath": backup_path,
            "updatedAt": updated_at,
            "backupCount": self._backup_count(skill_id),
            "content": text,
            "stepsView": render_steps_view(document),
        }

    # -- 版本 / 恢复 / 删除 ---------------------------------------------
    def versions(self, skill_id: str) -> dict:
        self.ensure_root()
        validate_id(skill_id)
        if not self._json_path(skill_id).exists():
            raise SkillError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
        backups = self._backups_dir(skill_id)
        items = []
        if backups.exists():
            try:
                files = [item for item in backups.glob("*.json") if item.is_file()]
            except OSError:
                files = []
            for path in sorted(files, key=lambda item: item.name, reverse=True):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append(
                    {
                        "name": path.name,
                        "savedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        "bytes": int(stat.st_size),
                    }
                )
        return {"id": skill_id, "count": len(items), "versions": items}

    def restore(self, skill_id: str, backup) -> dict:
        """恢复备份文本（原样写回，保留 JSONC 注释）；恢复前先备份当前版本。"""
        self.ensure_root()
        validate_id(skill_id)
        if not self._json_path(skill_id).exists():
            raise SkillError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
        versions = self.versions(skill_id)["versions"]
        if not versions:
            raise SkillError("SKILL_NOT_FOUND", "技能没有可恢复的备份: %s" % skill_id)
        if backup is None or str(backup).strip() in ("", "latest"):
            backup_name = versions[0]["name"]
        else:
            backup_name = str(backup).strip()
            if os.path.basename(backup_name) != backup_name or not backup_name.endswith(".json"):
                raise SkillError("INVALID_SKILL", 'backup 必须是 backups/ 下的文件名或 "latest"')
            if backup_name not in {item["name"] for item in versions}:
                raise SkillError("SKILL_NOT_FOUND", "备份不存在: %s" % backup_name)
        backup_of_current = self._backup_current(skill_id)
        backup_path = self._backups_dir(skill_id) / backup_name
        try:
            text = backup_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError("FILE_IO_ERROR", "读取备份失败: %s (%s)" % (backup_path, exc)) from exc
        # 校验备份内容可解析/结构合法（避免把损坏文件恢复成当前版本），但原样写回保留注释
        parsed = parse_jsonc(text, "备份文件")
        self.validate_document(parsed)
        self._atomic_write_text(self._json_path(skill_id), text)
        return {
            "id": skill_id,
            "restoredFrom": backup_name,
            "backupOfCurrent": backup_of_current,
            "updatedAt": _iso(),
        }

    def delete(self, skill_id: str, hard: bool = False) -> dict:
        self.ensure_root()
        validate_id(skill_id)
        skill_dir = self._dir(skill_id)
        if not skill_dir.exists():
            raise SkillError("SKILL_NOT_FOUND", "技能不存在: %s" % skill_id)
        self._backup_current(skill_id)
        if hard:
            try:
                shutil.rmtree(skill_dir)
            except OSError as exc:
                raise SkillError("FILE_IO_ERROR", "硬删除技能失败: %s (%s)" % (skill_dir, exc)) from exc
            return {"id": skill_id, "deleted": True, "trashPath": None, "hardDeleted": True}
        trash_root = self.root / self.trash_rootname
        stamp = _now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        target = trash_root / ("%s-%s" % (skill_id, stamp))
        try:
            trash_root.mkdir(parents=True, exist_ok=True)
            suffix = 1
            while target.exists():
                suffix += 1
                target = trash_root / ("%s-%s-%d" % (skill_id, stamp, suffix))
            shutil.move(str(skill_dir), str(target))
        except OSError as exc:
            raise SkillError("FILE_IO_ERROR", "移动技能到回收站失败: %s (%s)" % (skill_dir, exc)) from exc
        return {"id": skill_id, "deleted": True, "trashPath": str(target), "hardDeleted": False}
