"""模板匹配（纯 numpy FFT 归一化互相关，无 OpenCV 依赖；SPEC 8.2.4）。

对外：:func:`find_template` —— 输入一张 **已在内存里的 RGB 图像**（截图得到的原始字节）、
模板路径或 numpy/PIL 图像，返回匹配列表。本模块不截图、不注入输入，因此可用合成图做单元测试。

算法：
1. 灰度化（默认亮度权重；``grayscale=False`` 时用三通道均值，避免 3 倍 FFT 开销）；
2. 多尺度：模板按 ``scales`` 缩放；区域较大时先降采样做粗搜（FFT NCC），
   再回到原分辨率在候选邻域做直接 NCC 精细比对，保证 4K 区域 + 数百像素模板 < 1s；
3. NCC 分母用积分图（cumsum）计算局部 ``sum(I)`` / ``sum(I²)``，分子用 ``rfft2`` 互相关；
4. 非极大值抑制：按 score 降序，抑制中心距离小于 ``max(8, min(tw,th)/4)`` 的结果；
5. 近似纯色模板（方差≈0，NCC 分母为 0）退化为归一化 SAD 匹配，避免除零并保持可用。

模板路径规则（SPEC 8.2.4）：绝对路径直接使用；相对路径相对
``<GAME_CONTROL_HOME>/templates/``，且必须落在该目录内（防目录穿越）；文件 ≤2MB、宽高 ≤1024。

错误码：``TEMPLATE_NOT_FOUND`` / ``INVALID_TEMPLATE`` / ``MISSING_DEPENDENCY``（numpy）。
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
MAX_TEMPLATE_SIZE = 1024
MAX_SCALES = 5
MAX_RESULTS_MIN, MAX_RESULTS_MAX = 1, 20
TEMPLATE_SUFFIXES = (".png", ".jpg", ".jpeg")

# 大图降采样阈值：像素数超过该值时先粗搜（4K 整屏约 8.3M 像素）
COARSE_THRESHOLD_PIXELS = 1_200_000
COARSE_PEAK_MARGIN = 0.15
COARSE_MIN_SCORE = 0.35


class TemplateMatchError(Exception):
    """模板匹配错误；``code`` / ``hint`` 直接进入响应信封。"""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


def _numpy():
    """懒加载 numpy；缺失时返回 MISSING_DEPENDENCY（SPEC 8.2.4）。"""
    try:
        import numpy

        return numpy
    except Exception as exc:  # pragma: no cover - 本机已安装
        raise TemplateMatchError(
            "MISSING_DEPENDENCY",
            "缺少 numpy，无法做模板匹配",
            "pip install numpy（DSH 自带 Python 通常已内置）",
        ) from exc


def _pillow():
    try:
        from PIL import Image

        return Image
    except Exception as exc:  # pragma: no cover
        raise TemplateMatchError(
            "MISSING_DEPENDENCY",
            "缺少 Pillow，无法解码模板图片",
            "pip install pillow",
        ) from exc


# ---------------------------------------------------------------------------
# 模板路径 / 加载
# ---------------------------------------------------------------------------


def resolve_template_path(template, templates_root) -> Path:
    """解析模板路径（绝对路径或相对 templates 根目录），并做安全/大小校验。"""
    if template is None or str(template).strip() == "":
        raise TemplateMatchError("INVALID_TEMPLATE", "template 不能为空", hint="传 PNG/JPG 文件路径")
    raw = str(template).strip()
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        if templates_root is None:
            raise TemplateMatchError(
                "INVALID_TEMPLATE",
                "相对模板路径需要 templates 根目录: %s" % raw,
                hint="相对路径相对 <GAME_CONTROL_HOME>/templates/",
            )
        root = Path(templates_root).resolve()
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise TemplateMatchError(
                "INVALID_TEMPLATE",
                "模板路径越出 templates 目录: %s" % raw,
                hint="模板必须放在 <GAME_CONTROL_HOME>/templates/ 之内（不允许 ..）",
            ) from exc
        path = candidate
    if path.suffix.lower() not in TEMPLATE_SUFFIXES:
        raise TemplateMatchError(
            "INVALID_TEMPLATE",
            "模板必须是 PNG/JPG 文件: %s" % path.name,
            hint="支持 .png / .jpg / .jpeg",
        )
    if not path.exists() or not path.is_file():
        raise TemplateMatchError(
            "TEMPLATE_NOT_FOUND",
            "模板文件不存在: %s" % path,
            hint="把模板放到 <GAME_CONTROL_HOME>/templates/ 下，或传绝对路径",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TemplateMatchError("INVALID_TEMPLATE", "无法读取模板文件信息: %s" % exc) from exc
    if size <= 0:
        raise TemplateMatchError("INVALID_TEMPLATE", "模板文件为空: %s" % path)
    if size > MAX_TEMPLATE_BYTES:
        raise TemplateMatchError(
            "INVALID_TEMPLATE",
            "模板文件过大: %.2fMB（上限 2MB）" % (size / 1024 / 1024),
            hint="裁剪成按钮文字区域的小图（建议 < 400KB）",
        )
    return path


def load_template(template_path_or_image, templates_root=None) -> dict:
    """加载模板为 grayscale float32 数组。

    支持：文件路径（str/Path）、PIL.Image、numpy.ndarray（H×W / H×W×3 / H×W×4，uint8）。
    返回 ``{path,width,height,gray}``；``path`` 对内存图像为 ``"<image>"``。
    """
    numpy = _numpy()
    Image = _pillow()
    display_path = "<image>"
    array = None

    # 允许直接传 load_template() 的返回值（game_control._find_image_once 会先加载模板，
    # 以便在截图前就返回 TEMPLATE_NOT_FOUND / INVALID_TEMPLATE）
    if isinstance(template_path_or_image, dict) and template_path_or_image.get("gray") is not None:
        gray = numpy.asarray(template_path_or_image["gray"], dtype=numpy.float32)
        if gray.ndim != 2:
            raise TemplateMatchError("INVALID_TEMPLATE", "已加载模板的 gray 必须是二维数组")
        height, width = int(gray.shape[0]), int(gray.shape[1])
        return {
            "path": str(template_path_or_image.get("path") or "<image>"),
            "width": width,
            "height": height,
            "gray": gray.copy(),
        }

    if isinstance(template_path_or_image, numpy.ndarray):
        array = template_path_or_image
    elif hasattr(template_path_or_image, "convert") and hasattr(template_path_or_image, "size"):
        image = template_path_or_image.convert("RGB")
        array = numpy.asarray(image)
        display_path = "<PIL.Image>"
    else:
        path = resolve_template_path(template_path_or_image, templates_root)
        display_path = str(path)
        try:
            with Image.open(str(path)) as image:
                array = numpy.asarray(image.convert("RGB"))
        except Exception as exc:  # noqa: BLE001 - 解码失败统一 INVALID_TEMPLATE
            raise TemplateMatchError(
                "INVALID_TEMPLATE",
                "模板图片无法解码: %s" % exc,
                hint="确认文件是有效 PNG/JPG；可用画图/截图工具重新导出",
            ) from exc

    if array is None:
        raise TemplateMatchError("INVALID_TEMPLATE", "模板图像为空")
    array = numpy.asarray(array)
    if array.ndim == 2:
        rgb = numpy.repeat(array[:, :, None], 3, axis=2)
    elif array.ndim == 3 and array.shape[2] >= 3:
        rgb = array[:, :, :3]
    elif array.ndim == 3 and array.shape[2] == 1:
        rgb = numpy.repeat(array, 3, axis=2)
    else:
        raise TemplateMatchError(
            "INVALID_TEMPLATE",
            "模板图像维度不支持: shape=%r" % (array.shape,),
            hint="需要 H×W 或 H×W×3/4 的图片",
        )
    if rgb.dtype != numpy.uint8:
        rgb = numpy.clip(rgb, 0, 255).astype(numpy.uint8)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    if width <= 0 or height <= 0:
        raise TemplateMatchError("INVALID_TEMPLATE", "模板宽高必须 > 0")
    if width > MAX_TEMPLATE_SIZE or height > MAX_TEMPLATE_SIZE:
        raise TemplateMatchError(
            "INVALID_TEMPLATE",
            "模板尺寸过大: %dx%d（上限 %dx%d）" % (width, height, MAX_TEMPLATE_SIZE, MAX_TEMPLATE_SIZE),
            hint="只裁剪目标文字/按钮区域，不要整屏模板",
        )
    gray = _to_gray(rgb, grayscale=True)
    return {"path": display_path, "width": width, "height": height, "gray": gray}


# ---------------------------------------------------------------------------
# 基础图像运算
# ---------------------------------------------------------------------------


def _to_gray(rgb, grayscale: bool):
    """RGB uint8 → float32 单通道。grayscale=False 时用三通道均值（等价近似）。"""
    numpy = _numpy()
    data = rgb.astype(numpy.float32)
    if grayscale:
        return numpy.clip(0.299 * data[:, :, 0] + 0.587 * data[:, :, 1] + 0.114 * data[:, :, 2], 0, 255)
    return numpy.clip(data.mean(axis=2), 0, 255)


def _resize_gray(gray, width: int, height: int):
    """把 float32 灰度图缩放到目标尺寸（PIL 双线性，再转回 float32）。"""
    numpy = _numpy()
    Image = _pillow()
    width = max(1, int(width))
    height = max(1, int(height))
    source_h, source_w = int(gray.shape[0]), int(gray.shape[1])
    if source_w == width and source_h == height:
        return gray.copy()
    image = Image.fromarray(numpy.clip(gray, 0, 255).astype(numpy.uint8), mode="L")
    return numpy.asarray(image.resize((width, height), Image.BILINEAR), dtype=numpy.float32)


def _next_fast_len(value: int) -> int:
    """返回 >= value 的最小 2/3/5 平滑数（FFT 友好，减少内存与耗时）。"""
    target = max(1, int(value))

    def _smooth(number: int) -> bool:
        while number % 2 == 0:
            number //= 2
        while number % 3 == 0:
            number //= 3
        while number % 5 == 0:
            number //= 5
        return number == 1

    candidate = target
    while not _smooth(candidate):
        candidate += 1
    return candidate


def _integral(image):
    """返回 (sum, sum_sq) 积分图，形状 (H+1, W+1)，float64。"""
    numpy = _numpy()
    data = image.astype(numpy.float64)
    sums = numpy.zeros((data.shape[0] + 1, data.shape[1] + 1), dtype=numpy.float64)
    sums[1:, 1:] = numpy.cumsum(numpy.cumsum(data, axis=0), axis=1)
    squares = numpy.zeros_like(sums)
    squares[1:, 1:] = numpy.cumsum(numpy.cumsum(data * data, axis=0), axis=1)
    return sums, squares


def _window_sums(integral, height: int, width: int):
    """由积分图取每个 h×w 窗口的和，输出形状 (H-h+1, W-w+1)。"""
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _ncc_map(image, template):
    """归一化互相关（零均值）score map，形状 (H-th+1, W-tw+1)。"""
    numpy = _numpy()
    th, tw = int(template.shape[0]), int(template.shape[1])
    height, width = int(image.shape[0]), int(image.shape[1])
    if th > height or tw > width or th <= 1 or tw <= 1:
        return numpy.zeros((0, 0), dtype=numpy.float32)
    sums, squares = _integral(image)
    win_sum = _window_sums(sums, th, tw)
    win_sq = _window_sums(squares, th, tw)
    count = float(th * tw)
    template64 = template.astype(numpy.float64)
    template_mean = float(template64.mean())
    template_centered = template64 - template_mean
    template_sq = float((template_centered * template_centered).sum())
    if template_sq < 1e-9:
        # 纯色模板由 _score_map 拦截；兜底返回 0 分而不是除零
        return numpy.zeros((height - th + 1, width - tw + 1), dtype=numpy.float32)
    fft_h = _next_fast_len(height + th - 1)
    fft_w = _next_fast_len(width + tw - 1)
    image_pad = numpy.zeros((fft_h, fft_w), dtype=numpy.float32)
    image_pad[:height, :width] = image
    template_pad = numpy.zeros((fft_h, fft_w), dtype=numpy.float32)
    template_pad[:th, :tw] = template
    cross = numpy.fft.irfft2(
        numpy.fft.rfft2(image_pad) * numpy.conj(numpy.fft.rfft2(template_pad)),
        s=(fft_h, fft_w),
    )
    cross = cross[: height - th + 1, : width - tw + 1]
    numerator = cross - template_mean * win_sum
    denominator = win_sq - win_sum * win_sum / count
    numpy.clip(denominator, 0.0, None, out=denominator)
    normalizer = numpy.sqrt(denominator * template_sq)
    scores = numpy.zeros_like(numerator, dtype=numpy.float64)
    valid = normalizer > 1e-9
    scores[valid] = numerator[valid] / normalizer[valid]
    numpy.clip(scores, -1.0, 1.0, out=scores)
    return scores.astype(numpy.float32)


def _score_map(image, template):
    """统一入口：普通模板用 FFT NCC，近似纯色模板用归一化 SAD。"""
    numpy = _numpy()
    th, tw = int(template.shape[0]), int(template.shape[1])
    height, width = int(image.shape[0]), int(image.shape[1])
    if th > height or tw > width:
        return numpy.zeros((0, 0), dtype=numpy.float32)
    std = float(template.astype(numpy.float64).std())
    if std < 1e-3:
        # 纯色模板（NCC 分母为 0）：1 - 平均绝对差/255
        value = float(template.mean())
        diff_integral = numpy.zeros((height + 1, width + 1), dtype=numpy.float64)
        diff_integral[1:, 1:] = numpy.cumsum(
            numpy.cumsum(numpy.abs(image.astype(numpy.float64) - value), axis=0), axis=1
        )
        win = _window_sums(diff_integral, th, tw) / float(th * tw)
        return numpy.clip(1.0 - win / 255.0, 0.0, 1.0).astype(numpy.float32)
    return _ncc_map(image, template)


def _direct_ncc(image, template, x: int, y: int) -> float:
    """单窗口直接 NCC（用于降采样粗搜候选的原分辨率精化）。"""
    numpy = _numpy()
    th, tw = int(template.shape[0]), int(template.shape[1])
    height, width = int(image.shape[0]), int(image.shape[1])
    if x < 0 or y < 0 or x + tw > width or y + th > height:
        return -1.0
    window = image[y : y + th, x : x + tw].astype(numpy.float64)
    tpl = template.astype(numpy.float64)
    window_centered = window - window.mean()
    template_centered = tpl - tpl.mean()
    denominator = math.sqrt(float((window_centered**2).sum()) * float((template_centered**2).sum()))
    if denominator < 1e-9:
        return 0.0
    return float((window_centered * template_centered).sum() / denominator)


def _top_peaks(score_map, min_score: float, limit: int, suppress_distance: float) -> list:
    """从 score map 取最多 limit 个局部峰（按分数降序、中心距 NMS）。返回 [(y, x, score)]。"""
    numpy = _numpy()
    if score_map.size == 0:
        return []
    flat = score_map.ravel()
    candidates = numpy.flatnonzero(flat >= float(min_score))
    if candidates.size == 0:
        return []
    keep = min(candidates.size, max(limit * 4, limit + 8))
    if candidates.size > keep:
        values = flat[candidates]
        top = numpy.argpartition(values, -keep)[-keep:]
        candidates = candidates[top]
    order = candidates[numpy.argsort(flat[candidates])[::-1]]
    width = int(score_map.shape[1])
    accepted = []
    for index in order:
        y, x = divmod(int(index), width)
        blocked = False
        for _ay, ax, _ascore in accepted:
            if math.hypot(x - ax, y - _ay) < suppress_distance:
                blocked = True
                break
        if blocked:
            continue
        accepted.append((y, x, float(flat[index])))
        if len(accepted) >= limit:
            break
    return accepted


def _downsample_factor(height: int, width: int) -> int:
    """大图先降采样的倍率（1/2/4/8），控制 FFT 内存与耗时。"""
    factor = 1
    while (height / factor) * (width / factor) > COARSE_THRESHOLD_PIXELS and factor < 8:
        factor *= 2
    return factor


def _normalize_scales(scales) -> list:
    numpy = _numpy()
    if scales is None:
        return [1.0]
    if not isinstance(scales, (list, tuple)):
        raise TemplateMatchError("INVALID_TEMPLATE", "scales 必须是数组")
    if not scales:
        return [1.0]
    if len(scales) > MAX_SCALES:
        raise TemplateMatchError("INVALID_TEMPLATE", "scales 最多 5 个")
    result = []
    for item in scales:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TemplateMatchError("INVALID_TEMPLATE", "scales 元素必须是数字")
        value = float(item)
        if not math.isfinite(value) or value <= 0.05 or value > 10.0:
            raise TemplateMatchError("INVALID_TEMPLATE", "scales 元素必须在 (0.05, 10] 内")
        result.append(value)
    return result


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def find_template(
    image_rgb_bytes: bytes,
    width: int,
    height: int,
    template_path_or_image,
    region: dict | None = None,
    threshold: float = 0.85,
    scales=None,
    grayscale: bool = True,
    max_results: int = 3,
    templates_root=None,
) -> dict:
    """在内存 RGB 图像里做多尺度模板匹配。

    - ``image_rgb_bytes``：截图得到的紧密 RGB 字节（``width*height*3``）；
    - ``template_path_or_image``：模板路径 / PIL.Image / numpy.ndarray；
    - ``region``：可选屏幕区域 ``{x,y,width,height}``，仅用于把返回坐标偏移到屏幕坐标系；
    - 返回 ``{found, matches, template:{path,width,height}, scales, elapsedMs}``；
      ``matches`` 元素 ``{x,y,width,height,center:{x,y},score,scale}``。
    """
    numpy = _numpy()
    started = time.perf_counter()
    try:
        image_width = int(width)
        image_height = int(height)
    except (TypeError, ValueError) as exc:
        raise TemplateMatchError("INVALID_ARGUMENT", "图像宽高必须是整数") from exc
    if image_width <= 0 or image_height <= 0:
        raise TemplateMatchError("INVALID_ARGUMENT", "图像宽高必须 > 0")
    raw = numpy.frombuffer(image_rgb_bytes, dtype=numpy.uint8)
    expected = image_width * image_height * 3
    if raw.size != expected:
        raise TemplateMatchError(
            "INVALID_ARGUMENT",
            "图像字节长度与宽高不符：%d != %dx%dx3" % (raw.size, image_width, image_height),
        )
    rgb = raw.reshape(image_height, image_width, 3)
    image_gray = _to_gray(rgb, grayscale=bool(grayscale))

    loaded = load_template(template_path_or_image, templates_root=templates_root)
    template_gray = loaded["gray"]
    template_height, template_width = int(template_gray.shape[0]), int(template_gray.shape[1])
    scale_list = _normalize_scales(scales)
    threshold = float(threshold)
    threshold = max(0.0, min(1.0, threshold))
    max_results = max(MAX_RESULTS_MIN, min(MAX_RESULTS_MAX, int(max_results)))

    factor = _downsample_factor(image_height, image_width)
    coarse_image = (
        _resize_gray(image_gray, image_width // factor, image_height // factor) if factor > 1 else image_gray
    )
    coarse_height, coarse_width = int(coarse_image.shape[0]), int(coarse_image.shape[1])
    coarse_min_score = max(COARSE_MIN_SCORE, threshold - COARSE_PEAK_MARGIN)
    raw_candidates: list = []

    for scale in scale_list:
        tw = max(2, int(round(template_width * scale)))
        th = max(2, int(round(template_height * scale)))
        if tw > image_width or th > image_height:
            continue
        template = _resize_gray(template_gray, tw, th)
        if factor == 1:
            score_map = _score_map(image_gray, template)
            if score_map.size == 0:
                continue
            peaks = _top_peaks(
                score_map,
                min_score=coarse_min_score,
                limit=max(8, max_results * 6),
                suppress_distance=max(4.0, min(tw, th) / 4.0),
            )
            for (y, x, score) in peaks:
                raw_candidates.append({"x": int(x), "y": int(y), "score": score, "scale": scale})
            continue
        # --- 降采样粗搜 ---
        coarse_tw = max(4, int(round(tw / factor)))
        coarse_th = max(4, int(round(th / factor)))
        if coarse_tw > coarse_width or coarse_th > coarse_height:
            continue
        coarse_template = _resize_gray(template, coarse_tw, coarse_th)
        coarse_map = _score_map(coarse_image, coarse_template)
        if coarse_map.size == 0:
            continue
        peaks = _top_peaks(
            coarse_map,
            min_score=coarse_min_score,
            limit=max(8, max_results * 4),
            suppress_distance=max(4.0, min(coarse_tw, coarse_th) / 4.0),
        )
        # --- 原分辨率邻域精化 ---
        search_radius = 2 * factor + 2
        for (coarse_y, coarse_x, _coarse_score) in peaks:
            anchor_x = int(round(coarse_x * factor))
            anchor_y = int(round(coarse_y * factor))
            best = None
            for dy in range(-search_radius, search_radius + 1):
                yy = anchor_y + dy
                if yy < 0 or yy + th > image_height:
                    continue
                for dx in range(-search_radius, search_radius + 1):
                    xx = anchor_x + dx
                    if xx < 0 or xx + tw > image_width:
                        continue
                    score = _direct_ncc(image_gray, template, xx, yy)
                    if score < 0:
                        continue
                    if best is None or score > best[0]:
                        best = (score, xx, yy)
            if best is not None:
                raw_candidates.append(
                    {"x": int(best[1]), "y": int(best[2]), "score": float(best[0]), "scale": scale}
                )

    # 阈值过滤 + 全局 NMS
    filtered = [item for item in raw_candidates if item["score"] >= threshold]
    filtered.sort(key=lambda item: item["score"], reverse=True)
    selected: list = []
    for candidate in filtered:
        tw = max(2, int(round(template_width * candidate["scale"])))
        th = max(2, int(round(template_height * candidate["scale"])))
        center_x = candidate["x"] + tw / 2.0
        center_y = candidate["y"] + th / 2.0
        too_close = False
        for chosen in selected:
            chosen_w = max(2, int(round(template_width * chosen["scale"])))
            chosen_h = max(2, int(round(template_height * chosen["scale"])))
            distance = math.hypot(
                center_x - (chosen["x"] + chosen_w / 2.0),
                center_y - (chosen["y"] + chosen_h / 2.0),
            )
            if distance < max(8.0, min(tw, th) / 4.0):
                too_close = True
                break
        if too_close:
            continue
        selected.append(candidate)
        if len(selected) >= max_results:
            break

    offset_x = 0
    offset_y = 0
    if isinstance(region, dict):
        try:
            offset_x = int(region.get("x", 0))
            offset_y = int(region.get("y", 0))
        except (TypeError, ValueError):
            offset_x = offset_y = 0
    matches = []
    for item in selected:
        tw = max(2, int(round(template_width * item["scale"])))
        th = max(2, int(round(template_height * item["scale"])))
        x = int(item["x"]) + offset_x
        y = int(item["y"]) + offset_y
        matches.append(
            {
                "x": x,
                "y": y,
                "width": tw,
                "height": th,
                "center": {"x": int(round(x + tw / 2.0)), "y": int(round(y + th / 2.0))},
                "score": round(float(item["score"]), 4),
                "scale": round(float(item["scale"]), 4),
            }
        )
    return {
        "found": bool(matches),
        "matches": matches,
        "template": {
            "path": loaded["path"],
            "width": template_width,
            "height": template_height,
        },
        "scales": scale_list,
        "grayscale": bool(grayscale),
        "elapsedMs": int(round((time.perf_counter() - started) * 1000.0)),
    }
