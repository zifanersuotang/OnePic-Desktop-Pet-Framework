"""
本模块提供与渲染和素材解耦的桌宠养成数值模拟，用于承载 VPet 类玩法。

职责范围：
- 维护饱腹、饮水、活力、心情、健康、好感与经验；
- 按真实经过时间和当前基础动作推进数值；
- 接收喂食、饮水、玩耍、专注奖励和友好互动等离散事件；
- 校验每只宠物运行清单中的养成参数并生成语义哈希；
- 提供按宠物持久化所需的安全 JSON 映射；
- 输出行为层可消费的休息与身体需求压力，不直接选择动画。

Agent 快速定位：
- 养成数值位于 PetCareState；
- 时间推进位于 advance()；
- 行为权重输入位于 influence()；
- 清单规范化位于 normalize_care_config()。

本模块只依赖 Python 标准库，不读取 VPet 存档，不复制其实现，也不访问窗口、
文件或网络。调用方可通过功能开关逐步接入，关闭时不会改变现有桌宠行为。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def _bounded(value: float) -> float:
    """把百分制数值限制在 0 到 100。"""

    return min(100.0, max(0.0, float(value)))


@dataclass(frozen=True)
class CareInfluence:
    """提供给行为状态机的归一化需求压力。"""

    rest_pressure: float
    physical_pressure: float
    social_pressure: float


@dataclass
class PetCareState:
    """保存一只宠物的轻量养成状态。"""

    satiety: float = 80.0
    hydration: float = 80.0
    vitality: float = 75.0
    happiness: float = 70.0
    health: float = 100.0
    bond: float = 50.0
    experience: float = 0.0
    coins: int = 60

    def clamp(self) -> None:
        """约束百分制数值，并保证经验不为负数。"""

        self.satiety = _bounded(self.satiety)
        self.hydration = _bounded(self.hydration)
        self.vitality = _bounded(self.vitality)
        self.happiness = _bounded(self.happiness)
        self.health = _bounded(self.health)
        self.bond = _bounded(self.bond)
        self.experience = max(0.0, float(self.experience))
        self.coins = min(999_999, max(0, int(self.coins)))

    @property
    def level(self) -> int:
        """按累计经验返回从 1 开始的展示等级。"""

        return min(999, int(self.experience // 100) + 1)

    def advance(self, elapsed_seconds: float, state: str) -> None:
        """按真实经过时间和基础动作推进养成数值。"""

        minutes = min(60.0, max(0.0, float(elapsed_seconds)) / 60.0)
        if minutes <= 0:
            return

        normalized_state = str(state).strip().lower()
        self.satiety -= 0.08 * minutes
        self.hydration -= 0.12 * minutes
        self.happiness -= 0.03 * minutes

        if normalized_state == "sleep":
            self.vitality += 0.60 * minutes
            self.happiness += 0.02 * minutes
        elif normalized_state == "sit":
            self.vitality += 0.12 * minutes
        elif normalized_state == "walk":
            self.vitality -= 0.18 * minutes
            self.experience += 0.06 * minutes
        else:
            self.vitality -= 0.08 * minutes
            self.experience += 0.02 * minutes

        if self.satiety < 10 or self.hydration < 10:
            self.health -= 0.12 * minutes
        elif self.satiety >= 45 and self.hydration >= 45:
            self.health += 0.02 * minutes
        self.clamp()

    def feed(self, nutrition: float = 20.0) -> None:
        """补充饱腹度，并提供少量心情反馈。"""

        amount = max(0.0, float(nutrition))
        self.satiety += amount
        self.happiness += amount * 0.08
        self.experience += amount * 0.02
        self.clamp()

    def drink(self, hydration: float = 24.0) -> None:
        """补充饮水值。"""

        amount = max(0.0, float(hydration))
        self.hydration += amount
        self.experience += amount * 0.01
        self.clamp()

    def comfort(self, strength: float = 8.0) -> None:
        """记录摸头等友好互动带来的心情和好感变化。"""

        amount = max(0.0, float(strength))
        self.happiness += amount
        self.bond += amount * 0.35
        self.experience += amount * 0.03
        self.clamp()

    def play(
        self,
        *,
        vitality_cost: float = 6.0,
        happiness_gain: float = 14.0,
        bond_gain: float = 2.0,
        experience_gain: float = 4.0,
    ) -> bool:
        """消耗活力完成一次玩耍；活力不足时不改变状态。"""

        cost = max(0.0, float(vitality_cost))
        if self.vitality < max(8.0, cost):
            return False
        self.vitality -= cost
        self.happiness += max(0.0, float(happiness_gain))
        self.bond += max(0.0, float(bond_gain))
        self.experience += max(0.0, float(experience_gain))
        self.clamp()
        return True

    def try_spend(self, cost: int) -> bool:
        """尝试支付非负金币，余额不足时保持原值。"""

        normalized = max(0, int(cost))
        if self.coins < normalized:
            return False
        self.coins -= normalized
        return True

    def reward(self, *, coins: int = 0, experience: float = 0.0) -> None:
        """发放专注或工作奖励。"""

        self.coins += max(0, int(coins))
        self.experience += max(0.0, float(experience))
        self.happiness += min(5.0, max(0.0, float(experience)) * 0.1)
        self.clamp()

    def influence(self) -> CareInfluence:
        """把养成数值转换为行为状态机可使用的需求压力。"""

        rest_pressure = _bounded(45.0 - self.vitality) / 45.0
        hunger_pressure = _bounded(35.0 - self.satiety) / 35.0
        thirst_pressure = _bounded(35.0 - self.hydration) / 35.0
        social_pressure = _bounded(40.0 - self.happiness) / 40.0
        return CareInfluence(
            rest_pressure=rest_pressure,
            physical_pressure=max(
                rest_pressure,
                hunger_pressure,
                thirst_pressure,
            ),
            social_pressure=social_pressure,
        )

    def summary(self) -> str:
        """返回适合菜单显示的紧凑中文摘要。"""

        return (
            f"饱腹 {round(self.satiety)} · 饮水 {round(self.hydration)} · "
            f"活力 {round(self.vitality)} · 心情 {round(self.happiness)}"
        )

    def to_dict(self) -> dict[str, float | int]:
        """返回可写入用户设置的稳定 JSON 映射。"""

        self.clamp()
        return {
            "schema_version": 1,
            "satiety": round(self.satiety, 3),
            "hydration": round(self.hydration, 3),
            "vitality": round(self.vitality, 3),
            "happiness": round(self.happiness, 3),
            "health": round(self.health, 3),
            "bond": round(self.bond, 3),
            "experience": round(self.experience, 3),
            "coins": self.coins,
        }

    @classmethod
    def from_dict(
        cls,
        raw: object,
        *,
        initial_coins: int = 60,
    ) -> "PetCareState":
        """从不可信用户设置恢复状态，损坏字段回退到安全默认值。"""

        values = raw if isinstance(raw, dict) else {}

        def number(name: str, default: float) -> float:
            value = values.get(name, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        state = cls(
            satiety=number("satiety", 80.0),
            hydration=number("hydration", 80.0),
            vitality=number("vitality", 75.0),
            happiness=number("happiness", 70.0),
            health=number("health", 100.0),
            bond=number("bond", 50.0),
            experience=number("experience", 0.0),
            coins=round(number("coins", float(initial_coins))),
        )
        state.clamp()
        return state


def _limited_int(
    raw: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """读取并限制养成配置中的整数。"""

    try:
        value = int(raw.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def normalize_care_config(raw: object) -> dict[str, Any]:
    """校验运行清单中的养成配置，并补齐稳定默认值。"""

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("care_simulation 必须是 JSON 对象")
    enabled = bool(raw.get("enabled", False))
    if not enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "schema_version": 1,
        "initial_coins": _limited_int(raw, "initial_coins", 60, 0, 10_000),
        "feed_cost": _limited_int(raw, "feed_cost", 8, 0, 1_000),
        "feed_amount": _limited_int(raw, "feed_amount", 24, 1, 100),
        "drink_cost": _limited_int(raw, "drink_cost", 2, 0, 1_000),
        "drink_amount": _limited_int(raw, "drink_amount", 28, 1, 100),
        "play_vitality_cost": _limited_int(
            raw,
            "play_vitality_cost",
            6,
            0,
            100,
        ),
        "play_happiness_gain": _limited_int(
            raw,
            "play_happiness_gain",
            14,
            1,
            100,
        ),
        "focus_reward_coins": _limited_int(
            raw,
            "focus_reward_coins",
            18,
            0,
            10_000,
        ),
        "focus_reward_experience": _limited_int(
            raw,
            "focus_reward_experience",
            10,
            0,
            10_000,
        ),
        "action_cooldown_ms": _limited_int(
            raw,
            "action_cooldown_ms",
            900,
            200,
            10_000,
        ),
    }


def care_config_sha256(raw: object) -> str:
    """返回规范化养成配置的稳定 SHA-256。"""

    payload = json.dumps(
        normalize_care_config(raw),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
