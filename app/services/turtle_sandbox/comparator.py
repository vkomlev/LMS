# app/services/turtle_sandbox/comparator.py
"""
Сравнение трассы ответа ученика с эталонной трассой (tsk-412).

Обе трассы получены ОДНИМ И ТЕМ ЖЕ стабом (`stub_turtle`), поэтому сравнение
чисто геометрическое — числовое совпадение с допуском на накопление float
(`TurtleSimRules.tolerance_px`), без всякой эвристики.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_HEADING_TOLERANCE_DEG = 1.0
_EXTENT_TOLERANCE_DEG = 1.0
_COLOR_TOLERANCE = 0.05


def _points_close(a: List[float], b: List[float], tolerance: float) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def _colors_close(a: List[float], b: List[float], tolerance: float = _COLOR_TOLERANCE) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _heading_close(a: float, b: float, tolerance: float = _HEADING_TOLERANCE_DEG) -> bool:
    diff = abs((a - b + 180) % 360 - 180)
    return diff <= tolerance


def compare_traces(
    expected: Dict[str, Any],
    actual: Dict[str, Any],
    tolerance_px: float,
) -> Tuple[bool, Optional[str]]:
    """
    Сравнивает две трассы.

    Returns:
        (True, None) при совпадении в пределах допуска;
        (False, "причина расхождения — для лога, ученику не показывается") иначе.
    """
    exp_segments = expected.get("segments", [])
    act_segments = actual.get("segments", [])

    if len(exp_segments) != len(act_segments):
        return False, f"число отрезков не совпадает: эталон={len(exp_segments)}, ответ={len(act_segments)}"

    for index, (exp_seg, act_seg) in enumerate(zip(exp_segments, act_segments)):
        if exp_seg["kind"] != act_seg["kind"]:
            return False, f"сегмент {index}: тип {exp_seg['kind']} != {act_seg['kind']}"
        if not _points_close(exp_seg["start"], act_seg["start"], tolerance_px):
            return False, f"сегмент {index}: начало не совпадает"
        if not _points_close(exp_seg["end"], act_seg["end"], tolerance_px):
            return False, f"сегмент {index}: конец не совпадает"
        if not _colors_close(exp_seg["color_rgb"], act_seg["color_rgb"]):
            return False, f"сегмент {index}: цвет не совпадает"
        if exp_seg["kind"] == "circle":
            if abs(exp_seg["radius"] - act_seg["radius"]) > tolerance_px:
                return False, f"сегмент {index}: радиус не совпадает"
            if abs(exp_seg["extent"] - act_seg["extent"]) > _EXTENT_TOLERANCE_DEG:
                return False, f"сегмент {index}: угол дуги не совпадает"

    exp_final = expected.get("final_state", {})
    act_final = actual.get("final_state", {})
    if not _points_close(
        exp_final.get("position", [0, 0]), act_final.get("position", [0, 0]), tolerance_px
    ):
        return False, "финальная позиция не совпадает"
    if not _heading_close(exp_final.get("heading", 0.0), act_final.get("heading", 0.0)):
        return False, "финальное направление не совпадает"

    return True, None
