"""
本模块验证 Live2D 关键姿态到 OnePic 73 帧候选包的隔离装配契约。

测试只在 pytest 沙箱创建临时工作区，检查计数、路径、透明通道、画布、物理唯一性
和缺失预设回退报告，不读取或修改真实宠物素材。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from tools.build_live2d_sprite_pack import build_sprite_pack


def _preset(path: Path, color: tuple[int, int, int, int]) -> None:
    """写入带透明四角和非对称细节的测试关键姿态。"""

    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((24, 8, 72, 54), fill=color)
    draw.rounded_rectangle((18, 42, 78, 89), radius=14, fill=color)
    draw.rectangle((59, 22, 67, 30), fill=(255, 255, 255, 230))
    image.save(path, "PNG")


def test_builds_exact_unique_73_frame_private_pack(tmp_path: Path) -> None:
    """输出必须恰好 73 帧且路径、文件、像素、RGBA 和画布均通过。"""

    workspace = tmp_path / "workspace"
    preset_dir = tmp_path / "presets"
    workspace.mkdir()
    preset_dir.mkdir()
    _preset(preset_dir / "neutral.png", (80, 150, 230, 255))
    _preset(preset_dir / "happy.png", (90, 210, 145, 255))
    _preset(preset_dir / "walk_a.png", (225, 120, 95, 255))
    _preset(preset_dir / "walk_b.png", (210, 90, 150, 255))
    _preset(preset_dir / "side_a.png", (130, 100, 225, 255))
    _preset(preset_dir / "side_b.png", (170, 115, 220, 255))

    report = build_sprite_pack(
        workspace,
        preset_dir,
        canvas_size=(96, 96),
    )

    output = workspace / "generated" / "live2d-sprite-pack-73"
    saved_report = json.loads(
        (output / "sprite-pack-73-report.json").read_text(encoding="utf-8")
    )
    frames = saved_report["frames"]
    assert report["counts"] == {
        "base": 38,
        "touch": 2,
        "advanced": 25,
        "side_walk": 8,
        "total": 73,
    }
    assert len(frames) == 73
    assert len({item["path"] for item in frames}) == 73
    assert len({item["file_sha256"] for item in frames}) == 73
    assert len({item["pixel_sha256"] for item in frames}) == 73
    assert saved_report["uniqueness"] == {
        "path_count": 73,
        "unique_file_sha256_count": 73,
        "unique_pixel_sha256_count": 73,
        "all_physical_frames_unique": True,
    }
    assert saved_report["fallbacks"]["shy"] == "neutral"
    assert saved_report["approval_written"] is False
    assert saved_report["builder_invoked"] is False

    for item in frames:
        path = output / item["path"]
        assert path.is_file()
        with Image.open(path) as image:
            assert image.mode == "RGBA"
            assert image.size == (96, 96)
            assert image.getchannel("A").getbbox() is not None
            assert image.getpixel((0, 0))[3] == 0

    expected_sequences = {
        "walk",
        "signature_hug_bear_v1",
        "focus_writing_v1",
        "happy_jump_v1",
        "waiting_response_v1",
        "micro_expressions_v1",
        "side_walk_v1",
    }
    assert set(saved_report["previews"]) == expected_sequences
    for artifacts in saved_report["previews"].values():
        assert (output / artifacts["preview"]).is_file()
        assert (output / artifacts["contact_sheet"]).is_file()
        assert (output / artifacts["report"]).is_file()
