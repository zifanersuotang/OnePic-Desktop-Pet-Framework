"""
本模块验证多桌宠工作区初始化、素材契约检查、通用 38 帧构建器，以及配置化
本地动作台词、养成玩法、扩展动作、可选八相位走路样式、八向视线素材的安全装配，
以及只读 Agent 控制快照对阶段、阻塞、build 取证、最终验收和唯一下一步的判断。

测试只在 pytest 临时目录中生成简化透明占位图，不读取用户真实照片或视频，
不替换当前 `user_assets/pet`，也不执行打包；同时验证 Windows 归档失败不会
遮蔽原始构建诊断；还覆盖摸头来源篡改、走路/替代互动的 CAS 反馈闭环和静止路线。
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from tools import build_pet_workspace, onepic_workflow, pet_workspace


def make_pose(path: Path, *, size: tuple[int, int] = (320, 480)) -> None:
    """创建带透明边缘的简化人物占位图。"""

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((125, 20, 195, 90), fill=(45, 45, 50, 255))
    draw.rounded_rectangle((110, 85, 210, 300), radius=20, fill=(25, 25, 30, 255))
    draw.rectangle((120, 290, 150, 450), fill=(25, 25, 30, 255))
    draw.rectangle((170, 290, 200, 450), fill=(25, 25, 30, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def make_walk(path: Path, horizontal: int) -> None:
    """创建统一画布且含腿部形变和身体起伏的走路占位帧。"""

    image = Image.new("RGBA", (560, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rise = abs(horizontal) // 2
    draw.ellipse((245, 34 - rise, 315, 104 - rise), fill=(45, 45, 50, 255))
    draw.rectangle((230, 100 - rise, 330, 330 - rise), fill=(25, 25, 30, 255))
    draw.line((260, 325 - rise, 245 - horizontal * 4, 484 - rise), fill=(25, 25, 30, 255), width=24)
    draw.line((300, 325 - rise, 315 + horizontal * 4, 484 - rise), fill=(25, 25, 30, 255), width=24)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def make_complete_workspace(
    tmp_path: Path,
    monkeypatch,
    pet_id: str,
) -> Path:
    """创建可供装配测试复用的完整非隐私占位工作区。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )
    monkeypatch.setattr(
        build_pet_workspace,
        "validate_workflow_bindings",
        lambda _workspace: [],
    )
    source = tmp_path / f"{pet_id}-source.png"
    Image.new("RGB", (480, 640), (80, 80, 80)).save(source, "PNG")
    workspace = pet_workspace.initialize_workspace(
        pet_id,
        display_name=pet_id,
        source=source,
        selfie_frame=None,
        recommended_display_height=360,
    )
    make_pose(workspace / "candidate" / "approved.png")
    approve_workspace_character(workspace)
    for name in pet_workspace.ACTION_NAMES:
        candidates = [
            workspace / "actions" / "candidates" / name / "v1" / f"candidate_{index:02d}.png"
            for index in (1, 2)
        ]
        for path in candidates:
            make_pose(path)
        pet_workspace.record_action_source_review(
            workspace,
            action=name,
            candidates=candidates,
            selected=candidates[0],
            checks={check: True for check in pet_workspace.ACTION_REVIEW_CHECKS},
            confirmed=True,
        )
    pet_workspace.create_touch_transition_sources(workspace)
    for index, horizontal in enumerate(
        (-4, -2, 0, 2, 4, 2, 0, -2),
        start=1,
    ):
        make_walk(
            workspace / "walk" / "frames" / f"walk_{index:02d}.png",
            horizontal,
        )
    return workspace


def approve_workspace_character(workspace: Path) -> None:
    """把测试标准角色绑定到当前候选哈希，不绕过实际工作区前置检查。"""

    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    candidate_hash = pet_workspace.file_sha256(
        workspace / "candidate" / "approved.png"
    )
    workflow["character"] = {
        "status": "approved",
        "user_confirmed": True,
        "candidate_sha256": candidate_hash,
        "approved_candidate_sha256": candidate_hash,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def approve_workspace_walk(workspace: Path) -> None:
    """把测试走路门禁绑定到当前八相位素材束。"""

    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    walk_paths = [
        workspace / "walk" / "frames" / f"walk_{index:02d}.png"
        for index in range(1, 9)
    ]
    walk_hash = pet_workspace.file_bundle_sha256(walk_paths)
    workflow["walk"] = {
        "status": "approved",
        "user_confirmed": True,
        "asset_sha256": walk_hash,
        "approved_asset_sha256": walk_hash,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_attested_runtime(workspace: Path) -> dict[str, object]:
    """写入与当前输入绑定的最小 runtime、QA 和 latest-build 测试证据。"""

    evidence = pet_workspace.workspace_build_input_evidence(workspace)
    build_id = "20260822T010203000000Z"
    runtime = workspace / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime_frame = runtime / "idle" / "idle_01.png"
    runtime_frame.parent.mkdir(parents=True, exist_ok=True)
    runtime_frame.write_bytes(b"attested-runtime-frame")
    runtime_asset_fingerprint = pet_workspace.runtime_asset_bundle_sha256(
        runtime
    )
    manifest = {
        "schema_version": 2,
        "pet_id": json.loads(
            (workspace / "pet-config.json").read_text(encoding="utf-8")
        )["pet_id"],
        "build_id": build_id,
        "build_input_schema_version": evidence["schema_version"],
        "build_input_fingerprint": evidence["fingerprint"],
        "application_fingerprint": evidence["application_fingerprint"],
        "runtime_asset_fingerprint": runtime_asset_fingerprint,
    }
    manifest_path = runtime / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review = workspace / "review"
    review.mkdir(parents=True, exist_ok=True)
    for name in pet_workspace.REVIEW_VISUAL_ARTIFACTS:
        (review / name).write_bytes(f"visual:{name}".encode("utf-8"))
    review_visual_fingerprint = pet_workspace.review_visual_evidence_sha256(
        review
    )
    qa = {
        "status": "passed",
        "build_id": build_id,
        "build_input_schema_version": evidence["schema_version"],
        "build_input_fingerprint": evidence["fingerprint"],
        "application_fingerprint": evidence["application_fingerprint"],
        "runtime_asset_fingerprint": runtime_asset_fingerprint,
        "review_visual_fingerprint": review_visual_fingerprint,
    }
    qa_path = review / "assets-qa.json"
    qa_path.write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    build_review = review / "builds" / build_id
    build_review.mkdir(parents=True, exist_ok=True)
    for name in pet_workspace.REVIEW_VISUAL_ARTIFACTS:
        (build_review / name).write_bytes((review / name).read_bytes())
    (build_review / "assets-qa.json").write_text(
        qa_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    latest = {
        "pet_id": manifest["pet_id"],
        "build_id": build_id,
        "path": build_review.relative_to(workspace).as_posix(),
        "build_input_schema_version": evidence["schema_version"],
        "build_input_fingerprint": evidence["fingerprint"],
        "application_fingerprint": evidence["application_fingerprint"],
        "runtime_asset_fingerprint": runtime_asset_fingerprint,
        "review_visual_fingerprint": review_visual_fingerprint,
        "runtime_manifest_sha256": pet_workspace.file_sha256(manifest_path),
        "assets_qa_sha256": pet_workspace.file_sha256(qa_path),
    }
    (review / "latest-build.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def test_alpha_mass_normalization_equalizes_different_shapes() -> None:
    """高瘦和矮宽姿势在不触碰画布边界时应具有接近的视觉体积。"""

    tall = Image.new("RGBA", (240, 480), (0, 0, 0, 0))
    ImageDraw.Draw(tall).rectangle(
        (90, 30, 150, 430),
        fill=(40, 40, 40, 255),
    )
    wide = Image.new("RGBA", (480, 240), (0, 0, 0, 0))
    ImageDraw.Draw(wide).rectangle(
        (30, 90, 430, 150),
        fill=(40, 40, 40, 255),
    )
    target_mass = 28_000.0

    tall_result = build_pet_workspace.normalize_pose_by_alpha_mass(
        tall,
        target_mass,
        canvas_size=(560, 500),
    )
    wide_result = build_pet_workspace.normalize_pose_by_alpha_mass(
        wide,
        target_mass,
        canvas_size=(560, 500),
    )

    assert build_pet_workspace.alpha_mass(tall_result) == pytest.approx(
        target_mass,
        rel=0.03,
    )
    assert build_pet_workspace.alpha_mass(wide_result) == pytest.approx(
        target_mass,
        rel=0.03,
    )


def test_green_edge_despill_preserves_interior_green() -> None:
    """边缘去溢色只应处理透明轮廓附近，不得抹除主体内部绿色。"""

    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((6, 6, 25, 25), fill=(230, 140, 40, 255))
    draw.rectangle((6, 6, 25, 6), fill=(0, 255, 0, 255))
    draw.rectangle((14, 14, 17, 17), fill=(0, 255, 0, 255))

    cleaned = build_pet_workspace.despill_green_edges(
        image,
        edge_radius=2,
    )

    assert cleaned.getpixel((10, 6))[1] == 0
    assert cleaned.getpixel((15, 15)) == (0, 255, 0, 255)
    assert cleaned.getpixel((0, 0))[3] == 0


def test_initialize_image_workspace_creates_private_selfie(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """图片来源应创建独立工作区、原始副本和自拍 PNG。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    source = tmp_path / "source.jpg"
    Image.new("RGB", (640, 480), (120, 80, 60)).save(source, "JPEG")

    workspace = pet_workspace.initialize_workspace(
        "sample-pet",
        display_name="示例桌宠",
        source=source,
        selfie_frame=None,
        recommended_display_height=450,
    )

    config = pet_workspace.load_config(workspace)
    assert config["source_type"] == "image"
    assert config["recommended_display_height"] == 450
    assert config["rendering_mode"] == "sprite"
    assert config["touch_feedback"]["default_transition_frame_count"] == 2
    assert config["care_simulation"] == {"enabled": False}
    assert config["extension_animations"] == {}
    assert config["directional_mouse_follow"] == {"enabled": False}
    assert config["action_source_gate"]["required"] is True
    assert (workspace / "actions" / "candidates").is_dir()
    assert (workspace / "source" / "original.jpg").is_file()
    assert (workspace / "source" / "selfie.png").is_file()
    assert not (workspace / "candidate" / "approved.png").exists()


def test_agent_snapshot_waits_for_style_before_character_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """来源登记后必须先等待画风选择，不能直接批量生成角色或动作。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 480), (80, 70, 60)).save(source, "PNG")
    workspace = pet_workspace.initialize_workspace(
        "agent-style",
        display_name="Agent 画风门禁",
        source=source,
        selfie_frame=None,
        recommended_display_height=220,
    )

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "awaiting_character_style"
    assert snapshot["must_pause"] is True
    assert snapshot["allowed_next_actions"] == [
        "choose_style_and_generate_one_character_candidate"
    ]
    assert snapshot["evidence"]["workflow_sha256"]


def test_agent_snapshot_exposes_exact_next_action_and_replans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """控制快照应随真实门禁变化，从单动作推进到走路审核和构建。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 480), (80, 70, 60)).save(source, "PNG")
    workspace = pet_workspace.initialize_workspace(
        "agent-replan",
        display_name="Agent 重规划",
        source=source,
        selfie_frame=None,
        recommended_display_height=220,
    )
    make_pose(workspace / "candidate" / "approved.png")
    approve_workspace_character(workspace)

    action_snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert action_snapshot["phase"] == "action_source_review"
    assert action_snapshot["decision"]["target"] == "wave"
    assert action_snapshot["must_pause"] is False

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-complete")
    walk_snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert walk_snapshot["phase"] == "walk_review_generation"
    assert walk_snapshot["decision"]["action"] == "create_walk_review"

    approve_workspace_walk(workspace)
    build_snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert build_snapshot["phase"] == "ready_to_build"
    assert build_snapshot["decision"]["action"] == (
        "build_and_activate_staging"
    )
    assert build_snapshot["blockers"] == []


def test_agent_snapshot_routes_stale_action_to_approved_character(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """动作入库后被替换时必须进入修复态，并回到批准角色而非残次帧。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-stale")
    Image.new("RGBA", (320, 480), (255, 0, 0, 255)).save(
        workspace / "actions" / "transparent" / "wave.png",
        "PNG",
    )

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "action_source_repair"
    assert snapshot["decision"]["kind"] == "repair"
    assert snapshot["decision"]["action"] == (
        "repair_stale_action_source_gate"
    )
    assert any(
        "已在质检后被替换" in item["message"]
        for item in snapshot["blockers"]
    )


def test_agent_snapshot_requires_authorized_source_even_with_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """无来源、无迁移证据时不能用一个候选文件绕过授权身份锚点。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    workspace = pet_workspace.initialize_workspace(
        "agent-no-source",
        display_name="无来源候选",
        source=None,
        selfie_frame=None,
        recommended_display_height=220,
    )
    make_pose(workspace / "candidate" / "approved.png")
    approve_workspace_character(workspace)

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "awaiting_authorized_source"
    assert snapshot["allowed_next_actions"] == ["register_authorized_source"]

    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["migration_evidence"] = ["x"]
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "awaiting_authorized_source"
    )
    assert any(
        "授权来源" in failure
        for failure in pet_workspace.validate_workflow_bindings(workspace)
    )


def test_agent_snapshot_rejects_missing_or_hash_mismatched_source_reference(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """伪路径或伪哈希不得冒充已登记的授权来源。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    workspace = pet_workspace.initialize_workspace(
        "agent-fake-source",
        display_name="伪来源",
        source=None,
        selfie_frame=None,
        recommended_display_height=220,
    )
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["source"]["references"] = [
        {
            "path": "source/does-not-exist.png",
            "sha256": "a" * 64,
            "relationship": "same_subject",
        }
    ]
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "awaiting_authorized_source"
    )

    source = workspace / "source" / "does-not-exist.png"
    source.write_bytes(b"real-source")
    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "awaiting_authorized_source"
    )


@pytest.mark.parametrize(
    ("relative", "expected_fragment"),
    (
        ("workflow.json", "workflow"),
        ("review/action-source-gate.json", "action-source"),
    ),
)
def test_agent_snapshot_reports_corrupted_state_as_stable_repair(
    tmp_path: Path,
    monkeypatch,
    relative: str,
    expected_fragment: str,
) -> None:
    """损坏状态 JSON 应产生可恢复 blocker，而不是向上抛解析异常。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-bad-json")
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{broken", encoding="utf-8")

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "workspace_state_repair"
    assert snapshot["decision"]["action"] == "repair_corrupted_workspace_state"
    assert snapshot["decision"]["command"] is None
    assert expected_fragment in relative


@pytest.mark.parametrize(
    "relative",
    ("workflow.json", "review/action-source-gate.json"),
)
def test_agent_snapshot_reports_non_utf8_state_as_stable_repair(
    tmp_path: Path,
    monkeypatch,
    relative: str,
) -> None:
    """非 UTF-8 状态文件也必须进入稳定修复态。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-bad-utf8")
    target = workspace / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe\xfa")

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "workspace_state_repair"
    assert snapshot["decision"]["action"] == "repair_corrupted_workspace_state"


@pytest.mark.parametrize("payload", (b"{broken", b"\xff\xfe\xfa"))
def test_agent_snapshot_reports_corrupted_pet_config_as_stable_repair(
    tmp_path: Path,
    monkeypatch,
    payload: bytes,
) -> None:
    """损坏或非 UTF-8 pet-config 不得让只读控制面崩溃。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-bad-config")
    (workspace / "pet-config.json").write_bytes(payload)

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "workspace_state_repair"
    assert snapshot["blockers"][0]["code"] == "pet_config_invalid"


def test_runtime_attestation_and_final_review_close_the_agent_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """最终验收必须绑定当前 build；配置变化后旧 runtime 应立即失效。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-runtime")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)

    awaiting = pet_workspace.agent_control_snapshot(workspace)
    assert awaiting["phase"] == "awaiting_final_visual_review"
    assert awaiting["decision"]["action"] == (
        "present_runtime_evidence_for_final_review"
    )
    assert awaiting["decision"]["command"] is None
    assert "--build-id" in awaiting["decision"]["approval_command"]
    assert "reject-runtime" in awaiting["decision"]["rejection_command_template"]
    assert awaiting["decision"]["rejection_command"] is None
    assert "user_feedback" in awaiting["decision"]["required_inputs"]

    attestation = pet_workspace.inspect_runtime_build_attestation(workspace)
    with pytest.raises(ValueError, match="build_id 已变化"):
        pet_workspace.approve_runtime_review(
            workspace,
            confirmed=True,
            expected_build_id="wrong-build",
            expected_review_visual_fingerprint=str(
                attestation["review_visual_fingerprint"]
            ),
        )
    review = pet_workspace.approve_runtime_review(
        workspace,
        confirmed=True,
        expected_build_id=str(attestation["build_id"]),
        expected_review_visual_fingerprint=str(
            attestation["review_visual_fingerprint"]
        ),
    )
    assert review["status"] == "approved"
    ready = pet_workspace.agent_control_snapshot(workspace)
    assert ready["phase"] == "ready_to_release"
    assert ready["decision"]["action"] == "release_validated_pet"
    assert pet_workspace.current_runtime_review_status(workspace)["status"] == (
        "passed"
    )

    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["recommended_display_height"] += 1
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stale = pet_workspace.agent_control_snapshot(workspace)
    assert stale["phase"] == "runtime_rebuild_required"
    assert stale["decision"]["action"] == "rebuild_attested_runtime"
    assert pet_workspace.current_runtime_review_status(workspace)["status"] == (
        "failed"
    )


def test_release_only_sharing_change_does_not_invalidate_runtime_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """只影响发布隐私模式的 sharing 授权不应强制重建 runtime。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "sharing-only")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)
    attestation = pet_workspace.inspect_runtime_build_attestation(workspace)
    pet_workspace.approve_runtime_review(
        workspace,
        confirmed=True,
        expected_build_id=str(attestation["build_id"]),
        expected_review_visual_fingerprint=str(
            attestation["review_visual_fingerprint"]
        ),
    )
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["sharing"] = {"selfie_approved_sha256": "f" * 64}
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "ready_to_release"
    )


def test_inactive_approval_metadata_does_not_invalidate_runtime_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """未启用扩展或功能的占位审批记录不属于当前 runtime 构建输入。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "inactive-approvals")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)
    attestation = pet_workspace.inspect_runtime_build_attestation(workspace)
    pet_workspace.approve_runtime_review(
        workspace,
        confirmed=True,
        expected_build_id=str(attestation["build_id"]),
        expected_review_visual_fingerprint=str(
            attestation["review_visual_fingerprint"]
        ),
    )
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow.setdefault("extension_approvals", {})["future_pack"] = {
        "status": "not_requested",
        "items": {},
    }
    workflow.setdefault("feature_status", {})["future_feature"] = {
        "status": "not_requested"
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "ready_to_release"
    )


def test_application_source_change_invalidates_runtime_attestation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """审批后的应用源码/打包资源变化必须使旧 runtime 取证失效。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "app-change")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)
    monkeypatch.setattr(
        pet_workspace,
        "application_build_evidence",
        lambda: {"schema_version": 1, "files": {}, "fingerprint": "0" * 64},
    )

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "runtime_rebuild_required"
    assert any(
        "应用源码" in blocker["message"]
        for blocker in snapshot["blockers"]
    )


def test_runtime_rejection_persists_feedback_and_requires_new_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """最终视觉否决必须绑定当前 build，并进入修订态而非再次索要批准。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "runtime-reject")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)
    attestation = pet_workspace.inspect_runtime_build_attestation(workspace)
    rejected = pet_workspace.reject_runtime_review(
        workspace,
        confirmed=True,
        reason="摸头回弹太突然",
        expected_build_id=str(attestation["build_id"]),
        expected_review_visual_fingerprint=str(
            attestation["review_visual_fingerprint"]
        ),
    )

    assert rejected["status"] == "rejected"
    snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert snapshot["phase"] == "runtime_revision_required"
    assert "摸头回弹太突然" in snapshot["blockers"][0]["message"]


def test_pending_walk_tamper_invalidates_approval_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """展示后的八帧变化必须撤销旧 CAS 命令并要求生成新 GIF。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "pending-walk")
    walk_paths = [
        workspace / "walk" / "frames" / f"walk_{index:02d}.png"
        for index in range(1, 9)
    ]
    preview = workspace / "review" / "walk-user-review.gif"
    Image.new("RGB", (32, 32), "white").save(preview, "GIF")
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["walk"] = {
        "status": "awaiting_user_confirmation",
        "preview": preview.relative_to(pet_workspace.PROJECT_ROOT).as_posix(),
        "preview_sha256": pet_workspace.file_sha256(preview),
        "asset_sha256": pet_workspace.file_bundle_sha256(walk_paths),
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    awaiting = pet_workspace.agent_control_snapshot(workspace)
    assert awaiting["phase"] == "awaiting_walk_approval"
    assert awaiting["decision"]["approval_command"]
    walk_paths[0].write_bytes(walk_paths[0].read_bytes() + b"tampered")

    stale = pet_workspace.agent_control_snapshot(workspace)
    assert stale["phase"] == "walk_review_generation"
    assert stale["decision"]["approval_command"] is None


def test_empty_runtime_manifest_and_old_qa_never_reach_final_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """仅有空 manifest 与旧 passed QA 不能冒充当前成功构建。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-old-runtime")
    approve_workspace_walk(workspace)
    runtime = workspace / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "manifest.json").write_text("{}\n", encoding="utf-8")
    review = workspace / "review"
    (review / "assets-qa.json").write_text(
        '{"status":"passed"}\n',
        encoding="utf-8",
    )

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "runtime_rebuild_required"
    assert snapshot["phase"] != "awaiting_final_visual_review"


@pytest.mark.parametrize(
    "relative",
    (
        "runtime/idle/idle_01.png",
        "review/walk-runtime-preview.gif",
    ),
)
def test_runtime_or_visual_evidence_tampering_invalidates_final_review(
    tmp_path: Path,
    monkeypatch,
    relative: str,
) -> None:
    """运行帧或用户查看证据被替换后，旧 build 验收不得继续放行。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "agent-tamper")
    approve_workspace_walk(workspace)
    write_attested_runtime(workspace)
    attestation = pet_workspace.inspect_runtime_build_attestation(workspace)
    pet_workspace.approve_runtime_review(
        workspace,
        confirmed=True,
        expected_build_id=str(attestation["build_id"]),
        expected_review_visual_fingerprint=str(
            attestation["review_visual_fingerprint"]
        ),
    )
    target = workspace / relative
    target.write_bytes(target.read_bytes() + b"tampered")

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "runtime_rebuild_required"
    assert pet_workspace.current_runtime_review_status(workspace)["status"] == (
        "failed"
    )


@pytest.mark.parametrize("retain_unapproved_walk_frames", [False, True])
def test_approved_static_interaction_builds_stationary_runtime_without_approved_walk(
    tmp_path: Path,
    monkeypatch,
    retain_unapproved_walk_frames: bool,
) -> None:
    """静止互动审批必须忽略缺失或残留但未审批的走路帧。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "static-route")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["pet_mode"] = "static"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not retain_unapproved_walk_frames:
        for path in (workspace / "walk" / "frames").glob("walk_*.png"):
            path.unlink()
    preview = workspace / "review" / "static-interaction.gif"
    Image.new("RGB", (32, 32), "white").save(preview, "GIF")
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    preview_hash = pet_workspace.file_sha256(preview)
    character_hash = workflow["character"]["candidate_sha256"]
    workflow["interaction"] = {
        "status": "approved",
        "mode": "stationary_character",
        "approved_mode": "stationary_character",
        "preview": preview.relative_to(pet_workspace.PROJECT_ROOT).as_posix(),
        "preview_sha256": preview_hash,
        "approved_asset_sha256": preview_hash,
        "character_sha256": character_hash,
        "approved_character_sha256": character_hash,
        "user_confirmed": True,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert snapshot["phase"] == "ready_to_build"
    runtime, report = build_pet_workspace.assemble_workspace(workspace)
    manifest = json.loads(
        (runtime / "manifest.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "passed"
    assert manifest["stationary"] is True
    assert manifest["motion_source"] == "approved_interaction_fallback"
    if retain_unapproved_walk_frames:
        make_walk(workspace / "walk" / "frames" / "walk_01.png", 7)
        assert pet_workspace.inspect_runtime_build_attestation(workspace)[
            "status"
        ] == "passed"


@pytest.mark.parametrize("pet_mode", ["static", "vehicle-seated"])
def test_agent_control_closes_interaction_preview_feedback_loop(
    tmp_path: Path,
    monkeypatch,
    pet_mode: str,
) -> None:
    """静态与载具路线必须经历提交、等待、否决、重提和批准的完整闭环。"""

    workspace = make_complete_workspace(
        tmp_path,
        monkeypatch,
        f"interaction-loop-{pet_mode}",
    )
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["pet_mode"] = pet_mode
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    first = pet_workspace.agent_control_snapshot(workspace)
    assert first["phase"] == "interaction_preview_generation"
    assert first["decision"]["action"] == (
        "create_and_submit_interaction_preview"
    )
    assert first["decision"]["command"] is None
    assert "{preview_path}" in first["decision"]["command_template"]
    assert "preview_path" in first["decision"]["required_inputs"]

    preview = workspace / "review" / "interaction-preview.gif"
    Image.new("RGB", (32, 32), "white").save(preview, "GIF")
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    character_hash = workflow["character"]["candidate_sha256"]
    preview_hash = pet_workspace.file_sha256(preview)
    workflow["interaction"] = {
        "status": "awaiting_user_confirmation",
        "mode": "stationary_character",
        "preview": preview.relative_to(pet_workspace.PROJECT_ROOT).as_posix(),
        "preview_sha256": preview_hash,
        "character_sha256": character_hash,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pending = pet_workspace.agent_control_snapshot(workspace)
    assert pending["phase"] == "awaiting_interaction_approval"
    assert "approve-interaction" in pending["decision"]["approval_command"]
    assert "reject-interaction" in pending["decision"][
        "rejection_command_template"
    ]
    assert pending["decision"]["rejection_command"] is None
    assert preview_hash in pending["decision"]["approval_command"]

    workflow["interaction"].update(
        {
            "status": "rejected",
            "user_confirmed": False,
            "rejected_preview_sha256": preview_hash,
            "rejected_character_sha256": character_hash,
            "rejected_mode": "stationary_character",
            "rejection_reason": "循环末尾有跳帧",
        }
    )
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rejected = pet_workspace.agent_control_snapshot(workspace)
    assert rejected["phase"] == "interaction_revision_required"
    assert rejected["blockers"][0]["message"] == "循环末尾有跳帧"

    Image.new("RGB", (32, 32), "black").save(preview, "GIF")
    changed = pet_workspace.agent_control_snapshot(workspace)
    assert changed["phase"] == "interaction_preview_generation"
    new_hash = pet_workspace.file_sha256(preview)
    workflow["interaction"] = {
        "status": "awaiting_user_confirmation",
        "mode": "stationary_character",
        "preview": preview.relative_to(pet_workspace.PROJECT_ROOT).as_posix(),
        "preview_sha256": new_hash,
        "character_sha256": character_hash,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "awaiting_interaction_approval"
    )

    workflow["interaction"].update(
        {
            "status": "approved",
            "user_confirmed": True,
            "approved_asset_sha256": new_hash,
            "approved_character_sha256": character_hash,
            "approved_mode": "stationary_character",
        }
    )
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "ready_to_build"
    )


@pytest.mark.parametrize("pet_mode", ["full-body", "animal"])
def test_full_body_pet_cannot_replace_walk_with_static_interaction(
    tmp_path: Path,
    monkeypatch,
    pet_mode: str,
) -> None:
    """完整全身或动物项目不能用静止互动审批绕过八相位走路门禁。"""

    workspace = make_complete_workspace(
        tmp_path,
        monkeypatch,
        f"walk-only-{pet_mode}",
    )
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["pet_mode"] = pet_mode
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for path in (workspace / "walk" / "frames").glob("walk_*.png"):
        path.unlink()
    preview = workspace / "review" / "static-interaction.gif"
    Image.new("RGB", (32, 32), "white").save(preview, "GIF")
    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    preview_hash = pet_workspace.file_sha256(preview)
    character_hash = workflow["character"]["candidate_sha256"]
    workflow["interaction"] = {
        "status": "approved",
        "mode": "stationary_character",
        "approved_mode": "stationary_character",
        "preview": preview.relative_to(pet_workspace.PROJECT_ROOT).as_posix(),
        "preview_sha256": preview_hash,
        "approved_asset_sha256": preview_hash,
        "character_sha256": character_hash,
        "approved_character_sha256": character_hash,
        "user_confirmed": True,
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "walk_generation"
    )
    for index, horizontal in enumerate(
        (-4, -2, 0, 2, 4, 2, 0, -2),
        start=1,
    ):
        make_walk(
            workspace / "walk" / "frames" / f"walk_{index:02d}.png",
            horizontal,
        )
    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "walk_review_generation"
    )

    failures = pet_workspace.validate_workflow_bindings(workspace)

    assert any("pet_mode" in failure for failure in failures)
    monkeypatch.setattr(
        build_pet_workspace,
        "validate_workflow_bindings",
        pet_workspace.validate_workflow_bindings,
    )
    with pytest.raises(ValueError):
        build_pet_workspace.assemble_workspace(workspace)


def test_action_source_review_rejects_failed_limb_integrity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """四手、断肢或粘连道具任一失败时都不得把候选写入正式动作。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    workspace = pet_workspace.initialize_workspace(
        "limb-gate-pet",
        display_name="肢体门禁",
        source=None,
        selfie_frame=None,
        recommended_display_height=220,
    )
    make_pose(workspace / "candidate" / "approved.png")
    approve_workspace_character(workspace)
    candidates = [
        workspace / "actions" / "candidates" / "wave" / "v1" / f"candidate_{index:02d}.png"
        for index in (1, 2)
    ]
    for path in candidates:
        make_pose(path)

    checks = {check: True for check in pet_workspace.ACTION_REVIEW_CHECKS}
    checks["limb_integrity"] = False
    with pytest.raises(ValueError, match="limb_integrity"):
        pet_workspace.record_action_source_review(
            workspace,
            action="wave",
            candidates=candidates,
            selected=candidates[0],
            checks=checks,
            confirmed=True,
        )

    assert not (workspace / "actions" / "transparent" / "wave.png").exists()
    assert not (workspace / "review" / "action-source-gate.json").exists()


def test_action_source_gate_detects_post_review_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """通过质检后被悄悄替换的动作必须使整体校验失败。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "replaced-action")
    changed = workspace / "actions" / "transparent" / "wave.png"
    Image.new("RGBA", (320, 480), (255, 0, 0, 255)).save(changed, "PNG")

    failures = pet_workspace.validate_action_source_gate(workspace)

    assert "动作 wave 已在质检后被替换" in failures


def test_initialize_workspace_only_selects_live2d_explicitly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """混合 Live2D 必须由调用方显式选择，不能从普通建宠请求推断。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    workspace = pet_workspace.initialize_workspace(
        "explicit-live2d",
        display_name="显式混合版",
        source=None,
        selfie_frame=None,
        recommended_display_height=220,
        rendering_mode="hybrid-live2d",
    )

    config = pet_workspace.load_config(workspace)
    assert config["rendering_mode"] == "hybrid-live2d"


def test_invalid_source_does_not_leave_partial_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """来源路径无效时不应留下阻止重试的半成品目录。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    with pytest.raises(FileNotFoundError):
        pet_workspace.initialize_workspace(
            "missing-pet",
            display_name="缺失来源",
            source=tmp_path / "missing.png",
            selfie_frame=None,
            recommended_display_height=220,
        )
    assert not (tmp_path / "pets" / "missing-pet").exists()


def test_generated_character_workspace_can_explicitly_omit_original_selfie(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """虚构/历史生成角色不得为了通过校验而伪造原始自拍。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "no-selfie-pet")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["source_type"] = "legacy-generated-assets"
    config["selfie_required"] = False
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (workspace / "source" / "selfie.png").unlink()

    result = pet_workspace.validate_workspace(workspace)

    assert result["status"] == "passed"
    assert result["summary"]["selfie_required"] is False
    assert result["summary"]["selfie_ready"] is False


def test_walk_review_is_available_before_runtime_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """八帧走路应能在完整运行目录和走路批准之前生成确认 GIF。"""

    monkeypatch.setattr(pet_workspace, "PETS_ROOT", tmp_path / "pets")
    monkeypatch.setattr(pet_workspace, "PROJECT_ROOT", tmp_path)
    source = tmp_path / "source.png"
    Image.new("RGB", (480, 640), (80, 80, 80)).save(source, "PNG")
    workspace = pet_workspace.initialize_workspace(
        "walk-review-pet",
        display_name="走路预览",
        source=source,
        selfie_frame=None,
        recommended_display_height=220,
    )
    make_pose(workspace / "candidate" / "approved.png")
    approve_workspace_character(workspace)
    for index, horizontal in enumerate((-4, -2, 0, 2, 4, 2, 0, -2), start=1):
        make_walk(
            workspace / "walk" / "frames" / f"walk_{index:02d}.png",
            horizontal,
        )

    def fake_review(manifest_path: Path) -> dict[str, object]:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["animations"]["walk"]) == 8
        preview = tmp_path / "user_assets" / "review" / "walk-preview.gif"
        preview.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), "white").save(preview, "GIF")
        return {
            "walk": {
                "status": "awaiting_user_confirmation",
                "preview": "user_assets/review/walk-preview.gif",
                "asset_sha256": "test-walk-bundle",
            }
        }

    monkeypatch.setattr(onepic_workflow, "create_walk_review", fake_review)
    preview = pet_workspace.create_workspace_walk_review(workspace)

    assert preview == workspace / "review" / "walk-user-review.gif"
    assert preview.is_file()
    workflow = json.loads(
        (workspace / "workflow.json").read_text(encoding="utf-8")
    )
    assert workflow["walk"]["preview"] == (
        "pets/walk-review-pet/review/walk-user-review.gif"
    )
    assert workflow["walk"]["preview_sha256"] == pet_workspace.file_sha256(
        preview
    )
    monkeypatch.setattr(onepic_workflow, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        onepic_workflow,
        "STATE_PATH",
        workspace / "workflow.json",
    )
    Image.new("RGB", (32, 32), "black").save(preview, "GIF")
    with pytest.raises(
        onepic_workflow.WorkflowError,
        match="预览已在提交后变化",
    ):
        onepic_workflow.approve_walk(
            True,
            expected_preview_sha256=workflow["walk"]["preview_sha256"],
            expected_asset_sha256=workflow["walk"]["asset_sha256"],
        )
    assert not (workspace / "runtime" / "manifest.json").exists()


def test_optional_selfie_activation_clears_stale_photo_and_rollback_restores_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """无自拍宠物激活时不能沿用上一只照片，且回滚必须恢复旧照片。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "optional-selfie")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["selfie_required"] = False
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (workspace / "source" / "selfie.png").unlink()
    write_attested_runtime(workspace)
    runtime = workspace / "runtime"

    user_root = tmp_path / "user_assets"
    active_pet = user_root / "pet"
    active_pet.mkdir(parents=True)
    (active_pet / "manifest.json").write_text(
        '{"pet_id":"old-pet","display_name":"旧宠物","total_frame_count":38}\n',
        encoding="utf-8",
    )
    old_selfie = user_root / "selfie.png"
    old_selfie.write_bytes(b"old-private-photo")
    monkeypatch.setattr(build_pet_workspace, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(build_pet_workspace, "USER_ROOT", user_root)
    monkeypatch.setattr(build_pet_workspace, "ACTIVE_PET_ROOT", active_pet)
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )

    result = build_pet_workspace.activate_workspace(workspace, runtime)

    assert not old_selfie.exists()
    assert Path(str(result["previous_selfie_backup"])).read_bytes() == (
        b"old-private-photo"
    )
    assert json.loads(
        (active_pet / "manifest.json").read_text(encoding="utf-8")
    )["pet_id"] == "optional-selfie"

    build_pet_workspace.rollback_active_pet()

    assert old_selfie.read_bytes() == b"old-private-photo"
    assert json.loads(
        (active_pet / "manifest.json").read_text(encoding="utf-8")
    )["pet_id"] == "old-pet"


def test_activation_rejects_stale_attested_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """当前输入变化后，旧 passed QA 不能通过 activate-existing 激活。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "stale-activation")
    write_attested_runtime(workspace)
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["recommended_display_height"] += 1
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="构建取证未通过"):
        build_pet_workspace.activate_workspace(
            workspace,
            workspace / "runtime",
        )


def test_generic_new_pet_builder_creates_default_40_frames_and_reviews(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """新宠物默认应装配 38 帧基础 + 2 张摸头过渡帧。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "builder-pet")

    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )
    runtime, report = build_pet_workspace.assemble_workspace(workspace)

    assert report["status"] == "passed"
    assert report["frame_count"] == 40
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pet_id"] == "builder-pet"
    assert manifest["rendering_mode"] == "sprite"
    assert len(manifest["animations"]["walk"]) == 8
    assert manifest["extension_animations"] == {}
    assert manifest["care_simulation"] == {"enabled": False}
    assert manifest["extension_frame_count"] == 0
    assert manifest["directional_frame_count"] == 0
    assert "directional_mouse_follow" not in manifest
    assert (workspace / "review" / "all-actions-contact-sheet.jpg").is_file()
    assert (workspace / "review" / "walk-runtime-preview.gif").is_file()
    assert (workspace / "review" / "sit-sleep-wake-preview.gif").is_file()
    assert (workspace / "review" / "touch-feedback-preview.gif").is_file()


def test_builder_includes_two_optional_touch_frames_for_new_pet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """标准可选槽位存在时应装配为 38+2=40 帧并纳入 QA。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "touch-pet")
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )

    runtime, report = build_pet_workspace.assemble_workspace(workspace)

    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert report["base_frame_count"] == 38
    assert report["optional_touch_frame_count"] == 2
    assert report["total_frame_count"] == 40
    assert len(manifest["touch_feedback"]["transition_frames"]) == 2
    assert all(
        (runtime / relative).is_file()
        for relative in manifest["touch_feedback"]["transition_frames"]
    )


def test_builder_copies_generic_companion_behaviors_to_runtime_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """角色工作区声明的陪伴功能应原样进入运行清单，而不是写死在角色脚本中。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "companion-pet")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["companion_behaviors"] = {
        "cursor_chase": {"enabled": True, "default_enabled": False},
        "keyboard_companion": {"enabled": True, "burst_key_count": 5},
        "focus_session": {"enabled": True, "duration_minutes": 25},
        "signature_action": {"enabled": True, "state": "wave"},
    }
    config["dialogue_system"] = {
        "enabled": True,
        "cooldown_ms": 2600,
        "lines": {
            "wave": ["你好", "今天也请多关照"],
            "happy": ["开心～"],
        },
    }
    config["care_simulation"] = {
        "enabled": True,
        "initial_coins": 88,
        "feed_cost": 9,
        "focus_reward_coins": 21,
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )

    runtime, _report = build_pet_workspace.assemble_workspace(workspace)

    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["companion_behaviors"] == config["companion_behaviors"]
    assert manifest["dialogue_system"]["enabled"] is True
    assert manifest["dialogue_system"]["cooldown_ms"] == 2600
    assert manifest["dialogue_system"]["lines"] == config["dialogue_system"]["lines"]
    assert manifest["care_simulation"]["enabled"] is True
    assert manifest["care_simulation"]["initial_coins"] == 88
    assert manifest["care_simulation"]["feed_cost"] == 9
    assert manifest["care_simulation"]["focus_reward_coins"] == 21


def test_builder_validates_and_packages_break_reminders_and_wav(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """休息计划和显式 WAV 应进入同一桌宠运行目录，默认开关保持关闭。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "break-pet")
    sound = workspace / "sounds" / "soft-reminder.wav"
    sound.parent.mkdir(parents=True, exist_ok=True)
    sound.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["break_reminders"]["sounds"]["files"] = {
        "reminder": "sounds/soft-reminder.wav"
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    runtime, _report = build_pet_workspace.assemble_workspace(workspace)
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["break_reminders"]["enabled"] is False
    kinds = {
        item["kind"]
        for item in manifest["break_reminders"]["schedule"]["reminders"]
    }
    assert {"micro_break", "long_break", "pomodoro", "water", "sedentary"} <= kinds
    assert manifest["break_reminders"]["privacy"]["network_default"] == "off"
    assert (runtime / "sounds" / "soft-reminder.wav").read_bytes() == sound.read_bytes()


def test_builder_state_fallback_prevents_disabled_expression_from_returning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """按宠物禁用的基础表情应复用回退帧，且不再写出旧表情文件。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "fallback-pet")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["state_fallbacks"] = {"annoyed": "surprised"}
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )

    runtime, _report = build_pet_workspace.assemble_workspace(workspace)

    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["state_fallbacks"] == {"annoyed": "surprised"}
    assert manifest["animations"]["annoyed"] == manifest["animations"]["surprised"]
    assert not (runtime / "expressions" / "annoyed_01.png").exists()


def test_builder_stages_extension_animations_and_directional_gaze(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """扩展动作、可选走路和九向视线应独立装配并计入完整素材数量。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "extension-pet")
    extension_root = workspace / "actions" / "extensions"
    for index in range(1, 3):
        make_walk(extension_root / "jump" / f"jump_{index:02d}.png", index)
    for index in range(1, 9):
        make_walk(
            extension_root / "side_walk_v1" / f"side_walk_{index:02d}.png",
            index % 3,
        )
    directions = (
        "center",
        "north",
        "north_east",
        "east",
        "south_east",
        "south",
        "south_west",
        "west",
        "north_west",
    )
    for index, direction in enumerate(directions):
        make_walk(extension_root / "gaze" / f"{direction}.png", index % 3)

    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["extension_animations"] = {
        "jump": {
            "enabled": True,
            "frames": [
                "actions/extensions/jump/jump_01.png",
                "actions/extensions/jump/jump_02.png",
            ],
            "frame_duration_ms": 130,
            "loop": False,
            "base_state": "idle",
            "menu_label": "开心跳一下",
        },
        "side_walk_v1": {
            "enabled": True,
            "frames": [
                (
                    "actions/extensions/side_walk_v1/"
                    f"side_walk_{index:02d}.png"
                )
                for index in range(1, 9)
            ],
            "frame_duration_ms": 115,
            "loop": True,
            "base_state": "walk",
            "walk_variant": True,
            "walk_variant_label": "侧面走路",
            "walk_variant_default": False,
            "mirror_with_direction": True,
        }
    }
    config["directional_mouse_follow"] = {
        "enabled": True,
        "frames": {
            direction: f"actions/extensions/gaze/{direction}.png"
            for direction in directions
        },
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    runtime, report = build_pet_workspace.assemble_workspace(workspace)

    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["extension_frame_count"] == 10
    assert manifest["directional_frame_count"] == 9
    assert manifest["total_asset_frame_count"] == 59
    assert manifest["extension_animations"]["jump"]["menu_label"] == "开心跳一下"
    side_walk = manifest["extension_animations"]["side_walk_v1"]
    assert len(side_walk["frames"]) == 8
    assert side_walk["walk_variant"] is True
    assert side_walk["walk_variant_label"] == "侧面走路"
    assert side_walk["walk_variant_default"] is False
    assert side_walk["mirror_with_direction"] is True
    assert set(manifest["directional_mouse_follow"]["frames"]) == set(directions)
    assert report["extension_animations_qa"]["jump"]["status"] == "passed"
    assert report["extension_animations_qa"]["side_walk_v1"] == {
        "status": "passed",
        "frame_count": 8,
        "source_sha256": [
            build_pet_workspace._sha256(
                extension_root
                / "side_walk_v1"
                / f"side_walk_{index:02d}.png"
            )
            for index in range(1, 9)
        ],
        "walk_variant": True,
    }
    assert report["directional_mouse_follow_qa"]["status"] == "passed"
    assert all(
        (runtime / relative).is_file()
        for relative in manifest["extension_animations"]["jump"]["frames"]
    )


def test_builder_normalizes_extension_height_to_base_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """扩展动作可在运行时对齐基础状态体型，且不改动审批源图。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "size-normalized")
    extension_path = (
        workspace
        / "actions"
        / "extensions"
        / "large_idle"
        / "large_01.png"
    )
    extension = Image.new("RGBA", (560, 500), (0, 0, 0, 0))
    ImageDraw.Draw(extension).rounded_rectangle(
        (130, 38, 430, 488),
        radius=40,
        fill=(30, 30, 35, 255),
    )
    extension_path.parent.mkdir(parents=True, exist_ok=True)
    extension.save(extension_path, "PNG")

    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["extension_size_normalization"] = {
        "enabled": True,
        "mode": "base_state_height",
        "target_padding": 16,
        "minimum_scale": 0.6,
        "maximum_scale": 1.4,
    }
    config["extension_animations"] = {
        "large_idle": {
            "enabled": True,
            "frames": [
                "actions/extensions/large_idle/large_01.png",
            ],
            "frame_duration_ms": 180,
            "loop": False,
            "base_state": "idle",
        }
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    runtime, report = build_pet_workspace.assemble_workspace(workspace)
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    idle = Image.open(runtime / manifest["animations"]["idle"][0]).convert("RGBA")
    extension_runtime = Image.open(
        runtime / manifest["extension_animations"]["large_idle"]["frames"][0]
    ).convert("RGBA")
    idle_bbox = build_pet_workspace.alpha_bbox(idle)
    extension_bbox = build_pet_workspace.alpha_bbox(extension_runtime)
    assert extension_bbox[3] - extension_bbox[1] == pytest.approx(
        idle_bbox[3] - idle_bbox[1],
        abs=1,
    )
    assert extension_bbox[3] == idle_bbox[3]
    normalization = report["extension_animations_qa"]["large_idle"][
        "size_normalization"
    ]
    assert normalization["reference_state"] == "idle"
    assert normalization["scale_factor"] < 1.0
    assert report["extension_animations_qa"]["large_idle"][
        "source_sha256"
    ] == [build_pet_workspace._sha256(extension_path)]


def test_extension_approval_binds_current_frame_bundles_across_packs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """分批确认可共同生效，但任一已确认帧被替换都必须使激活校验失效。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "approved-extension")
    extension_root = workspace / "actions" / "extensions"
    jump_paths = [
        extension_root / "jump" / f"jump_{index:02d}.png"
        for index in range(1, 3)
    ]
    for index, path in enumerate(jump_paths, start=1):
        make_walk(path, index)
    gaze_names = (
        "center",
        "north",
        "north_east",
        "east",
        "south_east",
        "south",
        "south_west",
        "west",
        "north_west",
    )
    gaze_paths = {
        name: extension_root / "gaze" / f"{name}.png"
        for name in gaze_names
    }
    for index, path in enumerate(gaze_paths.values()):
        make_walk(path, index % 3)

    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["extension_animations"] = {
        "jump": {
            "enabled": True,
            "frames": [
                path.relative_to(workspace).as_posix() for path in jump_paths
            ],
        }
    }
    config["directional_mouse_follow"] = {
        "enabled": True,
        "frames": {
            name: path.relative_to(workspace).as_posix()
            for name, path in gaze_paths.items()
        },
    }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    workflow_path = workspace / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    candidate_hash = pet_workspace.file_sha256(
        workspace / "candidate" / "approved.png"
    )
    walk_paths = [
        workspace / "walk" / "frames" / f"walk_{index:02d}.png"
        for index in range(1, 9)
    ]
    walk_hash = pet_workspace.file_bundle_sha256(walk_paths)
    workflow["character"] = {
        "status": "approved",
        "user_confirmed": True,
        "candidate_sha256": candidate_hash,
        "approved_candidate_sha256": candidate_hash,
    }
    workflow["walk"] = {
        "status": "approved",
        "user_confirmed": True,
        "asset_sha256": walk_hash,
        "approved_asset_sha256": walk_hash,
    }
    jump_hash = pet_workspace.file_bundle_sha256(jump_paths)
    gaze_hash = pet_workspace.file_bundle_sha256(
        [gaze_paths[name] for name in sorted(gaze_paths)]
    )
    workflow["extension_approvals"] = {
        "actions_v1": {
            "status": "approved",
            "activation_allowed": True,
            "user_confirmed": True,
            "items": {
                "jump": {
                    "asset_sha256": jump_hash,
                    "approved_asset_sha256": jump_hash,
                }
            },
        },
        "gaze_v2": {
            "status": "approved",
            "activation_allowed": True,
            "user_confirmed": True,
            "items": {
                "gaze": {
                    "asset_sha256": gaze_hash,
                    "approved_asset_sha256": gaze_hash,
                },
            },
        }
    }
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert pet_workspace.validate_workflow_bindings(workspace) == []

    Image.new("RGBA", (560, 500), (0, 0, 0, 0)).save(jump_paths[0], "PNG")

    failures = pet_workspace.validate_workflow_bindings(workspace)
    assert any("扩展动作 jump 确认哈希" in failure for failure in failures)


def test_legacy_pet_without_touch_frames_uses_safe_38_frame_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """迁移配置没有新宠物硬要求时，缺少过渡图仍应生成平滑回退预览。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "legacy-touch-pet")
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["touch_feedback"].pop("default_transition_frame_count")
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for name in ("touch_soften.png", "touch_return.png"):
        (workspace / "actions" / "transparent" / name).unlink()

    runtime, report = build_pet_workspace.assemble_workspace(workspace)

    assert report["total_frame_count"] == 38
    assert report["touch_feedback_qa"]["fallback"].startswith("in-memory")
    assert (workspace / "review" / "touch-feedback-preview.gif").is_file()


def test_new_pet_missing_default_touch_frame_fails_before_activation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """新配置声明默认两帧后，少图必须在 staging 内明确失败。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "missing-touch-pet")
    (workspace / "actions" / "transparent" / "touch_return.png").unlink()

    preflight_failures = pet_workspace.validate_required_touch_transitions(
        workspace
    )
    assert any("touch_return" in item for item in preflight_failures)

    with pytest.raises(ValueError, match="摸头过渡帧派生证据无效"):
        build_pet_workspace.assemble_workspace(workspace)
    assert not (workspace / "runtime" / "manifest.json").exists()


def test_failed_build_archive_error_preserves_original_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows 拒绝 staging 改名时仍应报告真正的素材错误。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "locked-staging-pet")
    monkeypatch.setattr(
        build_pet_workspace,
        "build_walk_quality_report",
        lambda _frames, _config: {
            "status": "failed",
            "failures": ["人工注入失败"],
        },
    )
    original_rename = Path.rename

    def reject_failed_build_archive(self: Path, target: Path) -> Path:
        if self.parent.name == "staging" and Path(target).parent.name == "failed-builds":
            raise PermissionError("simulated Windows lock")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", reject_failed_build_archive)

    with pytest.raises(ValueError, match="人工注入失败"):
        build_pet_workspace.assemble_workspace(workspace)
    markers = list((workspace / "failed-builds").glob("*.archive-error.txt"))
    assert len(markers) == 1
    assert "simulated Windows lock" in markers[0].read_text(encoding="utf-8")


def test_touch_transition_sources_reuse_approved_frames_deterministically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """两张默认过渡图只能由已确认的惊讶、开心和待机素材确定性派生。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "touch-source-pet")
    action_root = workspace / "actions" / "transparent"
    for name in ("touch_soften.png", "touch_return.png"):
        (action_root / name).unlink()
    source_hashes = {
        name: pet_workspace.file_sha256(path)
        for name, path in {
            "candidate": workspace / "candidate" / "approved.png",
            "surprised": action_root / "surprised.png",
            "happy": action_root / "happy.png",
        }.items()
    }
    report = pet_workspace.create_touch_transition_sources(workspace)

    assert report["status"] == "created"
    assert report["method"] in {
        "deterministic_crossfade_of_approved_frames",
        "deterministic_hold_when_reaction_matches_idle",
    }
    for name in ("touch_soften.png", "touch_return.png"):
        path = action_root / name
        assert path.is_file()
        with Image.open(path) as image:
            assert image.size == (560, 500)
            assert image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0
    assert source_hashes == {
        name: pet_workspace.file_sha256(path)
        for name, path in {
            "candidate": workspace / "candidate" / "approved.png",
            "surprised": action_root / "surprised.png",
            "happy": action_root / "happy.png",
        }.items()
    }
    assert pet_workspace.validate_required_touch_transitions(workspace) == []


def test_touch_transition_sources_become_stale_after_happy_action_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """重新批准开心动作后，旧摸头过渡图必须回到确定性重生成阶段。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "touch-stale-pet")
    candidates = [
        workspace
        / "actions"
        / "candidates"
        / "happy"
        / "v2"
        / f"candidate_{index:02d}.png"
        for index in (1, 2)
    ]
    for index, path in enumerate(candidates):
        make_pose(path)
        with Image.open(path) as image:
            changed = image.convert("RGBA")
        ImageDraw.Draw(changed).rectangle(
            (20 + index * 5, 20, 36 + index * 5, 36),
            fill=(220, 40, 80, 255),
        )
        changed.save(path, "PNG")
    pet_workspace.record_action_source_review(
        workspace,
        action="happy",
        candidates=candidates,
        selected=candidates[0],
        checks={check: True for check in pet_workspace.ACTION_REVIEW_CHECKS},
        confirmed=True,
    )

    failures = pet_workspace.validate_required_touch_transitions(workspace)
    assert any("当前来源：happy" in item for item in failures)
    snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert snapshot["phase"] == "touch_transition_generation"


def test_failed_staging_preserves_last_runtime_and_is_quarantined(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """QA 失败不得替换最近可用 runtime，失败 staging 必须可审计。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "failed-pet")
    approve_workspace_walk(workspace)
    old_runtime = workspace / "runtime"
    (old_runtime / "sentinel.txt").write_text("last-known-good", encoding="utf-8")
    monkeypatch.setattr(
        build_pet_workspace,
        "require_custom_pet_approved",
        lambda _path: {},
    )
    monkeypatch.setattr(
        build_pet_workspace,
        "build_walk_quality_report",
        lambda _frames, _config: {
            "status": "failed",
            "failures": ["人工注入失败"],
        },
    )

    with pytest.raises(ValueError, match="人工注入失败"):
        build_pet_workspace.assemble_workspace(workspace)

    assert (old_runtime / "sentinel.txt").read_text(encoding="utf-8") == "last-known-good"
    failed = list((workspace / "failed-builds").iterdir())
    assert len(failed) == 1
    assert (failed[0] / "review" / "assets-qa.json").is_file()
    latest_failure = workspace / "review" / "latest-failure.json"
    assert latest_failure.is_file()
    snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert snapshot["phase"] == "build_failure_recovery"
    assert snapshot["decision"]["action"] == (
        "inspect_and_repair_latest_build_failure"
    )
    assert snapshot["decision"]["command"] is None
    assert "{diagnostic_reason}" in snapshot["decision"][
        "recovery_command_template"
    ]
    assert snapshot["decision"]["recovery_command"] is None
    failure = json.loads(latest_failure.read_text(encoding="utf-8"))
    acknowledgement = pet_workspace.acknowledge_build_failure(
        workspace,
        confirmed=True,
        build_id=failure["build_id"],
        build_input_fingerprint=failure["build_input_fingerprint"],
        classification="transient",
        reason="测试已确认这是人工注入的瞬态 QA 故障",
    )
    assert acknowledgement["status"] == "acknowledged"
    assert not latest_failure.exists()
    assert pet_workspace.agent_control_snapshot(workspace)["phase"] == (
        "ready_to_build"
    )


def test_pre_staging_recipe_failure_is_persisted_for_replanning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """配方加载等 staging 前失败也不得让控制面盲目返回同一构建命令。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "missing-recipe")
    approve_workspace_walk(workspace)
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["animation_recipe"] = "config/missing-audit-recipe.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="missing-audit-recipe"):
        build_pet_workspace.assemble_workspace(workspace)

    failure = json.loads(
        (workspace / "review" / "latest-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["build_input_fingerprint"]
    snapshot = pet_workspace.agent_control_snapshot(workspace)
    assert snapshot["phase"] == "build_failure_recovery"
    assert snapshot["decision"]["action"] != "build_and_activate_staging"


def test_missing_application_input_returns_repair_phase_after_null_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """应用取证缺失且失败指纹为空时，agent-status 也必须稳定返回修复动作。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "missing-app-input")
    approve_workspace_walk(workspace)
    (workspace / "review" / "latest-failure.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pet_id": "missing-app-input",
                "build_id": "failed-before-fingerprint",
                "build_input_fingerprint": None,
                "error": "application evidence missing",
            }
        ),
        encoding="utf-8",
    )
    missing_application = tmp_path / "missing-application-root"
    missing_application.mkdir()
    monkeypatch.setattr(pet_workspace, "APPLICATION_ROOT", missing_application)

    snapshot = pet_workspace.agent_control_snapshot(workspace)

    assert snapshot["phase"] == "build_input_repair"
    assert snapshot["decision"]["action"] == (
        "repair_missing_or_invalid_build_input"
    )
    assert snapshot["decision"]["command"] is None
    assert snapshot["observation"]["build_input_status"] == "failed"


def test_existing_38_frame_project_migrates_with_material_bindings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """历史项目只有在 QA 和人工预览证据齐全时才能建立每宠物审批。"""

    workspace = make_complete_workspace(tmp_path, monkeypatch, "legacy-evidence")
    (workspace / "workflow.json").unlink()
    config_path = workspace / "pet-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["touch_feedback"].pop("default_transition_frame_count")
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for name in ("touch_soften.png", "touch_return.png"):
        (workspace / "actions" / "transparent" / name).unlink()
    runtime, report = build_pet_workspace.assemble_workspace(workspace)
    # assemble_workspace 的门禁已在测试中隔离替代；这里补历史人工预览证据。
    preview = workspace / "review" / "walk-user-review.gif"
    Image.new("RGB", (32, 32), "white").save(preview, "GIF")
    assert report["base_frame_count"] == 38
    assert runtime.is_dir()

    migrated = pet_workspace.migrate_existing_workspace(workspace)

    assert migrated.is_file()
    assert pet_workspace.validate_workflow_bindings(workspace) == []
