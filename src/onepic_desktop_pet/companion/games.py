"""
本模块提供与界面、图片资源和网络解耦的桌宠本地小游戏核心。

当前实现为可注入随机源的扫雷模型：首次有效揭示必定安全，支持空白区扩散、
标记、胜负判定、重开以及不可变单元格视图。主题只保存标记名称和资源标识元数据，
不会读取卖家素材或假定具体文件存在；Qt 窗口可按这些语义字段自行渲染图片。
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence


Coordinate = tuple[int, int]


class CoordinateRandomSource(Protocol):
    """描述扫雷布雷所需的最小随机源接口。"""

    def sample(
        self,
        population: Sequence[Coordinate],
        count: int,
        /,
    ) -> Sequence[Coordinate]:
        """从坐标序列中无放回抽取指定数量的坐标。"""


class MinesweeperStatus(StrEnum):
    """扫雷局面的生命周期状态。"""

    READY = "ready"
    PLAYING = "playing"
    WON = "won"
    LOST = "lost"


class CellState(StrEnum):
    """界面可见的单元格状态。"""

    HIDDEN = "hidden"
    FLAGGED = "flagged"
    REVEALED = "revealed"


@dataclass(frozen=True)
class MinesweeperTheme:
    """保存可替换标记与地雷视觉所需的纯元数据。"""

    theme_id: str = "classic"
    marker_name: str = "旗帜"
    mine_name: str = "地雷"
    resource_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        theme_id = str(self.theme_id).strip()
        marker_name = str(self.marker_name).strip()
        mine_name = str(self.mine_name).strip()
        if not theme_id:
            raise ValueError("theme_id 不能为空")
        if not marker_name:
            raise ValueError("marker_name 不能为空")
        if not mine_name:
            raise ValueError("mine_name 不能为空")
        if not isinstance(self.resource_metadata, Mapping):
            raise TypeError("resource_metadata 必须是映射")

        normalized: dict[str, str] = {}
        for key, value in self.resource_metadata.items():
            normalized_key = str(key).strip()
            normalized_value = str(value).strip()
            if not normalized_key:
                raise ValueError("resource_metadata 的键不能为空")
            if not normalized_value:
                raise ValueError("resource_metadata 的值不能为空")
            normalized[normalized_key] = normalized_value

        object.__setattr__(self, "theme_id", theme_id)
        object.__setattr__(self, "marker_name", marker_name)
        object.__setattr__(self, "mine_name", mine_name)
        object.__setattr__(
            self,
            "resource_metadata",
            MappingProxyType(normalized),
        )

    @property
    def resources(self) -> Mapping[str, str]:
        """返回资源元数据的兼容只读别名。"""

        return self.resource_metadata


DEFAULT_MINESWEEPER_THEME = MinesweeperTheme()


@dataclass(frozen=True)
class MinesweeperCell:
    """提供给界面层的单元格不可变快照。"""

    row: int
    column: int
    state: CellState
    adjacent_mines: int | None
    contains_mine: bool
    incorrect_flag: bool


class MinesweeperModel:
    """实现首次揭示安全的经典矩形扫雷规则。"""

    def __init__(
        self,
        rows: int = 9,
        columns: int = 9,
        mine_count: int = 10,
        *,
        rng: CoordinateRandomSource | None = None,
        theme: MinesweeperTheme | None = None,
    ) -> None:
        self.rows = self._positive_int(rows, "rows")
        self.columns = self._positive_int(columns, "columns")
        self.mine_count = self._non_negative_int(mine_count, "mine_count")
        total_cells = self.rows * self.columns
        if self.mine_count >= total_cells:
            raise ValueError("mine_count 必须小于单元格总数，以保证首击安全")
        if theme is not None and not isinstance(theme, MinesweeperTheme):
            raise TypeError("theme 必须是 MinesweeperTheme")

        self.theme = theme or DEFAULT_MINESWEEPER_THEME
        self._rng = rng or random.Random()
        if not callable(getattr(self._rng, "sample", None)):
            raise TypeError("rng 必须提供 sample(population, count)")
        self._mines: set[Coordinate] | None = None
        self._revealed: set[Coordinate] = set()
        self._flagged: set[Coordinate] = set()
        self.status = MinesweeperStatus.READY

    @staticmethod
    def _positive_int(value: int, name: str) -> int:
        """校验正整数棋盘尺寸。"""

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} 必须是整数")
        if value <= 0:
            raise ValueError(f"{name} 必须大于零")
        return value

    @staticmethod
    def _non_negative_int(value: int, name: str) -> int:
        """校验非负整数。"""

        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} 必须是整数")
        if value < 0:
            raise ValueError(f"{name} 不能为负数")
        return value

    @property
    def first_click_done(self) -> bool:
        """返回是否已经完成首次有效揭示并生成雷区。"""

        return self._mines is not None

    @property
    def mine_positions(self) -> frozenset[Coordinate]:
        """返回当前雷区副本；首击前为空。"""

        return frozenset(self._mines or ())

    @property
    def flagged_positions(self) -> frozenset[Coordinate]:
        """返回当前标记坐标的只读副本。"""

        return frozenset(self._flagged)

    @property
    def revealed_positions(self) -> frozenset[Coordinate]:
        """返回当前已揭示坐标的只读副本。"""

        return frozenset(self._revealed)

    @property
    def flagged_count(self) -> int:
        """返回当前标记数量。"""

        return len(self._flagged)

    @property
    def revealed_count(self) -> int:
        """返回已揭示单元格数量。"""

        return len(self._revealed)

    @property
    def remaining_markers(self) -> int:
        """返回仍可放置的主题标记数量。"""

        return max(0, self.mine_count - len(self._flagged))

    @property
    def remaining_safe_cells(self) -> int:
        """返回尚未揭示的安全单元格数量。"""

        safe_total = self.rows * self.columns - self.mine_count
        revealed_safe = len(
            self._revealed.difference(self._mines or set())
        )
        return max(0, safe_total - revealed_safe)

    @property
    def won(self) -> bool:
        """返回当前是否已经胜利。"""

        return self.status is MinesweeperStatus.WON

    @property
    def lost(self) -> bool:
        """返回当前是否已经失败。"""

        return self.status is MinesweeperStatus.LOST

    def _coordinate(self, row: int, column: int) -> Coordinate:
        """校验坐标并返回规范二元组。"""

        if isinstance(row, bool) or not isinstance(row, int):
            raise TypeError("row 必须是整数")
        if isinstance(column, bool) or not isinstance(column, int):
            raise TypeError("column 必须是整数")
        if not 0 <= row < self.rows or not 0 <= column < self.columns:
            raise IndexError(f"坐标超出棋盘范围：({row}, {column})")
        return row, column

    def neighbors(self, row: int, column: int) -> tuple[Coordinate, ...]:
        """按行列顺序返回一个单元格周围最多八个坐标。"""

        center_row, center_column = self._coordinate(row, column)
        result: list[Coordinate] = []
        for candidate_row in range(
            max(0, center_row - 1),
            min(self.rows, center_row + 2),
        ):
            for candidate_column in range(
                max(0, center_column - 1),
                min(self.columns, center_column + 2),
            ):
                candidate = candidate_row, candidate_column
                if candidate != (center_row, center_column):
                    result.append(candidate)
        return tuple(result)

    def _plant_mines(self, safe_coordinate: Coordinate) -> None:
        """首次有效揭示时布雷，并强制排除该坐标。"""

        candidates = [
            (row, column)
            for row in range(self.rows)
            for column in range(self.columns)
            if (row, column) != safe_coordinate
        ]
        raw_selected = list(self._rng.sample(candidates, self.mine_count))
        if len(raw_selected) != self.mine_count:
            raise ValueError("随机源返回的雷坐标数量不正确")

        candidate_set = set(candidates)
        selected: set[Coordinate] = set()
        for raw_coordinate in raw_selected:
            try:
                coordinate = tuple(raw_coordinate)
            except TypeError as exc:
                raise ValueError("随机源返回了无效雷坐标") from exc
            if len(coordinate) != 2 or coordinate not in candidate_set:
                raise ValueError("随机源返回了候选范围外的雷坐标")
            selected.add((coordinate[0], coordinate[1]))
        if len(selected) != self.mine_count:
            raise ValueError("随机源返回了重复雷坐标")

        self._mines = selected
        self.status = MinesweeperStatus.PLAYING

    def adjacent_mine_count(self, row: int, column: int) -> int:
        """返回指定坐标周围的雷数；首击前尚无布局。"""

        coordinate = self._coordinate(row, column)
        if self._mines is None:
            raise RuntimeError("首击前尚未生成雷区")
        if coordinate in self._mines:
            raise ValueError("雷格没有相邻数字")
        return sum(neighbor in self._mines for neighbor in self.neighbors(*coordinate))

    def toggle_flag(self, row: int, column: int) -> bool:
        """切换隐藏格标记，并返回操作后的标记状态。"""

        coordinate = self._coordinate(row, column)
        if self.status in {MinesweeperStatus.WON, MinesweeperStatus.LOST}:
            return coordinate in self._flagged
        if coordinate in self._revealed:
            return False
        if coordinate in self._flagged:
            self._flagged.remove(coordinate)
            return False
        if len(self._flagged) >= self.mine_count:
            return False
        self._flagged.add(coordinate)
        return True

    def toggle_marker(self, row: int, column: int) -> bool:
        """使用主题术语切换标记；规则与 ``toggle_flag`` 相同。"""

        return self.toggle_flag(row, column)

    def reveal(self, row: int, column: int) -> tuple[Coordinate, ...]:
        """揭示单元格，零数字时扩散，并返回本次新揭示坐标。"""

        coordinate = self._coordinate(row, column)
        if self.status in {MinesweeperStatus.WON, MinesweeperStatus.LOST}:
            return ()
        if coordinate in self._flagged or coordinate in self._revealed:
            return ()
        if self._mines is None:
            self._plant_mines(coordinate)
        assert self._mines is not None

        if coordinate in self._mines:
            self._revealed.add(coordinate)
            self.status = MinesweeperStatus.LOST
            return (coordinate,)

        newly_revealed: set[Coordinate] = set()
        pending: deque[Coordinate] = deque([coordinate])
        while pending:
            current = pending.popleft()
            if (
                current in self._revealed
                or current in newly_revealed
                or current in self._flagged
                or current in self._mines
            ):
                continue
            newly_revealed.add(current)
            if self.adjacent_mine_count(*current) == 0:
                pending.extend(self.neighbors(*current))

        self._revealed.update(newly_revealed)
        if self.remaining_safe_cells == 0:
            self.status = MinesweeperStatus.WON
        return tuple(sorted(newly_revealed))

    def cell(self, row: int, column: int) -> MinesweeperCell:
        """返回一个不泄漏进行中隐藏雷位的界面快照。"""

        coordinate = self._coordinate(row, column)
        if coordinate in self._revealed:
            state = CellState.REVEALED
        elif coordinate in self._flagged:
            state = CellState.FLAGGED
        else:
            state = CellState.HIDDEN

        is_mine = self._mines is not None and coordinate in self._mines
        terminal = self.status in {
            MinesweeperStatus.WON,
            MinesweeperStatus.LOST,
        }
        contains_mine = bool(is_mine and (terminal or state is CellState.REVEALED))
        adjacent: int | None = None
        if state is CellState.REVEALED and not is_mine:
            adjacent = self.adjacent_mine_count(*coordinate)
        incorrect_flag = bool(
            self.status is MinesweeperStatus.LOST
            and coordinate in self._flagged
            and not is_mine
        )
        return MinesweeperCell(
            row=row,
            column=column,
            state=state,
            adjacent_mines=adjacent,
            contains_mine=contains_mine,
            incorrect_flag=incorrect_flag,
        )

    def board(self) -> tuple[tuple[MinesweeperCell, ...], ...]:
        """返回完整棋盘的不可变界面快照。"""

        return tuple(
            tuple(self.cell(row, column) for column in range(self.columns))
            for row in range(self.rows)
        )

    def reset(self) -> None:
        """清空局面，下次有效揭示会重新布雷。"""

        self._mines = None
        self._revealed.clear()
        self._flagged.clear()
        self.status = MinesweeperStatus.READY


__all__ = [
    "CellState",
    "Coordinate",
    "CoordinateRandomSource",
    "DEFAULT_MINESWEEPER_THEME",
    "MinesweeperCell",
    "MinesweeperModel",
    "MinesweeperStatus",
    "MinesweeperTheme",
]
