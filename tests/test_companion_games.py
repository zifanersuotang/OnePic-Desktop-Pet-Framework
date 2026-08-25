"""
本模块测试无界面扫雷核心的首击安全、扩散、标记、胜负和主题元数据。

测试使用固定布雷源，不加载图片、不读写存档，也不访问网络。
"""

from collections.abc import Sequence

import pytest

from onepic_desktop_pet.companion.games import (
    CellState,
    MinesweeperModel,
    MinesweeperStatus,
    MinesweeperTheme,
)


class _FixedMineRng:
    def __init__(self, coordinates: Sequence[tuple[int, int]]) -> None:
        self.coordinates = tuple(coordinates)

    def sample(
        self,
        population: Sequence[tuple[int, int]],
        count: int,
        /,
    ) -> Sequence[tuple[int, int]]:
        assert count == len(self.coordinates)
        assert set(self.coordinates).issubset(set(population))
        return self.coordinates


def test_board_configuration_preserves_first_click_safety_capacity() -> None:
    with pytest.raises(ValueError, match="大于零"):
        MinesweeperModel(rows=0, columns=3, mine_count=1)
    with pytest.raises(ValueError, match="首击安全"):
        MinesweeperModel(rows=2, columns=2, mine_count=4)
    with pytest.raises(TypeError, match="整数"):
        MinesweeperModel(rows=True, columns=2, mine_count=1)


def test_first_effective_reveal_is_safe_and_places_exact_mine_count() -> None:
    model = MinesweeperModel(rows=4, columns=4, mine_count=3)

    model.toggle_flag(0, 0)
    assert model.reveal(0, 0) == ()
    assert model.first_click_done is False

    model.reveal(1, 1)

    assert model.first_click_done is True
    assert len(model.mine_positions) == 3
    assert (1, 1) not in model.mine_positions
    assert model.cell(1, 1).state is CellState.REVEALED


def test_zero_cell_reveal_diffuses_to_all_connected_safe_cells() -> None:
    model = MinesweeperModel(
        rows=3,
        columns=3,
        mine_count=1,
        rng=_FixedMineRng([(2, 2)]),
    )

    revealed = model.reveal(0, 0)

    assert set(revealed) == {
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
        (2, 0),
        (2, 1),
    }
    assert model.cell(1, 1).adjacent_mines == 1
    assert model.status is MinesweeperStatus.WON
    assert model.cell(2, 2).contains_mine is True


def test_flagged_safe_cell_blocks_diffusion_until_unflagged() -> None:
    model = MinesweeperModel(
        rows=3,
        columns=3,
        mine_count=1,
        rng=_FixedMineRng([(2, 2)]),
    )
    assert model.toggle_marker(0, 1) is True

    revealed = model.reveal(0, 0)

    assert (0, 1) not in revealed
    assert model.status is MinesweeperStatus.PLAYING
    assert model.remaining_safe_cells == 3
    assert model.toggle_marker(0, 1) is False
    assert set(model.reveal(0, 1)) == {(0, 1), (0, 2), (1, 2)}
    assert model.won is True


def test_marker_count_is_limited_and_revealed_cells_cannot_be_marked() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=2,
        mine_count=1,
        rng=_FixedMineRng([(1, 1)]),
    )

    assert model.toggle_flag(0, 0) is True
    assert model.remaining_markers == 0
    assert model.toggle_flag(0, 1) is False
    assert model.toggle_flag(0, 0) is False
    model.reveal(0, 0)
    assert model.toggle_flag(0, 0) is False


def test_revealing_a_mine_after_safe_first_click_loses_game() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=2,
        mine_count=1,
        rng=_FixedMineRng([(0, 0)]),
    )
    model.reveal(0, 1)

    assert model.reveal(0, 0) == ((0, 0),)
    assert model.status is MinesweeperStatus.LOST
    assert model.lost is True
    assert model.cell(0, 0).contains_mine is True
    assert model.reveal(1, 1) == ()


def test_revealing_every_safe_number_wins_game() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=2,
        mine_count=1,
        rng=_FixedMineRng([(0, 0)]),
    )

    model.reveal(0, 1)
    model.reveal(1, 0)
    model.reveal(1, 1)

    assert model.status is MinesweeperStatus.WON
    assert model.remaining_safe_cells == 0


def test_loss_marks_incorrect_theme_marker_without_leaking_mines_early() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=3,
        mine_count=2,
        rng=_FixedMineRng([(0, 0), (1, 2)]),
    )
    model.toggle_flag(1, 1)
    model.reveal(0, 1)

    assert model.cell(0, 0).contains_mine is False
    model.reveal(0, 0)

    assert model.cell(1, 1).incorrect_flag is True
    assert model.cell(1, 2).contains_mine is True


def test_theme_names_and_resource_metadata_are_preserved_read_only() -> None:
    theme = MinesweeperTheme(
        theme_id="cat-room",
        marker_name="猫猫标记",
        mine_name="毛线炸弹",
        resource_metadata={
            "marker": "theme://cat/marker",
            "mine": "theme://cat/mine",
        },
    )
    model = MinesweeperModel(rows=2, columns=2, mine_count=1, theme=theme)

    assert model.theme.marker_name == "猫猫标记"
    assert model.theme.mine_name == "毛线炸弹"
    assert model.theme.resources["mine"] == "theme://cat/mine"
    with pytest.raises(TypeError):
        model.theme.resource_metadata["mine"] = "changed"  # type: ignore[index]


def test_reset_returns_to_ready_without_reusing_revealed_state() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=2,
        mine_count=1,
        rng=_FixedMineRng([(0, 0)]),
    )
    model.reveal(1, 1)
    model.toggle_flag(0, 0)

    model.reset()

    assert model.status is MinesweeperStatus.READY
    assert model.first_click_done is False
    assert model.mine_positions == frozenset()
    assert model.revealed_positions == frozenset()
    assert model.flagged_positions == frozenset()


class _UnsafeRng:
    def sample(
        self,
        population: Sequence[tuple[int, int]],
        count: int,
        /,
    ) -> Sequence[tuple[int, int]]:
        return [(0, 0)] * count


def test_injected_rng_cannot_break_first_click_safety_contract() -> None:
    model = MinesweeperModel(
        rows=2,
        columns=2,
        mine_count=1,
        rng=_UnsafeRng(),
    )

    with pytest.raises(ValueError, match="候选范围外"):
        model.reveal(0, 0)


def test_board_snapshot_and_coordinate_validation_are_stable() -> None:
    model = MinesweeperModel(rows=2, columns=3, mine_count=0)

    board = model.board()
    assert len(board) == 2
    assert len(board[0]) == 3
    assert all(cell.state is CellState.HIDDEN for row in board for cell in row)
    with pytest.raises(IndexError, match="超出"):
        model.cell(2, 0)
    with pytest.raises(RuntimeError, match="首击前"):
        model.adjacent_mine_count(0, 0)
