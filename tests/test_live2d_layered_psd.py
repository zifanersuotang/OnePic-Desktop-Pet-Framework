"""验证声明式 Live2D 分层 PSD 装配不依赖真实用户素材。"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

from tools.build_live2d_layered_psd import build


def test_build_layered_psd_in_test_sandbox(tmp_path: Path) -> None:
    """测试只在 pytest 沙箱写入并保留透明通道与图层名称。"""

    layer_root = tmp_path / "layers"
    layer_root.mkdir()
    red = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
    red.paste((255, 0, 0, 255), (5, 5, 35, 55))
    blue = Image.new("RGBA", (50, 30), (0, 0, 0, 0))
    blue.paste((0, 0, 255, 255), (4, 4, 46, 26))
    red.save(layer_root / "red.png")
    blue.save(layer_root / "blue.png")
    manifest = {
        "schema_version": 1,
        "canvas_size": [120, 100],
        "layers": [
            {
                "name": "BODY",
                "file": "layers/red.png",
                "target_bbox": [20, 10, 80, 90],
                "mirror_x": True,
                "visible": True,
            },
            {
                "name": "BLINK",
                "file": "layers/blue.png",
                "target_bbox": [30, 20, 100, 60],
                "visible": False,
                "preview_visible": False,
            },
        ],
    }
    manifest_path = tmp_path / "layout.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "model.psd"
    preview = tmp_path / "preview.png"

    report = build(manifest_path, output, preview)

    assert output.is_file()
    assert preview.is_file()
    assert report["canvas_size"] == [120, 100]
    psd = PSDImage.open(output)
    assert psd.pil_mode == "RGBA"
    assert {layer.name for layer in psd} == {"BODY", "BLINK"}
    assert report["layers"][0]["mirror_x"] is True
    assert Image.open(preview).getchannel("A").getbbox() is not None


def test_build_exports_auditable_runtime_layers(tmp_path: Path) -> None:
    """运行时分层应是全画布 PNG，并带角色、轴点和哈希。"""

    source = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    source.save(tmp_path / "part.png")
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(
        tmp_path / "open.png"
    )
    Image.new("RGBA", (20, 20), (0, 0, 255, 255)).save(
        tmp_path / "closed.png"
    )
    roles = (
        "body",
        "arm_left",
        "arm_right",
        "head_open",
        "head_closed",
        "accessory",
    )
    manifest = {
        "schema_version": 1,
        "canvas_size": [64, 80],
        "blink_regions": [[20, 20, 44, 50]],
        "layers": [
            {
                "name": role.upper(),
                "file": (
                    "open.png"
                    if role == "head_open"
                    else "closed.png"
                    if role == "head_closed"
                    else "part.png"
                ),
                "target_bbox": [10, 10, 54, 70],
                "runtime_role": role,
                "pivot": [32, 40],
                "visible": role != "head_closed",
                "preview_visible": role != "head_closed",
            }
            for role in roles
        ],
    }
    manifest_path = tmp_path / "layout.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    runtime = tmp_path / "runtime-layers"

    report = build(
        manifest_path,
        tmp_path / "model.psd",
        tmp_path / "preview.png",
        runtime,
    )

    runtime_manifest = json.loads(
        (runtime / "layered-idle.json").read_text(encoding="utf-8")
    )
    assert runtime_manifest["canvas_size"] == [64, 80]
    assert runtime_manifest["blink_regions"] == [[20, 20, 44, 50]]
    assert runtime_manifest["closed_head_alignment"] == "localized_feather"
    assert {layer["role"] for layer in runtime_manifest["layers"]} == set(
        roles
    )
    assert all(len(layer["sha256"]) == 64 for layer in report["runtime_layers"]["layers"])
    with Image.open(runtime / "head_open.png") as exported:
        assert exported.size == (64, 80)
    with (
        Image.open(runtime / "head_open.png").convert("RGBA") as opened,
        Image.open(runtime / "head_closed.png").convert("RGBA") as closed,
    ):
        assert closed.getpixel((12, 12)) == opened.getpixel((12, 12))
        assert closed.getpixel((32, 35)) != opened.getpixel((32, 35))
