"""验证 Live2D 配置、固定外观参数、单模型配件装配与 Web 宿主契约。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from onepic_desktop_pet.live2d_profile import (
    DEFAULT_LIVE2D_IDLE_ANIMATION,
    normalize_live2d_idle_animation,
    normalize_live2d_persistent_parameters,
    normalize_live2d_state_map,
)
from onepic_desktop_pet.live2d_view import resolve_live2d_files
from tools.build_pet_workspace import stage_hybrid_runtime


def test_resolve_live2d_files_accepts_local_model_and_optional_blink(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    model = tmp_path / "live2d" / "pet.model3.json"
    blink = tmp_path / "live2d" / "blink.png"
    layers = tmp_path / "live2d" / "layers.json"
    accessories = tmp_path / "live2d" / "accessories.json"
    model.parent.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    model.write_text("{}", encoding="utf-8")
    blink.write_bytes(b"\x89PNG\r\n\x1a\n")
    layers.write_text("{}", encoding="utf-8")
    accessories.write_text("{}", encoding="utf-8")

    files = resolve_live2d_files(
        manifest,
        {
            "model": "live2d/pet.model3.json",
            "blink_overlay": "live2d/blink.png",
            "layer_manifest": "live2d/layers.json",
            "accessory_manifest": "live2d/accessories.json",
        },
    )

    assert files.model == model.resolve()
    assert files.blink_overlay == blink.resolve()
    assert files.layer_manifest == layers.resolve()
    assert files.accessory_manifest == accessories.resolve()


def test_resolve_live2d_files_rejects_path_escape(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    manifest = runtime / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.model3.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="不能离开"):
        resolve_live2d_files(manifest, {"model": "../outside.model3.json"})


def test_live2d_idle_animation_uses_stable_defaults() -> None:
    profile = normalize_live2d_idle_animation(None)

    assert profile == DEFAULT_LIVE2D_IDLE_ANIMATION
    assert profile is not DEFAULT_LIVE2D_IDLE_ANIMATION
    assert profile["blink_interval_ms"] is not (
        DEFAULT_LIVE2D_IDLE_ANIMATION["blink_interval_ms"]
    )


def test_live2d_idle_animation_accepts_safe_overrides() -> None:
    profile = normalize_live2d_idle_animation(
        {
            "intensity": 1.25,
            "display_scale": 1.67,
            "display_offset_y_ratio": 0.08,
            "blink_interval_ms": [2200, 4200],
            "blink_duration_ms": [130, 160, 190],
            "blink_parameter_mode": "binary",
            "hair_motion": False,
        }
    )

    assert profile["intensity"] == 1.25
    assert profile["display_scale"] == 1.67
    assert profile["display_offset_y_ratio"] == 0.08
    assert profile["blink_interval_ms"] == [2200, 4200]
    assert profile["blink_duration_ms"] == [130, 160, 190]
    assert profile["blink_parameter_mode"] == "binary"
    assert profile["hair_motion"] is False
    assert profile["head_motion"] is True


def test_live2d_idle_animation_accepts_expressive_slow_blink() -> None:
    profile = normalize_live2d_idle_animation(
        {
            "blink_interval_ms": [15000, 18000],
            "blink_duration_ms": [700, 1000, 1200],
        }
    )

    assert profile["blink_interval_ms"] == [15000, 18000]
    assert profile["blink_duration_ms"] == [700, 1000, 1200]


def test_live2d_persistent_parameters_accepts_finite_model_values() -> None:
    parameters = normalize_live2d_persistent_parameters(
        {
            "Param35": 1,
            "Param9": 1.0,
            "Param21": 1,
            "Param31": 1,
        }
    )

    assert parameters == {
        "Param35": 1.0,
        "Param9": 1.0,
        "Param21": 1.0,
        "Param31": 1.0,
    }


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"": 1},
        {"Param35": True},
        {"Param35": float("inf")},
    ],
)
def test_live2d_persistent_parameters_rejects_invalid_values(
    value: object,
) -> None:
    with pytest.raises(ValueError):
        normalize_live2d_persistent_parameters(value)


@pytest.mark.parametrize(
    "value",
    [
        {"intensity": 2.5},
        {"display_scale": 1.81},
        {"display_offset_y_ratio": 0.21},
        {"blink_interval_ms": [500, 1200]},
        {"blink_duration_ms": [20, 500, 900]},
        {"blink_interval_ms": [15000, 61000]},
        {"blink_duration_ms": [1500, 2000, 2000]},
        {"blink": "yes"},
        {"blink_parameter_mode": "stepped"},
        {"unknown": True},
    ],
)
def test_live2d_idle_animation_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_live2d_idle_animation(value)


def test_live2d_state_map_accepts_expression_cycles_and_sleep_parameters() -> None:
    state_map = normalize_live2d_state_map(
        {
            "idle": {},
            "sleep": {
                "paused": True,
                "parameters": {
                    "ParamEyeLOpen": 0,
                    "ParamEyeROpen": 0,
                },
            },
            "extension:expressions": {
                "expressions": ["blush", "angry"],
            },
        }
    )

    assert state_map["idle"] == {}
    assert state_map["sleep"]["paused"] is True
    assert state_map["sleep"]["parameters"]["ParamEyeLOpen"] == 0.0
    assert state_map["extension:expressions"]["expressions"] == [
        "blush",
        "angry",
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"idle": {"expression": "a", "expressions": ["b"]}},
        {"idle": {"paused": "yes"}},
        {"idle": {"parameters": {"ParamX": float("nan")}}},
        {"idle": {"motion": {"group": "Idle", "no": -1}}},
        {"idle": {"script": "alert(1)"}},
    ],
)
def test_live2d_state_map_rejects_unsafe_values(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_live2d_state_map(value)


def test_stage_hybrid_runtime_is_minimal_and_adds_compatibility_keys(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    texture = source / "pet.2048" / "texture_00.png"
    texture.parent.mkdir(parents=True)
    (source / "pet.moc3").write_bytes(b"moc")
    texture.write_bytes(b"png")
    (source / "unused-private-export.moc3").write_bytes(b"unused")
    (source / "pet.model3.json").write_text(
        """{
          "Version": 3,
          "FileReferences": {
            "Moc": "pet.moc3",
            "Textures": ["pet.2048/texture_00.png"]
          }
        }""",
        encoding="utf-8",
    )
    layer_root = source / "layers"
    layer_root.mkdir()
    layer_roles = (
        "body",
        "arm_left",
        "arm_right",
        "head_open",
        "head_closed",
        "accessory",
    )
    for role in layer_roles:
        Image.new("RGBA", (16, 24), (255, 255, 255, 255)).save(
            layer_root / f"{role}.png"
        )
    (layer_root / "layered-idle.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "canvas_size": [16, 24],
                "layers": [
                    {
                        "role": role,
                        "file": f"{role}.png",
                        "pivot": [8, 12],
                    }
                    for role in layer_roles
                ],
            }
        ),
        encoding="utf-8",
    )
    blink = workspace / "authoring" / "blink.png"
    blink.parent.mkdir()
    blink.write_bytes(b"blink")
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "layer_manifest_file": "layers/layered-idle.json",
            "blink_overlay_file": "authoring/blink.png",
            "load_timeout_ms": 45_000,
            "idle_animation": {
                "intensity": 1.25,
                "blink_interval_ms": [2200, 4200],
            },
            "persistent_parameters": {"Param35": 1},
            "sprite_states": ["walk"],
            "fallback_backend": "sprite",
        }
    }
    stage = tmp_path / "stage"

    manifest, qa = stage_hybrid_runtime(workspace, config, stage)

    staged_model = json.loads(
        (stage / "live2d" / "pet.model3.json").read_text(encoding="utf-8")
    )
    assert staged_model["Groups"] == []
    assert staged_model["HitAreas"] == []
    assert not (stage / "live2d" / "unused-private-export.moc3").exists()
    assert (stage / "live2d" / "blink-overlay.png").read_bytes() == b"blink"
    assert manifest and manifest["model"] == "live2d/pet.model3.json"
    assert (
        manifest["layer_manifest"]
        == "live2d/layers/layered-idle.json"
    )
    assert manifest["idle_animation"]["intensity"] == 1.25
    assert manifest["idle_animation"]["blink"] is True
    assert manifest["persistent_parameters"] == {"Param35": 1.0}
    assert manifest["live2d_state_map"] == {}
    assert manifest["load_timeout_ms"] == 45_000
    assert qa["status"] == "passed"
    assert qa["render_backend"] == "layered-webgl"
    assert qa["compatibility_defaults_added"] == ["Groups", "HitAreas"]


def test_stage_hybrid_runtime_copies_independent_cubism_accessories(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    texture = source / "pet.2048" / "texture_00.png"
    texture.parent.mkdir(parents=True)
    (source / "pet.moc3").write_bytes(b"moc")
    texture.write_bytes(b"png")
    (source / "pet.model3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "pet.moc3",
                    "Textures": ["pet.2048/texture_00.png"],
                },
            }
        ),
        encoding="utf-8",
    )
    accessory_root = source / "accessories"
    accessory_root.mkdir()
    Image.new("RGBA", (12, 8), (255, 128, 128, 255)).save(
        accessory_root / "ears.png"
    )
    Image.new("RGBA", (8, 6), (255, 180, 180, 255)).save(
        accessory_root / "nose.png"
    )
    (accessory_root / "piggy-accessories.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "layers": [
                    {
                        "role": "pig_ears",
                        "file": "ears.png",
                        "position_ratio": [0.5, 0.18],
                        "width_ratio": 0.42,
                        "rotation_degrees": 0,
                    },
                    {
                        "role": "pig_nose",
                        "file": "nose.png",
                        "position_ratio": [0.5, 0.32],
                        "width_ratio": 0.16,
                        "rotation_degrees": 2.5,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "accessory_manifest_file": (
                "accessories/piggy-accessories.json"
            ),
            "fallback_backend": "sprite",
        },
    }
    stage = tmp_path / "stage"

    manifest, qa = stage_hybrid_runtime(workspace, config, stage)

    assert manifest
    assert manifest["layer_manifest"] is None
    assert (
        manifest["accessory_manifest"]
        == "live2d/accessories/piggy-accessories.json"
    )
    assert (stage / "live2d" / "accessories" / "ears.png").is_file()
    assert (stage / "live2d" / "accessories" / "nose.png").is_file()
    assert qa["render_backend"] == "cubism-parameters"
    assert qa["accessory_roles"] == ["pig_ears", "pig_nose"]


def test_stage_hybrid_runtime_locks_same_model_part_mesh_accessories(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    source.mkdir(parents=True)
    moc_bytes = b"bound-moc"
    (source / "pet.moc3").write_bytes(moc_bytes)
    (source / "texture.png").write_bytes(b"texture")
    cdi = {
        "Version": 3,
        "Parts": [
            {"Id": "Part58", "Name": "脸"},
            {"Id": "Part56", "Name": "鼻子"},
        ],
    }
    cdi_bytes = (json.dumps(cdi, ensure_ascii=False) + "\n").encode("utf-8")
    (source / "pet.cdi3.json").write_bytes(cdi_bytes)
    (source / "pet.model3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "pet.moc3",
                    "Textures": ["texture.png"],
                    "DisplayInfo": "pet.cdi3.json",
                },
            }
        ),
        encoding="utf-8",
    )
    accessory_root = source / "accessories"
    accessory_root.mkdir()
    Image.new("RGBA", (16, 10), (255, 150, 170, 255)).save(
        accessory_root / "ears.png"
    )
    Image.new("RGBA", (10, 6), (255, 180, 190, 255)).save(
        accessory_root / "nose.png"
    )
    (accessory_root / "bound-accessories.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "render_layer": "same_model_overlay",
                "binding": {
                    "mode": "cubism_part_mesh_affine",
                    "head_part_id": "Part58",
                    "nose_part_id": "Part56",
                    "model_moc_sha256": hashlib.sha256(moc_bytes).hexdigest(),
                    "cdi_file": "pet.cdi3.json",
                    "cdi_sha256": hashlib.sha256(cdi_bytes).hexdigest(),
                },
                "layers": [
                    {
                        "role": "pig_ears",
                        "file": "ears.png",
                        "position_ratio": [0.5, 0.18],
                        "width_ratio": 0.42,
                        "rotation_degrees": 0,
                        "pin_to": "head",
                    },
                    {
                        "role": "pig_nose",
                        "file": "nose.png",
                        "position_ratio": [0.5, 0.32],
                        "width_ratio": 0.16,
                        "rotation_degrees": 0,
                        "pin_to": "nose",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "accessory_manifest_file": "accessories/bound-accessories.json",
        },
    }

    manifest, qa = stage_hybrid_runtime(workspace, config, tmp_path / "stage")

    assert manifest
    assert manifest["accessory_manifest"] == (
        "live2d/accessories/bound-accessories.json"
    )
    assert qa["accessory_binding_mode"] == "cubism_part_mesh_affine"
    assert qa["accessory_render_layer"] == "same_model_overlay"
    assert qa["accessory_roles"] == ["pig_ears", "pig_nose"]
    assert (tmp_path / "stage" / "live2d" / "pet.cdi3.json").is_file()


@pytest.mark.parametrize(
    "layers, error",
    [
        (
            [
                {
                    "role": "pig_ears",
                    "file": "ears.png",
                    "position_ratio": [0.5, 0.2],
                    "width_ratio": 0.4,
                    "rotation_degrees": 0,
                },
                {
                    "role": "pig_ears",
                    "file": "ears.png",
                    "position_ratio": [0.5, 0.2],
                    "width_ratio": 0.4,
                    "rotation_degrees": 0,
                },
            ],
            "layers\\[1\\] 无效",
        ),
        (
            [
                {
                    "role": "pig_ears",
                    "file": "ears.png",
                    "position_ratio": [0.5],
                    "width_ratio": 0.4,
                    "rotation_degrees": 0,
                }
            ],
            "layers\\[0\\] 无效",
        ),
    ],
)
def test_stage_hybrid_runtime_rejects_invalid_accessory_layers(
    tmp_path: Path,
    layers: list[dict[str, object]],
    error: str,
) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    source.mkdir(parents=True)
    (source / "pet.moc3").write_bytes(b"moc")
    (source / "texture.png").write_bytes(b"texture")
    (source / "pet.model3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "pet.moc3",
                    "Textures": ["texture.png"],
                },
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(
        source / "ears.png"
    )
    (source / "accessories.json").write_text(
        json.dumps({"schema_version": 1, "layers": layers}),
        encoding="utf-8",
    )
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "accessory_manifest_file": "accessories.json",
        },
    }

    with pytest.raises(ValueError, match=error):
        stage_hybrid_runtime(workspace, config, tmp_path / "stage")


def test_stage_hybrid_runtime_rejects_non_rgba_accessory(tmp_path: Path) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    source.mkdir(parents=True)
    (source / "pet.moc3").write_bytes(b"moc")
    (source / "texture.png").write_bytes(b"texture")
    (source / "pet.model3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "pet.moc3",
                    "Textures": ["texture.png"],
                },
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGB", (8, 8), (255, 255, 255)).save(source / "ears.png")
    (source / "accessories.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "layers": [
                    {
                        "role": "pig_ears",
                        "file": "ears.png",
                        "position_ratio": [0.5, 0.2],
                        "width_ratio": 0.4,
                        "rotation_degrees": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "accessory_manifest_file": "accessories.json",
        },
    }

    with pytest.raises(ValueError, match="有效 RGBA PNG"):
        stage_hybrid_runtime(workspace, config, tmp_path / "stage")


def test_stage_hybrid_runtime_rejects_accessory_path_escape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "pet"
    source = workspace / "live2d-runtime"
    source.mkdir(parents=True)
    (source / "pet.moc3").write_bytes(b"moc")
    (source / "texture.png").write_bytes(b"texture")
    (source / "pet.model3.json").write_text(
        json.dumps(
            {
                "Version": 3,
                "FileReferences": {
                    "Moc": "pet.moc3",
                    "Textures": ["texture.png"],
                },
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(
        workspace / "outside.png"
    )
    (source / "accessories.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "layers": [
                    {
                        "role": "pig_ears",
                        "file": "../outside.png",
                        "position_ratio": [0.5, 0.2],
                        "width_ratio": 0.4,
                        "rotation_degrees": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = {
        "rendering_mode": "hybrid-live2d",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
            "source_directory": "live2d-runtime",
            "model_file": "pet.model3.json",
            "accessory_manifest_file": "accessories.json",
        },
    }

    with pytest.raises(FileNotFoundError, match="缺失或越界"):
        stage_hybrid_runtime(workspace, config, tmp_path / "stage")


def test_web_host_binds_accessories_to_single_cubism_model() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "live2d_web"
        / "src"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert 'params.get("accessories")' in source
    assert 'params.get("persistent_parameters")' in source
    assert "model.getBounds()" in source
    assert "loadCubismAccessories" in source
    assert 'root.dataset.renderLayer = "same-model-cubism-bound-overlay"' in source
    assert "new Image()" in source
    assert "selectCubismPartVertices" in source
    assert "sampleCubismPartVertices" in source
    assert "solveMeshAffine" in source
    assert "detectCubismContentBounds" in source
    assert "describeCubismAccessories(model, cubismAccessories)" in source
    assert "rawModelBounds" in source
    assert "getBoundingClientRect" in source
    assert "app.stage.addChild(cubismAccessories.root)" not in source
    assert "document.body.appendChild(cubismAccessories.root)" in source
    assert source.count("new Live2DSprite") == 1
    assert "model.onRender = (renderer)" in source
    assert "layerManifestUrl && accessoryManifestUrl" in source
    assert 'dataset.live2dReady = "false"' in source
    assert "model.getExpressions()" in source
    assert "model.getMotions()" in source
    assert "model.setExpression({ expressionId })" in source
    assert "model.startMotion({" in source
    assert "getParameterRange(id)" in source
    assert "renderNow:" in source
    assert "activeExpression: activeExpressionId" in source
    assert "renderedPresentationRevision" in source
    assert "applyPersistentParameters" in source
    assert "persistentParameterValues" in source
    assert "sampleLivelyGaze" in source
    assert "gazeMoveCount" in source
    assert "earPairCenter" in source
    assert "pairMidpointX" in source


def test_sprite_route_rejects_accidental_hybrid_runtime(tmp_path: Path) -> None:
    """默认基础款即使被误塞 hybrid_runtime 也必须停止，而不是静默启用。"""

    config = {
        "rendering_mode": "sprite",
        "hybrid_runtime": {
            "enabled": True,
            "idle_backend": "live2d",
        },
    }

    with pytest.raises(ValueError, match="基础款"):
        stage_hybrid_runtime(tmp_path, config, tmp_path / "stage")


def test_explicit_live2d_route_requires_complete_runtime(tmp_path: Path) -> None:
    """显式选择混合路线后，缺少运行配置应在 staging 前失败。"""

    with pytest.raises(ValueError, match="需要完整"):
        stage_hybrid_runtime(
            tmp_path,
            {"rendering_mode": "hybrid-live2d"},
            tmp_path / "stage",
        )
