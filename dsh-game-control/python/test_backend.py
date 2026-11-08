#!/usr/bin/env python3
"""Python 后端自测：覆盖 SPEC 2.7 要求的 6 类检查。

运行：``python test_backend.py``（工作目录任意，脚本自行定位后端目录）。

**安全约束**：本测试只执行 ``status`` / ``list_displays`` / ``screenshot`` /
``mouse_position`` / ``--selftest`` 等只读动作与参数校验失败分支，
**绝不触发真实点击、按键、拖拽或滚轮**。截图写入临时目录并在结束时清理。

检查项：
1. 信封 / 参数校验：未知 action、缺参数、非法 key、非法 region 等错误码；
2. ``status`` 返回合法几何（宽高 > 0）；
3. 全屏截图 + 区域截图（200×120）落盘、PNG 魔数、尺寸精确匹配；
4. ``base64:true`` 返回的 base64 可解码且能解出 PNG 魔数；
5. 源码扫描：``python/*.py`` 不含联网 import，且 Windows 原生实现符合纯 ctypes 要求；
6. ``--selftest`` / ``--json '{"action":"status"}'`` 子进程退出码 0 且 JSON 可解析。

额外补充（不影响 SPEC 契约，仅提高可信度）：stdin/worker 协议、环境变量、审计日志、
区域裁剪、JPEG 编码与 ``--version``。

v0.2 补充（SPEC 2.3.1，只读、无输入副作用）：
- ``list_windows`` 枚举非空且字段合法、``titleContains`` 过滤生效；
- ``capture_window`` 参数校验与 ``WINDOW_NOT_FOUND`` 错误码；
- ``engine=screen`` 抓取真实窗口落盘并用 Pillow 校验尺寸；``engine=auto`` 回退链可返回画面；
- 显式 ``engine=wgc`` 在受限/不受限环境都能优雅返回（成功或 ``WINDOW_CAPTURE_FAILED``，不崩溃）；
- vendor 包与 LICENSE、源码无联网 import。

v0.3 补充（SPEC 8.5，全部零真实输入）：
- ``circle_points()`` 纯函数：采样数/首点/四分之一位置/半径误差/ccw/越界错误码；
- ``mouse_circle dryRun``：不调用 ``move_to``（进程内 monkeypatch 存根断言 0 次输入调用）；
- ``find_image``：合成图命中中心误差 ≤2、score>0.95、空白图无命中、TEMPLATE_NOT_FOUND/INVALID_TEMPLATE；
  并用「注入截图」跑通 find_image 端到端（含 region 偏移）；
- ``wait_image``：注入 find 结果验证 ``timedOut:true``；注入截图验证 appear 命中；
- ``activate_window``：只做参数/不存在窗口校验，不做真实激活；
- ``skill_*``：save→update（自动备份）→list/get/versions→restore→delete(软删)；
  ``skill_run dryRun``、仅 ``wait(0)`` 的真实执行、运行期失败 ``success:false``、变量替换纯函数。

v0.4 补充（SPEC 9.6，全部零真实输入）：
- ``strip_jsonc``：字符串内 ``//`` 不被误删、行/块注释、无注释 JSON 兼容、未闭合注释报错；
- 结构化保存 → 读磁盘原文断言 ``// 第 N 步：``、显式 ``waitMs``、鼠标/圆形/找图默认参数；``content``/``stepsView`` 一致；
- ``content`` 模式（JSONC + 注释 + ``note``）保存：注释重生成、数据/note 保留；
- 非法 content/未知步骤字段/超限步骤 → ``INVALID_SKILL``；保存前旧版本进 backups 且保留原始 JSONC 文本；
- JSONC 技能 ``skill_run``（仅 ``wait durationMs:0``）真实执行。

v0.5 补充（SPEC 10.5，全部零真实输入）：
- ``mouse_circle``/``skill_run``/``find_image``/``wait_image`` 默认 ``abortKey="esc"``、``escape`` 兼容；
  进程内 monkeypatch ``win_native.key_is_down`` 验证 find/wait_image 按下立即 ``aborted:true``、``"none"`` 禁用；
- ``templates`` 多模板（合成图）：任一命中返回对应 ``template`` 路径，``match.template`` 可用；
  ``template``/``templates`` 冲突与数量越界 → ``INVALID_ARGUMENT``；
- ``skill_run`` 把自身 abortKey 透传给 ``wait_image`` 步骤并立即中止；
- ``skill-progress.json``：运行中 ``running/step.index`` 可读，结束 ``running:false``/success/finishedAt；``skill_progress`` 动作可读；
- 超时前置校验：估算公式（mouse_circle=leadIn+duration、wait_image=timeout、wait=duration、其它=1000，
  加 waitMs+500/步）、超限 ``INVALID_ARGUMENT`` + “至少需要”hint、``dryRun`` 返回 ``estimatedDurationMs``。

v0.6 补充（SPEC 11.7，全部零真实输入）：
- ``skill_run`` 总 deadline：``timeoutMs=1000`` + ``wait(5000)`` → ``aborted:true/abortReason:"timeout"`` 且 <3s；
- 超时后 ``skill-progress.json`` 为 ``running:false/abortReason:"timeout"/finishedAt``；
- 动作内部 deadline：``wait`` 睡眠截断、``find_image`` 立即中止、``mouse_circle`` 提前停止并释放鼠标键、
  ``wait_image`` 大 timeout + 小总 deadline → aborted timeout（注入截图，零真实输入）；
- 无 deadline 时原有行为回归；``skill_run`` 期间设置 ``runtime.skill_deadline``，结束后清除。

v0.6.2 补充（SPEC 13.3，全部零真实输入）：
- ``wait_image`` 未找到时默认 ``onMissing="fail"`` → 第 1 步失败 ``WAIT_IMAGE_TIMEOUT`` + 失败截图 ``screenshotPath``，
  不再出现与真实原因无关的 ``SKILL_VAR_UNRESOLVED``；
- ``onMissing="continue"`` → 继续到后续步骤并在变量引用处报 ``SKILL_VAR_UNRESOLVED``（hint 提示模板未找到/None）；
- ``onMissing`` 非法值或用在非模板步骤 → ``INVALID_SKILL``；
- 归一化后 find/wait_image 步骤显式含 ``onMissing:"fail"``，JSONC/stepsView 注释含「找不到时：失败」。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND = SCRIPT_DIR / "game_control.py"
PYTHON = sys.executable
# 直接 import 后端模块做纯函数/注入式测试（不启动子进程、不依赖屏幕）
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

# 只读动作白名单：测试代码自身只允许触发这些动作（窗口/模板/技能动作均不注入输入）
READ_ONLY_ACTIONS = {
    "status",
    "list_displays",
    "screenshot",
    "mouse_position",
    "list_windows",
    "capture_window",
    "wait",
    "find_image",
    "wait_image",
    "skill_list",
    "skill_get",
    "skill_save",
    "skill_versions",
    "skill_restore",
    "skill_delete",
    "skill_run",
    "skill_progress",
    "mouse_circle",  # 仅 dryRun 会在测试中调用
}

RESULTS: list = []


class CheckFailure(AssertionError):
    """测试断言失败。"""


def expect(condition, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    prefix = "[PASS]" if ok else "[FAIL]"
    line = "%s %s" % (prefix, name)
    if detail:
        line += "  — %s" % detail
    print(line, flush=True)


def norm(path: Path) -> str:
    """路径比较用的规范化字符串（Windows 大小写/短路径不敏感）。"""
    return os.path.normcase(str(path.resolve()))


# ---------------------------------------------------------------------------
# 子进程调用辅助
# ---------------------------------------------------------------------------


def run_backend(args: list, stdin_text: str | None = None, env_extra: dict | None = None, timeout: int = 120):
    """启动后端子进程（--json/--stdin/--serve/--selftest/--version 通用）。"""
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run(
        [PYTHON, str(BACKEND)] + list(args),
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(SCRIPT_DIR),
    )


def json_lines(text: str) -> list:
    """把 stdout 拆成非空行并逐行解析 JSON（worker 协议要求每行独立可解析）。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    parsed = []
    for index, line in enumerate(lines):
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CheckFailure("stdout 第 %d 行不是合法 JSON: %s (%r)" % (index + 1, exc, line[:200]))
    return parsed


def one_json_line(text: str) -> dict:
    """单次 CLI 模式必须只输出一行 JSON。"""
    parsed = json_lines(text)
    expect(len(parsed) == 1, "单次 CLI 期望 1 行 JSON，实际 %d 行: %r" % (len(parsed), text[:400]))
    return parsed[0]


def call(action: str, params: dict | None = None, env_extra: dict | None = None, request_id: int = 1) -> dict:
    """执行一条只读动作请求，返回信封。"""
    expect(action in READ_ONLY_ACTIONS, "测试禁止执行非只读动作: %s" % action)
    return raw_request(action, params, env_extra=env_extra, request_id=request_id)


def raw_request(action: str, params: dict | None = None, env_extra: dict | None = None, request_id: int = 1) -> dict:
    """执行任意 action 的 --json 请求（仅供错误路径 / 只读动作使用）。"""
    request: dict = {"id": request_id, "action": action}
    if params is not None:
        request["params"] = params
    proc = run_backend(["--json", json.dumps(request, ensure_ascii=False)], env_extra=env_extra)
    expect(
        proc.returncode == 0,
        "--json 动作失败不应改变退出码（期望 0，实际 %d），stderr=%r" % (proc.returncode, (proc.stderr or "")[-500:]),
    )
    response = one_json_line(proc.stdout)
    if request_id is not None:
        expect(response.get("id") == request_id, "响应未回显 id: %r" % response)
    return response


def expect_error(action: str, params: dict | None, code: str, env_extra: dict | None = None) -> dict:
    """断言请求返回指定错误码，且进程退出码仍为 0。"""
    response = raw_request(action, params, env_extra=env_extra)
    expect(response.get("ok") is False, "期望 ok:false，实际 %r" % response)
    error = response.get("error") or {}
    actual = error.get("code")
    expect(actual == code, "期望错误码 %s，实际 %s（message=%r）" % (code, actual, error.get("message")))
    expect(isinstance(error.get("message"), str) and error["message"], "错误消息不能为空")
    expect(isinstance(response.get("durationMs"), int), "durationMs 必须是整数")
    return response


def pil_size(path: Path) -> tuple:
    """用 Pillow 打开图片返回 (width, height)；Pillow 缺失直接失败（SPEC 要求必需依赖）。"""
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - 环境问题
        raise CheckFailure("缺少 Pillow，无法校验截图尺寸: %s" % exc) from exc
    with Image.open(str(path)) as image:
        return image.size


def fresh_env(root: Path) -> dict:
    """每个测试使用独立的 GAME_CONTROL_HOME，避免污染用户目录。"""
    return {"GAME_CONTROL_HOME": str(root)}


# ---------------------------------------------------------------------------
# 1. 信封 / 参数校验
# ---------------------------------------------------------------------------


def test_envelope_and_validation(ctx: dict) -> None:
    env = ctx["env"]
    # 未知 action
    expect_error("unknown_action_xyz", None, "UNKNOWN_ACTION", env)
    # 缺必需参数（mouse_move 缺 y）
    expect_error("mouse_move", {"x": 10}, "INVALID_ARGUMENT", env)
    # 参数类型错误
    expect_error("mouse_move", {"x": "abc", "y": 20}, "INVALID_ARGUMENT", env)
    # 非法键名
    expect_error("key_press", {"key": "not-a-real-key"}, "INVALID_KEY", env)
    # 非法 region（宽度 0）→ INVALID_REGION
    expect_error(
        "screenshot",
        {"region": {"x": 0, "y": 0, "width": 0, "height": 100}},
        "INVALID_REGION",
        env,
    )
    # 非法 region（width 非数字）→ INVALID_REGION
    expect_error(
        "screenshot",
        {"region": {"x": 0, "y": 0, "width": "abc", "height": 100}},
        "INVALID_REGION",
        env,
    )
    # 非法 region（高度为负）→ INVALID_REGION
    expect_error(
        "screenshot",
        {"region": {"x": 0, "y": 0, "width": 100, "height": -5}},
        "INVALID_REGION",
        env,
    )
    # region 完全在虚拟桌面外 → OUT_OF_BOUNDS
    expect_error(
        "screenshot",
        {"region": {"x": 10000000, "y": 10000000, "width": 50, "height": 50}},
        "OUT_OF_BOUNDS",
        env,
    )
    # region 内未知字段 → UNKNOWN_PARAM
    expect_error(
        "screenshot",
        {"region": {"x": 0, "y": 0, "width": 10, "height": 10, "z": 1}},
        "UNKNOWN_PARAM",
        env,
    )
    # params 内未知字段 → UNKNOWN_PARAM
    expect_error("mouse_click", {"x": 1, "y": 2, "bogus": 3}, "UNKNOWN_PARAM", env)
    # 非法鼠标按键
    expect_error("mouse_click", {"button": "weird"}, "INVALID_ARGUMENT", env)
    # 缺少必需参数 text
    expect_error("type_text", {}, "INVALID_ARGUMENT", env)
    # hotkey 超过 4 个键
    expect_error("hotkey", {"keys": ["a", "b", "c", "d", "e"]}, "INVALID_ARGUMENT", env)
    # 非法 JSON
    proc = run_backend(["--json", "this is not json"], env_extra=env)
    expect(proc.returncode == 0, "非法 JSON 也要打印信封并以退出码 0 结束")
    response = one_json_line(proc.stdout)
    expect(response.get("ok") is False, "非法 JSON 应返回 ok:false")
    expect((response.get("error") or {}).get("code") == "INVALID_ARGUMENT", "非法 JSON 错误码应为 INVALID_ARGUMENT")
    # 缺少 action
    proc = run_backend(["--json", '{"params":{}}'], env_extra=env)
    response = one_json_line(proc.stdout)
    expect((response.get("error") or {}).get("code") == "INVALID_ARGUMENT", "缺少 action 应为 INVALID_ARGUMENT")
    record("1. 信封/参数校验", True, "未知 action / 缺参数 / 非法 key / 非法 region / 未知参数 均返回正确错误码，退出码保持 0")


# ---------------------------------------------------------------------------
# 2. status 几何
# ---------------------------------------------------------------------------


def test_status(ctx: dict) -> None:
    env = ctx["env"]
    response = call("status", env_extra=env)
    expect(response.get("ok") is True, "status 应成功: %r" % response)
    result = response["result"]
    for key in (
        "platform",
        "pythonVersion",
        "backend",
        "dpiAware",
        "virtualScreen",
        "primary",
        "displays",
        "pointer",
        "shotDir",
        "logDir",
    ):
        expect(key in result, "status 缺少字段: %s" % key)
    virtual = result["virtualScreen"]
    primary = result["primary"]
    expect(virtual["width"] > 0 and virtual["height"] > 0, "虚拟桌面宽高必须 > 0: %r" % virtual)
    expect(primary["width"] > 0 and primary["height"] > 0, "主屏宽高必须 > 0: %r" % primary)
    expect(isinstance(result["displays"], list) and result["displays"], "displays 不能为空")
    for display in result["displays"]:
        expect(display["width"] > 0 and display["height"] > 0, "显示器几何非法: %r" % display)
    expect(isinstance(result["pointer"]["x"], int), "pointer.x 必须是整数")
    expect(isinstance(result["dpiAware"], bool), "dpiAware 必须是布尔值")
    automation = result.get("automation")
    expect(isinstance(automation, dict), "v0.3 status 应包含 automation 能力字段")
    for key in ("mouseCircle", "findImage", "skills"):
        expect(isinstance(automation.get(key), bool), "automation.%s 必须是布尔" % key)
    expect(result["backend"] == "win32-sendinput" if os.name == "nt" else True, "Windows 后端标识应为 win32-sendinput")
    record(
        "2. status 几何",
        True,
        "virtualScreen=%dx%d primary=%dx%d displays=%d backend=%s dpiAware=%s"
        % (
            virtual["width"],
            virtual["height"],
            primary["width"],
            primary["height"],
            len(result["displays"]),
            result["backend"],
            result["dpiAware"],
        ),
    )


# ---------------------------------------------------------------------------
# 3. 全屏 / 区域截图
# ---------------------------------------------------------------------------


def _screenshot_to(action_params: dict, path: Path, env: dict) -> dict:
    params = dict(action_params)
    params["path"] = str(path)
    response = call("screenshot", params, env_extra=env)
    expect(response.get("ok") is True, "截图失败: %r" % response)
    return response["result"]


def test_screenshots(ctx: dict) -> None:
    env = ctx["env"]
    shots = ctx["tmp"] / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # 3a 全屏（默认主屏）截图
    full_path = shots / "full.png"
    result = _screenshot_to({}, full_path, env)
    expect(full_path.exists(), "全屏截图文件未落盘: %s" % full_path)
    raw = full_path.read_bytes()
    expect(raw.startswith(PNG_MAGIC), "文件不是 PNG（魔数不匹配）")
    png_size = pil_size(full_path)
    expect(
        (result["width"], result["height"]) == png_size,
        "结果尺寸与 PNG 实际尺寸不一致: result=%rx%r png=%r"
        % (result["width"], result["height"], png_size),
    )
    expect(result["format"] == "png", "默认格式应为 png")
    expect(result["bytes"] == len(raw), "bytes 字段应等于文件大小: %r != %r" % (result["bytes"], len(raw)))
    expect(norm(Path(result["absolutePath"])) == norm(full_path), "absolutePath 不正确")
    expect(result["width"] > 0 and result["height"] > 0, "全屏截图宽高必须 > 0")
    full_size = (result["width"], result["height"])

    # 3b 区域截图 200×120
    region_path = shots / "region.png"
    result = _screenshot_to(
        {"region": {"x": 0, "y": 0, "width": 200, "height": 120}},
        region_path,
        env,
    )
    expect(region_path.exists(), "区域截图文件未落盘")
    expect((result["width"], result["height"]) == (200, 120), "区域截图宽高应精确为 200×120，实际 %r" % ((result["width"], result["height"]),))
    expect(pil_size(region_path) == (200, 120), "区域截图 PNG 实际尺寸不是 200×120")
    expect(region_path.read_bytes().startswith(PNG_MAGIC), "区域截图不是 PNG")

    # 3c 区域超出虚拟桌面时按交集裁剪（不与虚拟桌面求交则为 OUT_OF_BOUNDS，前面已测）
    status = call("status", env_extra=env)["result"]
    virtual = status["virtualScreen"]
    clip_path = shots / "clip.png"
    clip_region = {
        "x": virtual["x"] + virtual["width"] - 50,
        "y": virtual["y"],
        "width": 200,
        "height": 80,
    }
    result = _screenshot_to({"region": clip_region}, clip_path, env)
    expect(
        (result["width"], result["height"]) == (50, 80),
        "越界区域应按交集裁剪为 50×80，实际 %r" % ((result["width"], result["height"]),),
    )

    # 3d 超大区域（999999×999999，部分越界）→ 新 SPEC：裁剪到虚拟桌面并 ok:true
    huge_path = shots / "huge_clip.png"
    result = _screenshot_to(
        {"region": {"x": 0, "y": 0, "width": 999999, "height": 999999}},
        huge_path,
        env,
    )
    expect(
        result["width"] <= virtual["width"] and result["height"] <= virtual["height"],
        "超大区域裁剪后尺寸应 ≤ 虚拟桌面尺寸: %rx%r vs %rx%r"
        % (result["width"], result["height"], virtual["width"], virtual["height"]),
    )
    expect(result["width"] > 0 and result["height"] > 0, "超大区域裁剪后尺寸应 > 0")
    expect(pil_size(huge_path) == (result["width"], result["height"]), "超大区域裁剪后的 PNG 尺寸不一致")

    # 3e 多显示器：allScreens 尺寸应等于虚拟桌面；monitor=1 应等于第 1 个显示器
    all_path = shots / "all_screens.png"
    result = _screenshot_to({"allScreens": True}, all_path, env)
    expect(
        (result["width"], result["height"]) == (virtual["width"], virtual["height"]),
        "allScreens 尺寸应为虚拟桌面 %rx%r，实际 %rx%r"
        % (virtual["width"], virtual["height"], result["width"], result["height"]),
    )
    expect(pil_size(all_path) == (virtual["width"], virtual["height"]), "allScreens PNG 尺寸不一致")
    monitor_path = shots / "monitor1.png"
    result = _screenshot_to({"monitor": 1}, monitor_path, env)
    first_display = status["displays"][0]
    expect(
        (result["width"], result["height"]) == (first_display["width"], first_display["height"]),
        "monitor=1 尺寸应等于第 1 个显示器，实际 %rx%r"
        % (result["width"], result["height"]),
    )

    # 3f 默认路径落在 <GAME_CONTROL_HOME>/shots 下
    response = call("screenshot", env_extra=env)
    expect(response.get("ok") is True, "默认路径截图失败: %r" % response)
    default_result = response["result"]
    default_abs = Path(default_result["absolutePath"])
    expect(default_abs.exists(), "默认截图路径不存在: %s" % default_abs)
    expect(
        norm(default_abs.parent) == norm(Path(ctx["home"]) / "shots"),
        "默认截图目录应为 <GAME_CONTROL_HOME>/shots: %s" % default_abs.parent,
    )
    record(
        "3. 全屏/区域截图",
        True,
        "全屏 %dx%d、区域 200x120、裁剪 50x80、超大区域裁剪、allScreens %dx%d、monitor=1、默认目录均正确"
        % (full_size[0], full_size[1], virtual["width"], virtual["height"]),
    )


# ---------------------------------------------------------------------------
# 4. base64
# ---------------------------------------------------------------------------


def test_base64(ctx: dict) -> None:
    env = ctx["env"]
    shots = ctx["tmp"] / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / "b64.png"
    response = call(
        "screenshot",
        {"path": str(path), "base64": True, "region": {"x": 0, "y": 0, "width": 120, "height": 90}},
        env_extra=env,
    )
    expect(response.get("ok") is True, "base64 截图失败: %r" % response)
    result = response["result"]
    encoded = result.get("base64")
    expect(isinstance(encoded, str) and len(encoded) > 0, "base64 字段缺失或为空")
    expect(result.get("base64Length") == len(encoded), "base64Length 应等于 base64 字符串长度")
    decoded = base64.b64decode(encoded)
    expect(decoded.startswith(PNG_MAGIC), "base64 解码后不是 PNG（魔数不匹配）")
    expect(decoded == path.read_bytes(), "base64 内容应等于落盘文件内容")

    # 补充：JPEG 编码可落盘（base64=false，仅验证格式支持）
    jpeg_path = shots / "format.jpg"
    response = call(
        "screenshot",
        {"path": str(jpeg_path), "format": "jpeg", "quality": 80, "region": {"x": 0, "y": 0, "width": 64, "height": 48}},
        env_extra=env,
    )
    expect(response.get("ok") is True, "JPEG 截图失败: %r" % response)
    expect(jpeg_path.read_bytes().startswith(JPEG_MAGIC), "JPEG 文件魔数不匹配")
    expect(pil_size(jpeg_path) == (64, 48), "JPEG 截图尺寸不是 64×48")
    record("4. base64/JPEG", True, "PNG base64 可解码且长度正确；JPEG 编码落盘正常")


# ---------------------------------------------------------------------------
# 5. 源码扫描
# ---------------------------------------------------------------------------


def test_source_scan(ctx: dict) -> None:
    required = [
        "game_control.py",
        "win_native.py",
        "screenshot.py",
        "input_backend.py",
        "window_capture.py",
        "circle_motion.py",
        "template_match.py",
        "skills.py",
        "test_backend.py",
    ]
    missing = [name for name in required if not (SCRIPT_DIR / name).exists()]
    expect(not missing, "缺少 Python 源文件: %s" % missing)
    expect((SCRIPT_DIR / "requirements.txt").exists(), "缺少 python/requirements.txt")

    # v0.2：随插件分发的 WGC vendor 包 + LICENSE
    vendor_dir = SCRIPT_DIR / "vendor"
    for path in (
        vendor_dir / "windows_capture" / "__init__.py",
        vendor_dir / "windows_capture" / "windows_capture.pyd",
        vendor_dir / "windows_capture_LICENSE.txt",
    ):
        expect(path.exists(), "缺少 vendor 文件: %s" % path)
    license_text = (vendor_dir / "windows_capture_LICENSE.txt").read_text(encoding="utf-8", errors="replace")
    expect("MIT" in license_text, "vendor LICENSE 不是 MIT 文本")

    banned_roots = {"socket", "urllib", "http", "requests", "websocket"}
    import_re = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
    offenders = []
    for path in sorted(SCRIPT_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for root in import_re.findall(text):
            if root.split(".")[0] in banned_roots:
                offenders.append("%s -> %s" % (path.name, root))
    expect(not offenders, "发现联网库 import: %s" % offenders)

    win_text = (SCRIPT_DIR / "win_native.py").read_text(encoding="utf-8", errors="replace")
    for token in (
        "ctypes",
        "c_size_t",
        "SendInput",
        "GetDIBits",
        "SetProcessDpiAwarenessContext",
        "PrintWindow",
        "GetWindowDC",
        "SetForegroundWindow",
        "AttachThreadInput",
        "GetAsyncKeyState",
        "ClientToScreen",
    ):
        expect(token in win_text, "win_native.py 缺少关键实现: %s" % token)
    win_imports = {root.split(".")[0] for root in import_re.findall(win_text)}
    forbidden_imports = win_imports & {"pyautogui", "pynput", "keyboard", "mouse"}
    expect(
        not forbidden_imports,
        "win_native.py 不得 import 第三方输入库，实际: %s" % sorted(forbidden_imports),
    )

    window_text = (SCRIPT_DIR / "window_capture.py").read_text(encoding="utf-8", errors="replace")
    for token in (
        "EnumWindows",
        "PrintWindow",
        "start_free_threaded",
        "draw_border=None",
        "windows_capture",
        "WINDOW_CAPTURE_FAILED",
        "WINDOW_NOT_FOUND",
        "blackRatio",
        "clientRect",
    ):
        expect(token in window_text, "window_capture.py 缺少关键实现: %s" % token)

    circle_text = (SCRIPT_DIR / "circle_motion.py").read_text(encoding="utf-8", errors="replace")
    for token in ("circle_points", "point_at_elapsed", "INVALID_ARGUMENT", "virtual_screen"):
        expect(token in circle_text, "circle_motion.py 缺少关键实现: %s" % token)

    template_text = (SCRIPT_DIR / "template_match.py").read_text(encoding="utf-8", errors="replace")
    for token in ("rfft2", "numpy", "find_template", "TEMPLATE_NOT_FOUND", "INVALID_TEMPLATE", "NMS"):
        expect(token in template_text, "template_match.py 缺少关键实现: %s" % token)

    skills_text = (SCRIPT_DIR / "skills.py").read_text(encoding="utf-8", errors="replace")
    for token in (
        "skill.json",
        "backups",
        ".trash",
        "INVALID_SKILL",
        "SKILL_NOT_FOUND",
        "os.replace",
        # v0.4 JSONC
        "strip_jsonc",
        "normalize_skill",
        "render_skill_jsonc",
        "NORMALIZE_DEFAULTS",
        "第 %d 步",
    ):
        expect(token in skills_text, "skills.py 缺少关键实现: %s" % token)

    main_text = (SCRIPT_DIR / "game_control.py").read_text(encoding="utf-8", errors="replace")
    for token in (
        "--serve",
        "GAME_CONTROL_HOME",
        "SHOT_DIR",
        "LOG_DIR",
        "actions.jsonl",
        "type_text",
        "list_windows",
        "capture_window",
        "windowCapture",
        "activate_window",
        "mouse_circle",
        "find_image",
        "wait_image",
        "skill_run",
        "SKILL_VAR_UNRESOLVED",
        "GetAsyncKeyState",
        # v0.4
        "JSONC",
        # v0.5
        "templates",
        "abortReason",
        "skill_progress",
        "estimatedDurationMs",
        "skill-progress.json",
        # v0.6
        "skill_deadline",
        "effectiveDurationMs",
        "finishedAt",
        # v0.6.2
        "WAIT_IMAGE_TIMEOUT",
        "screenshotPath",
    ):
        expect(token in main_text, "game_control.py 缺少关键实现: %s" % token)
    record(
        "5. 源码扫描",
        True,
        "%d 个 py 文件无联网 import；win_native 纯 ctypes；vendor=%s"
        % (len(list(SCRIPT_DIR.glob("*.py"))), "windows_capture 2.0.1"),
    )


# ---------------------------------------------------------------------------
# 6. --selftest / --json status 子进程
# ---------------------------------------------------------------------------


def test_subprocess_modes(ctx: dict) -> None:
    env = ctx["env"]
    proc = run_backend(["--selftest"], env_extra=env)
    expect(proc.returncode == 0, "--selftest 退出码应为 0，实际 %d，stderr=%r" % (proc.returncode, (proc.stderr or "")[-800:]))
    report = one_json_line(proc.stdout)
    expect(report.get("ok") is True, "--selftest 报告 ok=false: %r" % report.get("failures"))
    expect(report.get("event") == "selftest", "--selftest 应输出 event=selftest")
    expect(isinstance(report.get("checks"), list) and report["checks"], "--selftest 缺少 checks")
    # v0.3（SPEC 8.5.8）：selftest 增加 numpy / 模板目录 / GetAsyncKeyState 等能力检查
    check_names = [str(item.get("name", "")) for item in report["checks"]]
    for needle in ("numpy", "模板目录", "技能库", "GetAsyncKeyState", "JSONC"):
        expect(any(needle in name for name in check_names), "--selftest 缺少 v0.3/v0.4 能力检查: %s" % needle)
    failed_checks = [item for item in report["checks"] if not item.get("ok")]
    expect(not failed_checks, "--selftest 存在失败项: %r" % failed_checks[:3])

    proc = run_backend(["--json", '{"action":"status"}'], env_extra=env)
    expect(proc.returncode == 0, "--json status 退出码应为 0，实际 %d" % proc.returncode)
    response = one_json_line(proc.stdout)
    expect(response.get("ok") is True, "--json status 应返回 ok:true")

    proc = run_backend(["--version"], env_extra=env)
    expect(proc.returncode == 0, "--version 退出码应为 0")
    version = one_json_line(proc.stdout)
    expect(version.get("ok") is True and version.get("version"), "--version 输出缺少 version: %r" % version)

    # stdin 流协议：两行请求 → 两行响应，id 回显
    requests = [
        {"id": 7, "action": "status"},
        {"id": 8, "action": "mouse_position"},
    ]
    stdin_text = "\n".join(json.dumps(item) for item in requests) + "\n"
    proc = run_backend(["--stdin"], stdin_text=stdin_text, env_extra=env)
    expect(proc.returncode == 0, "--stdin 退出码应为 0")
    responses = json_lines(proc.stdout)
    expect(len(responses) == 2, "--stdin 应输出 2 行响应，实际 %d" % len(responses))
    expect([item.get("id") for item in responses] == [7, 8], "--stdin 响应 id 未正确回显: %r" % responses)
    expect(all(item.get("ok") for item in responses), "--stdin 只读动作应全部成功")

    # worker 协议：首行 ready 事件，随后逐行响应
    proc = run_backend(["--serve"], stdin_text='{"id":9,"action":"status"}\n', env_extra=env)
    expect(proc.returncode == 0, "--serve 退出码应为 0")
    lines = json_lines(proc.stdout)
    expect(len(lines) == 2, "--serve 应输出 ready + 1 条响应，实际 %d 行" % len(lines))
    expect(lines[0].get("event") == "ready" and lines[0].get("ok") is True, "worker 首行必须是 ready 事件: %r" % lines[0])
    expect(lines[1].get("id") == 9 and lines[1].get("ok") is True, "worker 响应不正确: %r" % lines[1])
    record(
        "6. --selftest / --json status / stdin / worker",
        True,
        "selftest、CLI、stdin、--serve 退出码与 JSON 行协议全部正确",
    )


# ---------------------------------------------------------------------------
# 补充：环境变量 / 审计日志
# ---------------------------------------------------------------------------


def test_env_and_audit(ctx: dict) -> None:
    root = ctx["tmp"] / "envhome"
    shots = root / "custom-shots"
    logs = root / "custom-logs"
    env = {
        "GAME_CONTROL_HOME": str(root),
        "SHOT_DIR": str(shots),
        "LOG_DIR": str(logs),
    }
    response = call("status", env_extra=env)
    expect(response.get("ok") is True, "带 SHOT_DIR/LOG_DIR 的 status 失败")
    result = response["result"]
    expect(norm(Path(result["shotDir"])) == norm(shots), "SHOT_DIR 未生效: %s" % result["shotDir"])
    expect(norm(Path(result["logDir"])) == norm(logs), "LOG_DIR 未生效: %s" % result["logDir"])

    relative_path = "rel_shot.png"
    response = call("screenshot", {"path": relative_path}, env_extra=env)
    expect(response.get("ok") is True, "相对路径截图失败: %r" % response)
    saved = Path(response["result"]["absolutePath"])
    expect(saved.exists(), "相对路径截图未落盘: %s" % saved)
    expect(norm(saved) == norm(shots / relative_path), "相对路径应相对 SHOT_DIR: %s" % saved)

    log_file = logs / "game-control.log"
    expect(log_file.exists(), "日志文件未创建: %s" % log_file)
    audit_file = logs / "actions.jsonl"
    expect(audit_file.exists(), "审计文件未创建: %s" % audit_file)
    audit_lines = [line for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    expect(audit_lines, "actions.jsonl 不能为空")
    for line in audit_lines:
        entry = json.loads(line)
        for key in ("ts", "action", "ok", "durationMs"):
            expect(key in entry, "审计行缺少字段 %s: %r" % (key, entry))
    actions = [json.loads(line)["action"] for line in audit_lines]
    expect("status" in actions and "screenshot" in actions, "审计日志缺少动作记录: %s" % actions)
    record("补充. 环境变量/日志/审计", True, "SHOT_DIR/LOG_DIR/GAME_CONTROL_HOME 生效，日志与 actions.jsonl 正常")


# ---------------------------------------------------------------------------
# v0.2：窗口枚举 / 后台窗口截图（只读，不注入输入）
# ---------------------------------------------------------------------------


def _rect_intersects(rect: dict, virtual: dict) -> bool:
    left = max(int(rect["x"]), int(virtual["x"]))
    top = max(int(rect["y"]), int(virtual["y"]))
    right = min(int(rect["x"]) + int(rect["width"]), int(virtual["x"]) + int(virtual["width"]))
    bottom = min(int(rect["y"]) + int(rect["height"]), int(virtual["y"]) + int(virtual["height"]))
    return left < right and top < bottom


def _pick_capture_candidate(windows: list, virtual: dict) -> dict | None:
    """挑一个可见、非最小化、与虚拟桌面相交的真实窗口用于截图测试。"""
    candidates = []
    for window in windows:
        rect = window.get("rect") or {}
        if window.get("minimized") or not window.get("visible"):
            continue
        if not str(window.get("title") or "").strip():
            continue
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        if not _rect_intersects(rect, virtual):
            continue
        candidates.append((width * height, window))
    if not candidates:
        return None
    # 优先中等面积窗口（截图快），其次前台窗口，最后面积最小的
    moderate = [item for item in candidates if 100_000 <= item[0] <= 2_500_000]
    pool = moderate or candidates
    foreground = [item for item in pool if item[1].get("foreground")]
    pool = foreground or pool
    pool.sort(key=lambda item: item[0])
    return pool[0][1]


def test_window_capture(ctx: dict) -> None:
    env = ctx["env"]
    if os.name != "nt":
        # 窗口枚举/后台窗口截图仅 Windows 支持（SPEC 2.3.1 第 7 条）
        record("7. 窗口枚举/后台截图", True, "非 Windows 平台跳过（动作返回 UNSUPPORTED_PLATFORM）")
        return
    shots = ctx["tmp"] / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    # 1) list_windows：非空 + 字段合法
    response = call("list_windows", {"limit": 50}, env_extra=env)
    expect(response.get("ok") is True, "list_windows 失败: %r" % response)
    result = response["result"]
    expect(isinstance(result.get("count"), int), "list_windows.count 必须是整数")
    windows = result.get("windows")
    expect(isinstance(windows, list) and len(windows) > 0, "list_windows 未返回任何窗口（真实桌面应至少有几个）")
    expect(result["count"] == len(windows), "list_windows.count 与 windows 长度不一致")
    for window in windows:
        expect(isinstance(window.get("hwnd"), int) and window["hwnd"] > 0, "窗口 hwnd 非法: %r" % window)
        for key in ("title", "class", "process"):
            expect(isinstance(window.get(key), str), "窗口字段 %s 必须是字符串: %r" % (key, window))
        expect(isinstance(window.get("pid"), int), "窗口 pid 必须是整数: %r" % window)
        rect = window.get("rect") or {}
        for key in ("x", "y", "width", "height"):
            expect(isinstance(rect.get(key), int), "窗口 rect.%s 必须是整数: %r" % (key, rect))
        expect(rect["width"] >= 0 and rect["height"] >= 0, "窗口 rect 宽高不能为负: %r" % rect)
        for key in ("minimized", "visible", "foreground", "cloaked"):
            expect(isinstance(window.get(key), bool), "窗口字段 %s 必须是布尔: %r" % (key, window))
        # v0.3（SPEC 8.2.6/8.5.6）：每个窗口都有 clientRect 且宽高 > 0
        client = window.get("clientRect")
        expect(isinstance(client, dict), "窗口缺少 clientRect: %r" % window)
        for key in ("x", "y", "width", "height"):
            expect(isinstance(client.get(key), int), "窗口 clientRect.%s 必须是整数: %r" % (key, client))
        expect(
            client["width"] > 0 and client["height"] > 0,
            "窗口 clientRect 宽高必须 > 0: %r（title=%r）" % (client, window.get("title")),
        )
    # z 序：前台窗口在最前（若列表里有前台窗口）
    foreground_index = next((i for i, item in enumerate(windows) if item["foreground"]), None)
    if foreground_index is not None:
        expect(foreground_index == 0, "list_windows 未把前台窗口排在最前（index=%d）" % foreground_index)

    # titleContains 过滤（大小写不敏感）
    needle = next((window["title"][:4] for window in windows if len(window["title"]) >= 4), None)
    if needle:
        filtered = call("list_windows", {"titleContains": needle.lower(), "limit": 50}, env_extra=env)
        expect(filtered.get("ok") is True, "titleContains 查询失败: %r" % filtered)
        filtered_windows = filtered["result"]["windows"]
        expect(len(filtered_windows) >= 1, "titleContains=%r 未匹配到窗口" % needle)
        expect(
            all(needle.lower() in item["title"].lower() for item in filtered_windows),
            "titleContains 过滤结果含不匹配窗口",
        )

    # 2) capture_window 参数校验与错误码
    expect_error("capture_window", {}, "INVALID_ARGUMENT", env)
    expect_error("capture_window", {"window": "x", "hwnd": 123}, "INVALID_ARGUMENT", env)
    expect_error("capture_window", {"window": "__dgc_no_such_window_xyz__"}, "WINDOW_NOT_FOUND", env)
    expect_error("capture_window", {"hwnd": 999999999}, "WINDOW_NOT_FOUND", env)
    expect_error("capture_window", {"hwnd": 0}, "INVALID_ARGUMENT", env)
    expect_error("capture_window", {"window": "x", "engine": "bogus"}, "INVALID_ARGUMENT", env)
    expect_error("capture_window", {"window": "x", "timeoutMs": 100}, "INVALID_ARGUMENT", env)
    expect_error("capture_window", {"hwnd": 1, "unknownField": 1}, "UNKNOWN_PARAM", env)

    # 3) status.windowCapture 能力字段
    status = call("status", env_extra=env)["result"]
    capability = status.get("windowCapture")
    expect(isinstance(capability, dict), "status 缺少 windowCapture 能力字段")
    for key in ("wgcAvailable", "vendor", "printWindow"):
        expect(isinstance(capability.get(key), bool), "windowCapture.%s 必须是布尔" % key)

    # 4) engine=screen：抓真实窗口落盘 + Pillow 校验尺寸 + base64
    virtual = status["virtualScreen"]
    candidate = _pick_capture_candidate(windows, virtual)
    expect(candidate is not None, "没有可见且与虚拟桌面相交的真实窗口可用于 capture_window 测试")
    hwnd = candidate["hwnd"]
    screen_path = shots / "window_screen.png"
    response = call(
        "capture_window",
        {"hwnd": hwnd, "engine": "screen", "path": str(screen_path), "base64": True},
        env_extra=env,
    )
    expect(response.get("ok") is True, "engine=screen 抓取窗口失败: %r" % response)
    screen_result = response["result"]
    expect(screen_result.get("engine") == "screen", "engine 字段应如实返回 screen: %r" % screen_result.get("engine"))
    expect(screen_result["window"]["hwnd"] == hwnd, "结果 window.hwnd 与请求不一致")
    expect(screen_path.exists(), "窗口截图未落盘: %s" % screen_path)
    expect(screen_path.read_bytes().startswith(PNG_MAGIC), "窗口截图不是 PNG 魔数")
    expect(pil_size(screen_path) == (screen_result["width"], screen_result["height"]), "窗口截图尺寸不一致")
    expect(screen_result["bytes"] == screen_path.stat().st_size, "窗口截图 bytes 与文件大小不一致")
    expect(
        any("occluded" in str(item) for item in screen_result.get("warnings") or []),
        "engine=screen 应返回 window may be occluded 警告: %r" % screen_result.get("warnings"),
    )
    encoded = screen_result.get("base64")
    expect(isinstance(encoded, str) and encoded, "capture_window base64 字段缺失")
    expect(screen_result.get("base64Length") == len(encoded), "capture_window base64Length 不一致")
    expect(base64.b64decode(encoded).startswith(PNG_MAGIC), "capture_window base64 解码后不是 PNG")

    # 5) engine=auto：回退链应能返回画面；非 wgc 结果应带 fallbackReason
    auto_path = shots / "window_auto.png"
    auto_response = call(
        "capture_window",
        {"window": candidate["title"][:4] if len(candidate["title"]) >= 4 else candidate["title"], "engine": "auto", "path": str(auto_path)},
        env_extra=env,
    )
    expect(auto_response.get("ok") is True, "engine=auto 抓取窗口失败: %r" % auto_response)
    auto_result = auto_response["result"]
    expect(
        auto_result.get("engine") in ("wgc", "printwindow", "window-dc", "screen"),
        "auto 结果 engine 非法: %r" % auto_result.get("engine"),
    )
    expect(auto_path.exists(), "auto 窗口截图未落盘")
    expect(pil_size(auto_path) == (auto_result["width"], auto_result["height"]), "auto 窗口截图尺寸不一致")
    if auto_result.get("engine") != "wgc":
        expect(
            isinstance(auto_result.get("fallbackReason"), str) and auto_result["fallbackReason"],
            "回退到非 wgc 时应返回 fallbackReason: %r" % auto_result.get("fallbackReason"),
        )

    # 6) 显式 engine=wgc：受限/不受限环境都必须优雅返回（不崩溃）
    wgc_response = raw_request(
        "capture_window",
        {"hwnd": hwnd, "engine": "wgc", "timeoutMs": 1500, "path": "window_wgc.png"},
        env_extra=env,
    )
    if wgc_response.get("ok") is True:
        wgc_result = wgc_response["result"]
        expect(wgc_result.get("engine") == "wgc", "显式 wgc 成功时 engine 应为 wgc")
        expect(Path(wgc_result["absolutePath"]).exists(), "显式 wgc 截图未落盘")
        wgc_detail = "成功 engine=wgc（%dx%d）" % (wgc_result["width"], wgc_result["height"])
    else:
        code = (wgc_response.get("error") or {}).get("code")
        expect(
            code in ("WINDOW_CAPTURE_FAILED", "MISSING_DEPENDENCY"),
            "显式 WGC 失败应返回 WINDOW_CAPTURE_FAILED/MISSING_DEPENDENCY，实际 %r" % code,
        )
        wgc_detail = "预期失败 code=%s（受限沙箱正常）" % code

    record(
        "7. 窗口枚举/后台截图",
        True,
        "list_windows %d 个窗口；engine=screen %dx%d 落盘+base64；engine=auto→%s；WGC=%s"
        % (
            len(windows),
            screen_result["width"],
            screen_result["height"],
            auto_result.get("engine"),
            wgc_detail,
        ),
    )


# ---------------------------------------------------------------------------
# v0.3：圆形轨迹 / 模板匹配 / 激活窗口 / 技能库（全部零真实输入）
# ---------------------------------------------------------------------------


class _NoInputStub:
    """mouse_circle dryRun 专用输入存根：任何真实输入调用都记录并报错。"""

    def __init__(self) -> None:
        self.calls: list = []

    def get_virtual_screen(self) -> dict:
        return {"x": 0, "y": 0, "width": 3840, "height": 2160}

    def get_primary_screen(self) -> dict:
        return {"x": 0, "y": 0, "width": 3840, "height": 2160}

    def get_pointer(self) -> dict:
        self.calls.append("get_pointer")
        return {"x": 0, "y": 0}

    def move_to(self, *args):  # noqa: ANN002
        self.calls.append(("move_to", args))
        raise AssertionError("dryRun 不应调用 move_to")

    def button_down(self, *args):  # noqa: ANN002
        self.calls.append(("button_down", args))
        raise AssertionError("dryRun 不应按下鼠标键")

    def button_up(self, *args):  # noqa: ANN002
        self.calls.append(("button_up", args))

    def key_is_down(self, *args):  # noqa: ANN002
        self.calls.append(("key_is_down", args))
        return False

    def get_foreground_hwnd(self) -> int:
        return 0


def _load_backend():
    import game_control

    return game_control


def _fake_runtime(templates_dir=None, shot_dir=None):
    game_control = _load_backend()  # noqa: F841 - 确保模块已导入
    stub = _NoInputStub()
    config = types.SimpleNamespace(
        templates_dir=Path(templates_dir) if templates_dir else Path(tempfile.gettempdir()) / "dgc-none",
        shot_dir=Path(shot_dir) if shot_dir else Path(tempfile.gettempdir()) / "dgc-none",
        log_dir=Path(tempfile.gettempdir()) / "dgc-none",
    )
    runtime = types.SimpleNamespace(
        input_impl=stub,
        logger=logging.getLogger("dgc-test"),
        config=config,
    )
    return runtime, stub


def _synthetic_block_image():
    """400×300 合成图 + (120,80) 处 60×20 亮块模板（带条纹，NCC 有效）。"""
    import numpy as np

    rng = np.random.RandomState(11)
    image = rng.randint(20, 60, size=(300, 400, 3), dtype=np.uint8)
    block = np.zeros((20, 60, 3), dtype=np.uint8)
    for x in range(60):
        block[:, x, :] = 200 if (x // 6) % 2 == 0 else 120
    block[:10, :, :] = 255
    image[80:100, 120:180] = block
    return image, block


def test_circle_motion(ctx: dict) -> None:
    """SPEC 8.5.1：circle_points 纯函数。"""
    import math

    import circle_motion

    stats = circle_motion.circle_points(
        center_x=1920,
        center_y=1080,
        radius=100,
        period_ms=2000,
        duration_ms=4000,
        start_angle_deg=-90,
    )
    expect(stats["samples"] == int(4000 / 1000 * 120) + 1, "circle_points.samples 计算错误: %r" % stats["samples"])
    expect(abs(stats["loops"] - 2.0) < 1e-6, "circle_points.loops 错误: %r" % stats["loops"])
    expect(
        abs(stats["start"]["x"] - 1920) <= 1 and abs(stats["start"]["y"] - 980) <= 1,
        "startAngleDeg=-90 首点应在正上方: %r" % stats["start"],
    )
    quarter = circle_motion.point_at_elapsed(1920, 1080, 100, 2000, "cw", -90, 500)
    half = circle_motion.point_at_elapsed(1920, 1080, 100, 2000, "cw", -90, 1000)
    three_quarter = circle_motion.point_at_elapsed(1920, 1080, 100, 2000, "cw", -90, 1500)
    expect(abs(quarter[0] - 2020) <= 1 and abs(quarter[1] - 1080) <= 1, "顺时针 1/4 应在右侧: %r" % (quarter,))
    expect(abs(half[0] - 1920) <= 1 and abs(half[1] - 1180) <= 1, "顺时针 1/2 应在下方: %r" % (half,))
    expect(
        abs(three_quarter[0] - 1820) <= 1 and abs(three_quarter[1] - 1080) <= 1,
        "顺时针 3/4 应在左侧: %r" % (three_quarter,),
    )
    ccw_quarter = circle_motion.point_at_elapsed(1920, 1080, 100, 2000, "ccw", -90, 500)
    expect(abs(ccw_quarter[0] - 1820) <= 1 and abs(ccw_quarter[1] - 1080) <= 1, "ccw 1/4 应在左侧: %r" % (ccw_quarter,))
    for elapsed in (0, 250, 500, 1000, 1777):
        x, y = circle_motion.point_at_elapsed(1920, 1080, 100, 2000, "cw", -90, elapsed)
        radius_error = abs(math.hypot(x - 1920, y - 1080) - 100)
        expect(radius_error <= 1, "半径误差应 ≤1px（elapsed=%s，误差 %.3f）" % (elapsed, radius_error))
    expect(
        abs(stats["bbox"]["minX"] - 1820) < 1e-6 and abs(stats["bbox"]["maxY"] - 1180) < 1e-6,
        "bbox 计算错误: %r" % stats["bbox"],
    )
    try:
        circle_motion.circle_points(center_x=1920, center_y=1080, radius=0)
        raise CheckFailure("radius=0 应报 INVALID_ARGUMENT")
    except circle_motion.CircleMotionError as exc:
        expect(exc.code == "INVALID_ARGUMENT", "radius=0 错误码应为 INVALID_ARGUMENT，实际 %r" % exc.code)
    try:
        circle_motion.circle_points(
            center_x=100,
            center_y=100,
            radius=150,
            virtual_screen={"x": 0, "y": 0, "width": 200, "height": 200},
        )
        raise CheckFailure("圆形包围盒越界应报 INVALID_ARGUMENT")
    except circle_motion.CircleMotionError as exc:
        expect(exc.code == "INVALID_ARGUMENT", "越界错误码应为 INVALID_ARGUMENT，实际 %r" % exc.code)
    record(
        "9. circle_points 纯函数",
        True,
        "samples=481 / loops=2 / 四分之一位置 / 半径误差≤1 / ccw / 越界 INVALID_ARGUMENT 均正确",
    )


def test_mouse_circle_dry_run(ctx: dict) -> None:
    """SPEC 8.5.2：mouse_circle dryRun 不产生任何输入。"""
    if os.name != "nt":
        record("10. mouse_circle dryRun", True, "非 Windows 跳过（mouse_circle 返回 UNSUPPORTED_PLATFORM）")
        return
    game_control = _load_backend()
    runtime, stub = _fake_runtime()
    validated = game_control.validate_params(
        "mouse_circle",
        {"centerX": 1920, "centerY": 1080, "radius": 300, "dryRun": True},
    )
    result = game_control.action_mouse_circle(validated, runtime)
    expect(result["samples"] == 3601, "dryRun samples 应为 3601，实际 %r" % result["samples"])
    expect(result["aborted"] is False, "dryRun 不应 aborted")
    expect(result["abortReason"] is None, "dryRun abortReason 应为 None")
    expect(result["actualDurationMs"] == 0, "dryRun actualDurationMs 应为 0")
    expect(stub.calls == [], "dryRun 不应调用任何输入方法，实际调用: %r" % stub.calls)
    # 进程级（JSON 信封）验证
    response = call(
        "mouse_circle",
        {"centerX": 1920, "centerY": 1080, "radius": 300, "dryRun": True},
        env_extra=ctx["env"],
    )
    expect(response.get("ok") is True, "进程级 mouse_circle dryRun 失败: %r" % response)
    payload = response["result"]
    expect(payload["samples"] == 3601 and payload["aborted"] is False, "进程级 dryRun 返回不正确: %r" % payload)
    record("10. mouse_circle dryRun", True, "samples=3601、aborted=false、进程内 0 次输入调用")


def test_find_image_synthetic(ctx: dict) -> None:
    """SPEC 8.5.3：find_image 合成图纯函数 + 注入截图 + 错误码。"""
    if os.name != "nt":
        record("11. find_image 合成图", True, "非 Windows 跳过（find_image 返回 UNSUPPORTED_PLATFORM）")
        return
    import numpy as np
    from PIL import Image

    import template_match

    env = ctx["env"]
    image, block = _synthetic_block_image()
    result = template_match.find_template(
        image.tobytes(), 400, 300, block, threshold=0.85, max_results=3
    )
    expect(result["found"] is True, "合成图应命中模板")
    best = result["matches"][0]
    error_x = abs(best["center"]["x"] - 150)
    error_y = abs(best["center"]["y"] - 90)
    expect(error_x <= 2 and error_y <= 2, "命中中心误差应 ≤2px，实际 center=%r" % (best["center"],))
    expect(best["score"] > 0.95, "命中 score 应 >0.95，实际 %r" % best["score"])
    blank = np.zeros((300, 400, 3), dtype=np.uint8)
    blank_result = template_match.find_template(blank.tobytes(), 400, 300, block, threshold=0.85)
    expect(blank_result["found"] is False, "空白图不应命中")
    # 错误码（模板先加载，不截屏）
    expect_error("find_image", {"template": "__dgc_no_such_template__.png"}, "TEMPLATE_NOT_FOUND", env)
    expect_error("find_image", {"template": "../evil.png"}, "INVALID_TEMPLATE", env)
    templates = Path(ctx["home"]) / "templates" / "synth"
    templates.mkdir(parents=True, exist_ok=True)
    bad_file = templates / "bad.png"
    bad_file.write_bytes(b"not-an-image")
    expect_error("find_image", {"template": "synth/bad.png"}, "INVALID_TEMPLATE", env)
    # 端到端注入截图：模板文件 + patch screenshot.capture
    game_control = _load_backend()
    import screenshot

    good_file = templates / "block.png"
    Image.fromarray(block).save(str(good_file))
    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (image.tobytes(), int(w), int(h))  # type: ignore[assignment]
    runtime, _stub = _fake_runtime(
        templates_dir=Path(ctx["home"]) / "templates",
        shot_dir=ctx["tmp"] / "shots",
    )
    try:
        validated = game_control.validate_params(
            "find_image",
            {
                "template": "synth/block.png",
                "region": {"x": 50, "y": 60, "width": 400, "height": 300},
                "threshold": 0.85,
            },
        )
        injected = game_control.action_find_image(validated, runtime)
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]
    expect(injected["found"] is True, "注入截图后 find_image 应命中")
    injected_match = injected["matches"][0]
    expect(
        abs(injected_match["center"]["x"] - (50 + 150)) <= 2 and abs(injected_match["center"]["y"] - (60 + 90)) <= 2,
        "注入截图命中点应加 region 偏移: %r" % injected_match["center"],
    )
    expect(injected["searchRegion"]["x"] == 50 and injected["source"] == "region", "searchRegion/source 不正确: %r" % injected)
    record(
        "11. find_image 合成图",
        True,
        "命中中心误差≤2、score>0.95、空白图无命中、TEMPLATE_NOT_FOUND/INVALID_TEMPLATE、注入截图端到端通过",
    )


def test_wait_image_synthetic(ctx: dict) -> None:
    """SPEC 8.5.4：wait_image 合成图 + 短超时（注入 find 结果，不截图）。"""
    if os.name != "nt":
        record("12. wait_image 超时/出现", True, "非 Windows 跳过（wait_image 返回 UNSUPPORTED_PLATFORM）")
        return
    game_control = _load_backend()
    runtime, _stub = _fake_runtime()
    original = game_control._find_image_once

    def _never_found(params, rt):  # noqa: ANN001
        return {
            "found": False,
            "matches": [],
            "source": "screen",
            "searchRegion": {"x": 0, "y": 0, "width": 10, "height": 10},
            "template": {"path": "<test>", "width": 1, "height": 1},
            "elapsedMs": 0,
        }

    game_control._find_image_once = _never_found
    try:
        validated = game_control.validate_params(
            "wait_image", {"template": "x.png", "timeoutMs": 150, "intervalMs": 50}
        )
        timed_out = game_control.action_wait_image(validated, runtime)
    finally:
        game_control._find_image_once = original
    expect(timed_out["timedOut"] is True, "wait_image 短超时应 timedOut=true: %r" % timed_out)
    expect(timed_out["found"] is False, "超时 found 应为 false")
    expect(timed_out["attempts"] >= 1, "wait_image 应至少尝试一次")
    expect(timed_out["elapsedMs"] >= 100, "wait_image elapsedMs 应接近超时: %r" % timed_out["elapsedMs"])

    # 注入截图 + 真实模板：appear 应成功
    from PIL import Image

    import screenshot

    image, block = _synthetic_block_image()
    templates = Path(ctx["home"]) / "templates" / "synth"
    templates.mkdir(parents=True, exist_ok=True)
    Image.fromarray(block).save(str(templates / "block.png"))
    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (image.tobytes(), int(w), int(h))  # type: ignore[assignment]
    runtime2, _stub2 = _fake_runtime(
        templates_dir=Path(ctx["home"]) / "templates",
        shot_dir=ctx["tmp"] / "shots",
    )
    try:
        validated2 = game_control.validate_params(
            "wait_image",
            {
                "template": "synth/block.png",
                "region": {"x": 0, "y": 0, "width": 400, "height": 300},
                "timeoutMs": 800,
                "intervalMs": 100,
            },
        )
        appeared = game_control.action_wait_image(validated2, runtime2)
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]
    expect(appeared["found"] is True and appeared["timedOut"] is False, "wait_image appear 应命中: %r" % appeared)
    expect(appeared["match"] is not None and appeared["match"]["score"] > 0.95, "wait_image match 分数过低")
    record(
        "12. wait_image 超时/出现",
        True,
        "短超时 timedOut=true（attempts=%d），注入截图后 appear 命中" % timed_out["attempts"],
    )


def test_activate_window_validation(ctx: dict) -> None:
    """SPEC 8.5.5：activate_window 只做参数/不存在窗口校验，不做真实激活。"""
    if os.name != "nt":
        record("13. activate_window 参数校验", True, "非 Windows 跳过（UNSUPPORTED_PLATFORM）")
        return
    env = ctx["env"]
    expect_error("activate_window", {}, "INVALID_ARGUMENT", env)
    expect_error("activate_window", {"window": "x", "hwnd": 1}, "INVALID_ARGUMENT", env)
    expect_error("activate_window", {"hwnd": 999999999}, "WINDOW_NOT_FOUND", env)
    expect_error("activate_window", {"window": "__dgc_no_such_window__"}, "WINDOW_NOT_FOUND", env)
    expect_error("activate_window", {"hwnd": 1, "timeoutMs": 50}, "INVALID_ARGUMENT", env)
    expect_error("activate_window", {"hwnd": 1, "bogus": 1}, "UNKNOWN_PARAM", env)
    record("13. activate_window 参数校验", True, "缺参/双参/不存在窗口/非法 timeoutMs/未知参数 错误码正确，未真实激活")


def _skill_steps() -> list:
    return [{"action": "wait", "params": {"durationMs": 0}}]


def test_skills(ctx: dict) -> None:
    """SPEC 8.5.7：技能库 CRUD/版本/恢复/软删/skill_run dryRun + wait(0) 真实执行。"""
    game_control = _load_backend()
    root = ctx["tmp"] / "skills-home"
    env = {"GAME_CONTROL_HOME": str(root)}
    saved = call("skill_save", {"title": "DGC 测试技能", "steps": _skill_steps()}, env_extra=env)
    expect(saved.get("ok") is True, "skill_save 失败: %r" % saved)
    saved_result = saved["result"]
    skill_id = saved_result["id"]
    expect(saved_result["created"] is True and saved_result["backupCount"] == 0, "新建技能返回不正确: %r" % saved_result)

    updated = call(
        "skill_save",
        {"id": skill_id, "title": "DGC 测试技能 v2", "description": "desc", "tags": ["t"], "steps": _skill_steps()},
        env_extra=env,
    )
    expect(updated.get("ok") is True, "skill_save 更新失败: %r" % updated)
    expect(updated["result"]["created"] is False, "更新已有技能 created 应为 false")
    expect(isinstance(updated["result"].get("backupPath"), str), "更新前应自动备份: %r" % updated["result"])
    expect(updated["result"]["backupCount"] >= 1, "更新后 backupCount 应 ≥1")

    listing = call("skill_list", env_extra=env)
    expect(listing.get("ok") is True and listing["result"]["count"] >= 1, "skill_list 结果不正确")
    entry = listing["result"]["skills"][0]
    for key in ("id", "title", "description", "game", "tags", "stepCount", "updatedAt", "backupCount"):
        expect(key in entry, "skill_list 条目缺少字段: %s" % key)
    expect(entry["stepCount"] == 1, "skill_list stepCount 应为 1")

    got = call("skill_get", {"id": skill_id}, env_extra=env)
    expect(got.get("ok") is True and got["result"]["skill"]["id"] == skill_id, "skill_get 返回不正确")

    versions = call("skill_versions", {"id": skill_id}, env_extra=env)
    expect(versions.get("ok") is True and versions["result"]["count"] >= 1, "skill_versions 结果不正确")
    version = versions["result"]["versions"][0]
    for key in ("name", "savedAt", "bytes"):
        expect(key in version, "版本条目缺少字段: %s" % key)

    restored = call("skill_restore", {"id": skill_id, "backup": "latest"}, env_extra=env)
    expect(restored.get("ok") is True, "skill_restore 失败: %r" % restored)
    expect(restored["result"]["restoredFrom"] == version["name"], "restoredFrom 应为 latest 备份名")
    expect(isinstance(restored["result"].get("backupOfCurrent"), str), "恢复前应先备份当前版本")

    dry = call("skill_run", {"id": skill_id, "dryRun": True}, env_extra=env)
    expect(dry.get("ok") is True, "skill_run dryRun 失败: %r" % dry)
    expect(dry["result"]["dryRun"] is True and len(dry["result"]["steps"]) == 1, "dryRun 步骤计划不正确")
    expect(dry["result"]["steps"][0]["planned"] is True, "dryRun 步骤应标记 planned")

    real = call("skill_run", {"id": skill_id}, env_extra=env)
    expect(real.get("ok") is True, "skill_run（wait 0）失败: %r" % real)
    real_result = real["result"]
    expect(real_result["success"] is True and real_result["repeatsDone"] == 1, "skill_run 结果不正确: %r" % real_result)
    expect(real_result["steps"][0]["ok"] is True, "skill_run 步骤应成功")
    expect(real_result["aborted"] is False, "skill_run 不应 aborted")

    # 运行期某步失败 → ok:true, success:false, failedStep（模板先加载，不截屏）
    fail_id = call(
        "skill_save",
        {"title": "失败技能", "steps": [{"action": "find_image", "params": {"template": "no-such-template.png"}}]},
        env_extra=env,
    )["result"]["id"]
    failed = call("skill_run", {"id": fail_id}, env_extra=env)
    expect(failed.get("ok") is True, "运行期失败仍应 ok:true")
    expect(failed["result"]["success"] is False, "运行期失败 success 应为 false")
    expect(failed["result"].get("failedStep", {}).get("code") == "TEMPLATE_NOT_FOUND", "failedStep 错误码应为 TEMPLATE_NOT_FOUND")

    # 错误码
    expect_error("skill_get", {"id": "no-such-skill-id"}, "SKILL_NOT_FOUND", env)
    expect_error("skill_save", {"title": "bad", "steps": _skill_steps(), "id": "../evil"}, "INVALID_SKILL", env)
    expect_error(
        "skill_save",
        {"title": "bad", "steps": [{"action": "skill_run", "params": {}}]},
        "INVALID_SKILL",
        env,
    )
    expect_error(
        "skill_save",
        {"title": "bad", "steps": [{"action": "wait", "params": {"durationMs": 0}}] * 201},
        "INVALID_SKILL",
        env,
    )
    expect_error(
        "skill_save",
        {"title": "bad", "steps": [{"action": "wait", "params": {"durationMs": 0}, "saveAs": "1bad"}]},
        "INVALID_SKILL",
        env,
    )

    # 软删除
    deleted = call("skill_delete", {"id": fail_id}, env_extra=env)
    expect(deleted.get("ok") is True and deleted["result"]["deleted"] is True, "skill_delete 失败: %r" % deleted)
    expect(deleted["result"]["hardDeleted"] is False, "默认应为软删除")
    trash_path = Path(deleted["result"]["trashPath"] or "")
    expect(trash_path.exists(), "软删除后回收目录应存在: %s" % trash_path)
    expect_error("skill_get", {"id": fail_id}, "SKILL_NOT_FOUND", env)

    # 变量替换纯函数（不执行动作）
    window = {
        "hwnd": 123,
        "title": "测试窗口",
        "class": "TestClass",
        "pid": 456,
        "process": "test.exe",
        "rect": {"x": 90, "y": 190, "width": 420, "height": 320},
        "clientRect": {"x": 100, "y": 200, "width": 400, "height": 300},
    }
    window_context = game_control._skill_window_context(window)
    expect(window_context["quarterWidth"] == 100 and window_context["halfWidth"] == 200, "quarterWidth/halfWidth 计算错误")
    expect(window_context["halfHeight"] == 150, "halfHeight 计算错误")
    expect(window_context["clientCenterX"] == 300 and window_context["clientCenterY"] == 350, "客户端中心计算错误")
    expanded = game_control._expand_skill_value(
        {"x": "$window.clientCenterX", "y": "$window.clientCenterY", "r": "$window.quarterWidth"},
        window_context,
        {},
    )
    expect(expanded == {"x": 300, "y": 350, "r": 100}, "变量替换结果错误: %r" % expanded)
    contexts = {"start": {"matches": [{"center": {"x": 7, "y": 8}}]}}
    expect(
        game_control._expand_skill_value("$start.matches.0.center.x", window_context, contexts) == 7,
        "saveAs 数组下标变量解析错误",
    )
    try:
        game_control._expand_skill_value({"x": "$nope.center.x"}, window_context, contexts)
        raise CheckFailure("未知变量根应报 SKILL_VAR_UNRESOLVED")
    except game_control.GCError as exc:
        expect(exc.code == "SKILL_VAR_UNRESOLVED", "未知变量错误码应为 SKILL_VAR_UNRESOLVED，实际 %r" % exc.code)
    try:
        game_control._expand_skill_value({"x": "$window.clientRect.width"}, None, {})
        raise CheckFailure("$window 未解析时应报 SKILL_VAR_UNRESOLVED")
    except game_control.GCError as exc:
        expect(exc.code == "SKILL_VAR_UNRESOLVED", "$window 未解析错误码应为 SKILL_VAR_UNRESOLVED")
    record(
        "14. 技能库 CRUD/版本/恢复/软删/run",
        True,
        "save→update 备份→list/get/versions→restore→dryRun→wait(0) 真实执行→失败步骤→错误码→软删→变量替换 均通过",
    )


# ---------------------------------------------------------------------------
# v0.4：技能 JSONC / 参数显式化 / 中文注释（全部零真实输入）
# ---------------------------------------------------------------------------


def _jsonc_example_skill() -> str:
    """一份带 // 中文注释与 note 的 JSONC 技能（用于 content 模式）。"""
    return (
        "// 我的测试技能（JSONC 示例）\n"
        "{\n"
        '  "schema": 1,\n'
        '  "title": "Content 模式技能",\n'
        '  "description": "content 模式保存\\n第二行说明",\n'
        '  "game": "Keep on Mining!",\n'
        '  "tags": ["demo"],\n'
        '  "markdown": "## 说明",\n'
        '  "steps": [\n'
        "    // 第一步说明：等待 0ms\n"
        '    {"action": "wait", "params": {"durationMs": 0}, "waitMs": 5, "note": "content备注A\\n第二行"}\n'
        "  ]\n"
        "}\n"
    )


def test_skill_jsonc(ctx: dict) -> None:
    """SPEC 9.6：strip_jsonc 边界、保存渲染/归一化、content 模式、非法 content、JSONC skill_run。"""
    import skills

    # 1) strip_jsonc：字符串内 // 不误删、行/块注释、无注释 JSON 兼容
    strip_cases = [
        ('{"a": 1} // 行尾注释', {"a": 1}),
        ('// 文件头注释\n{"a": 1, "b": "text"}', {"a": 1, "b": "text"}),
        ('{"url": "http://example.com/a//b", "s": "/* keep */"}', {"url": "http://example.com/a//b", "s": "/* keep */"}),
        ('{\n  /* 块注释 */\n  "a": 2 // 行注释\n}', {"a": 2}),
        ('{"a": "\\" // inside string"}', {"a": '" // inside string'}),
    ]
    for text, expected in strip_cases:
        parsed = json.loads(skills.strip_jsonc(text))
        expect(parsed == expected, "strip_jsonc 解析错误: %r -> %r" % (text, parsed))
    plain = json.dumps({"a": 1, "b": [1, 2], "c": "无注释"}, ensure_ascii=False)
    expect(json.loads(skills.strip_jsonc(plain)) == json.loads(plain), "无注释纯 JSON 应原样兼容")
    try:
        skills.strip_jsonc('{"a": 1 /* 未闭合')
        raise CheckFailure("未闭合块注释应报 INVALID_SKILL")
    except skills.SkillError as exc:
        expect(exc.code == "INVALID_SKILL", "未闭合注释错误码应为 INVALID_SKILL，实际 %r" % exc.code)
    # 归一化 + 渲染可回读（注释剥离后仍是合法 JSON）
    normalized = skills.normalize_skill(
        {
            "schema": 1,
            "id": "roundtrip",
            "title": "回读测试",
            "steps": [
                {"action": "mouse_click", "params": {"x": 1, "y": 2}},
                {"action": "mouse_circle", "params": {"centerX": 10, "centerY": 20, "radius": 5}},
            ],
        }
    )
    rendered = skills.render_skill_jsonc(normalized)
    expect("// 第 1 步：" in rendered, "render_skill_jsonc 缺少第 1 步中文注释")
    expect('"waitMs": 0' in rendered, "渲染文本缺少显式 waitMs")
    roundtrip = json.loads(skills.strip_jsonc(rendered))
    expect(roundtrip["steps"][0]["params"]["clicks"] == 1, "渲染回读后 mouse_click 默认参数缺失")
    expect(roundtrip["steps"][1]["params"]["periodMs"] == 2500, "渲染回读后 mouse_circle 默认参数缺失")

    # 2) 结构化保存 → 磁盘 JSONC 断言 + content/stepsView 一致
    home = ctx["tmp"] / "jsonc-home"
    env = {"GAME_CONTROL_HOME": str(home)}
    steps = [
        {"action": "mouse_click", "params": {"x": 10, "y": 20}},
        {
            "action": "mouse_circle",
            "params": {"centerX": 100, "centerY": 200, "radius": 50, "durationMs": 1000},
            "note": "自定义备注A",
        },
        {"action": "wait", "params": {"durationMs": 0}, "waitMs": 10},
        {"action": "find_image", "params": {"template": "keep-on-mining/start.png"}},
    ]
    saved = call(
        "skill_save",
        {
            "title": "JSONC 测试技能",
            "description": "说明",
            "game": "Keep on Mining!",
            "tags": ["a"],
            "markdown": "# 标题",
            "steps": steps,
        },
        env_extra=env,
    )
    expect(saved.get("ok") is True, "结构化 skill_save 失败: %r" % saved)
    saved_result = saved["result"]
    skill_id = saved_result["id"]
    expect(isinstance(saved_result.get("content"), str), "skill_save 应返回 content JSONC 文本")
    expect(len(saved_result.get("stepsView") or []) == 4, "skill_save stepsView 长度应为 4")
    disk_path = home / "skills" / skill_id / "skill.json"
    expect(disk_path.exists(), "技能文件未落盘: %s" % disk_path)
    disk_text = disk_path.read_text(encoding="utf-8")
    for token in (
        "// 第 1 步：",
        "// 第 2 步：",
        "// 第 3 步：",
        "// 第 4 步：",
        '"waitMs": 0',
        '"waitMs": 10',
        '"continueOnError": false',
        '"clicks": 1',
        '"button": "left"',
        '"intervalMs": 60',
        '"periodMs": 2500',
        '"direction": "cw"',
        '"startAngleDeg": -90',
        '"samplesPerSecond": 120',
        '"leadInMs": 300',
        '"abortKey": "esc"',
        '"threshold": 0.85',
        '"maxResults": 3',
        "备注：自定义备注A",
    ):
        expect(token in disk_text, "技能文件缺少内容: %s" % token)
    expect(disk_text.lstrip().startswith("// dsh-game-control"), "技能文件应以中文文件头注释开头")
    expect(disk_text == saved_result["content"], "skill_save 返回的 content 应与磁盘文件一致")
    got = call("skill_get", {"id": skill_id}, env_extra=env)
    expect(got.get("ok") is True, "skill_get 失败: %r" % got)
    got_result = got["result"]
    for key in ("skill", "content", "stepsView"):
        expect(key in got_result, "skill_get 缺少字段: %s" % key)
    expect(got_result["content"] == disk_text, "skill_get.content 应与磁盘原文一致")
    expect(len(got_result["stepsView"]) == 4, "skill_get stepsView 长度错误")
    expect(got_result["stepsView"][2]["waitMs"] == 10, "stepsView waitMs 不正确")
    expect(
        isinstance(got_result["stepsView"][0]["comment"], str)
        and "// 第 1 步：" not in got_result["stepsView"][0]["comment"]
        and got_result["stepsView"][0]["comment"] in disk_text,
        "stepsView.comment 应与生成注释同文",
    )
    expect(got_result["skill"]["steps"][0]["params"]["clicks"] == 1, "skill_get.skill 应为归一化对象")
    expect(got_result["skill"]["steps"][1]["note"] == "自定义备注A", "note 字段应保留")

    # 3) content 模式（JSONC + 注释 + note）保存，注释被重生成但数据/note 保留
    jsonc = _jsonc_example_skill()
    content_saved = call(
        "skill_save",
        {"content": jsonc, "id": "content-skill"},
        env_extra=env,
    )
    expect(content_saved.get("ok") is True, "content 模式 skill_save 失败: %r" % content_saved)
    content_result = content_saved["result"]
    expect(content_result.get("id") == "content-skill", "content 模式 + 参数 id 应使用该 id")
    expect("备注：content备注A" in content_result.get("content", ""), "note 应出现在重新生成的注释里")
    expect("// 第 1 步：" in content_result.get("content", ""), "content 保存后应重新生成步骤注释")
    expect("\n第二行" not in content_result.get("content", ""), "注释中的换行应被压成单行，避免破坏 JSONC")
    content_disk = home / "skills" / "content-skill" / "skill.json"
    expect(content_disk.exists() and content_disk.read_text(encoding="utf-8") == content_result["content"], "content 模式落盘不一致")
    parsed_content = json.loads(skills.strip_jsonc(content_result["content"]))
    expect(parsed_content["title"] == "Content 模式技能", "content 数据应保留")
    expect(parsed_content["steps"][0]["waitMs"] == 5, "content 中显式 waitMs 应保留")
    expect(parsed_content["steps"][0]["note"] == "content备注A\n第二行", "content 中 note 应原样保留（含换行）")
    expect(parsed_content["description"] == "content 模式保存\n第二行说明", "content 中 description 应原样保留（含换行）")

    # content.id 与参数 id 冲突 → INVALID_SKILL
    conflict = '{"id": "aaa", "title": "冲突", "steps": [{"action": "wait", "params": {"durationMs": 0}}]}'
    expect_error("skill_save", {"content": conflict, "id": "bbb"}, "INVALID_SKILL", env)

    # 4) 非法 content → INVALID_SKILL；保存前旧版本进 backups（保留原始 JSONC 文本）
    expect_error("skill_save", {"content": "{ bad json"}, "INVALID_SKILL", env)
    expect_error(
        "skill_save",
        {"content": '{"title":"x","steps":[{"action":"wait","params":{},"bogus":1}]}'},
        "INVALID_SKILL",
        env,
    )
    too_many_steps = json.dumps(
        {"title": "x", "steps": [{"action": "wait", "params": {"durationMs": 0}}] * 201},
        ensure_ascii=False,
    )
    expect_error("skill_save", {"content": too_many_steps}, "INVALID_SKILL", env)
    # 无 title/steps 且无 content → INVALID_SKILL
    expect_error("skill_save", {}, "INVALID_SKILL", env)
    before_versions = call("skill_versions", {"id": skill_id}, env_extra=env)["result"]["count"]
    updated = call(
        "skill_save",
        {"id": skill_id, "title": "JSONC 测试技能 v2", "steps": [{"action": "wait", "params": {"durationMs": 0}}]},
        env_extra=env,
    )
    expect(updated.get("ok") is True, "更新技能失败: %r" % updated)
    after_versions = call("skill_versions", {"id": skill_id}, env_extra=env)["result"]
    expect(after_versions["count"] >= before_versions + 1, "保存前应新增备份")
    latest_backup = home / "skills" / skill_id / "backups" / after_versions["versions"][0]["name"]
    expect(latest_backup.exists(), "最新备份文件不存在: %s" % latest_backup)
    backup_text = latest_backup.read_text(encoding="utf-8")
    expect("// 第 1 步：" in backup_text, "备份应保留原始 JSONC 注释文本")
    expect("JSONC 测试技能" in backup_text, "备份应是旧版本内容")

    # 5) JSONC 技能 skill_run（仅 wait durationMs:0，真实执行且零输入）
    wait_only = (
        "// 仅等待技能\n{\n  \"title\": \"JSONC 等待技能\",\n  \"steps\": [\n"
        "    // 等待 0ms\n    {\"action\": \"wait\", \"params\": {\"durationMs\": 0}, \"note\": \"不执行 note\"}\n"
        "  ]\n}\n"
    )
    wait_saved = call("skill_save", {"content": wait_only}, env_extra=env)
    expect(wait_saved.get("ok") is True, "保存 JSONC 等待技能失败: %r" % wait_saved)
    wait_id = wait_saved["result"]["id"]
    run = call("skill_run", {"id": wait_id}, env_extra=env)
    expect(run.get("ok") is True, "JSONC 技能 skill_run 失败: %r" % run)
    run_result = run["result"]
    expect(run_result["success"] is True, "JSONC 技能应执行成功: %r" % run_result)
    expect(run_result["steps"][0]["ok"] is True and run_result["steps"][0]["action"] == "wait", "JSONC 技能步骤执行不正确")
    record(
        "15. 技能 JSONC/参数显式化",
        True,
        "strip_jsonc 边界、保存渲染（// 第 N 步 + 显式 waitMs + 默认参数）、content 模式 note 保留、非法 content、备份原文、JSONC skill_run 均通过",
    )


# ---------------------------------------------------------------------------
# v0.5：Esc 中止 / 多模板 / 进度 / 超时前置校验（全部零真实输入）
# ---------------------------------------------------------------------------


class _SkillRuntimeStub:
    """进程内技能运行用的最小 runtime（input_impl 可替换，技能库懒加载）。"""

    def __init__(self, config, input_impl, logger_name: str = "dgc-v05-test"):
        self.config = config
        self.input_impl = input_impl
        self.logger = logging.getLogger(logger_name)
        self._store = None

    def get_skills_store(self):
        if self._store is None:
            import skills

            game_control = _load_backend()
            self._store = skills.SkillsStore(self.config.skills_dir, game_control.SKILL_STEP_ACTIONS)
        return self._store


def _v05_config(root: Path, templates: Path | None = None, shots: Path | None = None):
    root = Path(root)
    templates = Path(templates) if templates else root / "templates"
    shots = Path(shots) if shots else root / "shots"
    return types.SimpleNamespace(
        templates_dir=templates,
        shot_dir=shots,
        skills_dir=root / "skills",
        log_dir=root / "logs",
        home=root,
    )


def _write_template(path: Path, array) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(str(path))
    return path


def _second_block_image():
    """第二张合成图：不同图案的 60×20 块（用于多模板区分）。"""
    import numpy as np

    rng = np.random.RandomState(23)
    image = rng.randint(30, 80, size=(300, 400, 3), dtype=np.uint8)
    block = np.zeros((20, 60, 3), dtype=np.uint8)
    for x in range(60):
        for y in range(20):
            block[y, x, :] = [255, 40, 40] if (x + y) % 10 < 5 else [40, 40, 255]
    image[80:100, 120:180] = block
    return image, block


def test_v05_abort_defaults(ctx: dict) -> None:
    """SPEC 10.5.1/2/7：默认 Esc、键名兼容、find/wait_image 的 abortKey 中止。"""
    if os.name != "nt":
        record("16. v0.5 Esc 中止/默认值", True, "非 Windows 跳过（中止键依赖 GetAsyncKeyState）")
        return
    import win_native

    import skills

    game_control = _load_backend()
    # 1) 默认值与键名兼容
    for action in ("mouse_circle", "skill_run", "find_image", "wait_image"):
        param = game_control.ACTION_PARAMS[action]["abortKey"]
        expect(param["default"] == "esc", "%s.abortKey 默认值应为 esc，实际 %r" % (action, param["default"]))
    for action in ("mouse_circle", "find_image", "wait_image"):
        expect(
            skills.NORMALIZE_DEFAULTS[action]["abortKey"] == "esc",
            "归一化表 %s.abortKey 应为 esc" % action,
        )
    expect(game_control.normalize_key("escape") == "esc", "escape 应规范化为 esc")
    expect(game_control.normalize_key("ESC") == "esc", "ESC 应规范化为 esc")
    dry = call("mouse_circle", {"centerX": 1920, "centerY": 1080, "radius": 100, "dryRun": True}, env_extra=ctx["env"])
    expect(dry.get("ok") is True and dry["result"]["aborted"] is False, "默认 abortKey=esc 的 dryRun 应正常")
    expect_error("skill_run", {"id": "no-such-skill", "dryRun": True, "abortKey": "escape"}, "SKILL_NOT_FOUND", ctx["env"])

    # 2) find_image：monkeypatch key_is_down → aborted（不截图、不注入）
    templates_dir = Path(ctx["home"]) / "templates"
    config = _v05_config(Path(ctx["home"]), templates=templates_dir)
    runtime = _SkillRuntimeStub(config, win_native)
    original_key_down = win_native.key_is_down
    try:
        win_native.key_is_down = lambda name: True  # type: ignore[assignment]
        find_params = game_control.validate_params(
            "find_image",
            {"template": "not-loaded.png", "region": {"x": 0, "y": 0, "width": 100, "height": 100}},
        )
        aborted_find = game_control.action_find_image(find_params, runtime)
        expect(aborted_find["found"] is False, "Esc 按下时 find_image 不应命中")
        expect(aborted_find["aborted"] is True and aborted_find["abortReason"] == "abort-key", "find_image 中止字段不正确")
        wait_params = game_control.validate_params(
            "wait_image",
            {"template": "not-loaded.png", "timeoutMs": 5000, "intervalMs": 100},
        )
        started = time.perf_counter()
        aborted_wait = game_control.action_wait_image(wait_params, runtime)
        elapsed = (time.perf_counter() - started) * 1000.0
        expect(aborted_wait["aborted"] is True and aborted_wait["abortReason"] == "abort-key", "wait_image 中止字段不正确")
        expect(aborted_wait["timedOut"] is False and aborted_wait["found"] is False, "中止不应是 timedOut/found")
        expect(elapsed < 2000, "Esc 中止应立即返回（实际 %.0fms，timeout=5000）" % elapsed)
    finally:
        win_native.key_is_down = original_key_down  # type: ignore[assignment]

    # 3) "none" 禁用中止：key 一直“按下”也应正常匹配
    image, block = _synthetic_block_image()
    _write_template(templates_dir / "none.png", block)
    import screenshot

    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (image.tobytes(), int(w), int(h))  # type: ignore[assignment]
    try:
        win_native.key_is_down = lambda name: True  # type: ignore[assignment]
        disabled_params = game_control.validate_params(
            "find_image",
            {
                "template": "none.png",
                "region": {"x": 0, "y": 0, "width": 400, "height": 300},
                "abortKey": "none",
            },
        )
        disabled = game_control.action_find_image(disabled_params, runtime)
        expect(disabled["found"] is True and not disabled.get("aborted"), "abortKey=none 时应正常匹配: %r" % disabled)
    finally:
        win_native.key_is_down = original_key_down  # type: ignore[assignment]
        screenshot.capture = original_capture  # type: ignore[assignment]
    # 非法键名 → INVALID_KEY
    expect_error(
        "mouse_circle",
        {"centerX": 1920, "centerY": 1080, "radius": 100, "abortKey": "not-a-key", "dryRun": True},
        "INVALID_KEY",
        ctx["env"],
    )
    record(
        "16. v0.5 Esc 中止/默认值",
        True,
        "mouse_circle/skill_run/find_image/wait_image 默认 esc；escape/ESC 兼容；find/wait_image 按下立即 aborted（<2s）；none 禁用；非法键名 INVALID_KEY",
    )


def test_v05_multi_templates(ctx: dict) -> None:
    """SPEC 10.5.3：find_image/wait_image 的 templates 多模板（合成图）。"""
    if os.name != "nt":
        record("17. v0.5 多模板 templates", True, "非 Windows 跳过")
        return
    import win_native

    game_control = _load_backend()
    templates_dir = Path(ctx["home"]) / "templates" / "multi"
    image_a, block_a = _synthetic_block_image()
    image_b, block_b = _second_block_image()
    _write_template(templates_dir / "a.png", block_a)
    _write_template(templates_dir / "b.png", block_b)
    config = _v05_config(Path(ctx["home"]) / "multi-home", templates=Path(ctx["home"]) / "templates")
    runtime = _SkillRuntimeStub(config, win_native)
    import screenshot

    original_capture = screenshot.capture
    region = {"x": 0, "y": 0, "width": 400, "height": 300}
    try:
        # 图里只有 B → 应命中 b.png
        screenshot.capture = lambda x, y, w, h: (image_b.tobytes(), int(w), int(h))  # type: ignore[assignment]
        params = game_control.validate_params(
            "find_image",
            {"templates": ["multi/a.png", "multi/b.png"], "region": region, "threshold": 0.85},
        )
        result = game_control.action_find_image(params, runtime)
        expect(result["found"] is True, "多模板 find_image 应命中 B: %r" % result)
        expect(result["template"]["path"].endswith("b.png"), "命中模板应为 b.png，实际 %r" % result["template"])
        expect(len(result.get("candidateTemplates") or []) == 2, "应返回 candidateTemplates 两个")
        expect(result["matches"][0].get("template", "").endswith("b.png"), "match 应带命中模板路径")
        # wait_image 多模板：图里只有 A → match 带 a.png
        screenshot.capture = lambda x, y, w, h: (image_a.tobytes(), int(w), int(h))  # type: ignore[assignment]
        wait_params = game_control.validate_params(
            "wait_image",
            {"templates": ["multi/a.png", "multi/b.png"], "region": region, "timeoutMs": 800, "intervalMs": 100},
        )
        wait_result = game_control.action_wait_image(wait_params, runtime)
        expect(wait_result["found"] is True and wait_result["timedOut"] is False, "多模板 wait_image 应命中: %r" % wait_result)
        expect(wait_result["match"]["template"].endswith("a.png"), "wait_image match 应带 a.png 路径")
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]

    # 参数校验（错误路径不截图）
    env = ctx["env"]
    expect_error("find_image", {}, "INVALID_ARGUMENT", env)
    expect_error(
        "find_image",
        {"template": "a.png", "templates": ["b.png"]},
        "INVALID_ARGUMENT",
        env,
    )
    expect_error("find_image", {"templates": []}, "INVALID_ARGUMENT", env)
    expect_error("find_image", {"templates": ["a.png"] * 9}, "INVALID_ARGUMENT", env)
    expect_error("wait_image", {"templates": ["a.png"] * 9}, "INVALID_ARGUMENT", env)
    record(
        "17. v0.5 多模板 templates",
        True,
        "templates 任一命中返回对应 template 路径（find/wait 均带 match.template）；candidateTemplates 2 个；template/templates 冲突与 0/9 个 → INVALID_ARGUMENT",
    )


def test_v05_skill_run_progress_precheck(ctx: dict) -> None:
    """SPEC 10.5.4/5/6：skill_run 透传 abortKey、进度文件、超时前置校验。"""
    import win_native

    import skills

    game_control = _load_backend()
    if os.name != "nt":
        record("18. v0.5 skill_run 进度/预校验", True, "非 Windows 跳过（中止键依赖 GetAsyncKeyState）")
        return
    root = Path(ctx["tmp"]) / "v05-skill-home"
    config = _v05_config(root)
    runtime = _SkillRuntimeStub(config, win_native)
    store = runtime.get_skills_store()

    # 1) 进度文件：运行 wait(0) 技能，处理每一步时读取磁盘进度
    skill = store.save(
        {
            "title": "v05 进度技能",
            "steps": [
                {"action": "wait", "params": {"durationMs": 0}},
                {"action": "wait", "params": {"durationMs": 0}},
            ],
        }
    )
    progress_path = config.log_dir / "skill-progress.json"
    observed = []
    original_wait = game_control.ACTION_HANDLERS["wait"]

    def _spy_wait(params, rt):  # noqa: ANN001
        if progress_path.exists():
            observed.append(json.loads(progress_path.read_text(encoding="utf-8")))
        return original_wait(params, rt)

    game_control.ACTION_HANDLERS["wait"] = _spy_wait
    try:
        run_params = game_control.validate_params("skill_run", {"id": skill["id"]})
        run_result = game_control.action_skill_run(run_params, runtime)
    finally:
        game_control.ACTION_HANDLERS["wait"] = original_wait
    expect(run_result["success"] is True, "wait(0) 技能应执行成功: %r" % run_result)
    expect(len(observed) >= 2, "运行过程中应能读到进度文件（实际 %d 次）" % len(observed))
    expect(observed[0]["running"] is True and observed[0]["step"]["index"] == 0, "第 1 步开始进度不正确: %r" % observed[0])
    expect(observed[1]["step"]["index"] == 1, "第 2 步开始进度不正确: %r" % observed[1])
    for item in observed:
        for key in ("running", "skillId", "startedAt", "elapsedMs", "repeat", "repeatsDone", "step", "updatedAt"):
            expect(key in item, "进度文件缺少字段: %s" % key)
    final_progress = json.loads(progress_path.read_text(encoding="utf-8"))
    expect(final_progress["running"] is False, "结束后 running 应为 false: %r" % final_progress)
    expect(final_progress["success"] is True and final_progress["finishedAt"], "结束进度缺少 success/finishedAt")
    expect(final_progress["skillId"] == skill["id"] and final_progress["repeatsDone"] == 1, "结束进度字段不正确")
    progress_read = game_control.action_skill_progress({}, runtime)
    expect(isinstance(progress_read.get("progress"), dict), "skill_progress 动作应返回进度对象")
    # CLI 侧 skill_progress 动作也能读到同一进度文件
    progress_cli = call("skill_progress", env_extra={"GAME_CONTROL_HOME": str(root)})
    expect(progress_cli.get("ok") is True and isinstance(progress_cli["result"].get("progress"), dict), "CLI skill_progress 应返回进度")

    # 2) skill_run 透传 abortKey：wait_image 步在 Esc 按下时中止（不等待 timeout）
    abort_skill = store.save(
        {
            "title": "v05 中止技能",
            "steps": [{"action": "wait_image", "params": {"template": "x.png", "timeoutMs": 5000}}],
        }
    )
    original_key_down = win_native.key_is_down
    calls = {"count": 0}

    def _key_down_sequence(name):  # noqa: ANN001
        calls["count"] += 1
        return calls["count"] >= 2  # 第 1 次（步骤间检查）未按，之后按下

    started = time.perf_counter()
    try:
        win_native.key_is_down = _key_down_sequence  # type: ignore[assignment]
        abort_params = game_control.validate_params("skill_run", {"id": abort_skill["id"]})
        abort_result = game_control.action_skill_run(abort_params, runtime)
    finally:
        win_native.key_is_down = original_key_down  # type: ignore[assignment]
    elapsed = (time.perf_counter() - started) * 1000.0
    expect(abort_result["aborted"] is True and abort_result["abortReason"] == "abort-key", "skill_run 应透传 Esc 中止: %r" % abort_result)
    expect(abort_result["success"] is False, "中止时 success 应为 false")
    expect(elapsed < 2000, "skill_run 中止应立即返回（实际 %.0fms，wait_image timeout=5000）" % elapsed)

    # 3) 超时前置校验（v0.6 起 dryRun 严格拒绝；真实运行交给总 deadline 优雅中止，见第 19 类）
    slow_skill = store.save(
        {"title": "v05 慢技能", "steps": [{"action": "wait", "params": {"durationMs": 600000}}]}
    )
    slow_response = call(
        "skill_run", {"id": slow_skill["id"], "dryRun": True}, env_extra={"GAME_CONTROL_HOME": str(root)}
    )
    expect(slow_response.get("ok") is False, "dryRun 超出 timeoutMs 的技能应返回错误信封")
    error = slow_response.get("error") or {}
    expect(error.get("code") == "INVALID_ARGUMENT", "超时前置校验错误码应为 INVALID_ARGUMENT，实际 %r" % error.get("code"))
    expect("至少需要" in str(error.get("hint", "")), "超时前置校验 hint 应包含“至少需要”: %r" % error.get("hint"))

    dry_skill = store.save({"title": "v05 估算技能", "steps": [{"action": "wait", "params": {"durationMs": 0}}]})
    dry = call("skill_run", {"id": dry_skill["id"], "dryRun": True}, env_extra={"GAME_CONTROL_HOME": str(root)})
    expect(dry.get("ok") is True, "合法技能 dryRun 应成功: %r" % dry)
    expect(isinstance(dry["result"].get("estimatedDurationMs"), int), "dryRun 应返回 estimatedDurationMs")
    expect(dry["result"]["estimatedDurationMs"] >= 500, "wait(0) 技能估算应 ≥500ms")
    # 估算公式：mouse_circle(leadIn+duration) + wait_image(timeout) + wait + 500/步
    mixed = store.save(
        {
            "title": "v05 混合估算",
            "steps": [
                {"action": "mouse_circle", "params": {"centerX": 960, "centerY": 540, "radius": 100, "durationMs": 1000, "leadInMs": 200}},
                {"action": "wait_image", "params": {"template": "x.png", "timeoutMs": 5000}},
                {"action": "wait", "params": {"durationMs": 100}},
            ],
        }
    )
    mixed_dry = call("skill_run", {"id": mixed["id"], "dryRun": True}, env_extra={"GAME_CONTROL_HOME": str(root)})
    expected_estimate = (200 + 1000) + 5000 + 100 + 500 * 3
    expect(
        mixed_dry["result"]["estimatedDurationMs"] == expected_estimate,
        "估算公式错误：期望 %d，实际 %r" % (expected_estimate, mixed_dry["result"].get("estimatedDurationMs")),
    )
    # 非法步骤参数（变量外）→ 前置校验 INVALID_ARGUMENT
    bad_skill = store.save(
        {
            "title": "v05 非法参数",
            "steps": [{"action": "mouse_circle", "params": {"centerX": 960, "centerY": 540, "radius": "abc"}}],
        }
    )
    bad = call("skill_run", {"id": bad_skill["id"], "dryRun": True}, env_extra={"GAME_CONTROL_HOME": str(root)})
    expect(bad.get("ok") is False and (bad.get("error") or {}).get("code") == "INVALID_ARGUMENT", "非法步骤参数应 INVALID_ARGUMENT: %r" % bad)
    record(
        "18. v0.5 skill_run 进度/预校验",
        True,
        "进度文件运行中 running/step.index、结束 running:false/success/finishedAt 可读；Esc 透传 wait_image 立即中止；超时估算公式/超限 INVALID_ARGUMENT（含“至少需要”hint）",
    )


# ---------------------------------------------------------------------------
# v0.6：skill_run 总 deadline 优雅中止（全部零真实输入）
# ---------------------------------------------------------------------------


class _DeadlineInputStub:
    """v0.6 deadline 测试用输入存根：允许 move_to/按键调用并记录，不碰真实输入。"""

    def __init__(self) -> None:
        self.moves: list = []
        self.buttons: list = []

    def get_virtual_screen(self) -> dict:
        return {"x": 0, "y": 0, "width": 3840, "height": 2160}

    def get_primary_screen(self) -> dict:
        return {"x": 0, "y": 0, "width": 3840, "height": 2160}

    def get_pointer(self) -> dict:
        return {"x": 0, "y": 0}

    def move_to(self, x, y) -> None:  # noqa: ANN001
        self.moves.append((x, y))

    def button_down(self, button) -> None:  # noqa: ANN001
        self.buttons.append(("down", button))

    def button_up(self, button) -> None:  # noqa: ANN001
        self.buttons.append(("up", button))

    def key_is_down(self, name) -> bool:  # noqa: ANN001
        return False

    def get_foreground_hwnd(self) -> int:
        return 0


def test_v06_skill_deadline(ctx: dict) -> None:
    """SPEC 11.7.1–4：总 deadline 优雅中止、进度、动作内部 deadline、无 deadline 回归。"""
    if os.name != "nt":
        record("19. v0.6 总 deadline 优雅中止", True, "非 Windows 跳过（动作依赖 Windows 后端）")
        return
    import win_native

    game_control = _load_backend()

    # 1) skill_run：timeoutMs=1000 + wait(5000) → aborted:true / abortReason:timeout，耗时 <3s
    root = Path(ctx["tmp"]) / "v06-deadline-home"
    env = {"GAME_CONTROL_HOME": str(root)}
    saved = call("skill_save", {"title": "v06 超时技能", "steps": [{"action": "wait", "params": {"durationMs": 5000}}]}, env_extra=env)
    expect(saved.get("ok") is True, "skill_save 失败: %r" % saved)
    skill_id = saved["result"]["id"]
    started = time.perf_counter()
    run = call("skill_run", {"id": skill_id, "timeoutMs": 1000}, env_extra=env)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    expect(run.get("ok") is True, "超时技能应返回 ok:true 信封（优雅中止）: %r" % run)
    result = run["result"]
    expect(result.get("aborted") is True, "总 deadline 到点应 aborted:true: %r" % result)
    expect(result.get("abortReason") == "timeout", "abortReason 应为 timeout: %r" % result.get("abortReason"))
    expect(result.get("success") is False, "超时中止 success 应为 false")
    expect(elapsed_ms < 3000, "总 deadline 应在 <3s 内返回（实际 %.0fms）" % elapsed_ms)
    expect(isinstance(result.get("estimatedDurationMs"), int), "结果应包含 estimatedDurationMs")

    # 2) 进度文件结束为 running:false / abortReason:timeout / finishedAt
    progress_path = root / "logs" / "skill-progress.json"
    expect(progress_path.exists(), "超时后应写 skill-progress.json")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    expect(progress.get("running") is False, "进度文件结束 running 应为 false: %r" % progress)
    expect(progress.get("aborted") is True, "进度文件结束 aborted 应为 true")
    expect(progress.get("abortReason") == "timeout", "进度文件应记录 abortReason=timeout: %r" % progress.get("abortReason"))
    expect(progress.get("finishedAt"), "进度文件结束应有 finishedAt")

    # 3) 动作内部 deadline：wait 截断、find_image/wait_image 提前中止
    config = _v05_config(Path(ctx["tmp"]) / "v06-inproc-home")
    input_stub = _DeadlineInputStub()
    runtime = _SkillRuntimeStub(config, input_stub)
    # wait：5s 被总 deadline 截断到 ~150ms
    runtime.skill_deadline = time.monotonic() + 0.15
    started = time.perf_counter()
    waited = game_control.action_wait(game_control.validate_params("wait", {"durationMs": 5000}), runtime)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    expect(waited.get("aborted") is True and waited.get("abortReason") == "timeout", "wait 应被总 deadline 截断: %r" % waited)
    expect(elapsed_ms < 1000, "wait 截断后应 <1s 返回（实际 %.0fms）" % elapsed_ms)
    expect(int(waited.get("waitedMs", 0)) <= 300, "wait 实际等待应接近剩余时间: %r" % waited)
    # find_image：deadline 已过 → 立即 aborted timeout（不截图）
    runtime.skill_deadline = time.monotonic() - 1.0
    find_params = game_control.validate_params(
        "find_image", {"template": "x.png", "region": {"x": 0, "y": 0, "width": 100, "height": 100}}
    )
    find_result = game_control.action_find_image(find_params, runtime)
    expect(find_result.get("aborted") is True and find_result.get("abortReason") == "timeout", "find_image 应检测总 deadline: %r" % find_result)
    # mouse_circle：有效时长截断为剩余时间，每帧检查，释放鼠标键
    runtime.skill_deadline = time.monotonic() + 0.3
    circle_params = game_control.validate_params(
        "mouse_circle",
        {"centerX": 1920, "centerY": 1080, "radius": 100, "durationMs": 30000, "button": "left"},
    )
    started = time.perf_counter()
    circle_result = game_control.action_mouse_circle(circle_params, runtime)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    expect(circle_result.get("aborted") is True and circle_result.get("abortReason") == "timeout", "mouse_circle 应被总 deadline 提前停止: %r" % circle_result)
    expect(elapsed_ms < 2000, "mouse_circle 应提前停止（实际 %.0fms）" % elapsed_ms)
    expect(0 < int(circle_result.get("effectiveDurationMs", 0)) <= 1000, "effectiveDurationMs 应被截断: %r" % circle_result.get("effectiveDurationMs"))
    expect(input_stub.buttons and input_stub.buttons[0] == ("down", "left"), "mouse_circle 应按下鼠标键: %r" % input_stub.buttons)
    expect(input_stub.buttons[-1] == ("up", "left"), "中止后必须释放鼠标键: %r" % input_stub.buttons)

    # wait_image：大 timeout + 小总 deadline → 注入截图（永远不命中）→ aborted timeout
    import screenshot

    templates_dir = config.templates_dir
    image, block = _synthetic_block_image()
    _write_template(templates_dir / "probe.png", block)
    # 用不匹配的空白图（同尺寸）让 find_image 一直未命中
    blank = b"\x00" * (200 * 150 * 3)
    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (blank if (int(w), int(h)) == (200, 150) else (_synthetic_block_image()[0].tobytes()), int(w), int(h))  # type: ignore[assignment]
    try:
        runtime.skill_deadline = time.monotonic() + 0.4
        wait_params = game_control.validate_params(
            "wait_image",
            {
                "template": "probe.png",
                "region": {"x": 0, "y": 0, "width": 200, "height": 150},
                "timeoutMs": 5000,
                "intervalMs": 100,
            },
        )
        started = time.perf_counter()
        wait_result = game_control.action_wait_image(wait_params, runtime)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        expect(wait_result.get("aborted") is True and wait_result.get("abortReason") == "timeout", "wait_image 应被总 deadline 中止: %r" % wait_result)
        expect(wait_result.get("timedOut") is False, "总 deadline 中止不应是 timedOut")
        expect(elapsed_ms < 2000, "wait_image 总 deadline 应在 <2s 返回（实际 %.0fms）" % elapsed_ms)
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]
        runtime.skill_deadline = None

    # 4) 无 deadline 时行为不变（回归）；skill_run 运行中 deadline 已设置、结束后清除
    runtime.skill_deadline = None
    normal_wait = game_control.action_wait(game_control.validate_params("wait", {"durationMs": 30}), runtime)
    expect(normal_wait == {"waitedMs": 30}, "无 deadline 时 wait 行为应不变: %r" % normal_wait)
    normal_find_params = game_control.validate_params(
        "find_image",
        {"template": "probe.png", "region": {"x": 0, "y": 0, "width": 400, "height": 300}},
    )
    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (image.tobytes(), int(w), int(h))  # type: ignore[assignment]
    try:
        normal_find = game_control.action_find_image(normal_find_params, runtime)
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]
    expect(normal_find.get("found") is True and not normal_find.get("aborted"), "无 deadline 时 find_image 应正常: %r" % normal_find)

    store = runtime.get_skills_store()
    deadline_skill = store.save({"title": "v06 deadline 设置检查", "steps": [{"action": "wait", "params": {"durationMs": 0}}]})
    observed = []
    original_wait = game_control.ACTION_HANDLERS["wait"]

    def _spy_wait(params, rt):  # noqa: ANN001
        observed.append(getattr(rt, "skill_deadline", None))
        return original_wait(params, rt)

    game_control.ACTION_HANDLERS["wait"] = _spy_wait
    try:
        run_params = game_control.validate_params("skill_run", {"id": deadline_skill["id"]})
        run_ok = game_control.action_skill_run(run_params, runtime)
    finally:
        game_control.ACTION_HANDLERS["wait"] = original_wait
    expect(run_ok.get("success") is True and not run_ok.get("aborted"), "无超时的技能应正常成功: %r" % run_ok)
    expect(observed and observed[0] is not None, "skill_run 执行期间应设置 runtime.skill_deadline")
    expect(runtime.skill_deadline is None, "skill_run 结束后应清除 runtime.skill_deadline")
    record(
        "19. v0.6 总 deadline 优雅中止",
        True,
        "skill_run timeoutMs=1000+wait(5000) → aborted:timeout <3s；进度 running:false/abortReason:timeout/finishedAt；"
        "wait 截断 / find_image 立即中止 / mouse_circle 提前停止并释放按键 / wait_image 大 timeout 提前 aborted；无 deadline 回归正常",
    )


# ---------------------------------------------------------------------------
# v0.6.2：模板未找到 onMissing / 失败截图 / 变量 None 提示（零真实输入）
# ---------------------------------------------------------------------------


def test_v062_on_missing(ctx: dict) -> None:
    """SPEC 13.3.1–4：wait_image 超时默认 fail、onMissing=continue、非法值、归一化注释。"""
    if os.name != "nt":
        record("20. v0.6.2 onMissing/失败截图", True, "非 Windows 跳过（模板动作依赖 Windows 后端）")
        return
    import skills

    game_control = _load_backend()
    root = Path(ctx["tmp"]) / "v062-home"
    config = _v05_config(root)
    runtime = _SkillRuntimeStub(config, _DeadlineInputStub())
    store = runtime.get_skills_store()
    templates_dir = config.templates_dir
    _image, block = _synthetic_block_image()
    _write_template(templates_dir / "probe.png", block)

    import screenshot

    blank = b"\x00" * (200 * 150 * 3)
    original_capture = screenshot.capture
    screenshot.capture = lambda x, y, w, h: (blank if (int(w), int(h)) == (200, 150) else b"\x00" * (int(w) * int(h) * 3), int(w), int(h))  # type: ignore[assignment]
    region = {"x": 0, "y": 0, "width": 200, "height": 150}
    try:
        # 1) 默认 onMissing=fail：失败停在第 1 步 WAIT_IMAGE_TIMEOUT，不出现变量错误 + 失败截图
        fail_skill = store.save(
            {
                "title": "v062 默认 fail",
                "steps": [
                    {
                        "action": "wait_image",
                        "params": {"template": "probe.png", "region": region, "timeoutMs": 300, "intervalMs": 50},
                        "saveAs": "btn",
                    },
                    {"action": "mouse_click", "params": {"x": "$btn.match.center.x", "y": "$btn.match.center.y"}},
                ],
            }
        )
        fail_run = game_control.action_skill_run(
            game_control.validate_params("skill_run", {"id": fail_skill["id"]}), runtime
        )
        expect(fail_run["success"] is False, "模板未找到默认应失败: %r" % fail_run)
        failed = fail_run.get("failedStep") or {}
        expect(failed.get("index") == 0, "应失败在第 1 步（index=0），实际 %r" % failed)
        expect(failed.get("code") == "WAIT_IMAGE_TIMEOUT", "failedStep.code 应为 WAIT_IMAGE_TIMEOUT，实际 %r" % failed.get("code"))
        message = str(failed.get("message") or "")
        expect("等待模板超时" in message and "probe.png" in message, "message 应含超时/模板信息: %r" % message)
        expect("提高 timeoutMs" in message or "补充模板" in message, "message 应含建议: %r" % message)
        screenshot_path = str(failed.get("screenshotPath") or "")
        expect(screenshot_path.endswith(".png"), "failedStep.screenshotPath 应为 png：%r" % screenshot_path)
        expect(Path(screenshot_path).exists(), "失败截图文件应存在：%s" % screenshot_path)
        from PIL import Image

        with Image.open(screenshot_path) as shot_image:
            expect(
                shot_image.size == (200, 150),
                "失败截图应优先使用失败步骤 searchRegion（200x150），实际 %r" % (shot_image.size,),
            )
        expect(len(fail_run["steps"]) == 1, "默认 fail 不应执行后续 mouse_click 步骤: %r" % fail_run["steps"])
        dumped = json.dumps(fail_run, ensure_ascii=False)
        expect("SKILL_VAR_UNRESOLVED" not in dumped, "默认 fail 不应出现变量解析错误: %s" % dumped[:400])

        # 2) onMissing=continue：继续到 mouse_click，变量引用报 SKILL_VAR_UNRESOLVED 且 hint 提示模板未找到
        continue_skill = store.save(
            {
                "title": "v062 continue",
                "steps": [
                    {
                        "action": "wait_image",
                        "params": {"template": "probe.png", "region": region, "timeoutMs": 300, "intervalMs": 50},
                        "saveAs": "btn",
                        "onMissing": "continue",
                    },
                    {"action": "mouse_click", "params": {"x": "$btn.match.center.x", "y": "$btn.match.center.y"}},
                ],
            }
        )
        continue_run = game_control.action_skill_run(
            game_control.validate_params("skill_run", {"id": continue_skill["id"]}), runtime
        )
        expect(continue_run["success"] is False, "后续变量解析失败应导致技能失败")
        continue_failed = continue_run.get("failedStep") or {}
        expect(continue_failed.get("index") == 1, "应失败在第 2 步（index=1），实际 %r" % continue_failed)
        expect(
            continue_failed.get("code") == "SKILL_VAR_UNRESOLVED",
            "onMissing=continue 后变量错误码应为 SKILL_VAR_UNRESOLVED，实际 %r" % continue_failed.get("code"),
        )
        combined = "%s %s" % (continue_failed.get("message") or "", continue_failed.get("hint") or "")
        expect("模板" in combined or "None" in combined, "变量错误提示应说明模板未找到/None: %r" % combined)
        expect(len(continue_run["steps"]) == 2, "onMissing=continue 应执行到第 2 步: %r" % continue_run["steps"])
        expect(continue_run["steps"][0].get("ok") is True, "onMissing=continue 时第 1 步应视为已执行继续")
    finally:
        screenshot.capture = original_capture  # type: ignore[assignment]

    # 3) onMissing 非法值 / 用在不支持的步骤 → INVALID_SKILL
    env = {"GAME_CONTROL_HOME": str(root)}
    expect_error(
        "skill_save",
        {
            "title": "v062 bad onMissing",
            "steps": [
                {"action": "wait_image", "params": {"template": "probe.png"}, "onMissing": "ignore"},
            ],
        },
        "INVALID_SKILL",
        env,
    )
    expect_error(
        "skill_save",
        {
            "title": "v062 onMissing unsupported",
            "steps": [{"action": "mouse_click", "params": {"x": 1, "y": 2}, "onMissing": "fail"}],
        },
        "INVALID_SKILL",
        env,
    )

    # 4) 归一化后 find/wait_image 显式 onMissing=fail，注释含“找不到时”
    normalized = store.save(
        {
            "title": "v062 normalize",
            "steps": [
                {"action": "find_image", "params": {"template": "probe.png"}},
                {"action": "wait_image", "params": {"template": "probe.png", "timeoutMs": 500}},
            ],
        }
    )
    got = store.get(normalized["id"])
    parsed_doc = json.loads(skills.strip_jsonc(got["content"]))
    for step in parsed_doc["steps"]:
        expect(step.get("onMissing") == "fail", "归一化后 %s 步骤应显式 onMissing=fail: %r" % (step.get("action"), step))
    comments = " ".join(item.get("comment", "") for item in got["stepsView"])
    expect("找不到时：失败" in comments, "stepsView 注释应含“找不到时：失败”: %r" % comments)
    expect("找不到时：失败" in got["content"], "JSONC 注释应含“找不到时：失败”")
    record(
        "20. v0.6.2 onMissing/失败截图",
        True,
        "wait_image 超时默认 fail→WAIT_IMAGE_TIMEOUT+失败截图+不执行后续；continue→SKILL_VAR_UNRESOLVED（hint 提示模板/None）；非法值 INVALID_SKILL；归一化显式 onMissing=fail 且注释含“找不到时”",
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


TESTS = [
    test_envelope_and_validation,
    test_status,
    test_screenshots,
    test_base64,
    test_source_scan,
    test_subprocess_modes,
    test_env_and_audit,
    test_window_capture,
    test_circle_motion,
    test_mouse_circle_dry_run,
    test_find_image_synthetic,
    test_wait_image_synthetic,
    test_activate_window_validation,
    test_skills,
    test_skill_jsonc,
    test_v05_abort_defaults,
    test_v05_multi_templates,
    test_v05_skill_run_progress_precheck,
    test_v06_skill_deadline,
    test_v062_on_missing,
]


def _make_unique_dir(base: Path) -> Path:
    """在 base 下创建唯一子目录（默认 Windows ACL）。

    注意：不能使用 ``tempfile.mkdtemp`` —— 它按 POSIX 习惯传 mode=0o700，
    在 Windows 上会创建出当前沙箱进程无法写入的目录（WinError 5）。
    """
    import uuid

    for _attempt in range(20):
        candidate = base / ("py-tests-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise CheckFailure("无法在 %s 下创建唯一测试目录" % base)


def make_temp_root() -> Path:
    """创建测试临时根目录。

    优先用工作区 ``test/.tmp``（DSH 文件沙箱下系统临时目录可能不可写），
    其次系统临时目录，最后脚本目录；测试结束会整目录删除。
    """
    workspace_root = SCRIPT_DIR.parent.parent
    candidates = [
        workspace_root / "test" / ".tmp",
        Path(tempfile.gettempdir()),
        SCRIPT_DIR,
    ]
    last_error: Exception | None = None
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            return _make_unique_dir(base)
        except OSError as exc:
            last_error = exc
            continue
    raise CheckFailure("无法创建测试临时目录: %s" % last_error)


def main() -> int:
    # 单元测试自身也统一 UTF-8 输出，避免 Windows 管道里中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        pass
    print("=" * 72)
    print("dsh-game-control Python 后端自测（只读；不会触发真实点击/按键）")
    print("python: %s" % PYTHON)
    print("backend: %s" % BACKEND)
    print("=" * 72)
    expect(BACKEND.exists(), "找不到后端入口: %s" % BACKEND)
    root = make_temp_root()
    ctx = {
        "tmp": root,
        "home": root / "home",
        "env": {"GAME_CONTROL_HOME": str(root / "home")},
    }
    try:
        for test in TESTS:
            try:
                test(ctx)
            except CheckFailure as exc:
                record(test.__name__, False, str(exc))
            except Exception as exc:  # noqa: BLE001 - 未预期异常也要留在汇总里
                record(test.__name__, False, "未预期异常: %s" % exc)
                traceback.print_exc()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for item in RESULTS if item["ok"])
    failed = len(RESULTS) - passed
    print("-" * 72)
    print("汇总: %d PASS / %d FAIL" % (passed, failed))
    if failed:
        print("失败项:")
        for item in RESULTS:
            if not item["ok"]:
                print("  - %s: %s" % (item["name"], item["detail"]))
        print("=" * 72)
        return 1
    print("全部通过 ✓")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
