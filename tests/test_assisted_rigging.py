"""
验证辅助绑定实验的确定性 PSD、隐私边界与 Stretchy 语义标签契约。

测试只使用临时生成的透明几何角色，不读取 `user_assets`、不下载模型、不启动
浏览器，也不修改当前宠物运行目录。真实 See-through 拆层质量仍需单独视觉验收。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from psd_tools import PSDImage

from tools.assisted_rigging import (
    AssistedRiggingError,
    prepare_proxy_psd,
    validate_layered_psd,
)


def _demo_character(path: Path) -> None:
    image = Image.new("RGBA", (160, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 12, 112, 80), fill=(245, 205, 180, 255))
    draw.rounded_rectangle((38, 72, 122, 158), 16, fill=(70, 95, 160, 255))
    draw.rounded_rectangle((18, 78, 48, 158), 10, fill=(70, 95, 160, 255))
    draw.rounded_rectangle((112, 78, 142, 158), 10, fill=(70, 95, 160, 255))
    draw.rectangle((48, 150, 112, 178), fill=(45, 55, 85, 255))
    draw.rounded_rectangle((48, 170, 76, 224), 8, fill=(225, 205, 185, 255))
    draw.rounded_rectangle((84, 170, 112, 224), 8, fill=(225, 205, 185, 255))
    draw.ellipse((42, 214, 78, 234), fill=(40, 40, 48, 255))
    draw.ellipse((82, 214, 118, 234), fill=(40, 40, 48, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def test_prepare_proxy_psd_roundtrips_without_pixel_loss(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "assets" / "demo" / "character.png"
    output = root / ".tmp" / "assisted-rigging" / "demo"
    _demo_character(source)

    report = prepare_proxy_psd(source, output, project_root=root)

    assert report["artifact_kind"] == "pipeline-smoke-proxy-psd"
    assert report["output"]["recognized_layer_count"] >= 4
    assert report["output"]["roundtrip_alpha_delta"] == 0
    assert report["output"]["roundtrip_visible_rgb_delta"] <= 1
    assert report["privacy"] == {
        "user_assets_read": False,
        "absolute_paths_recorded": False,
        "runtime_modified": False,
    }
    psd_path = output / "onepic-proxy-rig.psd"
    assert psd_path.is_file()
    assert (output / "assisted-rigging-report.json").is_file()
    assert PSDImage.open(psd_path).size == (160, 240)

    validation = validate_layered_psd(psd_path)
    assert validation["recognized_layer_count"] >= 4
    assert validation["pixel_layer_count"] >= 4


def test_prepare_proxy_psd_rejects_user_assets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "user_assets" / "pets" / "private" / "source.png"
    _demo_character(source)

    with pytest.raises(AssistedRiggingError, match="user_assets"):
        prepare_proxy_psd(
            source,
            root / ".tmp" / "assisted-rigging",
            project_root=root,
        )


@pytest.mark.parametrize("protected", ["assets/pet", "dist", "user_assets"])
def test_prepare_proxy_psd_rejects_protected_outputs(
    tmp_path: Path,
    protected: str,
) -> None:
    root = tmp_path / "project"
    source = root / "assets" / "demo" / "character.png"
    _demo_character(source)

    with pytest.raises(AssistedRiggingError, match="受保护目录"):
        prepare_proxy_psd(
            source,
            root / protected / "assisted-rigging",
            project_root=root,
        )


def test_validate_layered_psd_rejects_unknown_layers(tmp_path: Path) -> None:
    psd = PSDImage.new("RGBA", (32, 48), color=(0, 0, 0, 0))
    PixelLayer = pytest.importorskip("psd_tools.api.layers").PixelLayer
    PixelLayer.frompil(
        Image.new("RGBA", (20, 30), (30, 60, 90, 255)),
        parent=psd,
        name="unknown",
        top=4,
        left=6,
    )
    path = tmp_path / "unknown.psd"
    psd.save(path)

    with pytest.raises(AssistedRiggingError, match="至少需要 4 个"):
        validate_layered_psd(path)
