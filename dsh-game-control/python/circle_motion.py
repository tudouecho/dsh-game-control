"""圆形轨迹纯函数（SPEC 8.2.2 / 8.5.1）。

本模块只做**数学计算**：不注入输入、不 sleep、不访问屏幕。``game_control.py`` 的
``mouse_circle`` 动作与 ``test_backend.py`` 复用同一套参数校验/轨迹统计，避免两边算法漂移。

坐标约定与鼠标动作一致：屏幕物理像素、y 轴向下；``startAngleDeg=-90`` 表示屏幕正上方
（12 点钟），``0``=右、``90``=下、``180``=左。角度增大在屏幕上表现为**顺时针**。

用法::

    stats = circle_points(center_x=1920, center_y=1080, radius=480,
                          period_ms=2500, duration_ms=30000)
    stats["samples"]   # int(durationMs/1000*sps)+1
    stats["start"]     # {"x": 1920, "y": 600}
    stats["bbox"]      # {"minX": 1440, ...}
"""

from __future__ import annotations

import math

# 默认值（SPEC 8.2.2）
DEFAULT_PERIOD_MS = 2500
DEFAULT_DURATION_MS = 30000
DEFAULT_DIRECTION = "cw"
DEFAULT_START_ANGLE_DEG = -90.0
DEFAULT_SAMPLES_PER_SECOND = 120
DEFAULT_LEAD_IN_MS = 300

RADIUS_MIN, RADIUS_MAX = 1.0, 5000.0
PERIOD_MIN, PERIOD_MAX = 300.0, 20000.0
DURATION_MIN, DURATION_MAX = 100.0, 120000.0
SPS_MIN, SPS_MAX = 30.0, 240.0
LEAD_IN_MIN, LEAD_IN_MAX = 0.0, 2000.0
DIRECTIONS = ("cw", "ccw")


class CircleMotionError(Exception):
    """圆轨迹参数/几何错误（``code`` 进入响应信封，通常为 INVALID_ARGUMENT）。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


def _number(name: str, value, minimum=None, maximum=None) -> float:
    """校验数字（拒绝 bool / NaN / Inf），返回 float。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CircleMotionError(
            "INVALID_ARGUMENT",
            "参数 %s 必须是数字" % name,
            hint="物理像素/毫秒请传数字",
        )
    number = float(value)
    if not math.isfinite(number):
        raise CircleMotionError("INVALID_ARGUMENT", "参数 %s 不能是 NaN/Infinity" % name)
    if minimum is not None and number < minimum:
        raise CircleMotionError("INVALID_ARGUMENT", "参数 %s 不能小于 %s（收到 %s）" % (name, minimum, number))
    if maximum is not None and number > maximum:
        raise CircleMotionError("INVALID_ARGUMENT", "参数 %s 不能大于 %s（收到 %s）" % (name, maximum, number))
    return number


def _direction(value) -> str:
    direction = str(value or DEFAULT_DIRECTION).strip().lower()
    if direction not in DIRECTIONS:
        raise CircleMotionError(
            "INVALID_ARGUMENT",
            "direction 只能是 cw 或 ccw（收到 %r）" % value,
            hint="cw=顺时针（默认），ccw=逆时针",
        )
    return direction


def point_at_angle(center_x: float, center_y: float, radius: float, angle_rad: float) -> tuple:
    """由角度（弧度）计算圆上坐标；返回 ``(x, y)`` 浮点数。"""
    return (
        float(center_x) + float(radius) * math.cos(angle_rad),
        float(center_y) + float(radius) * math.sin(angle_rad),
    )


def point_at_elapsed(
    center_x: float,
    center_y: float,
    radius: float,
    period_ms: float,
    direction: str,
    start_angle_deg: float,
    elapsed_ms: float,
) -> tuple:
    """按绝对时间（相对圆开始时刻的毫秒数）计算轨迹点。

    角度：``θ = θ0 + sign * 2π * elapsed/period``；``cw`` 时 sign=+1（屏幕坐标下即顺时针）。
    """
    if period_ms <= 0:
        raise CircleMotionError("INVALID_ARGUMENT", "periodMs 必须大于 0")
    sign = 1.0 if _direction(direction) == "cw" else -1.0
    theta = math.radians(float(start_angle_deg)) + sign * 2.0 * math.pi * (float(elapsed_ms) / float(period_ms))
    return point_at_angle(center_x, center_y, radius, theta)


def validate_circle(
    center_x: float,
    center_y: float,
    radius: float,
    period_ms: float = DEFAULT_PERIOD_MS,
    duration_ms: float = DEFAULT_DURATION_MS,
    direction: str = DEFAULT_DIRECTION,
    start_angle_deg: float = DEFAULT_START_ANGLE_DEG,
    samples_per_second: float = DEFAULT_SAMPLES_PER_SECOND,
    lead_in_ms: float = DEFAULT_LEAD_IN_MS,
    virtual_screen: dict | None = None,
) -> dict:
    """校验圆轨迹参数（含虚拟桌面包围盒），返回归一化后的参数字典。"""
    cx = _number("centerX", center_x)
    cy = _number("centerY", center_y)
    rad = _number("radius", radius, RADIUS_MIN, RADIUS_MAX)
    period = _number("periodMs", period_ms, PERIOD_MIN, PERIOD_MAX)
    duration = _number("durationMs", duration_ms, DURATION_MIN, DURATION_MAX)
    sps = _number("samplesPerSecond", samples_per_second, SPS_MIN, SPS_MAX)
    lead_in = _number("leadInMs", lead_in_ms, LEAD_IN_MIN, LEAD_IN_MAX)
    start_angle = _number("startAngleDeg", start_angle_deg)
    direction_norm = _direction(direction)

    bbox = {
        "minX": cx - rad,
        "minY": cy - rad,
        "maxX": cx + rad,
        "maxY": cy + rad,
    }
    if virtual_screen is not None:
        try:
            left = float(virtual_screen["x"])
            top = float(virtual_screen["y"])
            right = left + float(virtual_screen["width"])
            bottom = top + float(virtual_screen["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CircleMotionError("INVALID_ARGUMENT", "虚拟桌面几何不可用: %s" % exc) from exc
        if bbox["minX"] < left or bbox["minY"] < top or bbox["maxX"] > right or bbox["maxY"] > bottom:
            raise CircleMotionError(
                "INVALID_ARGUMENT",
                "圆形包围盒超出虚拟桌面：bbox=(%.0f,%.0f)-(%.0f,%.0f)，虚拟桌面=(%.0f,%.0f %.0fx%.0f)"
                % (bbox["minX"], bbox["minY"], bbox["maxX"], bbox["maxY"], left, top, right - left, bottom - top),
                hint="调整 centerX/centerY 或减小 radius，或先用 status 查看 virtualScreen",
            )
    return {
        "centerX": cx,
        "centerY": cy,
        "radius": rad,
        "periodMs": period,
        "durationMs": duration,
        "direction": direction_norm,
        "startAngleDeg": start_angle,
        "samplesPerSecond": sps,
        "leadInMs": lead_in,
    }


def circle_points(
    center_x: float,
    center_y: float,
    radius: float,
    period_ms: float = DEFAULT_PERIOD_MS,
    duration_ms: float = DEFAULT_DURATION_MS,
    direction: str = DEFAULT_DIRECTION,
    start_angle_deg: float = DEFAULT_START_ANGLE_DEG,
    samples_per_second: float = DEFAULT_SAMPLES_PER_SECOND,
    lead_in_ms: float = DEFAULT_LEAD_IN_MS,
    virtual_screen: dict | None = None,
) -> dict:
    """生成圆轨迹**统计**（不生成全部点、不 sleep），并做几何校验。

    返回字段（供 ``mouse_circle`` 返回结构与测试复用）：

    - ``samples``：``int(durationMs/1000*samplesPerSecond)+1``
    - ``loops``：``durationMs/periodMs``（可为小数）
    - ``start`` / ``end``：首/末采样点（四舍五入到整数像素）
    - ``bbox``：完整圆的包围盒（含 center±radius，用于越界校验）
    - 以及归一化后的全部参数
    """
    params = validate_circle(
        center_x=center_x,
        center_y=center_y,
        radius=radius,
        period_ms=period_ms,
        duration_ms=duration_ms,
        direction=direction,
        start_angle_deg=start_angle_deg,
        samples_per_second=samples_per_second,
        lead_in_ms=lead_in_ms,
        virtual_screen=virtual_screen,
    )
    cx = params["centerX"]
    cy = params["centerY"]
    rad = params["radius"]
    samples = int(params["durationMs"] / 1000.0 * params["samplesPerSecond"]) + 1
    loops = params["durationMs"] / params["periodMs"]
    start_x, start_y = point_at_elapsed(
        cx, cy, rad, params["periodMs"], params["direction"], params["startAngleDeg"], 0.0
    )
    # 末采样第 i=(samples-1) 个点：elapsed = i / samplesPerSecond 秒
    end_elapsed_ms = (samples - 1) / params["samplesPerSecond"] * 1000.0
    end_x, end_y = point_at_elapsed(
        cx, cy, rad, params["periodMs"], params["direction"], params["startAngleDeg"], end_elapsed_ms
    )
    bbox = {
        "minX": round(cx - rad, 3),
        "minY": round(cy - rad, 3),
        "maxX": round(cx + rad, 3),
        "maxY": round(cy + rad, 3),
    }
    return {
        **params,
        "samples": samples,
        "loops": round(loops, 6),
        "start": {"x": int(round(start_x)), "y": int(round(start_y))},
        "end": {"x": int(round(end_x)), "y": int(round(end_y))},
        "bbox": bbox,
    }
