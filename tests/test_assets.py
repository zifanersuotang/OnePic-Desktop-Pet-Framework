"""
本模块检查桌面宠物派生素材清单、透明通道、连续帧数量和统一画布规格。

测试读取项目内生成的 PNG，不修改素材、不启动 GUI，也不访问网络。
"""

import json
from pathlib import Path

from PIL import Image

from tools.prepare_assets import split_equal_horizontal_sheet, split_horizontal_sheet


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_assets_exist_and_are_transparent() -> None:
    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_size = tuple(manifest["canvas_size"])

    animations = manifest["animations"]
    assert "assets/generated/interaction-expressions-v2-alpha.png" in manifest["sources"]
    assert set(animations) == {
        "idle",
        "wave",
        "walk",
        "happy",
        "sit",
        "sleep",
        "selfie",
        "drag",
        "shy",
        "surprised",
        "annoyed",
        "sleepy",
        "curious",
    }
    assert len(animations["walk"]) == 8
    assert len(animations["selfie"]) == 4
    assert len(animations["idle"]) == 6
    assert len(animations["sit"]) == 5
    assert len(animations["sleep"]) == 5
    assert len(animations["drag"]) == 3
    assert sum(len(paths) for paths in animations.values()) == 38
    for relative_paths in animations.values():
        for relative in relative_paths:
            path = manifest_path.parent / relative
            with Image.open(path) as image:
                assert image.mode == "RGBA"
                assert image.size == expected_size
                assert image.getchannel("A").getextrema()[0] == 0
                assert image.getchannel("A").getbbox() is not None


def test_standing_animation_frames_use_consistent_character_height() -> None:
    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    animations = manifest["animations"]
    heights = []
    for state in (
        "idle",
        "wave",
        "happy",
        "shy",
        "surprised",
        "annoyed",
        "sleepy",
        "curious",
        "selfie",
    ):
        for relative in animations[state]:
            with Image.open(manifest_path.parent / relative) as image:
                bbox = image.getchannel("A").getbbox()
                assert bbox is not None
                heights.append(bbox[3] - bbox[1])

    assert max(heights) - min(heights) <= 8


def test_run_cycle_preserves_airborne_height() -> None:
    """跑步腾空帧的鞋底必须离开公共基线，不能被素材规范化重新贴地。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bottoms = []
    for relative in manifest["animations"]["walk"]:
        with Image.open(manifest_path.parent / relative) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            bottoms.append(bbox[3])

    assert max(bottoms) - min(bottoms) >= 12


def test_sleep_sheet_is_split_by_transparent_gutters_not_equal_width() -> None:
    """宽度逐渐增加的躺姿必须按透明间隔拆分，不能被等宽边界截头。"""

    source_path = (
        PROJECT_ROOT / "assets" / "generated" / "sit-to-sleep-v2-alpha.png"
    )
    with Image.open(source_path) as source:
        frames = split_horizontal_sheet(source.convert("RGBA"), 5)

    widths = [frame.getchannel("A").getbbox()[2] for frame in frames]
    assert len(frames) == 5
    assert len(set(frame.width for frame in frames)) > 1
    assert widths[-1] > widths[0]
    assert frames[-1].width >= frames[0].width * 1.5


def test_expression_sheet_keeps_symbols_inside_six_equal_cells() -> None:
    """互动表情必须按六个固定单元格拆分，不能把独立漫画符号误判为人物帧。"""

    source_path = (
        PROJECT_ROOT
        / "assets"
        / "generated"
        / "interaction-expressions-v2-alpha.png"
    )
    with Image.open(source_path) as source:
        frames = split_equal_horizontal_sheet(source.convert("RGBA"), 6)

    assert len(frames) == 6
    assert all(frame.getchannel("A").getbbox() is not None for frame in frames)


def test_seated_sleep_starts_at_same_height_as_final_sit_frame() -> None:
    """坐姿入睡首帧必须与坐下末帧同高，避免状态切换时人物突然放大。"""

    paths = (
        PROJECT_ROOT / "assets" / "pet" / "sit" / "sit_05.png",
        PROJECT_ROOT / "assets" / "pet" / "sleep" / "sleep_01.png",
    )
    heights = []
    for path in paths:
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights == [316, 316]


def test_corrected_sit_sheet_keeps_transition_height_and_source() -> None:
    """正确版坐下主图集必须登记，且第四帧处于深蹲与盘腿坐姿之间。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "assets/generated/sit-transition-v4-alpha.png" in manifest["sources"]

    heights = []
    for index in (3, 4, 5):
        path = PROJECT_ROOT / "assets" / "pet" / "sit" / f"sit_{index:02d}.png"
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights[0] > heights[1] > heights[2]


def test_corrected_sleep_sheet_keeps_source_and_lowers_gradually() -> None:
    """正确版入睡主图集必须登记，且前三帧人物高度应随侧卧逐步降低。"""

    manifest_path = PROJECT_ROOT / "assets" / "pet" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "assets/generated/sit-to-sleep-v2-alpha.png" in manifest["sources"]

    heights = []
    for index in (1, 2, 3):
        path = PROJECT_ROOT / "assets" / "pet" / "sleep" / f"sleep_{index:02d}.png"
        with Image.open(path) as image:
            bbox = image.getchannel("A").getbbox()
            assert bbox is not None
            heights.append(bbox[3] - bbox[1])

    assert heights[0] > heights[1] > heights[2]
