"""
本模块提供桌面宠物的纯逻辑行为状态机，不直接创建窗口或访问文件。

职责范围：
- 定义待机、跑步、坐下、睡觉、互动表情和拖动状态；
- 维护亲密度、精力与无聊度三个轻量情绪数值；
- 根据是否允许跑动及可选养成需求选择下一生活状态及持续时间；
- 计算到达屏幕边界后的新位置和行走方向。

Agent 快速定位：
- 状态枚举位于 PetState；
- 自主状态选择位于 BehaviorModel.next_autonomous_state()；
- 水平边界计算位于 BehaviorModel.advance_horizontal()。

输入为当前状态、配置时长、坐标和边界，输出为新的状态、时长、坐标或方向。
模块只依赖 Python 标准库，不执行文件写入、网络请求或 GUI 操作，便于单元测试。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .care import PetCareState
from .config import PetSettings


class PetState(str, Enum):
    """桌面宠物可显示的行为状态。"""

    IDLE = "idle"
    WALK = "walk"
    SIT = "sit"
    SLEEP = "sleep"
    WAVE = "wave"
    HAPPY = "happy"
    SHY = "shy"
    SURPRISED = "surprised"
    ANNOYED = "annoyed"
    SLEEPY = "sleepy"
    CURIOUS = "curious"
    SELFIE = "selfie"
    DRAG = "drag"


@dataclass(frozen=True)
class StateDecision:
    """一次自主状态选择的结果。"""

    state: PetState
    duration_ms: int


@dataclass
class PetMood:
    """保存并约束宠物的亲密度、精力和无聊度。"""

    affinity: int = 50
    energy: int = 75
    boredom: int = 10

    def _clamp(self) -> None:
        """把三个情绪数值限制在 0 到 100。"""

        self.affinity = min(100, max(0, self.affinity))
        self.energy = min(100, max(0, self.energy))
        self.boredom = min(100, max(0, self.boredom))

    def receive_affection(self) -> None:
        """记录摸头或友好点击带来的亲密反馈。"""

        self.affinity += 5
        self.energy -= 1
        self.boredom -= 18
        self._clamp()

    def receive_poke(self, repeated: bool) -> None:
        """记录身体戳击；连续戳击会轻微降低亲密度。"""

        self.affinity -= 2 if repeated else 0
        self.energy -= 2
        self.boredom -= 8
        self._clamp()

    def receive_drag(self) -> None:
        """记录一次拖拽带来的精力消耗和解闷效果。"""

        self.energy -= 3
        self.boredom -= 12
        self._clamp()

    def pass_time(self, state: PetState) -> None:
        """在自主状态到期时更新精力和无聊度。"""

        if state is PetState.SLEEP:
            self.energy += 12
            self.boredom -= 5
        elif state is PetState.SIT:
            self.energy += 3
            self.boredom += 2
        else:
            self.energy -= 1
            self.boredom += 4
        self._clamp()


class BehaviorModel:
    """封装可注入随机源的桌面宠物行为决策。"""

    def __init__(
        self,
        settings: PetSettings,
        random_source: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.random = random_source or random.Random()

    def initial_idle(self) -> StateDecision:
        """返回初始待机状态和随机持续时间。"""

        return StateDecision(
            PetState.IDLE,
            self.random.randint(
                self.settings.idle_min_ms,
                self.settings.idle_max_ms,
            ),
        )

    def next_autonomous_state(
        self,
        current: PetState,
        allow_walk: bool = True,
        care: PetCareState | None = None,
    ) -> StateDecision:
        """按当前状态、跑动开关和可选养成需求选择下一生活状态。"""

        if current is PetState.WALK:
            state = self.random.choices(
                (PetState.IDLE, PetState.SIT, PetState.SELFIE),
                weights=(0.42, 0.33, 0.25),
                k=1,
            )[0]
            return self._action_decision(state)
        if current is not PetState.IDLE:
            return self.initial_idle()
        if care is not None and care.vitality <= 15:
            return self._action_decision(PetState.SLEEP)
        if allow_walk:
            if care is None:
                states = (PetState.WALK, PetState.SIT, PetState.SELFIE)
                weights = (0.56, 0.27, 0.17)
            else:
                influence = care.influence()
                states = (
                    PetState.WALK,
                    PetState.SIT,
                    PetState.SELFIE,
                    PetState.SLEEP,
                )
                weights = (
                    0.56 * (1.0 - 0.80 * influence.physical_pressure),
                    0.27 + 0.22 * influence.physical_pressure,
                    0.17 + 0.10 * influence.social_pressure,
                    0.30 * influence.rest_pressure,
                )
        else:
            states = (PetState.IDLE, PetState.SIT, PetState.SELFIE, PetState.SLEEP)
            if care is None:
                weights = (0.32, 0.30, 0.23, 0.15)
            else:
                influence = care.influence()
                weights = (
                    0.32 * (1.0 - 0.45 * influence.physical_pressure),
                    0.30 + 0.16 * influence.physical_pressure,
                    0.23 + 0.10 * influence.social_pressure,
                    0.15 + 0.35 * influence.rest_pressure,
                )
        state = self.random.choices(states, weights=weights, k=1)[0]
        return self._action_decision(state)

    def _action_decision(self, state: PetState) -> StateDecision:
        """为选中的生活状态附加统一范围内的随机持续时间。"""

        return StateDecision(
            state,
            self.random.randint(
                self.settings.action_min_ms,
                self.settings.action_max_ms,
            ),
        )

    @staticmethod
    def advance_horizontal(
        x: int,
        direction: int,
        step: int,
        minimum: int,
        maximum: int,
    ) -> tuple[int, int]:
        """移动一步并在边界处夹紧位置、反转方向。"""

        normalized_direction = 1 if direction >= 0 else -1
        next_x = x + normalized_direction * step
        if next_x <= minimum:
            return minimum, 1
        if next_x >= maximum:
            return maximum, -1
        return next_x, normalized_direction
