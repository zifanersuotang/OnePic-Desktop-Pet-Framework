"""
验证 Android 悬浮桌宠工程、可复用应用身份、审批门禁、素材白名单和隐私脱敏。

测试只在临时项目中创建透明占位 PNG，不读取真实 `user_assets`，也不运行 Android
SDK、Gradle、签名或设备安装。编译 SDK 检查兼容传统整数语法与 Android Gradle
Plugin 的 `release(36)` 结构化语法；真实手机视觉效果仍必须单独人工验收。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from onepic_desktop_pet.care import care_config_sha256
from tools.prepare_android_pet import AndroidPackageError, prepare_android_pet


ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _png(path: Path, color: tuple[int, int, int, int] = (60, 80, 120, 220)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (24, 30), color).save(path)


def _fixture_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    workspace = root / "user_assets" / "pets" / "demo"
    runtime = workspace / "runtime"
    target_parent = root / "android" / "app" / "src" / "main" / "assets"
    target_parent.mkdir(parents=True)

    manifest = {
        "schema_version": 2,
        "pet_id": "demo",
        "display_name": "测试桌宠",
        "directional_frame_count": 0,
        "animations": {
            "idle": ["idle/01.png"],
            "walk": ["walk/01.png", "walk/02.png"],
        },
        "touch_feedback": {
            "optional_transition_sources": [
                "actions/transparent/touch_soften.png",
                "actions/transparent/touch_return.png",
            ],
            "transition_frames": ["interact/touch_transition_01.png"],
        },
        "extension_animations": {
            "signature_wave": {
                "frames": ["extensions/signature_wave/01.png"],
                "frame_duration_ms": 180,
                "loop": False,
            }
        },
        "dialogue_system": {
            "enabled": True,
            "lines": {"idle": ["你好"], "signature_wave": ["挥挥手"]},
        },
        "break_reminders": {
            "schema_version": 1,
            "enabled": False,
            "schedule": {"reminders": []},
        },
        "icon": "icon.png",
        "selfie_photo": "../selfie.png",
    }
    workflow = {
        "character": {"status": "approved", "user_confirmed": True},
        "walk": {"status": "approved", "user_confirmed": True},
        "extension_approvals": {
            "pack": {
                "status": "approved",
                "activation_allowed": True,
                "user_confirmed": True,
                "items": {"signature_wave": {}},
            }
        },
        "feature_status": {
            "local_dialogue_v1": {
                "status": "activated",
                "user_confirmed": True,
            }
        },
    }
    runtime.mkdir(parents=True)
    (runtime / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "workflow.json").write_text(
        json.dumps(workflow, ensure_ascii=False),
        encoding="utf-8",
    )
    for relative in [
        "idle/01.png",
        "walk/01.png",
        "walk/02.png",
        "interact/touch_transition_01.png",
        "extensions/signature_wave/01.png",
        "icon.png",
    ]:
        _png(runtime / relative)
    _png(workspace / "source" / "selfie.png")
    return root, target_parent / "pet"


def test_prepare_android_pet_copies_only_runtime_allowlist(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)

    report = prepare_android_pet("demo", project_root=root, target_root=target)

    assert report["asset_count"] == 6
    assert report["privacy_checks"]["source_selfie_excluded"] is True
    assert not (target / "selfie.png").exists()
    assert not (target / "workflow.json").exists()
    bundled = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert "selfie_photo" not in bundled
    assert "break_reminders" not in bundled
    assert "optional_transition_sources" not in bundled["touch_feedback"]
    assert bundled["android_package"]["privacy_mode"] == "share-no-selfie"
    assert bundled["android_package"]["directional_mouse_follow_enabled"] is False
    assert bundled["android_package"]["break_reminders_supported"] is False
    assert (target / "package-report.json").is_file()


def test_prepare_android_pet_rejects_unapproved_character(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)
    workflow_path = root / "user_assets" / "pets" / "demo" / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["character"]["status"] = "pending"
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

    with pytest.raises(AndroidPackageError, match="标准角色尚未通过审批"):
        prepare_android_pet("demo", project_root=root, target_root=target)


def test_prepare_android_pet_rejects_unapproved_care_config(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)
    manifest_path = (
        root / "user_assets" / "pets" / "demo" / "runtime" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["care_simulation"] = {"enabled": True, "feed_cost": 9}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AndroidPackageError, match="VPet 养成配置"):
        prepare_android_pet("demo", project_root=root, target_root=target)


def test_prepare_android_pet_accepts_hash_bound_care_config(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)
    manifest_path = (
        root / "user_assets" / "pets" / "demo" / "runtime" / "manifest.json"
    )
    workflow_path = root / "user_assets" / "pets" / "demo" / "workflow.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    manifest["care_simulation"] = {"enabled": True, "feed_cost": 9}
    care_hash = care_config_sha256(manifest["care_simulation"])
    workflow["feature_status"]["vpet_care_v1"] = {
        "status": "activated",
        "user_confirmed": True,
        "activation_allowed": True,
        "config_sha256": care_hash,
        "approved_config_sha256": care_hash,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

    report = prepare_android_pet("demo", project_root=root, target_root=target)

    assert report["gates"]["care_enabled"] is True
    bundled = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert bundled["care_simulation"]["enabled"] is True


def test_prepare_android_pet_rejects_path_traversal(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)
    manifest_path = (
        root / "user_assets" / "pets" / "demo" / "runtime" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["animations"]["idle"] = ["../source/selfie.png"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AndroidPackageError, match="非法资源路径"):
        prepare_android_pet("demo", project_root=root, target_root=target)


def test_prepare_android_pet_rejects_unreferenced_png(tmp_path: Path) -> None:
    root, target = _fixture_project(tmp_path)
    runtime = root / "user_assets" / "pets" / "demo" / "runtime"
    _png(runtime / "draft.png")

    with pytest.raises(AndroidPackageError, match="清单未引用"):
        prepare_android_pet("demo", project_root=root, target_root=target)


def test_prepare_android_pet_excludes_windows_live2d_runtime(
    tmp_path: Path,
) -> None:
    """混合桌宠进入 Android 时应保留精灵动作，但不复制或悬挂 Live2D 引用。"""

    root, target = _fixture_project(tmp_path)
    runtime = root / "user_assets" / "pets" / "demo" / "runtime"
    manifest_path = runtime / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rendering_mode"] = "hybrid-live2d"
    manifest["hybrid_runtime"] = {
        "enabled": True,
        "idle_backend": "live2d",
        "model": "live2d/pet.model3.json",
        "blink_overlay": "live2d/blink-overlay.png",
        "fallback_backend": "sprite",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _png(runtime / "live2d" / "texture_00.png")
    _png(runtime / "live2d" / "blink-overlay.png")
    (runtime / "live2d" / "pet.model3.json").write_text(
        json.dumps({"FileReferences": {"Textures": ["texture_00.png"]}}),
        encoding="utf-8",
    )

    report = prepare_android_pet("demo", project_root=root, target_root=target)

    assert report["asset_count"] == 6
    assert report["privacy_checks"]["live2d_runtime_excluded"] is True
    assert not (target / "live2d").exists()
    bundled = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert bundled["rendering_mode"] == "sprite"
    assert "hybrid_runtime" not in bundled
    assert (
        bundled["android_package"]["source_rendering_mode"]
        == "hybrid-live2d"
    )
    assert bundled["android_package"]["live2d_runtime_excluded"] is True


def test_android_manifest_declares_visible_overlay_service() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    tree = ET.parse(manifest_path)
    manifest = tree.getroot()
    permissions = {
        node.attrib[f"{{{ANDROID_NS}}}name"]
        for node in manifest.findall("uses-permission")
    }
    assert "android.permission.SYSTEM_ALERT_WINDOW" in permissions
    assert "android.permission.INTERNET" in permissions
    assert "android.permission.FOREGROUND_SERVICE" in permissions
    assert "android.permission.FOREGROUND_SERVICE_SPECIAL_USE" in permissions
    assert "android.permission.POST_NOTIFICATIONS" in permissions
    assert not any(
        value in permissions
        for value in {
            "android.permission.READ_CONTACTS",
            "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.RECORD_AUDIO",
            "android.permission.QUERY_ALL_PACKAGES",
        }
    )

    service = manifest.find("application/service")
    assert service is not None
    assert service.attrib[f"{{{ANDROID_NS}}}exported"] == "false"
    assert service.attrib[f"{{{ANDROID_NS}}}foregroundServiceType"] == "specialUse"

    activities = manifest.findall("application/activity")
    companion = next(
        (
            activity
            for activity in activities
            if activity.attrib[f"{{{ANDROID_NS}}}name"] == ".CompanionActivity"
        ),
        None,
    )
    assert companion is not None
    assert companion.attrib[f"{{{ANDROID_NS}}}exported"] == "false"


def test_android_companion_security_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    java_root = (
        root
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "onepic"
        / "desktop"
        / "pet"
    )
    activity = (java_root / "CompanionActivity.java").read_text(encoding="utf-8")
    client = (java_root / "OpenAiCompatibleClient.java").read_text(
        encoding="utf-8"
    )
    secrets = (java_root / "CompanionSecretStore.java").read_text(
        encoding="utf-8"
    )
    manifest_source = (java_root / "PetAssetManifest.java").read_text(
        encoding="utf-8"
    )
    service = (java_root / "PetOverlayService.java").read_text(encoding="utf-8")

    assert "打开本页面不会产生网络请求" in activity
    assert "CompanionEndpoint.parse" in activity
    assert "FLAG_SECURE" in activity
    assert "AndroidKeyStore" in secrets
    assert "AES/GCM/NoPadding" in secrets
    assert "updateAAD" in secrets
    assert "HttpsURLConnection" in client
    assert "setInstanceFollowRedirects(false)" in client
    assert 'body.put("stream", false)' in client
    assert "MAX_HISTORY_CHARACTERS = 24000" in client
    assert "isCompanionActionAllowed" in manifest_source
    assert "ACTION_COMPANION_REPLY" in service
    assert "GestureDetector" in service
    assert "onSingleTapConfirmed" in service
    assert "onDoubleTap" in service
    assert "openCompanionFromOverlay" in service
    assert "FLAG_ACTIVITY_NEW_TASK" in service
    assert "petView.isAttachedToWindow()" in service
    assert (
        "else if (event.getActionMasked() == MotionEvent.ACTION_UP)"
        not in service
    )


def test_android_build_targets_current_play_requirement() -> None:
    root = Path(__file__).resolve().parents[1]
    build_file = (root / "android" / "app" / "build.gradle").read_text(
        encoding="utf-8"
    )
    assert (
        "compileSdk 36" in build_file
        or (
            "compileSdk {" in build_file
            and "version = release(36)" in build_file
            and "minorApiLevel = 1" in build_file
        )
    )
    assert "targetSdk 36" in build_file
    assert "minSdk 26" in build_file
    assert "ONEPIC_ANDROID_KEYSTORE_PASSWORD" in build_file


def test_android_build_identity_is_parameterized_for_multiple_pets() -> None:
    root = Path(__file__).resolve().parents[1]
    build_file = (root / "android" / "app" / "build.gradle").read_text(
        encoding="utf-8"
    )
    build_script = (root / "scripts" / "build_android.ps1").read_text(
        encoding="utf-8-sig"
    )
    main_activity = (
        root
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "onepic"
        / "desktop"
        / "pet"
        / "MainActivity.java"
    ).read_text(encoding="utf-8")

    for property_name in {
        "onepicApplicationId",
        "onepicDisplayName",
        "onepicVersionCode",
        "onepicVersionName",
    }:
        assert property_name in build_file
        assert f"-P{property_name}=" in build_script
    assert 'if ($PetId -eq "gugugaga2")' in build_script
    assert '"com.onepic.desktop.pet.$applicationSuffix"' in build_script
    assert "apksigner.bat" in build_script
    assert "offline_signature_verification" in build_script
    assert 'device_probe = "not-run"' in build_script
    assert "adb devices" not in build_script
    assert "private-character-name · Android 悬浮桌宠" not in main_activity
    assert "applicationLabel()" in main_activity
    assert "ACTION_CARE_FEED" in main_activity
    assert "养成状态" in main_activity


def test_android_runtime_selects_latest_side_walk_sequence() -> None:
    root = Path(__file__).resolve().parents[1]
    java_root = (
        root
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "onepic"
        / "desktop"
        / "pet"
    )
    manifest_source = (java_root / "PetAssetManifest.java").read_text(
        encoding="utf-8"
    )
    service_source = (java_root / "PetOverlayService.java").read_text(
        encoding="utf-8"
    )

    assert 'sequences.containsKey("side_walk_v2")' in manifest_source
    assert "preferredSideWalkSequence()" in service_source
    assert 'hasSequence("side_walk_v1")' not in service_source


def test_cross_platform_build_entry_preserves_both_platform_gates() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_cross_platform_pet.ps1").read_text(
        encoding="utf-8-sig"
    )
    normalized = script.replace(".\\tools\\", "").replace(".\\scripts\\", "")

    assert '"pet_workspace.py" validate $PetId' in normalized
    assert '"build_pet.ps1" -PetId $PetId -Activate' in normalized
    assert '"build_android.ps1" @androidArguments' in normalized
    assert '"release_pet.ps1" -PetId $PetId -Mode "share-no-selfie"' in normalized


def test_release_signer_supports_new_pets_without_persisting_passwords() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "OnePicReleaseSigner.cs").read_text(
        encoding="utf-8"
    )

    assert "Application ID" in source
    assert "-PetId " in source
    assert "-ApplicationId " in source
    assert "-VersionCode " in source
    assert "-VersionName " in source
    assert "if (!File.Exists(keyPath))" in source
    assert 'EnvironmentVariables["ONEPIC_ANDROID_KEYSTORE_PASSWORD"]' in source
    assert "C:\\Users\\" not in source
