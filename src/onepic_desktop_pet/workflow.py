"""
本模块管理“一图桌宠”每只宠物独立且与素材哈希绑定的人工确认状态。

职责范围：
- 将审批状态保存到 `user_assets/pets/<pet-id>/workflow.json`；
- 全局只读取 `active-pet.json`（兼容旧版指针文件）以定位活动宠物；
- 校验角色、走路或替代互动的确认结果仍绑定当前素材哈希；替代互动同时绑定
  提交时的标准角色，避免换角色后沿用旧预览；
- 对嵌套审批记录类型异常采取 fail-closed，不让损坏结构冒充已批准；
- 为旧版全局 workflow 提供显式、可审计、保留备份的迁移函数，并把走路审批绑定到真实八帧素材束；
- 使用 UTF-8 和原子替换保存 JSON。

本模块不生成或复制图片，不访问网络。调用方可用 `ONEPIC_USER_ROOT`
把全部私有状态重定向到测试沙箱，避免测试触碰真实用户素材。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .resources import resource_root


class WorkflowError(RuntimeError):
    """表示制作流程缺少前置确认或状态文件损坏。"""


def now_utc() -> str:
    """返回适合写入 JSON 的 UTC 时间。"""

    return datetime.now(UTC).isoformat(timespec="seconds")


def user_assets_root(project_root: Path | None = None) -> Path:
    """返回私有素材根目录，并支持测试或便携构建覆盖。"""

    override = os.environ.get("ONEPIC_USER_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (project_root or resource_root()) / "user_assets"


def active_pet_path(project_root: Path | None = None) -> Path:
    """返回只保存活动宠物信息的全局指针文件。"""

    return user_assets_root(project_root) / "active-pet.json"


def load_active_pet_id(project_root: Path | None = None) -> str | None:
    """读取活动宠物 id；没有指针时返回 None。"""

    pointer = active_pet_path(project_root)
    if not pointer.is_file():
        return None
    state = load_workflow(pointer)
    value = state.get("pet_id") or state.get("active_pet_id")
    return str(value).strip() if value else None


def workflow_path(
    project_root: Path | None = None,
    pet_id: str | None = None,
) -> Path:
    """返回指定或当前活动宠物的独立流程状态文件。"""

    selected = pet_id or load_active_pet_id(project_root)
    if not selected:
        # 只为读取旧项目保留该回退；新写入必须传 pet-id 或存在活动指针。
        legacy = user_assets_root(project_root) / "workflow.json"
        if legacy.is_file():
            return legacy
        raise WorkflowError("没有活动桌宠指针；请传入 pet-id 或先创建工作区。")
    return user_assets_root(project_root) / "pets" / selected / "workflow.json"


def load_workflow(path: Path | None = None) -> dict[str, Any]:
    """读取流程状态；文件不存在时返回空字典，格式错误时拒绝继续。"""

    target = path or workflow_path()
    if not target.is_file():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"无法读取制作流程状态：{target}：{exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"制作流程状态必须是 JSON 对象：{target}")
    return value


def save_workflow(state: dict[str, Any], path: Path | None = None) -> Path:
    """以 UTF-8 原子保存私有流程状态并返回目标路径。"""

    target = path or workflow_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def character_is_approved(state: dict[str, Any]) -> bool:
    """校验标准角色确认与候选素材哈希仍然一致。"""

    character = state.get("character", {})
    if not isinstance(character, dict):
        return False
    candidate_hash = character.get("candidate_sha256")
    return bool(
        character.get("status") == "approved"
        and character.get("user_confirmed") is True
        and candidate_hash
        and character.get("approved_candidate_sha256") == candidate_hash
    )


def walk_is_approved(state: dict[str, Any]) -> bool:
    """校验走路确认与提交时的帧束或预览哈希仍然一致。"""

    walk = state.get("walk", {})
    if not isinstance(walk, dict):
        return False
    asset_hash = walk.get("asset_sha256") or walk.get("preview_sha256")
    return bool(
        walk.get("status") == "approved"
        and walk.get("user_confirmed") is True
        and asset_hash
        and walk.get("approved_asset_sha256") == asset_hash
    )


def interaction_is_approved(state: dict[str, Any]) -> bool:
    """校验替代互动确认与预览、标准角色哈希仍然一致。"""

    interaction = state.get("interaction", {})
    character = state.get("character", {})
    if not isinstance(interaction, dict) or not isinstance(character, dict):
        return False
    preview_hash = interaction.get("preview_sha256")
    character_hash = character.get("candidate_sha256")
    return bool(
        interaction.get("status") == "approved"
        and interaction.get("user_confirmed") is True
        and interaction.get("mode")
        and interaction.get("approved_mode") == interaction.get("mode")
        and preview_hash
        and interaction.get("approved_asset_sha256") == preview_hash
        and character_hash
        and interaction.get("character_sha256") == character_hash
        and interaction.get("approved_character_sha256") == character_hash
    )


def custom_pet_is_approved(path: Path | None = None) -> bool:
    """角色确认后，走路或替代互动任一绑定确认即可加载私有桌宠。"""

    state = load_workflow(path)
    return character_is_approved(state) and (
        walk_is_approved(state) or interaction_is_approved(state)
    )


def require_character_approved(path: Path | None = None) -> dict[str, Any]:
    """要求首张标准角色已确认且哈希匹配，否则抛出可读错误。"""

    state = load_workflow(path)
    if not character_is_approved(state):
        raise WorkflowError(
            "尚未由用户确认首张标准角色，或候选素材哈希已变化；"
            "禁止继续批量生成动作。"
        )
    return state


def require_custom_pet_approved(path: Path | None = None) -> dict[str, Any]:
    """要求角色及走路/替代互动均通过素材绑定确认。"""

    state = require_character_approved(path)
    if not (walk_is_approved(state) or interaction_is_approved(state)):
        raise WorkflowError(
            "尚未确认走路/替代互动，或确认后素材哈希已变化；"
            "禁止使用或打包私有桌宠。"
        )
    return state


def bind_legacy_approval_hashes(
    state: dict[str, Any],
    *,
    preview_sha256: str | None = None,
    walk_asset_sha256: str | None = None,
    interaction_sha256: str | None = None,
) -> dict[str, Any]:
    """为旧版已确认状态补齐哈希绑定，不凭空创建用户确认。"""

    migrated = json.loads(json.dumps(state))
    walk = migrated.get("walk", {})
    if walk.get("status") == "approved":
        if preview_sha256 and not walk.get("preview_sha256"):
            walk["preview_sha256"] = preview_sha256
        bound = walk_asset_sha256 or walk.get("asset_sha256")
        if not bound:
            bound = walk.get("preview_sha256")
            if bound:
                walk["asset_binding_kind"] = "legacy_preview"
        elif walk_asset_sha256:
            walk["asset_binding_kind"] = "walk_frame_bundle"
        if bound:
            walk["asset_sha256"] = bound
            walk["approved_asset_sha256"] = bound
            walk["user_confirmed"] = True
        else:
            walk["status"] = "legacy_unbound"
            walk["user_confirmed"] = False
        migrated["walk"] = walk

    interaction = migrated.get("interaction", {})
    if interaction.get("status") == "approved":
        bound = interaction.get("preview_sha256") or interaction_sha256
        character = migrated.get("character", {})
        character_hash = (
            character.get("candidate_sha256")
            if isinstance(character, dict)
            else None
        )
        mode = interaction.get("mode")
        if bound and character_hash and mode:
            interaction["preview_sha256"] = bound
            interaction["approved_asset_sha256"] = bound
            interaction["character_sha256"] = character_hash
            interaction["approved_character_sha256"] = character_hash
            interaction["approved_mode"] = mode
        else:
            interaction["status"] = "legacy_unbound"
            interaction["user_confirmed"] = False
        migrated["interaction"] = interaction
    return migrated


def migrate_legacy_workflow(
    user_root: Path,
    pet_id: str,
    *,
    preview_sha256: str | None = None,
    walk_asset_sha256: str | None = None,
    interaction_sha256: str | None = None,
) -> tuple[Path, Path]:
    """把旧全局 workflow 迁移到指定宠物并把旧文件改为纯指针。

    返回 `(per_pet_workflow, backup_file)`；现有每宠物状态不会被覆盖。
    """

    legacy = user_root / "workflow.json"
    if not legacy.is_file():
        raise FileNotFoundError(f"没有可迁移的旧流程状态：{legacy}")
    destination = user_root / "pets" / pet_id / "workflow.json"
    if destination.exists():
        raise FileExistsError(f"目标工作流已存在，拒绝覆盖：{destination}")
    original = load_workflow(legacy)
    migrated = bind_legacy_approval_hashes(
        original,
        preview_sha256=preview_sha256,
        walk_asset_sha256=walk_asset_sha256,
        interaction_sha256=interaction_sha256,
    )
    migrated["version"] = 2
    migrated["pet_id"] = pet_id
    migrated["migrated_from"] = "user_assets/workflow.json"
    migrated["migrated_at"] = now_utc()
    save_workflow(migrated, destination)

    backup_root = user_root / "backups" / "workflow-migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"workflow-{pet_id}-{stamp}.json"
    shutil.copy2(legacy, backup)
    active_pointer = user_root / "active-pet.json"
    pointer_pet_id = pet_id
    if active_pointer.is_file():
        active_state = load_workflow(active_pointer)
        existing_active = active_state.get("pet_id") or active_state.get(
            "active_pet_id"
        )
        if existing_active:
            pointer_pet_id = str(existing_active)
    save_workflow(
        {
            "version": 2,
            "active_pet_id": pointer_pet_id,
            "workflow": f"pets/{pointer_pet_id}/workflow.json",
            "migrated_at": now_utc(),
        },
        legacy,
    )
    return destination, backup
