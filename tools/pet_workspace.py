"""
本模块为下一只桌宠创建互不污染的私有制作工作区，并检查素材是否齐全。

职责范围：
- 从统一模板创建 `user_assets/pets/<pet-id>/`；
- 本地保存用户提供的图片或视频，图片可直接生成自拍成片；
- 为视频接受一张从原视频抽取的代表帧作为自拍成片；
- 默认建立升级基础款精灵路线，只有明确选择时才登记混合 Live2D 路线；
- 输出标准角色、八种动作、八相位走路和运行目录的固定路径；
- 要求每个动作从 2–3 个独立候选中选择，并记录肢体完整性质检；
- 校验可选养成玩法、多帧扩展动作与八向视线配置，使额外能力仍由每宠物 JSON 驱动；
- 校验默认关闭的休息提醒、喝水、久坐和角色番茄钟配置；
- 检查透明通道、走路画布一致性和必需文件，但不代替人工视觉验收。
- 把工作区的真实文件、审批和 QA 证据汇总为只读 Agent 控制快照，给出当前阶段、
  阻塞原因、唯一安全下一步和回退点，但绝不代替用户审批。
- 为摸头派生帧记录来源/输出哈希，为 runtime、QA 和最终视觉验收核对同一 build
  输入指纹，并让静止替代互动路线安全进入 `stationary` 构建。
- 对走路、替代互动和最终 runtime 同时保留接受/否决 CAS，并让负反馈驱动下一轮
  修订；所有构建失败都写入可诊断证据，避免相同输入盲目重试。

本工具不访问网络、不覆盖已有工作区，也不把私有素材复制到公开 `assets/`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps
from onepic_desktop_pet.care import (
    care_config_sha256,
    normalize_care_config,
)
from onepic_desktop_pet.break_scheduler import (
    canonical_break_reminder_manifest,
)
from onepic_desktop_pet.workflow import (
    WorkflowError,
    character_is_approved,
    interaction_is_approved,
    load_workflow as load_gate_state,
    require_character_approved,
    save_workflow as save_gate_state,
    walk_is_approved,
)


APPLICATION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APPLICATION_ROOT
USER_ROOT = Path(
    os.environ.get("ONEPIC_USER_ROOT", PROJECT_ROOT / "user_assets")
).expanduser().resolve()
PETS_ROOT = USER_ROOT / "pets"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "pet-workspace" / "pet-config.json"
PET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
PET_MODES = {
    "full-body",
    "half-body",
    "animal",
    "vehicle-seated",
    "static",
}
RENDERING_MODES = {"sprite", "hybrid-live2d"}
INTERACTION_FALLBACK_PET_MODES = {"half-body", "vehicle-seated", "static"}
INTERACTION_PRIMARY_PET_MODES = {"vehicle-seated", "static"}
ACTION_NAMES = (
    "wave",
    "happy",
    "selfie",
    "surprised",
    "annoyed",
    "sit",
    "sleep",
    "drag",
)
ACTION_REVIEW_CHECKS = (
    "limb_integrity",
    "single_action",
    "identity_consistency",
    "no_fused_props",
    "clean_silhouette",
)
WORKSPACE_DIRECTORIES = (
    "source",
    "candidate",
    "actions/key",
    "actions/candidates",
    "actions/transparent",
    "walk/key",
    "walk/frames",
    "review",
    "runtime",
    "backups",
)


def now_utc() -> str:
    """返回适合写入 JSON 的 UTC 时间。"""

    return datetime.now(UTC).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    """计算本地素材哈希，用于来源身份和确认门禁绑定。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def workspace_file_reference_is_current(
    workspace: Path,
    raw_path: Any,
    raw_sha256: Any,
) -> bool:
    """只接受工作区内真实存在且哈希匹配的授权来源引用。"""

    value = str(raw_path or "").strip()
    expected = str(raw_sha256 or "").strip().lower()
    if not value or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        return False
    workspace_root = workspace.resolve()
    supplied = Path(value)
    candidates = (
        [supplied.resolve()]
        if supplied.is_absolute()
        else [(workspace_root / supplied).resolve(), (PROJECT_ROOT / supplied).resolve()]
    )
    for candidate in candidates:
        if candidate == workspace_root or workspace_root not in candidate.parents:
            continue
        if candidate.is_file() and file_sha256(candidate).lower() == expected:
            return True
    return False


def interaction_fallback_is_allowed(
    config: dict[str, Any],
    workflow: dict[str, Any],
) -> bool:
    """仅让半身、载具或静止项目用已审批互动代替未审批走路。"""

    return bool(
        str(config.get("pet_mode", "full-body"))
        in INTERACTION_FALLBACK_PET_MODES
        and interaction_is_approved(workflow)
        and not walk_is_approved(workflow)
    )


def authorized_source_is_current(
    workspace: Path,
    workflow: dict[str, Any],
) -> bool:
    """验证普通来源记录至少有一项真实文件与 SHA-256 仍匹配。"""

    source = workflow.get("source", {})
    if not isinstance(source, dict):
        return False
    if workspace_file_reference_is_current(
        workspace,
        source.get("original"),
        source.get("original_sha256"),
    ):
        return True
    references = source.get("references", [])
    return bool(
        isinstance(references, list)
        and any(
            isinstance(item, dict)
            and workspace_file_reference_is_current(
                workspace,
                item.get("path"),
                item.get("sha256"),
            )
            for item in references
        )
    )


def migration_evidence_is_current(
    workspace: Path,
    workflow: dict[str, Any],
) -> bool:
    """验证无原图历史迁移由当前 runtime、QA 与人工 GIF 的哈希共同支撑。"""

    evidence = workflow.get("migration_evidence")
    if not isinstance(evidence, list):
        return False
    required_roles = {"runtime_manifest", "assets_qa", "walk_preview"}
    if evidence and all(isinstance(item, str) for item in evidence):
        legacy_roles = {
            "runtime/manifest.json": "runtime_manifest",
            "review/assets-qa.json": "assets_qa",
            "review/walk-user-review.gif": "walk_preview",
        }
        records: dict[str, dict[str, Any]] = {}
        for raw_path in evidence:
            normalized = Path(raw_path).as_posix()
            role = legacy_roles.get(normalized)
            path = (workspace / normalized).resolve()
            workspace_root = workspace.resolve()
            if (
                role is None
                or workspace_root not in path.parents
                or not path.is_file()
            ):
                return False
            records[role] = {
                "role": role,
                "path": normalized,
                "sha256": file_sha256(path),
            }
    else:
        records = {
            str(item.get("role", "")): item
            for item in evidence
            if isinstance(item, dict)
        }
    if set(records) != required_roles:
        return False
    if not all(
        workspace_file_reference_is_current(
            workspace,
            records[role].get("path"),
            records[role].get("sha256"),
        )
        for role in required_roles
    ):
        return False

    def evidence_path(role: str) -> Path:
        return (workspace / str(records[role]["path"])).resolve()

    try:
        manifest = json.loads(
            evidence_path("runtime_manifest").read_text(encoding="utf-8-sig")
        )
        qa = json.loads(evidence_path("assets_qa").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict) or not isinstance(qa, dict):
        return False
    frame_count = sum(
        len(paths)
        for paths in manifest.get("animations", {}).values()
        if isinstance(paths, list)
    )
    walk = workflow.get("walk", {})
    preview_hash = records["walk_preview"].get("sha256")
    return bool(
        manifest.get("pet_id") in (None, workflow.get("pet_id"))
        and frame_count == 38
        and qa.get("status") == "passed"
        and isinstance(walk, dict)
        and walk.get("preview_sha256") == preview_hash
        and walk.get("approved_asset_sha256") == walk.get("asset_sha256")
    )


def file_bundle_sha256(paths: list[Path]) -> str:
    """按文件名和内容哈希计算可重复的素材束哈希。"""

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    """计算不受 JSON 缩进和键顺序影响的语义哈希。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def relative_file_bundle_sha256(root: Path, paths: list[Path]) -> str:
    """按相对路径和内容计算目录内文件束哈希，避免同名文件碰撞。"""

    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted((item.resolve() for item in paths), key=lambda item: item.as_posix()):
        if path == root or root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"文件束成员不存在或越过根目录：{path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


REVIEW_VISUAL_ARTIFACTS = (
    "all-actions-contact-sheet.jpg",
    "all-actions-contact-sheet.png",
    "walk-runtime-preview.gif",
    "sit-sleep-wake-preview.gif",
    "touch-feedback-preview.gif",
)


def runtime_asset_bundle_sha256(runtime_root: Path) -> str:
    """哈希 runtime 中除 manifest 外的所有实际运行文件。"""

    paths = [
        path
        for path in runtime_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    ]
    if not paths:
        raise FileNotFoundError("runtime 不含任何可执行素材文件")
    return relative_file_bundle_sha256(runtime_root, paths)


def review_visual_evidence_sha256(review_root: Path) -> str:
    """哈希用户最终视觉验收必须查看的固定联系表与 GIF 证据。"""

    paths = [review_root / name for name in REVIEW_VISUAL_ARTIFACTS]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少最终视觉证据：" + ", ".join(missing))
    return relative_file_bundle_sha256(review_root, paths)


def read_json_object(path: Path) -> tuple[dict[str, Any], str | None]:
    """容错读取 JSON 对象，供只读控制面返回稳定损坏状态。"""

    if not path.is_file():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    if not isinstance(value, dict):
        return {}, "JSON 根节点不是对象"
    return value, None


def validate_pet_id(pet_id: str) -> str:
    """验证可移植的工作区标识符。"""

    value = pet_id.strip().lower()
    if not PET_ID_PATTERN.fullmatch(value):
        raise ValueError("pet-id 只能使用 2–48 位小写字母、数字、下划线或连字符")
    return value


def workspace_path(pet_id: str) -> Path:
    """返回指定桌宠的私有工作区路径。"""

    return PETS_ROOT / validate_pet_id(pet_id)


def load_config(workspace: Path) -> dict[str, Any]:
    """读取并向后兼容地验证工作区配置。"""

    path = workspace / "pet-config.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺少工作区配置：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"工作区配置必须是 JSON 对象：{path}")
    required = {
        "pet_id",
        "canvas_size",
        "character_file",
        "actions_directory",
        "walk_directory",
        "runtime_directory",
        "review_directory",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ValueError(f"工作区配置缺少字段：{', '.join(missing)}")
    if value.get("pet_mode", "full-body") not in PET_MODES:
        raise ValueError(f"未知 pet_mode：{value.get('pet_mode')}")
    canvas = value.get("canvas_size")
    if (
        not isinstance(canvas, list)
        or len(canvas) != 2
        or any(not isinstance(item, int) or item < 64 for item in canvas)
    ):
        raise ValueError("canvas_size 必须包含两个不小于 64 的整数")
    value.setdefault("schema_version", 1)
    value.setdefault("pet_mode", "full-body")
    value.setdefault("selfie_required", True)
    value.setdefault("animation_recipe", "config/animation-recipe.v1.json")
    legacy_mass = str(value.get("visual_mass_normalization", "")).strip()
    value.setdefault(
        "visual_mass",
        {
            "mode": (
                "walk_median_safe"
                if legacy_mass in {"", "walk_median"}
                else legacy_mass
            ),
            "static_tolerance_ratio": 0.12,
            "transition_tolerance_ratio": 0.22,
            "minimum_target_ratio": 0.6,
            "exemptions": {
                "sleep": "躺卧姿态按配方目标质量检查",
                "sit": "坐下过程按声明式 mass_factor 检查",
            },
        },
    )
    value.setdefault(
        "walk_qa",
        {
            "minimum_shape_change_ratio": 0.012,
            "maximum_shared_scale_delta": 0.0,
            "minimum_body_rise_pixels": 2,
            "maximum_loop_seam_ratio": 2.0,
        },
    )
    value.setdefault(
        "touch_feedback",
        {
            "policy": "weighted_no_repeat",
            "branch_weights": {"shy": 0.55, "happy": 0.45},
            "throttle_ms": 1400,
            "repeat_click_behavior": "ignore_until_sequence_finishes",
        },
    )
    companion = value.setdefault("companion_behaviors", {})
    if not isinstance(companion, dict):
        raise ValueError("companion_behaviors 必须是 JSON 对象")
    break_reminders = value.setdefault("break_reminders", {})
    if not isinstance(break_reminders, dict):
        raise ValueError("break_reminders 必须是 JSON 对象")
    value["break_reminders"] = canonical_break_reminder_manifest(
        break_reminders
    )
    extensions = value.setdefault("extension_animations", {})
    if not isinstance(extensions, dict):
        raise ValueError("extension_animations 必须是 JSON 对象")
    directional = value.setdefault("directional_mouse_follow", {})
    if not isinstance(directional, dict):
        raise ValueError("directional_mouse_follow 必须是 JSON 对象")
    action_gate = value.setdefault(
        "action_source_gate",
        {
            "required": False,
            "minimum_candidates": 2,
            "maximum_candidates": 3,
            "checks": list(ACTION_REVIEW_CHECKS),
        },
    )
    if not isinstance(action_gate, dict):
        raise ValueError("action_source_gate 必须是 JSON 对象")
    action_gate.setdefault("required", False)
    action_gate.setdefault("minimum_candidates", 2)
    action_gate.setdefault("maximum_candidates", 3)
    action_gate.setdefault("checks", list(ACTION_REVIEW_CHECKS))
    minimum = int(action_gate["minimum_candidates"])
    maximum = int(action_gate["maximum_candidates"])
    if minimum < 2 or maximum < minimum or maximum > 3:
        raise ValueError("action_source_gate 候选数必须限定为 2–3 张")
    if set(action_gate["checks"]) != set(ACTION_REVIEW_CHECKS):
        raise ValueError("action_source_gate 质检项与当前契约不一致")
    return value


BUILD_INPUT_SCHEMA_VERSION = 2


def application_build_evidence() -> dict[str, Any]:
    """哈希会进入 EXE 或改变其默认界面/行为的源码、规格和公开资源。"""

    root = APPLICATION_ROOT.resolve()
    paths = [
        root / "main.py",
        root / "OnePicDesktopPet.spec",
        root / "pyproject.toml",
        root / "scripts" / "build.ps1",
    ]
    paths.extend(sorted(root.glob("requirements*.txt")))
    paths.extend(sorted((root / "src" / "onepic_desktop_pet").rglob("*.py")))
    for directory in (root / "assets", root / "config"):
        paths.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    files: dict[str, dict[str, Any]] = {}
    for path in paths:
        resolved = path.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(f"应用构建输入不存在或越过项目目录：{resolved}")
        relative = resolved.relative_to(root).as_posix()
        files[relative] = {
            "sha256": file_sha256(resolved),
            "size": resolved.stat().st_size,
        }
    payload = {
        "schema_version": 1,
        "files": {key: files[key] for key in sorted(files)},
    }
    return {
        **payload,
        "fingerprint": canonical_json_sha256(payload),
    }


def workspace_build_input_evidence(workspace: Path) -> dict[str, Any]:
    """对会改变运行产物的配置、审批和私有素材生成统一指纹。"""

    config = load_config(workspace)
    workflow = load_gate_state(workspace / "workflow.json")
    workspace_root = workspace.resolve()
    project_root = PROJECT_ROOT.resolve()
    files: dict[str, dict[str, Any]] = {}

    def add_file(path: Path, *, allow_project: bool = False) -> None:
        resolved = path.resolve()
        if resolved == workspace_root or workspace_root in resolved.parents:
            key = resolved.relative_to(workspace_root).as_posix()
        elif allow_project and (
            resolved == project_root or project_root in resolved.parents
        ):
            key = "project:" + resolved.relative_to(project_root).as_posix()
        else:
            raise ValueError(f"构建输入路径越过受控目录：{resolved}")
        files[key] = {
            "sha256": file_sha256(resolved) if resolved.is_file() else None,
            "size": resolved.stat().st_size if resolved.is_file() else None,
        }

    add_file(workspace / str(config["character_file"]))
    selfie = workspace / str(config.get("selfie_file", "source/selfie.png"))
    if selfie.is_file() or bool(config.get("selfie_required", True)):
        add_file(selfie)
    action_root = workspace / str(config["actions_directory"])
    for name in ACTION_NAMES:
        add_file(action_root / f"{name}.png")
    required_touch = int(
        config.get("touch_feedback", {}).get(
            "default_transition_frame_count",
            0,
        )
    )
    for name in ("touch_soften", "touch_return")[:required_touch]:
        add_file(action_root / f"{name}.png")
    review_root = workspace / str(config["review_directory"])
    add_file(review_root / "action-source-gate.json")
    if required_touch:
        add_file(review_root / "touch-transitions.json")
    walk_root = workspace / str(config["walk_directory"])
    if not interaction_fallback_is_allowed(config, workflow):
        for index in range(1, 9):
            add_file(walk_root / f"walk_{index:02d}.png")

    recipe = PROJECT_ROOT / str(config["animation_recipe"])
    add_file(recipe, allow_project=True)
    extensions = config.get("extension_animations", {})
    active_extension_names: set[str] = set()
    if isinstance(extensions, dict):
        for extension_name, extension in extensions.items():
            if not isinstance(extension, dict) or not extension.get("enabled", False):
                continue
            active_extension_names.add(str(extension_name))
            frames = extension.get("frames", [])
            if isinstance(frames, list):
                for value in frames:
                    add_file(workspace / str(value))
    directional = config.get("directional_mouse_follow", {})
    if isinstance(directional, dict) and directional.get("enabled", False):
        active_extension_names.add("gaze")
        frames = directional.get("frames", {})
        if isinstance(frames, dict):
            for value in frames.values():
                add_file(workspace / str(value))
    break_reminders = config.get("break_reminders", {})
    sounds = (
        break_reminders.get("sounds", {}).get("files", {})
        if isinstance(break_reminders, dict)
        else {}
    )
    if isinstance(sounds, dict):
        for value in sounds.values():
            add_file(workspace / str(value))
    hybrid = config.get("hybrid_runtime", {})
    if isinstance(hybrid, dict) and hybrid.get("enabled", False):
        source_root = workspace / str(
            hybrid.get("source_directory", "live2d-runtime")
        )
        resolved_source = source_root.resolve()
        if (
            resolved_source != workspace_root
            and workspace_root not in resolved_source.parents
        ):
            raise ValueError("Live2D 构建输入目录越过当前工作区")
        if source_root.is_dir():
            for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
                add_file(path)
        else:
            add_file(source_root / str(hybrid.get("model_file", "missing.model3.json")))

    def selected_fields(value: Any, names: tuple[str, ...]) -> dict[str, Any] | None:
        """只保留构建门禁实际消费的稳定审批字段。"""

        if not isinstance(value, dict):
            return None
        return {name: value.get(name) for name in names}

    relevant_extension_approvals: dict[str, Any] = {}
    raw_extension_approvals = workflow.get("extension_approvals", {})
    if isinstance(raw_extension_approvals, dict) and active_extension_names:
        for pack_name, pack in sorted(raw_extension_approvals.items()):
            if not isinstance(pack, dict) or not isinstance(pack.get("items"), dict):
                continue
            relevant_items = {
                name: selected_fields(
                    pack["items"].get(name),
                    ("asset_sha256", "approved_asset_sha256"),
                )
                for name in sorted(active_extension_names)
                if isinstance(pack["items"].get(name), dict)
            }
            if relevant_items:
                relevant_extension_approvals[str(pack_name)] = {
                    **(
                        selected_fields(
                            pack,
                            ("status", "activation_allowed", "user_confirmed"),
                        )
                        or {}
                    ),
                    "items": relevant_items,
                }

    relevant_feature_status: dict[str, Any] = {}
    care = normalize_care_config(config.get("care_simulation", {}))
    if care.get("enabled") is True:
        raw_feature_status = workflow.get("feature_status", {})
        care_feature = (
            raw_feature_status.get("vpet_care_v1")
            if isinstance(raw_feature_status, dict)
            else None
        )
        relevant_feature_status["vpet_care_v1"] = selected_fields(
            care_feature,
            (
                "status",
                "activation_allowed",
                "user_confirmed",
                "config_sha256",
                "approved_config_sha256",
            ),
        )

    approval_state = {
        "version": workflow.get("version"),
        "pet_id": workflow.get("pet_id"),
        "character": selected_fields(
            workflow.get("character"),
            (
                "status",
                "user_confirmed",
                "candidate_sha256",
                "approved_candidate_sha256",
            ),
        ),
        "walk": selected_fields(
            workflow.get("walk"),
            ("status", "user_confirmed", "asset_sha256", "approved_asset_sha256"),
        ),
        "interaction": selected_fields(
            workflow.get("interaction"),
            (
                "status",
                "user_confirmed",
                "mode",
                "approved_mode",
                "preview_sha256",
                "approved_asset_sha256",
                "character_sha256",
                "approved_character_sha256",
            ),
        ),
        "extension_approvals": relevant_extension_approvals,
        "feature_status": relevant_feature_status,
    }
    runtime_config = {
        key: value
        for key, value in config.items()
        if key != "sharing"
    }
    application_evidence = application_build_evidence()
    payload = {
        "schema_version": BUILD_INPUT_SCHEMA_VERSION,
        "pet_id": config["pet_id"],
        "runtime_config_sha256": canonical_json_sha256(runtime_config),
        "application_fingerprint": application_evidence["fingerprint"],
        "workflow_approval_sha256": canonical_json_sha256(approval_state),
        "files": {key: files[key] for key in sorted(files)},
    }
    return {
        **payload,
        "fingerprint": canonical_json_sha256(payload),
    }


def save_selfie(source: Path, destination: Path) -> None:
    """把一张本地原图或原视频帧按原始像素保存为自拍 PNG。"""

    try:
        with Image.open(source) as image:
            converted = ImageOps.exif_transpose(image).convert("RGBA")
            converted.save(destination, "PNG", optimize=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取自拍来源图片：{source}：{exc}") from exc


def initialize_workspace(
    pet_id: str,
    *,
    display_name: str,
    source: Path | None,
    selfie_frame: Path | None,
    recommended_display_height: int,
    pet_mode: str = "full-body",
    rendering_mode: str = "sprite",
) -> Path:
    """创建新工作区，保存来源文件并写入配置。"""

    if pet_mode not in PET_MODES:
        raise ValueError(f"不支持的宠物模式：{pet_mode}")
    if rendering_mode not in RENDERING_MODES:
        raise ValueError(f"不支持的渲染路线：{rendering_mode}")
    workspace = workspace_path(pet_id)
    if workspace.exists():
        raise FileExistsError(f"工作区已经存在，拒绝覆盖：{workspace}")
    resolved_source: Path | None = None
    source_suffix = ""
    if source is not None:
        resolved_source = source.resolve()
        if not resolved_source.is_file():
            raise FileNotFoundError(f"找不到来源文件：{resolved_source}")
        source_suffix = resolved_source.suffix.lower()
        if source_suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
            raise ValueError(f"暂不支持的来源格式：{source_suffix or '无扩展名'}")
    resolved_selfie: Path | None = None
    if selfie_frame is not None:
        resolved_selfie = selfie_frame.resolve()
        if not resolved_selfie.is_file():
            raise FileNotFoundError(f"找不到视频代表帧：{resolved_selfie}")

    workspace.mkdir(parents=True)
    for relative in WORKSPACE_DIRECTORIES:
        (workspace / relative).mkdir(parents=True, exist_ok=True)

    config = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    config.update(
        {
            "pet_id": validate_pet_id(pet_id),
            "display_name": display_name.strip() or pet_id,
            "recommended_display_height": min(
                600,
                max(120, int(recommended_display_height)),
            ),
            "pet_mode": pet_mode,
            "rendering_mode": rendering_mode,
            "created_at": now_utc(),
        }
    )

    source_copy: Path | None = None
    if resolved_source is not None:
        source_copy = workspace / "source" / f"original{source_suffix}"
        shutil.copy2(resolved_source, source_copy)
        config["source_type"] = (
            "image" if source_suffix in IMAGE_SUFFIXES else "video"
        )
        config["original_file"] = source_copy.relative_to(workspace).as_posix()

    selfie_path = workspace / "source" / "selfie.png"
    if resolved_selfie is not None:
        save_selfie(resolved_selfie, selfie_path)
    elif source_copy is not None and source_copy.suffix.lower() in IMAGE_SUFFIXES:
        save_selfie(source_copy, selfie_path)

    (workspace / "pet-config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    references: list[dict[str, str]] = []
    if source_copy is not None:
        references.append(
            {
                "path": source_copy.relative_to(workspace).as_posix(),
                "sha256": file_sha256(source_copy),
                "relationship": "same_subject",
            }
        )
    if selfie_path.is_file():
        references.append(
            {
                "path": selfie_path.relative_to(workspace).as_posix(),
                "sha256": file_sha256(selfie_path),
                "relationship": "same_subject_derived_view",
            }
        )
    workflow = {
        "version": 2,
        "pet_id": validate_pet_id(pet_id),
        "created_at": now_utc(),
        "source": {
            "subject_id": validate_pet_id(pet_id),
            "same_subject_policy": (
                "crop, frame removal, background removal and alternate angles "
                "remain the same subject"
            ),
            "references": references,
        },
        "character": {
            "status": "not_submitted",
            "candidate": None,
            "identity_features": [],
        },
        "walk": {"status": "not_generated", "preview": None},
    }
    (workspace / "workflow.json").write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return workspace


def create_workspace_walk_review(workspace: Path) -> Path:
    """用工作区八帧走路生成第二个人工确认门禁预览。"""

    config = load_config(workspace)
    walk_root = workspace / str(config["walk_directory"])
    review_root = workspace / str(config["review_directory"])
    review_root.mkdir(parents=True, exist_ok=True)
    relatives: list[str] = []
    sizes: set[tuple[int, int]] = set()
    for index in range(1, 9):
        frame = walk_root / f"walk_{index:02d}.png"
        if not frame.is_file():
            raise FileNotFoundError(f"缺少走路帧：{frame}")
        result = inspect_image(frame, require_alpha=True)
        if result.get("failure"):
            raise ValueError(f"走路帧 {index} {result['failure']}")
        sizes.add(tuple(result["size"]))
        relatives.append(
            Path("..", str(config["walk_directory"]), frame.name).as_posix()
        )
    if len(sizes) != 1:
        raise ValueError(f"八帧走路画布尺寸不一致：{sorted(sizes)}")

    manifest_path = review_root / "walk-review-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"animations": {"walk": relatives}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        from .onepic_workflow import configure_pet, create_walk_review
    except ImportError:
        from onepic_workflow import configure_pet, create_walk_review

    configure_pet(str(config["pet_id"]))
    state = create_walk_review(manifest_path)
    global_preview = PROJECT_ROOT / str(state["walk"]["preview"])
    workspace_preview = review_root / "walk-user-review.gif"
    shutil.copy2(global_preview, workspace_preview)
    workflow = load_gate_state(workspace / "workflow.json")
    walk_record = state.get("walk", {})
    if not isinstance(walk_record, dict):
        raise ValueError("走路预览未返回有效的 workflow.walk 记录")
    try:
        preview_value = workspace_preview.relative_to(PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise ValueError("走路确认预览必须位于项目私有工作区内") from exc
    workflow["walk"] = {
        **walk_record,
        "preview": preview_value,
        "preview_sha256": file_sha256(workspace_preview),
    }
    save_gate_state(workflow, workspace / "workflow.json")
    return workspace_preview


def inspect_image(path: Path, *, require_alpha: bool) -> dict[str, Any]:
    """返回图片尺寸和透明通道信息。"""

    with Image.open(path) as image:
        image.load()
        bands = image.getbands()
        has_alpha = "A" in bands or "transparency" in image.info
        result = {
            "path": path.as_posix(),
            "size": list(image.size),
            "mode": image.mode,
            "has_alpha": has_alpha,
        }
        if require_alpha and not has_alpha:
            result["failure"] = "缺少透明通道"
        return result


def _checked_workspace_file(workspace: Path, value: Path) -> Path:
    """解析工作区内文件，拒绝跨目录引用。"""

    path = value if value.is_absolute() else workspace / value
    resolved = path.resolve()
    workspace_root = workspace.resolve()
    if resolved == workspace_root or workspace_root not in resolved.parents:
        raise ValueError(f"动作候选路径越过当前工作区：{resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(f"找不到动作候选：{resolved}")
    return resolved


def record_action_source_review(
    workspace: Path,
    *,
    action: str,
    candidates: list[Path],
    selected: Path,
    checks: dict[str, bool],
    confirmed: bool,
) -> dict[str, Any]:
    """将通过视觉质检的单动作候选原子写入正式素材。"""

    if not confirmed:
        raise ValueError("必须传入 --yes，且只能在逐张完成视觉质检后执行")
    if action not in ACTION_NAMES:
        raise ValueError(f"未知基础动作：{action}")
    require_workspace_character_binding(workspace)
    config = load_config(workspace)
    gate = config["action_source_gate"]
    minimum = int(gate["minimum_candidates"])
    maximum = int(gate["maximum_candidates"])
    resolved_candidates = list(
        dict.fromkeys(_checked_workspace_file(workspace, path) for path in candidates)
    )
    if not minimum <= len(resolved_candidates) <= maximum:
        raise ValueError(
            f"动作 {action} 必须提供 {minimum}–{maximum} 个不同候选，"
            f"当前为 {len(resolved_candidates)} 个"
        )
    candidate_root = (workspace / "actions" / "candidates" / action).resolve()
    for path in resolved_candidates:
        if candidate_root not in path.parents:
            raise ValueError(
                f"动作 {action} 候选必须位于 {candidate_root}：{path}"
            )
        result = inspect_image(path, require_alpha=True)
        if result.get("failure"):
            raise ValueError(f"动作候选 {path.name} {result['failure']}")
    resolved_selected = _checked_workspace_file(workspace, selected)
    if resolved_selected not in resolved_candidates:
        raise ValueError("选中帧必须来自本次 2–3 个候选")
    normalized_checks = {
        name: checks.get(name) is True for name in ACTION_REVIEW_CHECKS
    }
    failed_checks = [name for name, passed in normalized_checks.items() if not passed]
    if failed_checks:
        raise ValueError(
            "动作源帧质检未通过，拒绝入库：" + ", ".join(failed_checks)
        )

    action_root = workspace / str(config["actions_directory"])
    destination = action_root / f"{action}.png"
    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    staging = workspace / "staging" / f"action-source-{action}-{build_id}"
    staging.mkdir(parents=True)
    staged = staging / destination.name
    shutil.copy2(resolved_selected, staged)
    backup: str | None = None
    if destination.is_file():
        backup_path = (
            workspace / "backups" / f"action-source-{action}-{build_id}" / destination.name
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, backup_path)
        backup = backup_path.relative_to(workspace).as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(destination)
    staging.rmdir()

    character = workspace / str(config["character_file"])
    review_root = workspace / str(config["review_directory"])
    review_root.mkdir(parents=True, exist_ok=True)
    manifest_path = review_root / "action-source-gate.json"
    manifest: dict[str, Any] = {"version": 1, "actions": {}}
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded
    entries = manifest.setdefault("actions", {})
    entry = {
        "status": "passed",
        "action": action,
        "reviewed_at": now_utc(),
        "review_kind": "frame_level_visual_anatomy_gate",
        "character_sha256": file_sha256(character),
        "candidate_count": len(resolved_candidates),
        "candidates": [
            {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": file_sha256(path),
            }
            for path in resolved_candidates
        ],
        "selected": resolved_selected.relative_to(workspace).as_posix(),
        "selected_sha256": file_sha256(resolved_selected),
        "output": destination.relative_to(workspace).as_posix(),
        "output_sha256": file_sha256(destination),
        "checks": normalized_checks,
        "backup": backup,
    }
    entries[action] = entry
    manifest["updated_at"] = now_utc()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return entry


def validate_action_source_gate(workspace: Path) -> list[str]:
    """验证每张正式动作都绑定了合格候选和解剖质检。"""

    config = load_config(workspace)
    gate = config["action_source_gate"]
    if gate.get("required") is not True:
        return []
    failures: list[str] = []
    manifest_path = workspace / str(config["review_directory"]) / "action-source-gate.json"
    if not manifest_path.is_file():
        return ["缺少动作源帧质检记录 action-source-gate.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"动作源帧质检记录无法解析：{exc}"]
    entries = manifest.get("actions", {}) if isinstance(manifest, dict) else {}
    if not isinstance(entries, dict):
        return ["动作源帧质检记录缺少 actions 对象"]
    character = workspace / str(config["character_file"])
    character_hash = file_sha256(character) if character.is_file() else None
    minimum = int(gate["minimum_candidates"])
    maximum = int(gate["maximum_candidates"])
    action_root = workspace / str(config["actions_directory"])
    for action in ACTION_NAMES:
        entry = entries.get(action)
        if not isinstance(entry, dict) or entry.get("status") != "passed":
            failures.append(f"动作 {action} 尚未通过源帧质检")
            continue
        count = entry.get("candidate_count")
        if not isinstance(count, int) or not minimum <= count <= maximum:
            failures.append(f"动作 {action} 候选数不是 {minimum}–{maximum}")
        checks = entry.get("checks", {})
        if not isinstance(checks, dict) or any(
            checks.get(name) is not True for name in ACTION_REVIEW_CHECKS
        ):
            failures.append(f"动作 {action} 肢体/单动作视觉质检不完整")
        if entry.get("character_sha256") != character_hash:
            failures.append(f"动作 {action} 质检未绑定当前标准角色")
        output = action_root / f"{action}.png"
        actual_hash = file_sha256(output) if output.is_file() else None
        if actual_hash != entry.get("output_sha256"):
            failures.append(f"动作 {action} 已在质检后被替换")
    return failures


def workspace_status(workspace: Path) -> dict[str, Any]:
    """汇总制作工作区的文件和门禁状态。"""

    config = load_config(workspace)
    character = workspace / str(config["character_file"])
    selfie = workspace / str(config["selfie_file"])
    action_root = workspace / str(config["actions_directory"])
    walk_root = workspace / str(config["walk_directory"])
    actions = {
        name: (action_root / f"{name}.png").is_file()
        for name in ACTION_NAMES
    }
    walk = {
        f"walk_{index:02d}.png": (
            walk_root / f"walk_{index:02d}.png"
        ).is_file()
        for index in range(1, 9)
    }
    workflow_path = workspace / "workflow.json"
    workflow, workflow_read_error = read_json_object(workflow_path)
    action_gate_path = workspace / str(config["review_directory"]) / "action-source-gate.json"
    action_gate_count = 0
    action_gate, action_gate_read_error = read_json_object(action_gate_path)
    if isinstance(action_gate.get("actions"), dict):
        action_gate_count = sum(
            entry.get("status") == "passed"
            for entry in action_gate["actions"].values()
            if isinstance(entry, dict)
        )
    character_record = workflow.get("character", {})
    walk_record = workflow.get("walk", {})
    return {
        "pet_id": config["pet_id"],
        "workspace": str(workspace),
        "source_type": config.get("source_type", "unknown"),
        "selfie_required": bool(config.get("selfie_required", True)),
        "selfie_ready": selfie.is_file(),
        "character_ready": character.is_file(),
        "actions": actions,
        "walk": walk,
        "runtime_ready": (workspace / str(config["runtime_directory"]) / "manifest.json").is_file(),
        "character_gate": (
            character_record.get("status", "not_submitted")
            if isinstance(character_record, dict)
            else "invalid"
        ),
        "walk_gate": (
            walk_record.get("status", "not_generated")
            if isinstance(walk_record, dict)
            else "invalid"
        ),
        "workflow_read_error": workflow_read_error,
        "action_gate_read_error": action_gate_read_error,
        "action_source_gate_required": config["action_source_gate"].get("required") is True,
        "action_source_gate_passed": action_gate_count,
    }


def require_workspace_character_binding(workspace: Path) -> dict[str, Any]:
    """要求角色审批仍绑定当前正式候选，避免在陈旧身份上继续生产。"""

    state = require_character_approved(workspace / "workflow.json")
    config = load_config(workspace)
    candidate = workspace / str(config["character_file"])
    expected = state.get("character", {}).get("candidate_sha256")
    actual = file_sha256(candidate) if candidate.is_file() else None
    if not expected or actual != expected:
        raise ValueError(
            "标准角色文件与用户审批哈希不一致；请重新登记候选并让用户确认"
        )
    return state


def agent_control_snapshot(workspace: Path) -> dict[str, Any]:
    """观察工作区并返回确定性的制作阶段、下一步和证据快照。

    该函数只读取文件，不生成素材、不写审批，也不把自动检查提升为视觉通过。
    每次执行动作后应重新调用本函数，以环境中的最新事实重新规划。
    """

    try:
        config = load_config(workspace)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        config_path = workspace / "pet-config.json"
        message = f"pet-config.json 无法解析或校验：{exc}"
        return {
            "schema_version": 1,
            "pet_id": workspace.name,
            "goal": {"profile": "onepic-upgraded-base"},
            "phase": "workspace_state_repair",
            "must_pause": False,
            "observation": {"config_read_error": str(exc)},
            "blockers": [
                {
                    "code": "pet_config_invalid",
                    "category": "state_integrity",
                    "message": message,
                    "recovery": "从可恢复副本修复 pet-config.json；不得猜测路径或审批。",
                }
            ],
            "allowed_next_actions": ["repair_corrupted_workspace_state"],
            "decision": {
                "kind": "repair",
                "action": "repair_corrupted_workspace_state",
                "actor": "agent",
                "target": "pet-config.json",
                "command": None,
                "command_template": None,
                "approval_command": None,
                "rejection_command": None,
                "rejection_command_template": None,
                "recovery_command": None,
                "recovery_command_template": None,
                "required_inputs": {},
                "reason": "损坏配置不能作为路径、构建或审批依据。",
                "postcondition": "pet-config.json 可解析并通过 load_config 校验。",
                "reobserve_after_action": True,
            },
            "evidence": {
                "pet_config_sha256": (
                    file_sha256(config_path) if config_path.is_file() else None
                )
            },
            "safety": {
                "automatic_user_approval_allowed": False,
                "visual_checks_replace_user_review": False,
                "private_assets_uploaded": False,
            },
        }
    status = workspace_status(workspace)
    workflow_path = workspace / "workflow.json"
    workflow = (
        {}
        if status.get("workflow_read_error")
        else load_gate_state(workflow_path)
    )
    review_root = workspace / str(config["review_directory"])
    action_gate_path = review_root / "action-source-gate.json"
    latest_failure_path = review_root / "latest-failure.json"
    latest_failure, latest_failure_error = read_json_object(latest_failure_path)
    runtime_manifest = (
        workspace / str(config["runtime_directory"]) / "manifest.json"
    )
    qa_path = review_root / "assets-qa.json"
    character_path = workspace / str(config["character_file"])
    walk_root = workspace / str(config["walk_directory"])
    walk_paths = [
        walk_root / f"walk_{index:02d}.png" for index in range(1, 9)
    ]
    walk_complete = all(path.is_file() for path in walk_paths)

    def blocker(
        code: str,
        category: str,
        message: str,
        recovery: str,
    ) -> dict[str, str]:
        return {
            "code": code,
            "category": category,
            "message": message,
            "recovery": recovery,
        }

    def decision(
        action: str,
        *,
        kind: str,
        actor: str,
        reason: str,
        postcondition: str,
        command: str | None = None,
        command_template: str | None = None,
        approval_command: str | None = None,
        rejection_command: str | None = None,
        rejection_command_template: str | None = None,
        recovery_command: str | None = None,
        recovery_command_template: str | None = None,
        required_inputs: dict[str, str] | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "action": action,
            "actor": actor,
            "target": target,
            "command": command,
            "command_template": command_template,
            "approval_command": approval_command,
            "rejection_command": rejection_command,
            "rejection_command_template": rejection_command_template,
            "recovery_command": recovery_command,
            "recovery_command_template": recovery_command_template,
            "required_inputs": required_inputs or {},
            "reason": reason,
            "postcondition": postcondition,
            "reobserve_after_action": True,
        }

    evidence = {
        "pet_config_sha256": file_sha256(workspace / "pet-config.json"),
        "workflow_sha256": (
            file_sha256(workflow_path) if workflow_path.is_file() else None
        ),
        "character_sha256": (
            file_sha256(character_path) if character_path.is_file() else None
        ),
        "action_source_gate_sha256": (
            file_sha256(action_gate_path)
            if action_gate_path.is_file()
            else None
        ),
        "walk_bundle_sha256": (
            file_bundle_sha256(walk_paths) if walk_complete else None
        ),
        "runtime_manifest_sha256": (
            file_sha256(runtime_manifest) if runtime_manifest.is_file() else None
        ),
        "assets_qa_sha256": (
            file_sha256(qa_path) if qa_path.is_file() else None
        ),
    }
    expected_touch = min(
        2,
        max(
            0,
            int(
                config.get("touch_feedback", {}).get(
                    "default_transition_frame_count",
                    0,
                )
            ),
        ),
    )
    touch_files = [
        workspace / str(config["actions_directory"]) / "touch_soften.png",
        workspace / str(config["actions_directory"]) / "touch_return.png",
    ][:expected_touch]
    character_record = workflow.get("character", {})
    walk_record = workflow.get("walk", {})
    interaction_record = workflow.get("interaction", {})
    character_approved = bool(
        isinstance(character_record, dict)
        and character_is_approved(workflow)
    )
    walk_approved = bool(
        isinstance(walk_record, dict) and walk_is_approved(workflow)
    )
    interaction_approved = bool(
        isinstance(interaction_record, dict)
        and interaction_is_approved(workflow)
    )
    interaction_fallback_allowed = interaction_fallback_is_allowed(
        config,
        workflow,
    )
    pet_mode = str(config.get("pet_mode", "full-body"))
    interaction_status = str(
        interaction_record.get("status", "not_generated")
        if isinstance(interaction_record, dict)
        else "invalid"
    )
    interaction_route_selected = bool(
        pet_mode in INTERACTION_PRIMARY_PET_MODES
        or (
            pet_mode == "half-body"
            and interaction_status not in {"not_generated", "not_submitted", ""}
        )
    )
    observations = {
        **status,
        "rendering_mode": config.get("rendering_mode", "sprite"),
        "pet_mode": pet_mode,
        "touch_transition_expected": expected_touch,
        "touch_transition_ready": sum(path.is_file() for path in touch_files),
        "character_approved": character_approved,
        "walk_approved": walk_approved,
        "interaction_approved": interaction_approved,
        "interaction_fallback_allowed": interaction_fallback_allowed,
        "interaction_status": interaction_status,
        "interaction_route_selected": interaction_route_selected,
    }
    phase = "unknown"
    blockers: list[dict[str, str]] = []
    next_decision: dict[str, Any]

    source_registered = authorized_source_is_current(workspace, workflow)
    character = character_record if isinstance(character_record, dict) else {}
    character_status = str(character.get("status", "not_submitted"))
    approved_character_current = bool(
        character_approved
        and character_path.is_file()
        and file_sha256(character_path) == character.get("candidate_sha256")
    )

    state_read_errors = [
        message
        for message in (
            status.get("workflow_read_error"),
            status.get("action_gate_read_error"),
            latest_failure_error,
        )
        if message
    ]
    for key in (
        "source",
        "character",
        "walk",
        "interaction",
        "extension_approvals",
        "feature_status",
    ):
        if key in workflow and not isinstance(workflow.get(key), dict):
            state_read_errors.append(f"workflow.{key} 必须是 JSON 对象")
    migration_source_exception = bool(
        migration_evidence_is_current(workspace, workflow)
        and approved_character_current
    )

    if state_read_errors:
        phase = "workspace_state_repair"
        blockers.extend(
            blocker(
                "workspace_state_json_invalid",
                "state_integrity",
                message,
                "从当前宠物的可恢复副本修复对应 JSON；不得跳过或重建用户审批。",
            )
            for message in state_read_errors
        )
        next_decision = decision(
            "repair_corrupted_workspace_state",
            kind="repair",
            actor="agent",
            reason="损坏的状态文件不能作为生产或审批依据。",
            postcondition="workflow.json 与 action-source-gate.json 均可解析为 JSON 对象。",
        )
    elif not (source_registered or migration_source_exception):
        phase = "awaiting_authorized_source"
        blockers.append(
            blocker(
                "authorized_source_missing",
                "user_input",
                "尚未登记可追溯的授权原图、视频代表帧或同主体参考。",
                "登记来源；虚构或历史生成角色应明确关闭 selfie_required，"
                "不能伪造自拍。",
            )
        )
        next_decision = decision(
            "register_authorized_source",
            kind="wait_for_user",
            actor="user",
            reason="身份锚点和授权边界尚未建立。",
            postcondition="workflow.source 含有当前主体的 SHA-256 引用。",
        )
    elif not approved_character_current:
        if character_status == "awaiting_user_confirmation":
            phase = "awaiting_character_approval"
            blockers.append(
                blocker(
                    "character_user_approval_required",
                    "approval",
                    "标准角色候选正在等待用户直接查看和确认。",
                    "打开本地图形确认窗口；不得用文本推断或脚本参数代替点击确认。",
                )
            )
            next_decision = decision(
                "open_character_review",
                kind="wait_for_user",
                actor="agent_then_user",
                reason="角色身份和画风属于人工门禁。",
                command=(
                    ".\\.venv\\Scripts\\python.exe "
                    ".\\tools\\onepic_workflow.py "
                    f"--pet-id {config['pet_id']} approve-character"
                ),
                postcondition=(
                    "character.status=approved 且审批哈希匹配当前候选。"
                ),
            )
        elif character_approved:
            phase = "character_binding_repair"
            blockers.append(
                blocker(
                    "character_approval_stale",
                    "approval_stale",
                    "标准角色文件已缺失或与审批哈希不一致。",
                    "回到当前授权来源重新登记一张标准角色候选，再请求用户确认。",
                )
            )
            next_decision = decision(
                "rebind_character_candidate",
                kind="repair",
                actor="agent_then_user",
                reason="陈旧审批不能作为后续动作的身份锚点。",
                postcondition=(
                    "当前 candidate/approved.png 与新的用户审批哈希一致。"
                ),
            )
        else:
            rejected = character_status == "rejected"
            phase = (
                "character_revision_required"
                if rejected
                else "awaiting_character_style"
            )
            blockers.append(
                blocker(
                    "character_rejected"
                    if rejected
                    else "character_style_choice_required",
                    "visual_quality" if rejected else "user_choice",
                    (
                        "用户已拒绝上一张标准角色。"
                        if rejected
                        else "尚未记录保留原画风、轻度 Q 版或完整 Q 版的选择。"
                    ),
                    "只从授权来源生成一张新标准角色候选并提交人工确认。",
                )
            )
            next_decision = decision(
                "choose_style_and_generate_one_character_candidate",
                kind="repair" if rejected else "wait_for_user",
                actor="agent" if rejected else "user",
                reason="必须先锁定单一、可复用的身份锚点。",
                postcondition=(
                    "character.status=awaiting_user_confirmation，且只有一张候选。"
                ),
            )
    else:
        gate_required = config["action_source_gate"].get("required") is True
        action_gate_failures = validate_action_source_gate(workspace)
        gate_entries: dict[str, Any] = {}
        if action_gate_path.is_file():
            try:
                raw_gate = json.loads(
                    action_gate_path.read_text(encoding="utf-8")
                )
                if (
                    isinstance(raw_gate, dict)
                    and isinstance(raw_gate.get("actions"), dict)
                ):
                    gate_entries = raw_gate["actions"]
            except json.JSONDecodeError:
                gate_entries = {}
        reviewed_actions = [
            name
            for name in ACTION_NAMES
            if isinstance(gate_entries.get(name), dict)
            and gate_entries[name].get("status") == "passed"
        ]
        missing_reviews = [
            name for name in ACTION_NAMES if name not in reviewed_actions
        ]
        action_gate_complete = not gate_required or not action_gate_failures

        if not action_gate_complete:
            blocker_code = (
                "action_review_required"
                if missing_reviews
                else "action_source_gate_stale"
            )
            blocker_category = (
                "prerequisite" if missing_reviews else "approval_stale"
            )
            blocker_recovery = (
                "按固定顺序完成下一个单一动作的 2–3 个候选和五项质检。"
                if missing_reviews
                else "回到已批准标准角色，只重做证据失效的单一动作。"
            )
            blockers.extend(
                blocker(
                    blocker_code,
                    blocker_category,
                    message,
                    blocker_recovery,
                )
                for message in action_gate_failures
            )
            if missing_reviews:
                target = missing_reviews[0]
                phase = "action_source_review"
                next_decision = decision(
                    "generate_and_review_one_action",
                    kind="continue",
                    actor="agent",
                    target=target,
                    reason="基础动作必须逐个完成候选与五项视觉质检。",
                    postcondition=(
                        f"动作 {target} 的当前输出与审批角色、候选和五项检查"
                        "哈希绑定。"
                    ),
                )
            else:
                phase = "action_source_repair"
                next_decision = decision(
                    "repair_stale_action_source_gate",
                    kind="repair",
                    actor="agent",
                    reason="八项记录存在，但至少一项证据已失效。",
                    postcondition=(
                        "validate_action_source_gate 返回空失败列表。"
                    ),
                )
        elif (touch_failures := validate_required_touch_transitions(workspace)):
            phase = "touch_transition_generation"
            blockers.extend(
                blocker(
                    "touch_transition_evidence_stale",
                    "derived_asset",
                    message,
                    "从当前已批准角色、惊讶和开心动作重新确定性生成过渡帧。",
                )
                for message in touch_failures
            )
            next_decision = decision(
                "create_touch_transitions",
                kind="continue",
                actor="agent",
                reason="新宠物默认需要两张确定性摸头过渡帧。",
                command=(
                    ".\\.venv\\Scripts\\python.exe "
                    ".\\tools\\pet_workspace.py "
                    f"touch-transitions {config['pet_id']}"
                ),
                postcondition=(
                    f"{expected_touch} 张过渡帧存在、透明且绑定当前批准源哈希。"
                ),
            )
        elif (
            interaction_route_selected
            and not walk_approved
            and not interaction_fallback_allowed
        ):
            interaction = (
                interaction_record if isinstance(interaction_record, dict) else {}
            )
            preview_value = interaction.get("preview")
            preview_hash = interaction.get("preview_sha256")
            current_character_hash = character.get("candidate_sha256")
            pending_interaction_current = bool(
                interaction_status == "awaiting_user_confirmation"
                and interaction.get("mode")
                and re.fullmatch(
                    r"[a-z0-9][a-z0-9_-]{0,63}",
                    str(interaction.get("mode")),
                )
                is not None
                and workspace_file_reference_is_current(
                    workspace,
                    preview_value,
                    preview_hash,
                )
                and interaction.get("character_sha256")
                == current_character_hash
            )
            suggested_mode = {
                "static": "stationary_character",
                "vehicle-seated": "stationary_vehicle",
            }.get(pet_mode, "custom_interaction")
            submission_command_template = (
                ".\\.venv\\Scripts\\python.exe "
                ".\\tools\\onepic_workflow.py "
                f"--pet-id {config['pet_id']} interaction-review "
                f"--mode {suggested_mode} --preview \"{{preview_path}}\""
            )
            if pending_interaction_current:
                phase = "awaiting_interaction_approval"
                blockers.append(
                    blocker(
                        "interaction_user_approval_required",
                        "approval",
                        "替代互动预览正在等待用户确认行为、身份一致性和循环节奏。",
                        "展示当前预览；根据用户明确通过或否决记录同一证据。",
                    )
                )
                next_decision = decision(
                    "present_interaction_evidence_for_user_review",
                    kind="wait_for_user",
                    actor="agent_then_user",
                    approval_command=(
                        ".\\.venv\\Scripts\\python.exe "
                        ".\\tools\\onepic_workflow.py "
                        f"--pet-id {config['pet_id']} approve-interaction --yes "
                        f"--expected-preview-sha256 {preview_hash} "
                        f"--expected-mode {interaction.get('mode')}"
                    ),
                    rejection_command_template=(
                        ".\\.venv\\Scripts\\python.exe "
                        ".\\tools\\onepic_workflow.py "
                        f"--pet-id {config['pet_id']} reject-interaction --yes "
                        f"--expected-preview-sha256 {preview_hash} "
                        f"--expected-mode {interaction.get('mode')} "
                        '--reason "{user_feedback}"'
                    ),
                    required_inputs={
                        "user_feedback": "用户对当前互动预览的原始否决反馈；作为单个安全转义参数传入。"
                    },
                    reason="替代互动与走路一样需要可观察证据和显式人工反馈。",
                    postcondition=(
                        "interaction.status=approved 或 rejected，且结果绑定当前预览与角色哈希。"
                    ),
                )
            elif interaction_status == "rejected":
                rejection_is_current = bool(
                    workspace_file_reference_is_current(
                        workspace,
                        preview_value,
                        interaction.get("rejected_preview_sha256"),
                    )
                    and interaction.get("rejected_character_sha256")
                    == current_character_hash
                    and interaction.get("rejected_mode", interaction.get("mode"))
                    == interaction.get("mode")
                )
                if rejection_is_current:
                    phase = "interaction_revision_required"
                    blockers.append(
                        blocker(
                            "interaction_user_rejected",
                            "visual_feedback",
                            str(
                                interaction.get("rejection_reason")
                                or "用户否决了当前替代互动。"
                            ),
                            "回到已批准标准角色，只修改反馈涉及的互动效果并生成新预览。",
                        )
                    )
                    next_decision = decision(
                        "revise_rejected_interaction_from_approved_character",
                        kind="repair",
                        actor="agent",
                        reason="负反馈必须持久化并驱动下一轮互动修订。",
                        postcondition="新互动预览哈希不同于被否决版本。",
                    )
                else:
                    phase = "interaction_preview_generation"
                    next_decision = decision(
                        "submit_revised_interaction_preview",
                        kind="continue",
                        actor="agent",
                        command_template=submission_command_template,
                        required_inputs={
                            "preview_path": "按反馈修改后、当前工作区内的可视互动预览路径。"
                        },
                        reason="互动证据已按反馈变化，必须重新提交后才能请求批准。",
                        postcondition=(
                            "interaction.status=awaiting_user_confirmation，且新预览绑定当前角色哈希。"
                        ),
                    )
            else:
                stale_approval = interaction_status in {
                    "approved",
                    "awaiting_user_confirmation",
                    "legacy_unbound",
                }
                phase = "interaction_preview_generation"
                if stale_approval:
                    blockers.append(
                        blocker(
                            "interaction_evidence_stale",
                            "approval_stale",
                            "替代互动预览、角色绑定或旧审批已失效。",
                            "从当前已批准标准角色制作并提交新的可视预览。",
                        )
                    )
                next_decision = decision(
                    "create_and_submit_interaction_preview",
                    kind="repair" if stale_approval else "continue",
                    actor="agent",
                    command_template=submission_command_template,
                    required_inputs={
                        "preview_path": "当前工作区内新生成、准备提交的可视互动预览路径。"
                    },
                    reason=(
                        "静态和载具路线必须用已展示的替代互动证据取代走路门禁。"
                    ),
                    postcondition=(
                        "interaction.status=awaiting_user_confirmation，且预览和角色哈希已登记。"
                    ),
                )
        elif not interaction_fallback_allowed and not walk_complete:
            phase = "walk_generation"
            missing_walk = [
                path.name for path in walk_paths if not path.is_file()
            ]
            blockers.append(
                blocker(
                    "walk_phases_incomplete",
                    "temporal_motion",
                    "八相位走路素材不完整：" + ", ".join(missing_walk),
                    "从已批准标准角色制作缺失相位；不得用整体平移伪装走路。",
                )
            )
            next_decision = decision(
                "generate_eight_phase_walk",
                kind="continue",
                actor="agent",
                target=missing_walk[0],
                reason="走路需形成可观察的接触、下压、迈步和抬升闭环。",
                postcondition=(
                    "walk_01.png 到 walk_08.png 完整且画布一致。"
                ),
            )
        elif not (walk_approved or interaction_fallback_allowed):
            walk_status = str(
                walk_record.get("status", "not_generated")
                if isinstance(walk_record, dict)
                else "invalid"
            )
            if walk_status == "awaiting_user_confirmation":
                preview_value = walk_record.get("preview")
                preview_path = (
                    PROJECT_ROOT / str(preview_value)
                    if preview_value
                    else None
                )
                pending_walk_current = bool(
                    walk_complete
                    and file_bundle_sha256(walk_paths)
                    == walk_record.get("asset_sha256")
                    and preview_path is not None
                    and preview_path.is_file()
                    and file_sha256(preview_path)
                    == walk_record.get("preview_sha256")
                )
                if not pending_walk_current:
                    phase = "walk_review_generation"
                    blockers.append(
                        blocker(
                            "pending_walk_evidence_stale",
                            "approval_stale",
                            "待确认走路帧或 GIF 已变化，旧审批命令失效。",
                            "从当前八帧重新生成 GIF 并再次展示。",
                        )
                    )
                    next_decision = decision(
                        "create_walk_review",
                        kind="repair",
                        actor="agent",
                        command=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\pet_workspace.py "
                            f"walk-review {config['pet_id']}"
                        ),
                        reason="用户只能批准其实际查看且哈希未变化的动态证据。",
                        postcondition="当前八帧与新 GIF 哈希重新绑定。",
                    )
                else:
                    phase = "awaiting_walk_approval"
                    blockers.append(
                        blocker(
                            "walk_user_approval_required",
                            "approval",
                            "八相位走路 GIF 正在等待用户确认自然度。",
                            "展示当前 GIF；根据用户明确通过或否决记录同一证据。",
                        )
                    )
                    next_decision = decision(
                        "present_walk_evidence_for_user_review",
                        kind="wait_for_user",
                        actor="agent_then_user",
                        approval_command=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\onepic_workflow.py "
                            f"--pet-id {config['pet_id']} approve-walk --yes "
                            "--expected-preview-sha256 "
                            f"{walk_record.get('preview_sha256')} "
                            "--expected-asset-sha256 "
                            f"{walk_record.get('asset_sha256')}"
                        ),
                        rejection_command_template=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\onepic_workflow.py "
                            f"--pet-id {config['pet_id']} reject-walk --yes "
                            "--expected-preview-sha256 "
                            f"{walk_record.get('preview_sha256')} "
                            "--expected-asset-sha256 "
                            f"{walk_record.get('asset_sha256')} "
                            '--reason "{user_feedback}"'
                        ),
                        required_inputs={
                            "user_feedback": "用户对当前走路 GIF 的原始否决反馈；作为单个安全转义参数传入。"
                        },
                        reason="自动指标不能替代动态视觉验收。",
                        postcondition=(
                            "walk.status=approved 或 rejected，且反馈绑定当前帧束。"
                        ),
                    )
            elif walk_status == "rejected":
                rejected_hash = walk_record.get("rejected_asset_sha256")
                current_hash = (
                    file_bundle_sha256(walk_paths) if walk_complete else None
                )
                if current_hash and current_hash != rejected_hash:
                    phase = "walk_review_generation"
                    next_decision = decision(
                        "create_walk_review",
                        kind="continue",
                        actor="agent",
                        command=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\pet_workspace.py "
                            f"walk-review {config['pet_id']}"
                        ),
                        reason="走路已按负反馈修改，需要生成并展示新证据。",
                        postcondition="新 GIF 与修改后的八帧哈希绑定。",
                    )
                else:
                    phase = "walk_revision_required"
                    blockers.append(
                        blocker(
                            "walk_user_rejected",
                            "visual_feedback",
                            str(
                                walk_record.get("rejection_reason")
                                or "用户否决了当前走路效果。"
                            ),
                            "回到已批准标准角色，只修改当前八相位后重新生成预览。",
                        )
                    )
                    next_decision = decision(
                        "revise_rejected_walk_from_approved_character",
                        kind="repair",
                        actor="agent",
                        reason="负反馈必须持久化并驱动下一轮素材修订。",
                        postcondition="八帧素材束哈希不同于被否决版本。",
                    )
            else:
                phase = "walk_review_generation"
                next_decision = decision(
                    "create_walk_review",
                    kind="continue",
                    actor="agent",
                    command=(
                        ".\\.venv\\Scripts\\python.exe "
                        ".\\tools\\pet_workspace.py "
                        f"walk-review {config['pet_id']}"
                    ),
                    reason="完整走路帧必须先转换为用户可检查的动态证据。",
                    postcondition=(
                        "walk.status=awaiting_user_confirmation 且预览哈希已登记。"
                    ),
                )
        else:
            validation = validate_workspace(workspace)
            binding_failures = validate_workflow_bindings(workspace)
            combined_failures = [
                *validation["failures"],
                *validate_required_touch_transitions(workspace),
                *binding_failures,
            ]
            current_build_inputs: dict[str, Any] = {}
            build_input_error: str | None = None
            try:
                current_build_inputs = workspace_build_input_evidence(workspace)
            except (OSError, ValueError, WorkflowError) as exc:
                build_input_error = str(exc)
            observations["build_input_status"] = (
                "failed" if build_input_error else "passed"
            )
            evidence["build_input_fingerprint"] = current_build_inputs.get(
                "fingerprint"
            )
            if combined_failures:
                phase = "workspace_repair"
                blockers.extend(
                    blocker(
                        "workspace_validation_failed",
                        "integrity",
                        message,
                        "只修复对应素材、配置或陈旧审批；修复后重新观察，"
                        "不跨过当前门禁。",
                    )
                    for message in combined_failures
                )
                next_decision = decision(
                    "repair_workspace_validation",
                    kind="repair",
                    actor="agent",
                    target=combined_failures[0],
                    reason="构建前置条件或证据绑定未满足。",
                    postcondition=(
                        "素材校验与 workflow 哈希绑定均无失败。"
                    ),
                )
            elif build_input_error:
                phase = "build_input_repair"
                blockers.append(
                    blocker(
                        "application_or_workspace_build_input_invalid",
                        "build_input",
                        build_input_error,
                        "恢复缺失的应用源码、构建规格、公开资源或当前工作区输入；"
                        "修复后重新观察，不重复同一失败构建。",
                    )
                )
                next_decision = decision(
                    "repair_missing_or_invalid_build_input",
                    kind="repair",
                    actor="agent",
                    target=build_input_error,
                    reason="构建指纹无法形成时不能比较失败记录、构建或批准 runtime。",
                    postcondition="workspace_build_input_evidence 可生成非空指纹。",
                )
            elif (
                latest_failure
                and latest_failure.get("build_input_fingerprint")
                == current_build_inputs.get("fingerprint")
            ):
                phase = "build_failure_recovery"
                blockers.append(
                    blocker(
                        "latest_build_failed_for_current_inputs",
                        "runtime",
                        str(latest_failure.get("error") or "当前输入的最近构建失败"),
                        "检查 review/latest-failure.json 与 failed-builds；"
                        "修复对应输入或明确诊断瞬态故障后再重试。",
                    )
                )
                next_decision = decision(
                    "inspect_and_repair_latest_build_failure",
                    kind="repair",
                    actor="agent",
                    target=str(latest_failure.get("failed_build") or ""),
                    recovery_command_template=(
                        ".\\.venv\\Scripts\\python.exe "
                        ".\\tools\\pet_workspace.py "
                        f"ack-build-failure {config['pet_id']} --yes "
                        f"--build-id {latest_failure.get('build_id')} "
                        "--build-input-fingerprint "
                        f"{latest_failure.get('build_input_fingerprint')} "
                        '--classification transient --reason "{diagnostic_reason}"'
                    ),
                    required_inputs={
                        "diagnostic_reason": "确认该失败属于瞬态故障的具体诊断依据；作为单个安全转义参数传入。"
                    },
                    reason="相同输入已经失败，盲目重复同一构建不会形成新观察。",
                    postcondition=(
                        "构建输入指纹变化，或最近失败已被人工诊断并安全处理。"
                    ),
                )
            elif not runtime_manifest.is_file():
                phase = "ready_to_build"
                next_decision = decision(
                    "build_and_activate_staging",
                    kind="continue",
                    actor="agent",
                    command=(
                        f".\\scripts\\build_pet.ps1 -PetId {config['pet_id']} "
                        "-Activate"
                    ),
                    reason="输入、审批和自动 QA 前置条件均已满足。",
                    postcondition=(
                        "staging QA 与隔离测试通过后原子激活；失败时保留旧 runtime。"
                    ),
                )
            else:
                runtime_attestation = inspect_runtime_build_attestation(workspace)
                observations["runtime_attestation_status"] = runtime_attestation[
                    "status"
                ]
                evidence["build_input_fingerprint"] = runtime_attestation.get(
                    "current_build_input_fingerprint"
                )
                evidence["build_id"] = runtime_attestation.get("build_id")
                evidence["review_visual_fingerprint"] = runtime_attestation.get(
                    "review_visual_fingerprint"
                )
                if runtime_attestation["status"] != "passed":
                    phase = "runtime_rebuild_required"
                    blockers.append(
                        blocker(
                            "runtime_build_attestation_stale",
                            "runtime",
                            "；".join(runtime_attestation["failures"]),
                            "保留当前激活版本，从未变化的已批准输入重新构建并完成 QA。",
                        )
                    )
                    next_decision = decision(
                        "rebuild_attested_runtime",
                        kind="repair",
                        actor="agent",
                        command=(
                            f".\\scripts\\build_pet.ps1 -PetId {config['pet_id']} "
                            "-Activate"
                        ),
                        reason="旧 runtime、旧 QA 或已变化输入不能代表当前构建。",
                        postcondition=(
                            "runtime、assets QA 与 latest-build 绑定同一 build_id 和当前输入指纹。"
                        ),
                    )
                elif runtime_rejection_is_current(workspace, runtime_attestation):
                    phase = "runtime_revision_required"
                    runtime_review = workflow.get("runtime_review", {})
                    blockers.append(
                        blocker(
                            "runtime_user_rejected",
                            "visual_feedback",
                            str(
                                runtime_review.get("rejection_reason")
                                if isinstance(runtime_review, dict)
                                else "用户否决了当前成品效果。"
                            ),
                            "仅修改反馈涉及的素材、配置或应用行为；"
                            "新 build 必须重新 QA 和视觉验收。",
                        )
                    )
                    next_decision = decision(
                        "revise_rejected_runtime",
                        kind="repair",
                        actor="agent",
                        reason="用户负反馈必须绑定当前 build 并驱动下一轮修订。",
                        postcondition="产生不同 build_id 的新取证 runtime。",
                    )
                elif runtime_review_is_current(workspace, runtime_attestation):
                    phase = "ready_to_release"
                    next_decision = decision(
                        "release_validated_pet",
                        kind="continue",
                        actor="agent",
                        command=(
                            f".\\scripts\\release_pet.ps1 -PetId {config['pet_id']} "
                            "-Mode share-no-selfie"
                        ),
                        reason="当前构建、自动 QA 与用户最终视觉验收均已哈希绑定。",
                        postcondition="生成完整目录 ZIP、隐私扫描和发布报告。",
                    )
                else:
                    phase = "awaiting_final_visual_review"
                    blockers.append(
                        blocker(
                            "final_user_review_required",
                            "approval",
                            "自动 QA 已通过，仍需用户查看 GIF、联系表和真实窗口。",
                            "展示当前 build 对应证据；用户明确验收后才能发布。",
                        )
                    )
                    next_decision = decision(
                        "present_runtime_evidence_for_final_review",
                        kind="wait_for_user",
                        actor="agent_then_user",
                        approval_command=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\pet_workspace.py "
                            f"approve-runtime {config['pet_id']} --yes "
                            "--build-id "
                            f"{runtime_attestation['build_id']} "
                            "--review-visual-sha256 "
                            f"{runtime_attestation['review_visual_fingerprint']}"
                        ),
                        rejection_command_template=(
                            ".\\.venv\\Scripts\\python.exe "
                            ".\\tools\\pet_workspace.py "
                            f"reject-runtime {config['pet_id']} --yes "
                            "--build-id "
                            f"{runtime_attestation['build_id']} "
                            "--review-visual-sha256 "
                            f"{runtime_attestation['review_visual_fingerprint']} "
                            '--reason "{user_feedback}"'
                        ),
                        required_inputs={
                            "user_feedback": "用户对当前 build 的原始否决反馈；作为单个安全转义参数传入。"
                        },
                        reason="真实视觉体验是自动测试无法替代的终态条件。",
                        postcondition=(
                            "用户明确批准当前 build，runtime_review 绑定 manifest 与 QA 哈希。"
                        ),
                    )

    return {
        "schema_version": 1,
        "pet_id": config["pet_id"],
        "goal": {
            "profile": "onepic-upgraded-base",
            "rendering_mode": config.get("rendering_mode", "sprite"),
            "base_frame_count": 38,
            "expected_touch_frame_count": expected_touch,
        },
        "phase": phase,
        "must_pause": next_decision["actor"] != "agent",
        "observation": observations,
        "blockers": blockers,
        "allowed_next_actions": [next_decision["action"]],
        "decision": next_decision,
        "evidence": evidence,
        "safety": {
            "automatic_user_approval_allowed": False,
            "visual_checks_replace_user_review": False,
            "private_assets_uploaded": False,
        },
    }


def validate_workspace(workspace: Path) -> dict[str, Any]:
    """严格检查构建所需的角色、动作和走路素材。"""

    config = load_config(workspace)
    status = workspace_status(workspace)
    failures: list[str] = []
    images: list[dict[str, Any]] = []
    selfie = workspace / str(config["selfie_file"])
    character = workspace / str(config["character_file"])
    action_root = workspace / str(config["actions_directory"])
    walk_root = workspace / str(config["walk_directory"])
    try:
        workflow = load_gate_state(workspace / "workflow.json")
        interaction_fallback = interaction_fallback_is_allowed(config, workflow)
    except WorkflowError as exc:
        interaction_fallback = False
        failures.append(f"制作流程状态无法解析：{exc}")

    required_images = [("标准角色", character, True)]
    if bool(config.get("selfie_required", True)) or selfie.is_file():
        required_images.insert(0, ("自拍成片", selfie, False))
    for label, path, require_alpha in required_images:
        if not path.is_file():
            failures.append(f"缺少{label}：{path}")
            continue
        result = inspect_image(path, require_alpha=require_alpha)
        images.append(result)
        if result.get("failure"):
            failures.append(f"{label}{result['failure']}：{path}")

    for name in ACTION_NAMES:
        path = action_root / f"{name}.png"
        if not path.is_file():
            failures.append(f"缺少动作 {name}：{path}")
            continue
        result = inspect_image(path, require_alpha=True)
        images.append(result)
        if result.get("failure"):
            failures.append(f"动作 {name} {result['failure']}")

    if not interaction_fallback:
        walk_sizes: set[tuple[int, int]] = set()
        for index in range(1, 9):
            path = walk_root / f"walk_{index:02d}.png"
            if not path.is_file():
                failures.append(f"缺少走路帧：{path}")
                continue
            result = inspect_image(path, require_alpha=True)
            images.append(result)
            walk_sizes.add(tuple(result["size"]))
            if result.get("failure"):
                failures.append(f"走路帧 {index} {result['failure']}")
        if len(walk_sizes) > 1:
            failures.append(f"八帧走路画布尺寸不一致：{sorted(walk_sizes)}")

    failures.extend(validate_action_source_gate(workspace))

    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "summary": status,
        "images": images,
    }


def validate_required_touch_transitions(workspace: Path) -> list[str]:
    """检查 0–2 张摸头过渡图及其批准源素材的派生证据。"""

    config = load_config(workspace)
    required = int(
        config.get("touch_feedback", {}).get(
            "default_transition_frame_count",
            0,
        )
    )
    if required not in (0, 1, 2):
        return ["default_transition_frame_count 只能是 0、1 或 2"]
    action_root = workspace / str(config["actions_directory"])
    failures: list[str] = []
    required_names = ("touch_soften", "touch_return")[:required]
    for name in required_names:
        path = action_root / f"{name}.png"
        if not path.is_file():
            failures.append(f"缺少新宠物摸头过渡帧 {name}：{path}")
            continue
        result = inspect_image(path, require_alpha=True)
        if result.get("failure"):
            failures.append(f"摸头过渡帧 {name} {result['failure']}")
    if required == 0:
        return failures

    review_root = workspace / str(config["review_directory"])
    manifest_path = review_root / "touch-transitions.json"
    manifest, read_error = read_json_object(manifest_path)
    if read_error:
        failures.append(f"摸头过渡派生记录无法解析：{read_error}")
        return failures
    if not manifest:
        failures.append("缺少摸头过渡派生记录 touch-transitions.json")
        return failures
    if manifest.get("schema_version") != 1:
        failures.append("摸头过渡派生记录版本无效")
    sources = manifest.get("sources", {})
    outputs = manifest.get("outputs", {})
    if not isinstance(sources, dict) or not isinstance(outputs, dict):
        failures.append("摸头过渡派生记录缺少 sources/outputs 对象")
        return failures
    source_paths = {
        "character": workspace / str(config["character_file"]),
        "surprised": action_root / "surprised.png",
        "happy": action_root / "happy.png",
    }
    for name, path in source_paths.items():
        entry = sources.get(name, {})
        actual = file_sha256(path) if path.is_file() else None
        if not isinstance(entry, dict) or entry.get("sha256") != actual:
            failures.append(f"摸头过渡帧未绑定当前来源：{name}")
    for name in required_names:
        path = action_root / f"{name}.png"
        entry = outputs.get(f"{name}.png", {})
        actual = file_sha256(path) if path.is_file() else None
        if not isinstance(entry, dict) or entry.get("sha256") != actual:
            failures.append(f"摸头过渡帧已在派生后变化：{name}")
    return failures


def create_touch_transition_sources(workspace: Path) -> dict[str, Any]:
    """只复用已确认素材，确定性生成两张短促摸头过渡源图。"""

    require_workspace_character_binding(workspace)
    config = load_config(workspace)
    action_root = workspace / str(config["actions_directory"])
    candidate = workspace / str(config["character_file"])
    surprised = action_root / "surprised.png"
    reaction = action_root / "happy.png"
    for label, path in (
        ("标准角色", candidate),
        ("惊讶动作", surprised),
        ("开心动作", reaction),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"缺少{label}：{path}")

    try:
        from .build_pet_workspace import normalize_pose
    except ImportError:
        from build_pet_workspace import normalize_pose

    canvas_size = tuple(int(item) for item in config["canvas_size"])
    standing_height = int(config["standing_height"])
    with Image.open(candidate) as source:
        idle_frame = normalize_pose(
            source.convert("RGBA"),
            standing_height,
            canvas_size=canvas_size,
        )
    with Image.open(surprised) as source:
        surprised_frame = normalize_pose(
            source.convert("RGBA"),
            standing_height,
            canvas_size=canvas_size,
        )
    with Image.open(reaction) as source:
        reaction_frame = normalize_pose(
            source.convert("RGBA"),
            standing_height,
            canvas_size=canvas_size,
        )

    reaction_matches_idle = (
        ImageChops.difference(reaction_frame, idle_frame).getbbox() is None
    )
    if reaction_matches_idle:
        method = "deterministic_hold_when_reaction_matches_idle"
        outputs = {
            "touch_soften.png": reaction_frame.copy(),
            "touch_return.png": idle_frame.copy(),
        }
    else:
        method = "deterministic_crossfade_of_approved_frames"
        outputs = {
            "touch_soften.png": Image.blend(
                surprised_frame,
                reaction_frame,
                0.58,
            ),
            "touch_return.png": Image.blend(
                reaction_frame,
                idle_frame,
                0.62,
            ),
        }
    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    staging = workspace / "staging" / f"touch-transitions-{build_id}"
    staging.mkdir(parents=True)
    backups: dict[str, str] = {}
    try:
        for name, image in outputs.items():
            image.save(staging / name, "PNG", optimize=True)
        backup_root = workspace / "backups" / f"touch-transitions-{build_id}"
        for name in outputs:
            destination = action_root / name
            if destination.is_file():
                backup_root.mkdir(parents=True, exist_ok=True)
                backup = backup_root / name
                shutil.copy2(destination, backup)
                backups[name] = backup.relative_to(workspace).as_posix()
            (staging / name).replace(destination)
    except Exception:
        failed = workspace / "failed-builds" / f"touch-transitions-{build_id}"
        failed.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            staging.rename(failed)
        raise
    if staging.exists():
        staging.rmdir()
    report = {
        "status": "created",
        "method": method,
        "files": {
            name: {
                "path": (action_root / name).relative_to(workspace).as_posix(),
                "sha256": file_sha256(action_root / name),
            }
            for name in outputs
        },
        "backups": backups,
    }
    review_root = workspace / str(config["review_directory"])
    review_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "pet_id": config["pet_id"],
        "generated_at": now_utc(),
        "method": method,
        "sources": {
            name: {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": file_sha256(path),
            }
            for name, path in {
                "character": candidate,
                "surprised": surprised,
                "happy": reaction,
            }.items()
        },
        "outputs": report["files"],
    }
    manifest_path = review_root / "touch-transitions.json"
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    report["provenance"] = {
        "path": manifest_path.relative_to(workspace).as_posix(),
        "sha256": file_sha256(manifest_path),
    }
    return report


def validate_workflow_bindings(workspace: Path) -> list[str]:
    """验证每宠物审批确实绑定当前候选、走路、互动与扩展素材。"""

    config = load_config(workspace)
    state = load_gate_state(workspace / "workflow.json")
    failures: list[str] = []
    if not (
        authorized_source_is_current(workspace, state)
        or migration_evidence_is_current(workspace, state)
    ):
        failures.append("授权来源或受验证的历史迁移证据不存在/已失效")
    candidate = workspace / str(config["character_file"])
    if character_is_approved(state):
        actual = file_sha256(candidate) if candidate.is_file() else None
        if actual != state["character"].get("candidate_sha256"):
            failures.append("标准角色确认哈希与 candidate/approved.png 不一致")
    else:
        failures.append("标准角色确认门禁未通过")
    if walk_is_approved(state):
        walk_root = workspace / str(config["walk_directory"])
        paths = [walk_root / f"walk_{index:02d}.png" for index in range(1, 9)]
        if not all(path.is_file() for path in paths):
            failures.append("走路确认已存在但八帧素材不完整")
        else:
            actual = file_bundle_sha256(paths)
            if actual != state["walk"].get("asset_sha256"):
                failures.append("走路确认哈希与当前八帧素材束不一致")
    elif interaction_is_approved(state):
        if str(config.get("pet_mode", "full-body")) not in (
            INTERACTION_FALLBACK_PET_MODES
        ):
            failures.append(
                "当前 pet_mode 不允许用替代互动绕过八相位走路；"
                "仅 half-body、vehicle-seated 或 static 可用"
            )
        if not workspace_file_reference_is_current(
            workspace,
            state["interaction"].get("preview"),
            state["interaction"].get("preview_sha256"),
        ):
            failures.append("替代互动确认预览不在当前工作区、已缺失或哈希不一致")
    else:
        failures.append("走路或替代互动确认门禁未通过")

    care = normalize_care_config(config.get("care_simulation", {}))
    if care.get("enabled") is True:
        feature = state.get("feature_status", {}).get("vpet_care_v1", {})
        actual_hash = care_config_sha256(care)
        if (
            not isinstance(feature, dict)
            or feature.get("status") != "activated"
            or feature.get("user_confirmed") is not True
            or feature.get("activation_allowed") is not True
            or feature.get("approved_config_sha256") != actual_hash
            or feature.get("config_sha256") != actual_hash
        ):
            failures.append("VPet 养成配置未确认或确认哈希与当前配置不一致")

    configured_extensions = {
        str(name): value
        for name, value in config.get("extension_animations", {}).items()
        if isinstance(value, dict) and value.get("enabled", False)
    }
    directional = config.get("directional_mouse_follow", {})
    gaze_enabled = bool(
        isinstance(directional, dict) and directional.get("enabled", False)
    )
    if configured_extensions or gaze_enabled:
        approvals = state.get("extension_approvals", {})
        approved_packs = [
            value
            for value in approvals.values()
            if isinstance(value, dict)
            and value.get("status") == "approved"
            and value.get("activation_allowed") is True
            and value.get("user_confirmed") is True
        ]
        if not approved_packs:
            failures.append("扩展动作确认门禁未通过")
            return failures

        workspace_root = workspace.resolve()

        def checked_paths(
            values: list[object],
            *,
            label: str,
        ) -> list[Path] | None:
            paths: list[Path] = []
            for value in values:
                path = (workspace / str(value)).resolve()
                if (
                    path != workspace_root
                    and workspace_root not in path.parents
                ):
                    failures.append(f"{label}素材路径越过宠物工作区")
                    return None
                if not path.is_file():
                    failures.append(f"{label}素材不完整：{path.name}")
                    return None
                paths.append(path)
            return paths

        def has_matching_approval(name: str, actual: str) -> bool:
            """允许不同批次分别批准动作，但每项都必须匹配当前帧束。"""

            for pack in approved_packs:
                items = pack.get("items", {})
                if not isinstance(items, dict):
                    continue
                item = items.get(name, {})
                if not isinstance(item, dict):
                    continue
                approved = item.get("approved_asset_sha256")
                if (
                    actual == approved
                    and item.get("asset_sha256") == approved
                ):
                    return True
            return False

        for name, extension in configured_extensions.items():
            raw_frames = extension.get("frames", [])
            paths = (
                checked_paths(raw_frames, label=f"扩展动作 {name}")
                if isinstance(raw_frames, list)
                else None
            )
            if paths is None:
                continue
            actual = file_bundle_sha256(paths)
            if not has_matching_approval(name, actual):
                failures.append(f"扩展动作 {name} 确认哈希与当前帧束不一致")

        if gaze_enabled:
            raw_frames = directional.get("frames", {})
            ordered_values = (
                [raw_frames[name] for name in sorted(raw_frames)]
                if isinstance(raw_frames, dict)
                else []
            )
            paths = checked_paths(ordered_values, label="八向视线")
            if paths is not None:
                actual = file_bundle_sha256(paths)
                if not has_matching_approval("gaze", actual):
                    failures.append("八向视线确认哈希与当前帧束不一致")
    return failures


def inspect_runtime_build_attestation(workspace: Path) -> dict[str, Any]:
    """验证 runtime、最新 QA 与当前构建输入属于同一次可追溯构建。"""

    config = load_config(workspace)
    review_root = workspace / str(config["review_directory"])
    runtime_manifest_path = (
        workspace / str(config["runtime_directory"]) / "manifest.json"
    )
    qa_path = review_root / "assets-qa.json"
    latest_path = review_root / "latest-build.json"
    runtime_manifest, runtime_error = read_json_object(runtime_manifest_path)
    qa, qa_error = read_json_object(qa_path)
    latest, latest_error = read_json_object(latest_path)
    failures: list[str] = []
    for label, path, value, error in (
        ("runtime manifest", runtime_manifest_path, runtime_manifest, runtime_error),
        ("assets QA", qa_path, qa, qa_error),
        ("latest build", latest_path, latest, latest_error),
    ):
        if error:
            failures.append(f"{label} 无法解析：{error}")
        elif not path.is_file():
            failures.append(f"缺少 {label}：{path}")
        elif not value:
            failures.append(f"{label} 为空或缺少有效对象")

    try:
        current_inputs = workspace_build_input_evidence(workspace)
    except (OSError, ValueError, WorkflowError) as exc:
        current_inputs = {
            "schema_version": BUILD_INPUT_SCHEMA_VERSION,
            "fingerprint": None,
        }
        failures.append(f"当前构建输入无法取证：{exc}")
    try:
        current_runtime_asset_fingerprint = runtime_asset_bundle_sha256(
            runtime_manifest_path.parent
        )
    except (OSError, ValueError) as exc:
        current_runtime_asset_fingerprint = None
        failures.append(f"当前 runtime 素材无法取证：{exc}")
    try:
        current_review_visual_fingerprint = review_visual_evidence_sha256(
            review_root
        )
    except (OSError, ValueError) as exc:
        current_review_visual_fingerprint = None
        failures.append(f"当前顶层视觉证据无法取证：{exc}")
    build_id = runtime_manifest.get("build_id")
    fingerprint = runtime_manifest.get("build_input_fingerprint")
    application_fingerprint = runtime_manifest.get("application_fingerprint")
    runtime_asset_fingerprint = runtime_manifest.get(
        "runtime_asset_fingerprint"
    )
    schema_version = runtime_manifest.get("build_input_schema_version")
    if runtime_manifest:
        if runtime_manifest.get("pet_id") != config["pet_id"]:
            failures.append("runtime manifest 的 pet_id 与当前工作区不一致")
        if not isinstance(build_id, str) or not build_id:
            failures.append("runtime manifest 缺少 build_id")
        if schema_version != BUILD_INPUT_SCHEMA_VERSION:
            failures.append("runtime manifest 缺少当前构建输入证据版本")
        if not isinstance(fingerprint, str) or not fingerprint:
            failures.append("runtime manifest 缺少构建输入指纹")
        elif fingerprint != current_inputs.get("fingerprint"):
            failures.append("runtime 对应的构建输入已变化，需要重新构建")
        if (
            not isinstance(application_fingerprint, str)
            or not application_fingerprint
        ):
            failures.append("runtime manifest 缺少应用源码指纹")
        elif application_fingerprint != current_inputs.get(
            "application_fingerprint"
        ):
            failures.append("runtime 对应的应用源码或打包资源已变化")
        if (
            not isinstance(runtime_asset_fingerprint, str)
            or not runtime_asset_fingerprint
        ):
            failures.append("runtime manifest 缺少运行素材指纹")
        elif runtime_asset_fingerprint != current_runtime_asset_fingerprint:
            failures.append("runtime 运行素材已在构建后变化")
    if qa:
        if qa.get("status") != "passed":
            failures.append(f"assets QA 状态不是 passed：{qa.get('status')}")
        if qa.get("build_id") != build_id:
            failures.append("assets QA 与 runtime build_id 不一致")
        if qa.get("build_input_fingerprint") != fingerprint:
            failures.append("assets QA 与 runtime 构建输入指纹不一致")
        if qa.get("application_fingerprint") != application_fingerprint:
            failures.append("assets QA 与 runtime 应用源码指纹不一致")
        if qa.get("runtime_asset_fingerprint") != runtime_asset_fingerprint:
            failures.append("assets QA 与 runtime 运行素材指纹不一致")
        if (
            qa.get("review_visual_fingerprint")
            != current_review_visual_fingerprint
        ):
            failures.append("assets QA 与顶层最终视觉证据不一致")
    runtime_manifest_sha256 = (
        file_sha256(runtime_manifest_path)
        if runtime_manifest_path.is_file()
        else None
    )
    assets_qa_sha256 = file_sha256(qa_path) if qa_path.is_file() else None
    latest_build_sha256 = (
        file_sha256(latest_path) if latest_path.is_file() else None
    )
    if latest:
        if latest.get("pet_id") != config["pet_id"]:
            failures.append("latest-build.json 的 pet_id 与当前工作区不一致")
        if latest.get("build_id") != build_id:
            failures.append("latest-build.json 与 runtime build_id 不一致")
        if latest.get("build_input_fingerprint") != fingerprint:
            failures.append("latest-build.json 与 runtime 构建输入指纹不一致")
        if latest.get("application_fingerprint") != application_fingerprint:
            failures.append("latest-build.json 与 runtime 应用源码指纹不一致")
        if latest.get("runtime_asset_fingerprint") != runtime_asset_fingerprint:
            failures.append("latest-build.json 与 runtime 运行素材指纹不一致")
        if (
            latest.get("review_visual_fingerprint")
            != current_review_visual_fingerprint
        ):
            failures.append("latest-build.json 与顶层最终视觉证据不一致")
        if latest.get("runtime_manifest_sha256") != runtime_manifest_sha256:
            failures.append("latest-build.json 未绑定当前 runtime manifest")
        if latest.get("assets_qa_sha256") != assets_qa_sha256:
            failures.append("latest-build.json 未绑定当前 assets QA")
        build_path_value = latest.get("path")
        if not isinstance(build_path_value, str) or not build_path_value:
            failures.append("latest-build.json 缺少复核证据目录")
        else:
            build_path = (workspace / build_path_value).resolve()
            workspace_root = workspace.resolve()
            if (
                build_path == workspace_root
                or workspace_root not in build_path.parents
            ):
                failures.append("latest-build.json 的复核证据目录越过工作区")
            elif not build_path.is_dir():
                failures.append("latest-build.json 指向的复核证据目录不存在")
            else:
                build_qa = build_path / "assets-qa.json"
                if (
                    not build_qa.is_file()
                    or file_sha256(build_qa) != assets_qa_sha256
                ):
                    failures.append("最新复核目录与顶层 assets QA 不一致")
                try:
                    build_review_visual_fingerprint = (
                        review_visual_evidence_sha256(build_path)
                    )
                except (OSError, ValueError) as exc:
                    failures.append(f"最新复核目录视觉证据无法取证：{exc}")
                else:
                    if (
                        build_review_visual_fingerprint
                        != current_review_visual_fingerprint
                    ):
                        failures.append("最新复核目录与顶层视觉证据不一致")
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "pet_id": config["pet_id"],
        "build_id": build_id,
        "build_input_schema_version": schema_version,
        "build_input_fingerprint": fingerprint,
        "current_build_input_fingerprint": current_inputs.get("fingerprint"),
        "application_fingerprint": application_fingerprint,
        "current_application_fingerprint": current_inputs.get(
            "application_fingerprint"
        ),
        "runtime_asset_fingerprint": runtime_asset_fingerprint,
        "current_runtime_asset_fingerprint": (
            current_runtime_asset_fingerprint
        ),
        "review_visual_fingerprint": current_review_visual_fingerprint,
        "runtime_manifest_sha256": runtime_manifest_sha256,
        "assets_qa_sha256": assets_qa_sha256,
        "latest_build_sha256": latest_build_sha256,
    }


def runtime_review_is_current(
    workspace: Path,
    attestation: dict[str, Any] | None = None,
) -> bool:
    """判断用户最终视觉验收是否绑定当前已取证构建。"""

    evidence = attestation or inspect_runtime_build_attestation(workspace)
    if evidence.get("status") != "passed":
        return False
    workflow = load_gate_state(workspace / "workflow.json")
    review = workflow.get("runtime_review", {})
    return bool(
        isinstance(review, dict)
        and review.get("status") == "approved"
        and review.get("user_confirmed") is True
        and review.get("build_id") == evidence.get("build_id")
        and review.get("build_input_fingerprint")
        == evidence.get("build_input_fingerprint")
        and review.get("application_fingerprint")
        == evidence.get("application_fingerprint")
        and review.get("runtime_manifest_sha256")
        == evidence.get("runtime_manifest_sha256")
        and review.get("assets_qa_sha256")
        == evidence.get("assets_qa_sha256")
        and review.get("runtime_asset_fingerprint")
        == evidence.get("runtime_asset_fingerprint")
        and review.get("review_visual_fingerprint")
        == evidence.get("review_visual_fingerprint")
    )


def runtime_rejection_is_current(
    workspace: Path,
    attestation: dict[str, Any] | None = None,
) -> bool:
    """判断用户否决是否明确绑定当前 build 与当前视觉证据。"""

    evidence = attestation or inspect_runtime_build_attestation(workspace)
    if evidence.get("status") != "passed":
        return False
    workflow = load_gate_state(workspace / "workflow.json")
    review = workflow.get("runtime_review", {})
    return bool(
        isinstance(review, dict)
        and review.get("status") == "rejected"
        and review.get("build_id") == evidence.get("build_id")
        and review.get("build_input_fingerprint")
        == evidence.get("build_input_fingerprint")
        and review.get("application_fingerprint")
        == evidence.get("application_fingerprint")
        and review.get("runtime_manifest_sha256")
        == evidence.get("runtime_manifest_sha256")
        and review.get("assets_qa_sha256")
        == evidence.get("assets_qa_sha256")
        and review.get("runtime_asset_fingerprint")
        == evidence.get("runtime_asset_fingerprint")
        and review.get("review_visual_fingerprint")
        == evidence.get("review_visual_fingerprint")
    )


def approve_runtime_review(
    workspace: Path,
    *,
    confirmed: bool,
    expected_build_id: str,
    expected_review_visual_fingerprint: str,
) -> dict[str, Any]:
    """在用户查看指定 build 的证据后以 compare-and-set 绑定最终验收。"""

    if not confirmed:
        raise ValueError("必须传入 --yes，且只能在用户直接查看当前构建证据后执行")
    attestation = inspect_runtime_build_attestation(workspace)
    if attestation["status"] != "passed":
        raise ValueError(
            "当前 runtime 构建证据不完整：\n"
            + "\n".join(f"- {item}" for item in attestation["failures"])
        )
    if attestation.get("build_id") != expected_build_id:
        raise ValueError("当前 build_id 已变化；请重新展示并确认最新构建")
    if (
        attestation.get("review_visual_fingerprint")
        != expected_review_visual_fingerprint
    ):
        raise ValueError("当前视觉证据已变化；请重新展示并确认最新证据")
    workflow_path = workspace / "workflow.json"
    workflow = load_gate_state(workflow_path)
    workflow["runtime_review"] = {
        "status": "approved",
        "user_confirmed": True,
        "confirmation_method": "direct_user_visual_review",
        "build_id": attestation["build_id"],
        "build_input_fingerprint": attestation["build_input_fingerprint"],
        "application_fingerprint": attestation["application_fingerprint"],
        "runtime_manifest_sha256": attestation["runtime_manifest_sha256"],
        "assets_qa_sha256": attestation["assets_qa_sha256"],
        "runtime_asset_fingerprint": attestation[
            "runtime_asset_fingerprint"
        ],
        "review_visual_fingerprint": attestation[
            "review_visual_fingerprint"
        ],
        "approved_at": now_utc(),
    }
    save_gate_state(workflow, workflow_path)
    return workflow["runtime_review"]


def reject_runtime_review(
    workspace: Path,
    *,
    confirmed: bool,
    reason: str,
    expected_build_id: str,
    expected_review_visual_fingerprint: str,
) -> dict[str, Any]:
    """以 CAS 持久化用户对当前成品视觉证据的否决。"""

    if not confirmed:
        raise ValueError("拒绝当前 runtime 必须传入 --yes 并记录用户原始反馈")
    feedback = reason.strip()
    if not feedback:
        raise ValueError("拒绝当前 runtime 时必须记录具体原因")
    attestation = inspect_runtime_build_attestation(workspace)
    if attestation["status"] != "passed":
        raise ValueError("当前 runtime 构建证据不完整，无法绑定否决反馈")
    if (
        attestation.get("build_id") != expected_build_id
        or attestation.get("review_visual_fingerprint")
        != expected_review_visual_fingerprint
    ):
        raise ValueError("当前 build 或视觉证据已变化，请重新展示后再记录反馈")
    workflow_path = workspace / "workflow.json"
    workflow = load_gate_state(workflow_path)
    workflow["runtime_review"] = {
        "status": "rejected",
        "user_confirmed": False,
        "confirmation_method": "direct_user_visual_review",
        "rejection_reason": feedback,
        "build_id": attestation["build_id"],
        "build_input_fingerprint": attestation["build_input_fingerprint"],
        "application_fingerprint": attestation["application_fingerprint"],
        "runtime_manifest_sha256": attestation["runtime_manifest_sha256"],
        "assets_qa_sha256": attestation["assets_qa_sha256"],
        "runtime_asset_fingerprint": attestation["runtime_asset_fingerprint"],
        "review_visual_fingerprint": attestation[
            "review_visual_fingerprint"
        ],
        "rejected_at": now_utc(),
    }
    save_gate_state(workflow, workflow_path)
    return workflow["runtime_review"]


def require_current_runtime_review(workspace: Path) -> dict[str, Any]:
    """要求发布对象已通过绑定当前构建哈希的用户最终视觉验收。"""

    attestation = inspect_runtime_build_attestation(workspace)
    if attestation["status"] != "passed":
        raise ValueError("当前 runtime 构建取证未通过，拒绝发布")
    if not runtime_review_is_current(workspace, attestation):
        raise ValueError("当前 runtime 尚未通过绑定 build 哈希的用户最终视觉验收")
    return attestation


def current_runtime_review_status(workspace: Path) -> dict[str, Any]:
    """返回发布前可直接用于退出码判定的当前 runtime 最终验收状态。"""

    attestation = inspect_runtime_build_attestation(workspace)
    failures = list(attestation["failures"])
    review_current = runtime_review_is_current(workspace, attestation)
    if attestation["status"] == "passed" and not review_current:
        failures.append("当前 runtime 尚未通过绑定 build 哈希的用户最终视觉验收")
    return {
        **attestation,
        "status": "passed" if not failures and review_current else "failed",
        "runtime_review_current": review_current,
        "failures": failures,
    }


def acknowledge_build_failure(
    workspace: Path,
    *,
    confirmed: bool,
    build_id: str,
    build_input_fingerprint: str,
    classification: str,
    reason: str,
) -> dict[str, Any]:
    """归档已诊断的同输入瞬态失败，并只开放一次新的构建尝试。"""

    if not confirmed or classification != "transient":
        raise ValueError("只能用 --yes 明确归档已诊断的 transient 构建失败")
    diagnosis = reason.strip()
    if not diagnosis:
        raise ValueError("归档瞬态构建失败必须记录具体诊断依据")
    config = load_config(workspace)
    review_root = workspace / str(config["review_directory"])
    latest_path = review_root / "latest-failure.json"
    latest, error = read_json_object(latest_path)
    if error or not latest:
        raise ValueError("当前没有可归档的有效 latest-failure.json")
    current = workspace_build_input_evidence(workspace)
    if (
        latest.get("build_id") != build_id
        or latest.get("build_input_fingerprint") != build_input_fingerprint
        or current.get("fingerprint") != build_input_fingerprint
    ):
        raise ValueError("构建失败记录或当前输入已变化，请重新观察")
    disposition = {
        **latest,
        "disposition": {
            "classification": classification,
            "reason": diagnosis,
            "acknowledged_at": now_utc(),
        },
    }
    history = review_root / "failures"
    history.mkdir(parents=True, exist_ok=True)
    destination = history / f"{build_id}.json"
    if destination.exists():
        raise FileExistsError(f"构建失败归档已存在，拒绝覆盖：{destination}")
    destination.write_text(
        json.dumps(disposition, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    latest_path.unlink()
    return {
        "status": "acknowledged",
        "classification": classification,
        "build_id": build_id,
        "build_input_fingerprint": build_input_fingerprint,
        "archive": destination.relative_to(workspace).as_posix(),
    }


def migrate_existing_workspace(workspace: Path) -> Path:
    """依据已通过 QA 和人工预览的历史项目生成素材绑定工作流。

    只有同时存在候选、八帧走路、38 帧运行清单、passed QA 和人工走路 GIF
    时才迁移，避免把未验收草稿误标为用户确认。
    """

    config = load_config(workspace)
    destination = workspace / "workflow.json"
    if destination.exists():
        raise FileExistsError(f"工作流已存在，拒绝覆盖：{destination}")
    candidate = workspace / str(config["character_file"])
    walk_root = workspace / str(config["walk_directory"])
    walks = [walk_root / f"walk_{index:02d}.png" for index in range(1, 9)]
    runtime_manifest = workspace / str(config["runtime_directory"]) / "manifest.json"
    review_root = workspace / str(config["review_directory"])
    qa_path = review_root / "assets-qa.json"
    walk_preview = review_root / "walk-user-review.gif"
    required = [candidate, runtime_manifest, qa_path, walk_preview, *walks]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("迁移证据不完整：\n" + "\n".join(missing))
    qa = json.loads(qa_path.read_text(encoding="utf-8-sig"))
    manifest = json.loads(runtime_manifest.read_text(encoding="utf-8-sig"))
    frame_count = sum(
        len(paths) for paths in manifest.get("animations", {}).values()
    )
    if qa.get("status") != "passed" or frame_count != 38:
        raise ValueError("历史 runtime 必须有 passed QA 且基础运行帧恰好 38")
    candidate_hash = file_sha256(candidate)
    walk_hash = file_bundle_sha256(walks)
    preview_hash = file_sha256(walk_preview)
    state = {
        "version": 2,
        "pet_id": config["pet_id"],
        "migrated_at": now_utc(),
        "migration_evidence": [
            {
                "role": "runtime_manifest",
                "path": runtime_manifest.relative_to(workspace).as_posix(),
                "sha256": file_sha256(runtime_manifest),
            },
            {
                "role": "assets_qa",
                "path": qa_path.relative_to(workspace).as_posix(),
                "sha256": file_sha256(qa_path),
            },
            {
                "role": "walk_preview",
                "path": walk_preview.relative_to(workspace).as_posix(),
                "sha256": preview_hash,
            },
        ],
        "source": {
            "subject_id": config["pet_id"],
            "references": [],
        },
        "character": {
            "status": "approved",
            "candidate": candidate.relative_to(PROJECT_ROOT).as_posix(),
            "candidate_sha256": candidate_hash,
            "approved_candidate_sha256": candidate_hash,
            "user_confirmed": True,
            "confirmation_method": "legacy_runtime_and_review_evidence",
        },
        "walk": {
            "status": "approved",
            "preview": walk_preview.relative_to(PROJECT_ROOT).as_posix(),
            "preview_sha256": preview_hash,
            "asset_sha256": walk_hash,
            "approved_asset_sha256": walk_hash,
            "asset_binding_kind": "walk_frame_bundle",
            "user_confirmed": True,
            "confirmation_method": "legacy_runtime_and_review_evidence",
        },
    }
    destination.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="创建和检查独立桌宠制作工作区")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="创建下一只桌宠工作区")
    init.add_argument("pet_id")
    init.add_argument("--display-name", default="")
    init.add_argument("--source", type=Path)
    init.add_argument("--selfie-frame", type=Path)
    init.add_argument("--recommended-display-height", type=int, default=220)
    init.add_argument(
        "--mode",
        choices=sorted(PET_MODES),
        default="full-body",
    )
    init.add_argument(
        "--rendering-mode",
        choices=sorted(RENDERING_MODES),
        default="sprite",
        help="默认 sprite；只有用户明确要求 Live2D 时使用 hybrid-live2d",
    )

    action_review = commands.add_parser(
        "action-review",
        help="将 2–3 个单动作候选中逐张质检后的合格帧入库",
    )
    action_review.add_argument("pet_id")
    action_review.add_argument("--action", choices=ACTION_NAMES, required=True)
    action_review.add_argument("--candidate", type=Path, action="append", required=True)
    action_review.add_argument("--selected", type=Path, required=True)
    action_review.add_argument("--limb-integrity-ok", action="store_true")
    action_review.add_argument("--single-action-ok", action="store_true")
    action_review.add_argument("--identity-ok", action="store_true")
    action_review.add_argument("--no-fused-props", action="store_true")
    action_review.add_argument("--clean-silhouette", action="store_true")
    action_review.add_argument("--yes", action="store_true")

    runtime_review = commands.add_parser(
        "approve-runtime",
        help="在用户查看当前 GIF、联系表和真实窗口后绑定最终验收",
    )
    runtime_review.add_argument("pet_id")
    runtime_review.add_argument("--yes", action="store_true")
    runtime_review.add_argument("--build-id", required=True)
    runtime_review.add_argument("--review-visual-sha256", required=True)
    runtime_reject = commands.add_parser(
        "reject-runtime",
        help="在用户查看当前证据后以 CAS 记录否决反馈",
    )
    runtime_reject.add_argument("pet_id")
    runtime_reject.add_argument("--yes", action="store_true")
    runtime_reject.add_argument("--reason", required=True)
    runtime_reject.add_argument("--build-id", required=True)
    runtime_reject.add_argument("--review-visual-sha256", required=True)
    failure_ack = commands.add_parser(
        "ack-build-failure",
        help="归档已诊断的同输入瞬态失败并开放一次重试",
    )
    failure_ack.add_argument("pet_id")
    failure_ack.add_argument("--yes", action="store_true")
    failure_ack.add_argument("--build-id", required=True)
    failure_ack.add_argument("--build-input-fingerprint", required=True)
    failure_ack.add_argument(
        "--classification",
        choices=("transient",),
        required=True,
    )
    failure_ack.add_argument("--reason", required=True)

    for name in (
        "status",
        "agent-status",
        "verify-runtime-review",
        "validate",
        "walk-review",
        "touch-transitions",
        "migrate-existing",
    ):
        command = commands.add_parser(name, help=f"{name} 工作区")
        command.add_argument("pet_id")
    commands.add_parser("list", help="列出本地桌宠工作区")
    return parser.parse_args()


def main() -> int:
    """执行工作区命令。"""

    args = parse_args()
    if args.command == "init":
        workspace = initialize_workspace(
            args.pet_id,
            display_name=args.display_name,
            source=args.source,
            selfie_frame=args.selfie_frame,
            recommended_display_height=args.recommended_display_height,
            pet_mode=args.mode,
            rendering_mode=args.rendering_mode,
        )
        print(f"已创建私有桌宠工作区：{workspace}")
        if not (workspace / "source" / "selfie.png").is_file():
            print("视频来源还需要从原视频抽取一张代表帧作为 source/selfie.png。")
        print("下一步：只生成 candidate/approved.png 的候选版本并让用户确认。")
        return 0
    if args.command == "list":
        PETS_ROOT.mkdir(parents=True, exist_ok=True)
        for path in sorted(PETS_ROOT.iterdir()):
            if path.is_dir() and (path / "pet-config.json").is_file():
                print(path.name)
        return 0

    workspace = workspace_path(args.pet_id)
    if args.command == "status":
        report = workspace_status(workspace)
    elif args.command == "agent-status":
        report = agent_control_snapshot(workspace)
    elif args.command == "verify-runtime-review":
        report = current_runtime_review_status(workspace)
    elif args.command == "validate":
        report = validate_workspace(workspace)
        preflight_failures = [
            *validate_required_touch_transitions(workspace),
            *validate_workflow_bindings(workspace),
        ]
        if preflight_failures:
            report["failures"].extend(preflight_failures)
            report["status"] = "failed"
    elif args.command == "action-review":
        report = record_action_source_review(
            workspace,
            action=args.action,
            candidates=args.candidate,
            selected=args.selected,
            checks={
                "limb_integrity": args.limb_integrity_ok,
                "single_action": args.single_action_ok,
                "identity_consistency": args.identity_ok,
                "no_fused_props": args.no_fused_props,
                "clean_silhouette": args.clean_silhouette,
            },
            confirmed=args.yes,
        )
    elif args.command == "approve-runtime":
        report = approve_runtime_review(
            workspace,
            confirmed=args.yes,
            expected_build_id=args.build_id,
            expected_review_visual_fingerprint=args.review_visual_sha256,
        )
    elif args.command == "reject-runtime":
        report = reject_runtime_review(
            workspace,
            confirmed=args.yes,
            reason=args.reason,
            expected_build_id=args.build_id,
            expected_review_visual_fingerprint=args.review_visual_sha256,
        )
    elif args.command == "ack-build-failure":
        report = acknowledge_build_failure(
            workspace,
            confirmed=args.yes,
            build_id=args.build_id,
            build_input_fingerprint=args.build_input_fingerprint,
            classification=args.classification,
            reason=args.reason,
        )
    elif args.command == "migrate-existing":
        path = migrate_existing_workspace(workspace)
        print(f"已迁移历史工作区审批：{path}")
        return 0
    elif args.command == "touch-transitions":
        report = create_touch_transition_sources(workspace)
    else:
        preview = create_workspace_walk_review(workspace)
        print(f"走路确认预览：{preview}")
        print(
            "请重新运行 agent-status、展示当前证据；只有用户明确决定后，"
            "才能原样执行其 decision.approval_command 或 rejection_command。"
        )
        return 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status", "passed") != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
