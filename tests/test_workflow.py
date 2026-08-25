"""
本模块测试一图桌宠的原图登记、标准角色、走路和替代互动确认门禁，并验证走路
必须恰好八帧，所有正负反馈均以 CAS 绑定用户实际查看的动态证据。

测试只在 pytest 临时目录创建原图、候选图、走路帧和 workflow JSON，
不读取或覆盖真实 `user_assets/`，不启动 GUI，也不访问网络。
"""

from pathlib import Path

from PIL import Image
import pytest

from onepic_desktop_pet.workflow import (
    WorkflowError,
    bind_legacy_approval_hashes,
    custom_pet_is_approved,
    migrate_legacy_workflow,
    require_character_approved,
    walk_is_approved,
)
from tools import onepic_workflow


def _configure_private_root(monkeypatch, project_root: Path) -> Path:
    """让命令工具只操作测试临时目录。"""

    user_root = project_root / "user_assets"
    monkeypatch.setattr(onepic_workflow, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(onepic_workflow, "USER_ROOT", user_root)
    monkeypatch.setattr(onepic_workflow, "STATE_PATH", user_root / "workflow.json")
    return user_root


def _save_image(path: Path, color: tuple[int, int, int, int]) -> None:
    """创建测试使用的 RGBA 图片。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (48, 64), color).save(path)


def test_source_registration_preserves_original_and_sets_selfie(
    tmp_path,
    monkeypatch,
) -> None:
    """登记原图后应保留原始副本并生成同分辨率自拍 PNG。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "upload.jpg"
    Image.new("RGB", (96, 128), (220, 210, 200)).save(source, quality=95)

    state = onepic_workflow.register_source(source)

    assert (user_root / "source" / "original.jpg").read_bytes() == source.read_bytes()
    with Image.open(user_root / "selfie.png") as selfie:
        assert selfie.size == (96, 128)
    assert state["character"]["status"] == "not_submitted"
    assert state["walk"]["status"] == "not_generated"


def test_character_and_walk_require_explicit_user_approval(
    tmp_path,
    monkeypatch,
) -> None:
    """没有两个显式确认时，动作生成和私有桌宠加载都必须被阻止。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (240, 220, 210, 255))
    _save_image(candidate, (120, 80, 60, 255))
    onepic_workflow.register_source(source)
    state = onepic_workflow.register_character_candidate(
        candidate,
        ["长辫子", "白色 V 领"],
        "preserve_original",
    )
    assert state["character"]["style"] == "preserve_original"
    assert state["character"]["candidate_sha256"]

    try:
        require_character_approved(user_root / "workflow.json")
    except WorkflowError:
        pass
    else:
        raise AssertionError("未确认标准角色时不应允许生成动作")

    onepic_workflow.approve_character(True)
    assert not custom_pet_is_approved(user_root / "workflow.json")

    pet_root = user_root / "pet"
    walk_paths = []
    for index in range(8):
        relative = f"walk/walk_{index + 1:02d}.png"
        _save_image(pet_root / relative, (40 + index * 10, 80, 120, 255))
        walk_paths.append(relative)
    manifest = pet_root / "manifest.json"
    manifest.write_text(
        '{"animations": {"walk": '
        + str(walk_paths).replace("'", '"')
        + "}}",
        encoding="utf-8",
    )

    state = onepic_workflow.create_walk_review(manifest)
    assert state["walk"]["status"] == "awaiting_user_confirmation"
    assert (user_root / "review" / "walk-preview.gif").is_file()
    assert not custom_pet_is_approved(user_root / "workflow.json")

    onepic_workflow.approve_walk(
        True,
        expected_preview_sha256=state["walk"]["preview_sha256"],
        expected_asset_sha256=state["walk"]["asset_sha256"],
    )
    assert custom_pet_is_approved(user_root / "workflow.json")


def test_walk_review_rejects_more_than_eight_frames(
    tmp_path,
    monkeypatch,
) -> None:
    """多余帧不能偷偷进入基础八相位门禁。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (240, 220, 210, 255))
    _save_image(candidate, (120, 80, 60, 255))
    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["八帧角色"])
    onepic_workflow.approve_character(True)

    pet_root = user_root / "pet"
    walk_paths = []
    for index in range(9):
        relative = f"walk/walk_{index + 1:02d}.png"
        _save_image(pet_root / relative, (40 + index * 10, 80, 120, 255))
        walk_paths.append(relative)
    manifest = pet_root / "manifest.json"
    manifest.write_text(
        '{"animations": {"walk": '
        + str(walk_paths).replace("'", '"')
        + "}}",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowError, match="必须恰好 8 帧"):
        onepic_workflow.create_walk_review(manifest)


def test_walk_rejection_persists_feedback_for_replanning(
    tmp_path,
    monkeypatch,
) -> None:
    """用户否决必须绑定当前 GIF/帧束，并保留可驱动修订的原始反馈。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (240, 220, 210, 255))
    _save_image(candidate, (120, 80, 60, 255))
    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["负反馈角色"])
    onepic_workflow.approve_character(True)
    pet_root = user_root / "pet"
    walk_paths = []
    for index in range(8):
        relative = f"walk/walk_{index + 1:02d}.png"
        _save_image(pet_root / relative, (40 + index * 10, 80, 120, 255))
        walk_paths.append(relative)
    manifest = pet_root / "manifest.json"
    manifest.write_text(
        '{"animations": {"walk": '
        + str(walk_paths).replace("'", '"')
        + "}}",
        encoding="utf-8",
    )
    state = onepic_workflow.create_walk_review(manifest)

    rejected = onepic_workflow.reject_walk(
        True,
        reason="右脚落地像滑步",
        expected_preview_sha256=state["walk"]["preview_sha256"],
        expected_asset_sha256=state["walk"]["asset_sha256"],
    )

    assert rejected["walk"]["status"] == "rejected"
    assert rejected["walk"]["rejection_reason"] == "右脚落地像滑步"
    assert custom_pet_is_approved(user_root / "workflow.json") is False


def test_changed_walk_preview_cannot_be_approved(
    tmp_path,
    monkeypatch,
) -> None:
    """用户查看后被替换的 GIF 必须重新生成，不能沿用旧确认。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (240, 220, 210, 255))
    _save_image(candidate, (120, 80, 60, 255))
    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["预览角色"])
    onepic_workflow.approve_character(True)

    pet_root = user_root / "pet"
    walk_paths = []
    for index in range(8):
        relative = f"walk/walk_{index + 1:02d}.png"
        _save_image(pet_root / relative, (40 + index * 10, 80, 120, 255))
        walk_paths.append(relative)
    manifest = pet_root / "manifest.json"
    manifest.write_text(
        '{"animations": {"walk": '
        + str(walk_paths).replace("'", '"')
        + "}}",
        encoding="utf-8",
    )
    state = onepic_workflow.create_walk_review(manifest)
    preview = tmp_path / state["walk"]["preview"]
    Image.new("RGB", (24, 24), "red").save(preview, "GIF")

    with pytest.raises(WorkflowError, match="提交后变化"):
        onepic_workflow.approve_walk(
            True,
            expected_preview_sha256=state["walk"]["preview_sha256"],
            expected_asset_sha256=state["walk"]["asset_sha256"],
        )


def test_changed_candidate_and_rejected_character_cannot_pass_gate(
    tmp_path,
    monkeypatch,
) -> None:
    """候选被替换或用户要求重做后，旧审批不得继续生效。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    _save_image(source, (230, 220, 210, 255))
    _save_image(candidate, (100, 90, 80, 255))
    onepic_workflow.register_source(source)
    state = onepic_workflow.register_character_candidate(candidate, ["长侧辫"])
    stored_candidate = tmp_path / state["character"]["candidate"]
    _save_image(stored_candidate, (20, 30, 40, 255))

    try:
        onepic_workflow.approve_character(True)
    except WorkflowError:
        pass
    else:
        raise AssertionError("提交后被替换的候选图不应允许确认")

    onepic_workflow.register_character_candidate(candidate, ["长侧辫"])
    onepic_workflow.approve_character(True)
    assert custom_pet_is_approved(user_root / "workflow.json") is False
    state = onepic_workflow.reject_character("面部特征不准确")
    assert state["character"]["status"] == "rejected"
    assert state["character"]["user_confirmed"] is False
    assert state["walk"]["status"] == "not_generated"


def test_confirmed_directional_interaction_can_replace_walk_gate(
    tmp_path,
    monkeypatch,
) -> None:
    """静止鼠标跟随桌宠确认互动后不应被不存在的走路预览阻塞。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    preview = tmp_path / "mouse-follow.gif"
    _save_image(source, (230, 220, 210, 255))
    _save_image(candidate, (100, 90, 80, 255))
    Image.new("RGB", (48, 64), (30, 40, 50)).save(preview, "GIF")

    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["原视频人物"])
    onepic_workflow.approve_character(True)
    assert custom_pet_is_approved(user_root / "workflow.json") is False

    state = onepic_workflow.submit_interaction_preview(
        mode="directional_original_video_32",
        preview_path=preview,
    )
    expected_hash = state["interaction"]["preview_sha256"]
    assert state["interaction"]["status"] == "awaiting_user_confirmation"
    with pytest.raises(WorkflowError, match="模式已切换"):
        onepic_workflow.approve_interaction(
            True,
            expected_preview_sha256=expected_hash,
            expected_mode="stationary_vehicle",
        )

    state = onepic_workflow.approve_interaction(
        True,
        expected_preview_sha256=expected_hash,
        expected_mode="directional_original_video_32",
    )

    assert state["interaction"]["status"] == "approved"
    assert state["interaction"]["user_confirmed"] is True
    assert state["interaction"]["approved_character_sha256"] == (
        state["character"]["candidate_sha256"]
    )
    assert state["walk"]["status"] == "not_generated"
    assert custom_pet_is_approved(user_root / "workflow.json") is True


def test_interaction_rejection_is_cas_bound_and_requires_new_submission(
    tmp_path,
    monkeypatch,
) -> None:
    """互动否决保存原始反馈；旧哈希不能批准，新提交才重新开放确认。"""

    _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    preview = tmp_path / "interaction.gif"
    _save_image(source, (230, 220, 210, 255))
    _save_image(candidate, (100, 90, 80, 255))
    Image.new("RGB", (48, 64), (30, 40, 50)).save(preview, "GIF")
    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["原视频人物"])
    onepic_workflow.approve_character(True)

    pending = onepic_workflow.submit_interaction_preview(
        mode="stationary_character",
        preview_path=preview,
    )
    old_hash = pending["interaction"]["preview_sha256"]
    rejected = onepic_workflow.reject_interaction(
        True,
        reason="循环末尾有跳帧",
        expected_preview_sha256=old_hash,
        expected_mode="stationary_character",
    )
    assert rejected["interaction"]["status"] == "rejected"
    assert rejected["interaction"]["rejection_reason"] == "循环末尾有跳帧"

    with pytest.raises(WorkflowError, match="没有等待反馈"):
        onepic_workflow.approve_interaction(
            True,
            expected_preview_sha256=old_hash,
            expected_mode="stationary_character",
        )
    with pytest.raises(WorkflowError, match="没有变化"):
        onepic_workflow.submit_interaction_preview(
            mode="stationary_character",
            preview_path=preview,
        )

    Image.new("RGB", (48, 64), (90, 40, 50)).save(preview, "GIF")
    revised = onepic_workflow.submit_interaction_preview(
        mode="stationary_character",
        preview_path=preview,
    )
    assert revised["interaction"]["status"] == "awaiting_user_confirmation"
    assert revised["interaction"]["preview_sha256"] != old_hash


def test_new_character_candidate_invalidates_old_interaction(
    tmp_path,
    monkeypatch,
) -> None:
    """重新登记标准角色时必须清空旧互动，不能把旧预览批准沿用到新角色。"""

    user_root = _configure_private_root(monkeypatch, tmp_path)
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    preview = tmp_path / "interaction.gif"
    _save_image(source, (230, 220, 210, 255))
    _save_image(candidate, (100, 90, 80, 255))
    Image.new("RGB", (48, 64), (30, 40, 50)).save(preview, "GIF")
    onepic_workflow.register_source(source)
    onepic_workflow.register_character_candidate(candidate, ["第一版"])
    onepic_workflow.approve_character(True)
    pending = onepic_workflow.submit_interaction_preview(
        mode="stationary_character",
        preview_path=preview,
    )
    onepic_workflow.approve_interaction(
        True,
        expected_preview_sha256=pending["interaction"]["preview_sha256"],
        expected_mode="stationary_character",
    )
    assert custom_pet_is_approved(user_root / "workflow.json") is True

    _save_image(candidate, (120, 70, 60, 255))
    state = onepic_workflow.register_character_candidate(candidate, ["第二版"])
    assert state["interaction"]["status"] == "not_generated"
    assert custom_pet_is_approved(user_root / "workflow.json") is False


def test_legacy_workflow_migrates_per_pet_and_binds_preview_hash(
    tmp_path,
) -> None:
    """旧全局审批应保留备份、迁入指定宠物，并把确认绑定到哈希。"""

    user_root = tmp_path / "user_assets"
    user_root.mkdir()
    legacy = {
        "version": 1,
        "character": {
            "status": "approved",
            "user_confirmed": True,
            "candidate_sha256": "a" * 64,
            "approved_candidate_sha256": "a" * 64,
        },
        "walk": {"status": "approved"},
    }
    (user_root / "workflow.json").write_text(
        __import__("json").dumps(legacy),
        encoding="utf-8",
    )
    destination, backup = migrate_legacy_workflow(
        user_root,
        "legacy-pet",
        preview_sha256="b" * 64,
        walk_asset_sha256="c" * 64,
    )

    assert destination == user_root / "pets" / "legacy-pet" / "workflow.json"
    assert backup.is_file()
    assert custom_pet_is_approved(destination)
    migrated = __import__("json").loads(destination.read_text(encoding="utf-8"))
    assert migrated["walk"]["preview_sha256"] == "b" * 64
    assert migrated["walk"]["asset_sha256"] == "c" * 64
    assert migrated["walk"]["approved_asset_sha256"] == "c" * 64
    assert migrated["walk"]["asset_binding_kind"] == "walk_frame_bundle"
    pointer = __import__("json").loads(
        (user_root / "workflow.json").read_text(encoding="utf-8")
    )
    assert set(pointer) == {
        "version",
        "active_pet_id",
        "workflow",
        "migrated_at",
    }


def test_changed_walk_asset_hash_invalidates_old_confirmation() -> None:
    """走路素材束哈希变化后旧确认不能继续通过。"""

    state = bind_legacy_approval_hashes(
        {
            "character": {
                "status": "approved",
                "user_confirmed": True,
                "candidate_sha256": "a" * 64,
                "approved_candidate_sha256": "a" * 64,
            },
            "walk": {"status": "approved"},
        },
        preview_sha256="b" * 64,
    )
    state["walk"]["asset_sha256"] = "c" * 64
    assert not walk_is_approved(state)
