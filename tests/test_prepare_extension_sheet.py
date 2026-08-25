"""
本模块验证扩展动作图集拆分与装配工具的确定性基础能力。

测试只创建内存占位图，重点覆盖非整除网格、孤立去背残片清理，以及跳跃动作
保留相对地面高度；不读取或修改用户角色素材。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from tools.prepare_extension_sheet import (
    prepare_frames,
    remove_small_alpha_components,
    split_cells,
)


def test_split_cells_keeps_non_divisible_sheet_edges() -> None:
    """宽高不能整除时，末列和末行也应完整进入对应格子。"""

    sheet = Image.new("RGBA", (101, 61), (0, 0, 0, 0))
    cells = split_cells(sheet, columns=3, rows=2, count=6)

    assert len(cells) == 6
    assert sum(cell.width for cell in cells[:3]) == sheet.width
    assert cells[-1].height == sheet.height - round(sheet.height / 2)


def test_small_isolated_chroma_fragment_is_removed() -> None:
    """主体之外的小块去背残片应被清除，主体透明度保持不变。"""

    image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 20, 90, 110), fill=(20, 20, 20, 255))
    draw.rectangle((3, 3, 8, 8), fill=(255, 255, 255, 255))

    cleaned = remove_small_alpha_components(image, minimum_pixels=64)

    assert cleaned.getpixel((5, 5))[3] == 0
    assert cleaned.getpixel((60, 60))[3] == 255


def test_prepare_frames_preserves_jump_height() -> None:
    """启用垂直运动后，腾空帧的脚底应明显高于落地帧。"""

    grounded = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    airborne = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    ImageDraw.Draw(grounded).rectangle((35, 30, 85, 112), fill=(20, 20, 20, 255))
    ImageDraw.Draw(airborne).rectangle((35, 8, 85, 90), fill=(20, 20, 20, 255))

    frames = prepare_frames(
        [grounded, airborne],
        canvas_size=(560, 500),
        target_subject_height=360,
        preserve_vertical_motion=True,
    )
    grounded_bottom = frames[0].getchannel("A").getbbox()[3]
    airborne_bottom = frames[1].getchannel("A").getbbox()[3]

    assert grounded_bottom - airborne_bottom >= 20
