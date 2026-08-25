"""
本模块验证声明式配方、动作语义隔离、视觉质量契约、发布运行默认值、双击聊天
跨渲染路线与打包 EXE 烟测、证据/成品树哈希绑定、最小审批序列化、最终 build
验收、隐私模式与 ZIP 完整性。

测试只在项目测试沙箱生成占位文件，不读取真实 user_assets、不运行 PyInstaller，
也不访问网络。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

import main as desktop_main
from onepic_desktop_pet.recipe import RecipeError, load_recipe, validate_recipe
from onepic_desktop_pet.packaged_smoke import (
    distribution_tree_sha256 as app_distribution_tree_sha256,
)
from onepic_desktop_pet.workflow import interaction_is_approved
from tools import build_pet_workspace, create_package_workflow, release_pet


def approved_interaction_workflow() -> dict[str, object]:
    """返回同时绑定预览与标准角色哈希的最小互动审批状态。"""

    character_hash = "a" * 64
    preview_hash = "b" * 64
    return {
        "version": 2,
        "pet_id": "static-pet",
        "character": {
            "status": "approved",
            "user_confirmed": True,
            "candidate_sha256": character_hash,
            "approved_candidate_sha256": character_hash,
        },
        "interaction": {
            "status": "approved",
            "user_confirmed": True,
            "mode": "stationary_character",
            "approved_mode": "stationary_character",
            "preview_sha256": preview_hash,
            "approved_asset_sha256": preview_hash,
            "character_sha256": character_hash,
            "approved_character_sha256": character_hash,
        },
    }


def test_minimal_release_workflow_preserves_interaction_character_binding() -> None:
    """发布准备不能丢弃互动审批依赖的标准角色哈希。"""

    packaged = release_pet._minimal_workflow(approved_interaction_workflow())

    assert interaction_is_approved(packaged) is True
    assert packaged["interaction"]["character_sha256"] == "a" * 64


def test_minimal_release_workflow_matches_builder_walk_precedence() -> None:
    """双批准异常状态也必须与构建器一致，优先保留实际采用的走路门禁。"""

    state = approved_interaction_workflow()
    state["walk"] = {
        "status": "approved",
        "user_confirmed": True,
        "asset_sha256": "c" * 64,
        "approved_asset_sha256": "c" * 64,
    }

    packaged = release_pet._minimal_workflow(state)

    assert "walk" in packaged
    assert "interaction" not in packaged


def test_legacy_packaging_cli_preserves_interaction_character_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """旧的独立最小化工具也必须生成可通过运行门禁的互动状态。"""

    source = tmp_path / "workflow.json"
    output = tmp_path / "packaged-workflow.json"
    source.write_text(
        json.dumps(approved_interaction_workflow(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_package_workflow.py",
            "--source",
            str(source),
            "--output",
            str(output),
        ],
    )

    assert create_package_workflow.main() == 0
    packaged = json.loads(output.read_text(encoding="utf-8"))
    assert interaction_is_approved(packaged) is True


def test_privacy_scan_exempts_vendor_source_folder_but_not_user_assets(
    tmp_path: Path,
) -> None:
    vendor = (
        tmp_path
        / "_internal"
        / "PySide6"
        / "qml"
        / "designer"
        / "source"
        / "template.qml"
    )
    vendor.parent.mkdir(parents=True)
    vendor.write_text("Item {}", encoding="utf-8")

    passed = release_pet.privacy_scan(tmp_path)

    assert passed["status"] == "passed"

    private = (
        tmp_path
        / "_internal"
        / "user_assets"
        / "pets"
        / "victim"
        / "source"
        / "original.png"
    )
    private.parent.mkdir(parents=True)
    private.write_bytes(b"private")

    failed = release_pet.privacy_scan(tmp_path)

    assert failed["status"] == "failed"
    assert any("禁止的私密目录名" in item for item in failed["failures"])


def test_distribution_fingerprint_matches_inside_and_outside_packaged_app(
    tmp_path: Path,
) -> None:
    """成品内烟测与外部 finalizer 必须使用完全相同的目录指纹算法。"""

    (tmp_path / "_internal").mkdir()
    (tmp_path / "OnePicDesktopPet.exe").write_bytes(b"MZ-test")
    (tmp_path / "_internal" / "code.pyc").write_bytes(b"bytecode")

    assert release_pet.distribution_tree_sha256(tmp_path) == (
        app_distribution_tree_sha256(tmp_path)
    )


def test_basic_packaged_smoke_report_binds_tree_pet_and_preparation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """成品启动烟测报告应由运行中的 EXE 自己记录完整绑定证据。"""

    distribution = tmp_path / "OnePicDesktopPet"
    internal = distribution / "_internal"
    internal.mkdir(parents=True)
    executable = distribution / "OnePicDesktopPet.exe"
    executable.write_bytes(b"MZ-running")
    manifest = internal / "manifest.json"
    pointer = internal / "active-pet.json"
    manifest.write_text(
        json.dumps({"pet_id": "bound-pet", "build_id": "bound-build"}),
        encoding="utf-8",
    )
    pointer.write_text(
        json.dumps({"preparation_id": "prep-bound-001"}),
        encoding="utf-8",
    )
    resources = {
        "user_assets/pet/manifest.json": manifest,
        "user_assets/active-pet.json": pointer,
    }
    monkeypatch.setattr(
        desktop_main,
        "resource_path",
        lambda value: resources[str(value).replace("\\", "/")],
    )
    monkeypatch.setattr(desktop_main.sys, "executable", str(executable))
    monkeypatch.setattr(desktop_main.sys, "frozen", True, raising=False)
    output = tmp_path / "evidence" / "startup.json"

    assert desktop_main._write_basic_smoke_report(
        output,
        exit_code=0,
        expected_pet_id="bound-pet",
        smoke_test_ms=100,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["actual_pet_id"] == "bound-pet"
    assert report["build_id"] == "bound-build"
    assert report["runtime_manifest_sha256"] == release_pet.file_sha256(
        manifest
    )
    assert report["preparation_id"] == "prep-bound-001"
    assert report["executable_sha256"] == release_pet.file_sha256(executable)
    assert report["distribution_sha256"] == (
        release_pet.distribution_tree_sha256(distribution)
    )


def test_privacy_scan_rejects_companion_database_and_dpapi_ciphertext(
    tmp_path: Path,
) -> None:
    """陪伴历史和密钥密文即使被误复制也不得进入发布包。"""

    (tmp_path / "companion.sqlite3").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "companion-api-key.dpapi").write_bytes(b"ciphertext")

    result = release_pet.privacy_scan(tmp_path)

    assert result["status"] == "failed"
    assert len(result["failures"]) == 3


def test_privacy_scan_rejects_any_windows_absolute_path_and_disguised_data(
    tmp_path: Path,
) -> None:
    """非 Users/Documents 盘符路径与改后缀私密数据也必须失败。"""

    (tmp_path / "paths.json").write_text(
        json.dumps(
            {
                "first": r"D:\Private\client\profile.json",
                "second": r"C:\Temp\onepic\memory.db",
                "safe_url": "https://example.test/v1",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "renamed-cache.bin").write_bytes(
        b"SQLite format 3\x00" + b"\x00" * 32
    )
    (tmp_path / "renamed-secret.bin").write_bytes(
        b"ONEPIC-DPAPI\x01" + b"ciphertext"
    )

    result = release_pet.privacy_scan(tmp_path)

    assert result["status"] == "failed"
    assert any("本机绝对路径" in failure for failure in result["failures"])
    assert any("SQLite 数据库文件头" in failure for failure in result["failures"])
    assert any("DPAPI 密文文件头" in failure for failure in result["failures"])


def test_release_settings_require_disabled_empty_movement_region(
    tmp_path: Path,
) -> None:
    """分享构建必须提供区域入口，但禁止预置制作者开关和屏幕坐标。"""

    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "movement_region_enabled": False,
                "movement_region": None,
            }
        ),
        encoding="utf-8",
    )
    assert release_pet.validate_release_settings(settings)["status"] == "passed"

    settings.write_text(
        json.dumps(
            {
                "movement_region_enabled": True,
                "movement_region": [100, 100, 800, 500],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="默认开关"):
        release_pet.validate_release_settings(settings)

    settings.write_text(
        json.dumps(
            {
                "movement_region_enabled": False,
                "movement_region": [100, 100, 800, 500],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="活动区域坐标"):
        release_pet.validate_release_settings(settings)


def _approved_workflow(pet_id: str) -> dict[str, object]:
    """返回与素材哈希绑定的最小测试审批状态。"""

    return {
        "version": 2,
        "pet_id": pet_id,
        "character": {
            "status": "approved",
            "user_confirmed": True,
            "candidate_sha256": "a" * 64,
            "approved_candidate_sha256": "a" * 64,
        },
        "walk": {
            "status": "approved",
            "user_confirmed": True,
            "asset_sha256": "b" * 64,
            "approved_asset_sha256": "b" * 64,
        },
    }


def _release_workspace(root: Path, pet_id: str) -> Path:
    """创建最小的已 QA 工作区。"""

    workspace = root / pet_id
    (workspace / "runtime").mkdir(parents=True)
    (workspace / "review").mkdir()
    (workspace / "source").mkdir()
    config = {
        "schema_version": 2,
        "pet_id": pet_id,
        "display_name": "测试宠物",
        "canvas_size": [560, 500],
        "character_file": "candidate/approved.png",
        "selfie_file": "source/selfie.png",
        "actions_directory": "actions/transparent",
        "walk_directory": "walk/frames",
        "runtime_directory": "runtime",
        "review_directory": "review",
    }
    (workspace / "pet-config.json").write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "workflow.json").write_text(
        json.dumps(_approved_workflow(pet_id)),
        encoding="utf-8",
    )
    (workspace / "runtime" / "manifest.json").write_text(
        json.dumps(
            {
                "pet_id": pet_id,
                "animations": {},
                "base_frame_count": 38,
                "optional_touch_frame_count": 0,
                "total_frame_count": 38,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "runtime" / "frame.png").write_bytes(b"runtime")
    (workspace / "review" / "assets-qa.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "base_frame_count": 38,
                "optional_touch_frame_count": 0,
                "extension_frame_count": 33,
                "directional_frame_count": 0,
                "total_frame_count": 38,
                "total_asset_frame_count": 71,
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGB", (32, 32), "orange").save(
        workspace / "source" / "selfie.png"
    )
    return workspace


def _packaged_release_fixture(
    root: Path,
    *,
    pet_id: str = "guard-pet",
    mode: str = "share-no-selfie",
) -> tuple[Path, Path]:
    """创建含包内身份、runtime 指纹和准备报告的最小成品目录。"""

    distribution = root / "OnePicDesktopPet"
    distribution.mkdir()
    (distribution / "OnePicDesktopPet.exe").write_bytes(b"MZ-test")
    config_root = distribution / "_internal" / "config"
    config_root.mkdir(parents=True)
    (config_root / "settings.json").write_text(
        json.dumps(
            {"movement_region_enabled": False, "movement_region": None}
        ),
        encoding="utf-8",
    )
    user_root = distribution / "_internal" / "user_assets"
    runtime = user_root / "pet"
    runtime.mkdir(parents=True)
    (runtime / "frame.png").write_bytes(b"approved-runtime")
    runtime_fingerprint = release_pet.runtime_asset_bundle_sha256(runtime)
    application_fingerprint = release_pet.application_build_evidence()[
        "fingerprint"
    ]
    manifest = {
        "pet_id": pet_id,
        "build_id": "guard-build",
        "build_input_fingerprint": "a" * 64,
        "application_fingerprint": application_fingerprint,
        "runtime_asset_fingerprint": runtime_fingerprint,
        "rendering_mode": "sprite",
    }
    manifest_path = runtime / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    preparation_id = "prep-guard-001"
    pointer_path = user_root / "active-pet.json"
    pointer_path.write_text(
        json.dumps(
            {
                "pet_id": pet_id,
                "workflow": f"pets/{pet_id}/workflow.json",
                "package_mode": mode,
                "preparation_id": preparation_id,
            }
        ),
        encoding="utf-8",
    )
    workflow = user_root / "pets" / pet_id / "workflow.json"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(json.dumps(_approved_workflow(pet_id)), encoding="utf-8")
    include_selfie = mode in {"private-local", "share-with-approved-selfie"}
    selfie_sha256 = None
    if include_selfie:
        selfie = user_root / "selfie.png"
        selfie.write_bytes(b"approved-selfie")
        selfie_sha256 = release_pet.file_sha256(selfie)
    preparation = root / "package-preparation.json"
    preparation.write_text(
        json.dumps(
            {
                "schema_version": release_pet.PREPARATION_SCHEMA_VERSION,
                "preparation_id": preparation_id,
                "prepared_at": "2026-08-23T00:00:00+00:00",
                "pet_id": pet_id,
                "mode": mode,
                "selfie_included": include_selfie,
                "selfie_sha256": selfie_sha256,
                "qa_sha256": "q" * 64,
                "active_pointer_sha256": release_pet.file_sha256(pointer_path),
                "packaged_workflow_sha256": release_pet.file_sha256(workflow),
                "runtime_review": {
                    "build_id": "guard-build",
                    "build_input_fingerprint": "a" * 64,
                    "application_fingerprint": application_fingerprint,
                    "runtime_asset_fingerprint": runtime_fingerprint,
                    "review_visual_fingerprint": "v" * 64,
                    "runtime_manifest_sha256": release_pet.file_sha256(
                        manifest_path
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    return distribution, preparation


def _release_evidence_arguments(
    root: Path,
    distribution: Path,
    pet_id: str,
    preparation_id: str,
) -> dict[str, Path]:
    """创建与当前测试 EXE、pet-id 和 preparation 绑定的 JSON 证据。"""

    evidence_root = root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    executable_sha256 = release_pet.file_sha256(
        distribution / "OnePicDesktopPet.exe"
    )
    distribution_sha256 = release_pet.distribution_tree_sha256(distribution)
    runtime_manifest = (
        distribution / "_internal" / "user_assets" / "pet" / "manifest.json"
    )
    runtime_manifest_data = json.loads(
        runtime_manifest.read_text(encoding="utf-8")
    )
    runtime_evidence = {
        "build_id": runtime_manifest_data["build_id"],
        "runtime_manifest_sha256": release_pet.file_sha256(runtime_manifest),
    }
    payloads = {
        "tests_report": {
            "status": "passed",
            "unit_only": True,
            "pytest_exit_code": 0,
        },
        "source_smoke_report": {
            "status": "passed",
            "exit_code": 0,
            "frozen": False,
            "expected_pet_id": pet_id,
            "actual_pet_id": pet_id,
        },
        "packaged_smoke_report": {
            "status": "passed",
            "exit_code": 0,
            "frozen": True,
            "expected_pet_id": pet_id,
            "actual_pet_id": pet_id,
            "preparation_id": preparation_id,
            "executable_sha256": executable_sha256,
            "distribution_sha256": distribution_sha256,
        },
        "interaction_menu_smoke_report": {
            "status": "passed",
            "pet_id": pet_id,
        },
        "double_click_chat_smoke_report": {
            "status": "passed",
            "pet_id": pet_id,
        },
        "packaged_double_click_chat_smoke_report": {
            "status": "passed",
            "frozen": True,
            "expected_pet_id": pet_id,
            "actual_pet_id": pet_id,
            "preparation_id": preparation_id,
            "executable_sha256": executable_sha256,
            "distribution_sha256": distribution_sha256,
            "manifest_is_packaged_customer_runtime": True,
        },
    }
    for name, payload in payloads.items():
        if name != "tests_report":
            payload.update(runtime_evidence)
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        payload["schema_version"] = release_pet.EVIDENCE_SCHEMA_VERSION
        path = evidence_root / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    return paths


def test_recipe_declares_38_base_plus_two_optional_touch_slots() -> None:
    """默认配方应固定 38 帧基础，并为新宠物默认声明两张过渡帧。"""

    recipe = load_recipe()
    assert recipe["base_frame_count"] == 38
    assert sum(item["count"] for item in recipe["animations"].values()) == 38
    touch = recipe["touch_feedback"]
    assert touch["maximum_optional_frames"] == 2
    assert touch["default_for_new_pets"] == 2
    assert touch["recommended_for_new_pets"] == 2
    assert recipe["animations"]["curious"]["reuse"] == "happy"
    assert recipe["animations"]["curious"]["frames"][0]["source"] == "happy"


def test_recipe_rejects_changed_base_count() -> None:
    recipe = load_recipe()
    changed = json.loads(json.dumps(recipe))
    changed["animations"]["idle"]["count"] = 5
    with pytest.raises(RecipeError, match="frames 数量"):
        validate_recipe(changed)


def test_alpha_mass_contract_reports_exact_failing_frame(tmp_path: Path) -> None:
    """超阈值时 QA 必须指出具体帧、偏差和阈值。"""

    runtime = tmp_path / "runtime"
    frame = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle((20, 20, 79, 79), fill="black")
    (runtime / "idle").mkdir(parents=True)
    frame.save(runtime / "idle" / "idle_01.png")
    report = build_pet_workspace.build_quality_report(
        {"animations": {"idle": ["idle/idle_01.png"]}},
        runtime,
        canvas_size=(100, 100),
        expected_masses={"idle/idle_01.png": 10_000.0},
        visual_mass={"static_tolerance_ratio": 0.1},
    )
    assert report["status"] == "failed"
    assert any(
        "idle/idle_01.png" in failure and "alpha mass 偏差" in failure
        for failure in report["failures"]
    )


def test_walk_qa_rejects_translation_only_cycle() -> None:
    """八张完全相同轮廓仅整体平移时必须失败。"""

    frames = []
    for offset in range(8):
        image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle(
            (40 + offset, 20, 70 + offset, 100),
            fill="black",
        )
        frames.append(image)
    report = build_pet_workspace.build_walk_quality_report(frames, {})
    assert report["status"] == "failed"
    assert any("整体平移" in failure for failure in report["failures"])


def test_prepare_share_no_selfie_excludes_private_photo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _release_workspace(tmp_path / "pets", "safe-pet")
    monkeypatch.setattr(release_pet, "workspace_path", lambda _pet_id: workspace)
    monkeypatch.setattr(release_pet, "validate_workflow_bindings", lambda _workspace: [])
    monkeypatch.setattr(
        release_pet,
        "require_current_runtime_review",
        lambda _workspace: {
            "build_id": "test-build",
            "build_input_fingerprint": "a" * 64,
            "application_fingerprint": "d" * 64,
            "runtime_asset_fingerprint": "b" * 64,
            "review_visual_fingerprint": "c" * 64,
            "runtime_manifest_sha256": release_pet.file_sha256(
                workspace / "runtime" / "manifest.json"
            ),
        },
    )
    output = tmp_path / "package"

    report = release_pet.prepare_package("safe-pet", "share-no-selfie", output)

    assert not (output / "user_assets" / "selfie.png").exists()
    assert report["selfie_included"] is False
    assert report["base_frame_count"] == 38
    assert report["optional_touch_frame_count"] == 0
    assert report["extension_frame_count"] == 33
    assert report["directional_frame_count"] == 0
    assert report["total_frame_count"] == 38
    assert report["total_asset_frame_count"] == 71
    assert report["privacy_scan"]["status"] == "passed"
    assert not any(
        part in {"source", "candidate", "backups"}
        for path in report["privacy_scan"]["files"]
        for part in Path(path).parts
    )


def test_share_selfie_requires_hash_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _release_workspace(tmp_path / "pets", "approval-pet")
    monkeypatch.setattr(release_pet, "workspace_path", lambda _pet_id: workspace)
    monkeypatch.setattr(release_pet, "validate_workflow_bindings", lambda _workspace: [])
    monkeypatch.setattr(
        release_pet,
        "require_current_runtime_review",
        lambda _workspace: {
            "build_id": "test-build",
            "build_input_fingerprint": "a" * 64,
            "application_fingerprint": "d" * 64,
            "runtime_asset_fingerprint": "b" * 64,
            "review_visual_fingerprint": "c" * 64,
            "runtime_manifest_sha256": release_pet.file_sha256(
                workspace / "runtime" / "manifest.json"
            ),
        },
    )
    with pytest.raises(RuntimeError, match="selfie_approved_sha256"):
        release_pet.prepare_package(
            "approval-pet",
            "share-with-approved-selfie",
            tmp_path / "package",
        )


def test_current_preparation_rejects_revoked_selfie_authorization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """prepare 后撤销自拍授权时，旧报告不得继续用于 finalize。"""

    workspace = _release_workspace(tmp_path / "pets", "revoked-pet")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selfie_sha256 = release_pet.file_sha256(workspace / "source" / "selfie.png")
    config["sharing"] = {"selfie_approved_sha256": selfie_sha256}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    runtime_review = {
        "build_id": "test-build",
        "build_input_fingerprint": "a" * 64,
        "application_fingerprint": "d" * 64,
        "runtime_asset_fingerprint": "b" * 64,
        "review_visual_fingerprint": "c" * 64,
        "runtime_manifest_sha256": release_pet.file_sha256(
            workspace / "runtime" / "manifest.json"
        ),
    }
    monkeypatch.setattr(release_pet, "workspace_path", lambda _pet_id: workspace)
    monkeypatch.setattr(release_pet, "validate_workflow_bindings", lambda _path: [])
    monkeypatch.setattr(
        release_pet,
        "require_current_runtime_review",
        lambda _path: runtime_review,
    )
    output = tmp_path / "package"
    report = release_pet.prepare_package(
        "revoked-pet",
        "share-with-approved-selfie",
        output,
    )
    assert report["selfie_sha256"] == selfie_sha256
    assert report["preparation_id"]

    config["sharing"]["selfie_approved_sha256"] = "0" * 64
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(RuntimeError, match="自拍授权"):
        release_pet.verify_current_preparation_state(
            "revoked-pet",
            "share-with-approved-selfie",
            report,
        )


def test_finalize_release_verifies_complete_directory_zip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    distribution = tmp_path / "OnePicDesktopPet"
    distribution.mkdir()
    (distribution / "OnePicDesktopPet.exe").write_bytes(b"MZ-test")
    (distribution / "_internal").mkdir()
    (distribution / "_internal" / "runtime.bin").write_bytes(b"runtime")
    packaged_user_root = distribution / "_internal" / "user_assets"
    packaged_runtime = packaged_user_root / "pet"
    packaged_runtime.mkdir(parents=True)
    (packaged_runtime / "frame.png").write_bytes(b"approved-runtime")
    runtime_asset_fingerprint = release_pet.runtime_asset_bundle_sha256(
        packaged_runtime
    )
    application_fingerprint = release_pet.application_build_evidence()[
        "fingerprint"
    ]
    manifest = {
        "pet_id": "zip-pet",
        "build_id": "zip-build",
        "build_input_fingerprint": "a" * 64,
        "application_fingerprint": application_fingerprint,
        "runtime_asset_fingerprint": runtime_asset_fingerprint,
        "rendering_mode": "sprite",
    }
    manifest_path = packaged_runtime / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    preparation_id = "prep-zip-001"
    pointer_path = packaged_user_root / "active-pet.json"
    pointer_path.write_text(
        json.dumps(
            {
                "pet_id": "zip-pet",
                "workflow": "pets/zip-pet/workflow.json",
                "package_mode": "share-no-selfie",
                "preparation_id": preparation_id,
            }
        ),
        encoding="utf-8",
    )
    packaged_workflow = packaged_user_root / "pets" / "zip-pet" / "workflow.json"
    packaged_workflow.parent.mkdir(parents=True)
    packaged_workflow.write_text(
        json.dumps(_approved_workflow("zip-pet")),
        encoding="utf-8",
    )
    packaged_config = distribution / "_internal" / "config"
    packaged_config.mkdir()
    (packaged_config / "settings.json").write_text(
        json.dumps(
            {
                "movement_region_enabled": False,
                "movement_region": None,
            }
        ),
        encoding="utf-8",
    )
    preparation = tmp_path / "package-preparation.json"
    preparation.write_text(
        json.dumps(
            {
                "schema_version": release_pet.PREPARATION_SCHEMA_VERSION,
                "preparation_id": preparation_id,
                "prepared_at": "2026-08-23T00:00:00+00:00",
                "pet_id": "zip-pet",
                "mode": "share-no-selfie",
                "selfie_included": False,
                "selfie_sha256": None,
                "qa_sha256": "q" * 64,
                "active_pointer_sha256": release_pet.file_sha256(pointer_path),
                "packaged_workflow_sha256": release_pet.file_sha256(
                    packaged_workflow
                ),
                "runtime_review": {
                    "build_id": "zip-build",
                    "build_input_fingerprint": "a" * 64,
                    "application_fingerprint": application_fingerprint,
                    "runtime_asset_fingerprint": runtime_asset_fingerprint,
                    "review_visual_fingerprint": "v" * 64,
                    "runtime_manifest_sha256": release_pet.file_sha256(
                        manifest_path
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_pet,
        "verify_current_preparation_state",
        lambda *_args: {"status": "passed"},
    )
    evidence = _release_evidence_arguments(
        tmp_path,
        distribution,
        "zip-pet",
        preparation_id,
    )

    result = release_pet.finalize_release(
        "zip-pet",
        "share-no-selfie",
        distribution,
        tmp_path / "release",
        preparation_report=preparation,
        **evidence,
        tests_passed=True,
        source_smoke_passed=True,
        packaged_smoke_passed=True,
        hybrid_pet_window_smoke_passed=True,
        double_click_chat_smoke_passed=True,
        packaged_double_click_chat_smoke_passed=True,
    )

    details = result["details"]
    assert details["zip_crc"] == "passed"
    assert set(details["verification"].values()) >= {
        "passed",
        "isolated ONEPIC_SETTINGS_DIR",
        "isolated ONEPIC_COMPANION_DATA_DIR",
    }
    assert details["verification"]["hybrid_pet_window_smoke"] == "passed"
    assert details["verification"]["double_click_chat_smoke"] == "passed"
    assert details["verification"]["packaged_double_click_chat_smoke"] == (
        "passed"
    )
    assert details["packaged_runtime_attestation"]["status"] == "passed"
    assert details["release_settings"]["status"] == "passed"
    assert len(details["archive_sha256"]) == 64
    with zipfile.ZipFile(result["archive"]) as archive:
        assert archive.testzip() is None
        assert "OnePicDesktopPet/OnePicDesktopPet.exe" in archive.namelist()


def test_finalize_rejects_packaged_runtime_tampering_before_zip(tmp_path: Path) -> None:
    """成品 runtime 任一文件变化都必须在创建 ZIP 前失败。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    frame = distribution / "_internal" / "user_assets" / "pet" / "frame.png"
    frame.write_bytes(b"tampered-runtime")

    with pytest.raises(RuntimeError, match="成品 runtime 取证失败"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
        )

    assert not (tmp_path / "release").exists()


def test_finalize_rejects_invalid_packaged_workflow_before_zip(tmp_path: Path) -> None:
    """即使烟测标志被传入，成品最小 workflow 也必须实际通过审批语义门禁。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    workflow = (
        distribution
        / "_internal"
        / "user_assets"
        / "pets"
        / "guard-pet"
        / "workflow.json"
    )
    workflow.write_text(json.dumps({"pet_id": "guard-pet"}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="workflow"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
        )

    assert not (tmp_path / "release").exists()


def test_finalize_rejects_extra_selfie_in_share_no_selfie(tmp_path: Path) -> None:
    """share-no-selfie 成品误带根级自拍时必须失败。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    selfie = distribution / "_internal" / "user_assets" / "selfie.png"
    selfie.write_bytes(b"private-selfie")

    with pytest.raises(RuntimeError, match="自拍存在状态"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
        )


def test_finalize_rejects_replaced_approved_selfie_before_zip(
    tmp_path: Path,
) -> None:
    """包内获批自拍被换字节后，即使文件仍存在也必须失败。"""

    distribution, preparation = _packaged_release_fixture(
        tmp_path,
        mode="share-with-approved-selfie",
    )
    selfie = distribution / "_internal" / "user_assets" / "selfie.png"
    selfie.write_bytes(b"unapproved-replacement")

    with pytest.raises(RuntimeError, match="自拍 SHA-256"):
        release_pet.finalize_release(
            "guard-pet",
            "share-with-approved-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
        )


def test_finalize_rejects_boolean_only_smoke_claims(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """所有布尔标志为真时仍必须提供真实 JSON 证据。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    monkeypatch.setattr(
        release_pet,
        "verify_current_preparation_state",
        lambda *_args: {"status": "passed"},
    )
    with pytest.raises(FileNotFoundError, match="单元测试"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
        )


def test_finalize_rejects_exe_replaced_after_smoke(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """烟测后替换 EXE 时，旧报告哈希不得继续放行。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    evidence = _release_evidence_arguments(
        tmp_path,
        distribution,
        "guard-pet",
        "prep-guard-001",
    )
    (distribution / "OnePicDesktopPet.exe").write_bytes(b"MZ-replaced")
    monkeypatch.setattr(
        release_pet,
        "verify_current_preparation_state",
        lambda *_args: {"status": "passed"},
    )

    with pytest.raises(RuntimeError, match="packaged_smoke"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
            **evidence,
        )


def test_finalize_rejects_internal_tree_changed_after_smoke(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """烟测后新增或替换 _internal 代码时，完整成品树指纹必须失败。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    evidence = _release_evidence_arguments(
        tmp_path,
        distribution,
        "guard-pet",
        "prep-guard-001",
    )
    (distribution / "_internal" / "application-code.pyc").write_bytes(
        b"changed-after-smoke"
    )
    monkeypatch.setattr(
        release_pet,
        "verify_current_preparation_state",
        lambda *_args: {"status": "passed"},
    )

    with pytest.raises(RuntimeError, match="packaged_smoke"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
            **evidence,
        )


def test_finalize_rejects_stale_same_pet_smoke_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """同一 pet-id 的旧 build 烟测也不得复用于当前 runtime。"""

    distribution, preparation = _packaged_release_fixture(tmp_path)
    evidence = _release_evidence_arguments(
        tmp_path,
        distribution,
        "guard-pet",
        "prep-guard-001",
    )
    menu_report = evidence["interaction_menu_smoke_report"]
    stale = json.loads(menu_report.read_text(encoding="utf-8"))
    stale["build_id"] = "older-build"
    menu_report.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(
        release_pet,
        "verify_current_preparation_state",
        lambda *_args: {"status": "passed"},
    )

    with pytest.raises(RuntimeError, match="interaction_menu"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            distribution,
            tmp_path / "release",
            preparation_report=preparation,
            tests_passed=True,
            source_smoke_passed=True,
            packaged_smoke_passed=True,
            double_click_chat_smoke_passed=True,
            packaged_double_click_chat_smoke_passed=True,
            **evidence,
        )


@pytest.mark.parametrize(
    "missing",
    (
        "tests_passed",
        "source_smoke_passed",
        "packaged_smoke_passed",
        "double_click_chat_smoke_passed",
        "packaged_double_click_chat_smoke_passed",
    ),
)
def test_finalize_requires_every_common_release_gate(
    tmp_path: Path,
    missing: str,
) -> None:
    """直接调用 finalize 也不能绕过任一通用测试或烟测门禁。"""

    gates = {
        "tests_passed": True,
        "source_smoke_passed": True,
        "packaged_smoke_passed": True,
        "double_click_chat_smoke_passed": True,
        "packaged_double_click_chat_smoke_passed": True,
    }
    gates[missing] = False

    with pytest.raises(RuntimeError, match="发布前置测试未全部通过"):
        release_pet.finalize_release(
            "guard-pet",
            "share-no-selfie",
            tmp_path / "missing-distribution",
            tmp_path / "release",
            **gates,
        )


def test_release_smoke_isolates_and_restores_companion_data_directory() -> None:
    """源码与成品烟测都不得读取或改写用户真实陪伴数据库。"""

    script = (
        release_pet.PROJECT_ROOT / "scripts" / "release_pet.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "$savedCompanionDataRoot = $env:ONEPIC_COMPANION_DATA_DIR" in script
    assert "$smokeCompanionData" in script
    assert "$packagedCompanionData" in script
    assert script.count("$env:ONEPIC_COMPANION_DATA_DIR =") >= 3
    assert "Remove-Item Env:ONEPIC_COMPANION_DATA_DIR" in script
    assert "finally {" in script
    saved_names = {
        "ONEPIC_USER_ROOT": "$savedUserRoot",
        "ONEPIC_PET_MANIFEST": "$savedPetManifest",
        "ONEPIC_PET_CATALOG": "$savedPetCatalog",
        "ONEPIC_BREAK_TEST_MODE": "$savedBreakTestMode",
        "ONEPIC_TESTS_PASSED": "$savedTestsPassed",
        "ONEPIC_INCLUDE_USER_ASSETS": "$savedIncludeUserAssets",
        "ONEPIC_PACKAGE_USER_ROOT": "$savedPackageUserRoot",
        "ONEPIC_INCLUDE_SELFIE_ASSET": "$savedIncludeSelfieAsset",
    }
    for name, saved in saved_names.items():
        assert f"{saved} = $env:{name}" in script
        assert f"Remove-Item Env:{name}" in script


def test_release_runs_double_click_chat_smoke_for_every_rendering_route() -> None:
    """双击聊天烟测必须位于 hybrid 条件块之后，sprite 包也要执行。"""

    script = (
        release_pet.PROJECT_ROOT / "scripts" / "release_pet.ps1"
    ).read_text(encoding="utf-8-sig")

    assert script.count("smoke_double_click_chat.py") == 1
    assert '"--double-click-chat-smoke-passed"' in script
    assert '"--packaged-double-click-chat-smoke-passed"' in script
    assert "--smoke-double-click-chat-output" in script
    assert "--smoke-double-click-chat-capture" in script
    assert "--smoke-expected-pet-id $PetId" in script
    assert '"--tests-report", $testReport' in script
    assert '"--source-smoke-report", $sourceSmokeReport' in script
    assert '"--packaged-smoke-report", $packagedSmokeReport' in script
    assert '"--interaction-menu-smoke-report", $actionMenuSmokeReport' in script
    assert '"--double-click-chat-smoke-report", $sourceDoubleClickSmokeReport' in script
    assert '"--packaged-double-click-chat-smoke-report", $packagedDoubleClickSmokeReport' in script
    assert script.index("$companionSmokeRoot") > script.index(
        "$hybridPetWindowSmokePassed = $true"
    )
    assert "窗口双击快捷聊天与零联网烟测失败" in script
    assert "打包后 EXE 双击快捷聊天与零自动请求烟测失败" in script
    assert "verify-runtime-review $PetId" in script
    assert script.index("verify-runtime-review $PetId") < script.index(
        '& "$PSScriptRoot\\build_pet.ps1"'
    )


def test_release_runs_part56_dom_alignment_gate_for_bound_accessories() -> None:
    """声明 Cubism Part 绑定后，发布必须重跑最终 DOM 鼻位门禁。"""

    script = (
        release_pet.PROJECT_ROOT / "scripts" / "release_pet.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "accessory_qa_preset_file" in script
    assert "capture_live2d_presets.py" in script
    assert "--runtime-manifest $activeManifest" in script
    assert "maximum_nose_alignment_error_css_px" in script
    assert "-gt 1.0" in script
