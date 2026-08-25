"""
本模块测试桌面宠物纯逻辑行为模型的状态选择、养成需求适配与屏幕边界处理。

测试输入为固定配置、伪随机源和坐标边界，输出为可重复断言的状态及位置。
测试不创建真实窗口、不读写用户文件，也不访问网络。
"""

from onepic_desktop_pet.behavior import BehaviorModel, PetMood, PetState
from onepic_desktop_pet.care import PetCareState
from onepic_desktop_pet.config import PetSettings


class ChoiceRandom:
    """按指定索引返回候选状态，并让持续时间固定为下界。"""

    def __init__(self, index: int) -> None:
        self.index = index

    def choices(self, population, weights, k):
        return [population[self.index]]

    @staticmethod
    def randint(minimum: int, _maximum: int) -> int:
        return minimum


def test_walk_can_end_in_idle_sit_or_selfie() -> None:
    settings = PetSettings(action_min_ms=2000, action_max_ms=2000)
    states = []
    for index in range(3):
        model = BehaviorModel(settings, ChoiceRandom(index))
        states.append(model.next_autonomous_state(PetState.WALK).state)

    assert states == [PetState.IDLE, PetState.SIT, PetState.SELFIE]


def test_paused_life_choices_never_include_walk() -> None:
    settings = PetSettings()
    states = []
    for index in range(4):
        model = BehaviorModel(settings, ChoiceRandom(index))
        decision = model.next_autonomous_state(PetState.IDLE, allow_walk=False)
        states.append(decision.state)

    assert states == [PetState.IDLE, PetState.SIT, PetState.SELFIE, PetState.SLEEP]


def test_advance_horizontal_reverses_at_both_edges() -> None:
    assert BehaviorModel.advance_horizontal(2, -1, 4, 0, 100) == (0, 1)
    assert BehaviorModel.advance_horizontal(98, 1, 4, 0, 100) == (100, -1)


def test_advance_horizontal_keeps_direction_inside_bounds() -> None:
    assert BehaviorModel.advance_horizontal(50, 1, 3, 0, 100) == (53, 1)
    assert BehaviorModel.advance_horizontal(50, -1, 3, 0, 100) == (47, -1)


def test_mood_reacts_to_affection_repeated_pokes_and_sleep() -> None:
    """亲密互动、连续戳击和睡眠应分别改变对应的情绪数值。"""

    mood = PetMood(affinity=50, energy=50, boredom=50)
    mood.receive_affection()
    assert (mood.affinity, mood.energy, mood.boredom) == (55, 49, 32)

    mood.receive_poke(repeated=True)
    assert (mood.affinity, mood.energy, mood.boredom) == (53, 47, 24)

    mood.pass_time(PetState.SLEEP)
    assert (mood.affinity, mood.energy, mood.boredom) == (53, 59, 19)


def test_critical_vitality_requests_existing_sleep_state() -> None:
    """养成层不新增动画状态，极低活力直接复用现有睡眠状态。"""

    settings = PetSettings(action_min_ms=2000, action_max_ms=2000)
    model = BehaviorModel(settings, ChoiceRandom(0))

    decision = model.next_autonomous_state(
        PetState.IDLE,
        care=PetCareState(vitality=10),
    )

    assert decision.state is PetState.SLEEP
    assert decision.duration_ms == 2000
