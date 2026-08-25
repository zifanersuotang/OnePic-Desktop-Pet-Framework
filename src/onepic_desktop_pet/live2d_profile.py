"""定义并校验混合 Live2D 动画、固定外观参数与状态呈现配置。"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


DEFAULT_LIVE2D_IDLE_ANIMATION: dict[str, Any] = {
    "enabled": True,
    "intensity": 1.0,
    "display_scale": 1.0,
    "display_offset_y_ratio": 0.0,
    "blink": True,
    "eye_motion": True,
    "head_motion": True,
    "body_motion": True,
    "hair_motion": True,
    "root_motion": True,
    "blink_parameter_mode": "smooth",
    "blink_interval_ms": [2600, 4600],
    "blink_duration_ms": [105, 60, 160],
}

_BOOLEAN_KEYS = {
    "enabled",
    "blink",
    "eye_motion",
    "head_motion",
    "body_motion",
    "hair_motion",
    "root_motion",
}
_ALLOWED_KEYS = _BOOLEAN_KEYS | {
    "intensity",
    "display_scale",
    "display_offset_y_ratio",
    "blink_parameter_mode",
    "blink_interval_ms",
    "blink_duration_ms",
}
_PRESENTATION_KEYS = {
    "expression",
    "expressions",
    "parameters",
    "paused",
    "motion",
}


def normalize_live2d_persistent_parameters(
    value: object,
) -> dict[str, float]:
    """校验每帧重施的模型外观参数，防止表情切换恢复水印或造型。"""

    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError(
            "hybrid_runtime.persistent_parameters 必须是最多 64 项对象"
        )
    result: dict[str, float] = {}
    for raw_id, raw_value in value.items():
        parameter_id = str(raw_id).strip()
        if (
            not parameter_id
            or len(parameter_id) > 120
            or isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
        ):
            raise ValueError(
                "hybrid_runtime.persistent_parameters 含无效参数"
            )
        result[parameter_id] = float(raw_value)
    return result


def normalize_live2d_idle_animation(value: object) -> dict[str, Any]:
    """返回稳定的待机配置，并拒绝类型错误或危险幅度。"""

    if value is None:
        return deepcopy(DEFAULT_LIVE2D_IDLE_ANIMATION)
    if not isinstance(value, dict):
        raise ValueError("hybrid_runtime.idle_animation 必须是 JSON 对象")
    unknown = sorted(set(value) - _ALLOWED_KEYS)
    if unknown:
        raise ValueError(
            "hybrid_runtime.idle_animation 含未知字段："
            + ", ".join(unknown)
        )

    result = deepcopy(DEFAULT_LIVE2D_IDLE_ANIMATION)
    for key in _BOOLEAN_KEYS:
        if key not in value:
            continue
        if not isinstance(value[key], bool):
            raise ValueError(f"idle_animation.{key} 必须是布尔值")
        result[key] = value[key]

    if "intensity" in value:
        intensity = value["intensity"]
        if isinstance(intensity, bool) or not isinstance(
            intensity, (int, float)
        ):
            raise ValueError("idle_animation.intensity 必须是数字")
        if not 0.25 <= float(intensity) <= 1.8:
            raise ValueError("idle_animation.intensity 必须在 0.25–1.8")
        result["intensity"] = float(intensity)

    if "display_scale" in value:
        display_scale = value["display_scale"]
        if isinstance(display_scale, bool) or not isinstance(
            display_scale,
            (int, float),
        ):
            raise ValueError("idle_animation.display_scale 必须是数字")
        if not 0.7 <= float(display_scale) <= 1.8:
            raise ValueError(
                "idle_animation.display_scale 必须在 0.7–1.8"
            )
        result["display_scale"] = float(display_scale)

    if "display_offset_y_ratio" in value:
        offset = value["display_offset_y_ratio"]
        if isinstance(offset, bool) or not isinstance(offset, (int, float)):
            raise ValueError(
                "idle_animation.display_offset_y_ratio 必须是数字"
            )
        if not -0.2 <= float(offset) <= 0.2:
            raise ValueError(
                "idle_animation.display_offset_y_ratio 必须在 -0.2–0.2"
            )
        result["display_offset_y_ratio"] = float(offset)

    if "blink_parameter_mode" in value:
        mode = value["blink_parameter_mode"]
        if mode not in {"smooth", "binary"}:
            raise ValueError(
                "idle_animation.blink_parameter_mode "
                "必须是 smooth 或 binary"
            )
        result["blink_parameter_mode"] = mode

    if "blink_interval_ms" in value:
        intervals = value["blink_interval_ms"]
        if (
            not isinstance(intervals, list)
            or len(intervals) != 2
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in intervals
            )
        ):
            raise ValueError(
                "idle_animation.blink_interval_ms 必须是两个整数"
            )
        minimum, maximum = intervals
        if not 1200 <= minimum <= maximum <= 60000:
            raise ValueError(
                "idle_animation.blink_interval_ms 必须满足 "
                "1200 <= 最小值 <= 最大值 <= 60000"
            )
        result["blink_interval_ms"] = [minimum, maximum]

    if "blink_duration_ms" in value:
        durations = value["blink_duration_ms"]
        if (
            not isinstance(durations, list)
            or len(durations) != 3
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in durations
            )
        ):
            raise ValueError(
                "idle_animation.blink_duration_ms "
                "必须是闭合、停留、睁开三个整数"
            )
        close_ms, hold_ms, open_ms = durations
        if not (
            60 <= close_ms <= 1500
            and 20 <= hold_ms <= 3000
            and 60 <= open_ms <= 2000
            and sum(durations) <= 5000
        ):
            raise ValueError(
                "idle_animation.blink_duration_ms 超出安全范围"
            )
        result["blink_duration_ms"] = [close_ms, hold_ms, open_ms]

    return result


def normalize_live2d_state_map(value: object) -> dict[str, dict[str, Any]]:
    """规范化状态到模型表情/参数的映射，拒绝脚本与非有限数值。"""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("hybrid_runtime.live2d_state_map 必须是 JSON 对象")
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in value.items():
        name = str(raw_name).strip()
        if not name or len(name) > 120:
            raise ValueError("live2d_state_map 的状态名无效")
        if not isinstance(raw_spec, dict):
            raise ValueError(f"live2d_state_map.{name} 必须是 JSON 对象")
        unknown = sorted(set(raw_spec) - _PRESENTATION_KEYS)
        if unknown:
            raise ValueError(
                f"live2d_state_map.{name} 含未知字段："
                + ", ".join(unknown)
            )
        spec: dict[str, Any] = {}
        expression = raw_spec.get("expression")
        expressions = raw_spec.get("expressions")
        if expression is not None and expressions is not None:
            raise ValueError(
                f"live2d_state_map.{name} 不能同时声明 expression 和 expressions"
            )
        if expression is not None:
            expression_id = str(expression).strip()
            if not expression_id or len(expression_id) > 120:
                raise ValueError(
                    f"live2d_state_map.{name}.expression 无效"
                )
            spec["expression"] = expression_id
        if expressions is not None:
            if (
                not isinstance(expressions, list)
                or not expressions
                or len(expressions) > 32
            ):
                raise ValueError(
                    f"live2d_state_map.{name}.expressions 必须是 1–32 项数组"
                )
            expression_ids = [str(item).strip() for item in expressions]
            if any(not item or len(item) > 120 for item in expression_ids):
                raise ValueError(
                    f"live2d_state_map.{name}.expressions 含无效标识"
                )
            spec["expressions"] = expression_ids

        parameters = raw_spec.get("parameters")
        if parameters is not None:
            if not isinstance(parameters, dict) or len(parameters) > 64:
                raise ValueError(
                    f"live2d_state_map.{name}.parameters 必须是最多 64 项对象"
                )
            normalized_parameters: dict[str, float] = {}
            for raw_id, raw_value in parameters.items():
                parameter_id = str(raw_id).strip()
                if (
                    not parameter_id
                    or len(parameter_id) > 120
                    or isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(float(raw_value))
                ):
                    raise ValueError(
                        f"live2d_state_map.{name}.parameters 含无效参数"
                    )
                normalized_parameters[parameter_id] = float(raw_value)
            spec["parameters"] = normalized_parameters

        paused = raw_spec.get("paused")
        if paused is not None:
            if not isinstance(paused, bool):
                raise ValueError(
                    f"live2d_state_map.{name}.paused 必须是布尔值"
                )
            spec["paused"] = paused

        motion = raw_spec.get("motion")
        if motion is not None:
            if not isinstance(motion, dict) or set(motion) - {
                "group",
                "no",
                "priority",
            }:
                raise ValueError(
                    f"live2d_state_map.{name}.motion 结构无效"
                )
            group = str(motion.get("group", "")).strip()
            no = motion.get("no")
            priority = motion.get("priority", 2)
            if (
                not group
                or len(group) > 120
                or isinstance(no, bool)
                or not isinstance(no, int)
                or no < 0
                or isinstance(priority, bool)
                or not isinstance(priority, int)
                or not 0 <= priority <= 3
            ):
                raise ValueError(
                    f"live2d_state_map.{name}.motion 值无效"
                )
            spec["motion"] = {
                "group": group,
                "no": no,
                "priority": priority,
            }
        result[name] = spec
    return result
