"""
本模块把已通过桌面版审批门禁的宠物运行包脱敏装配为 Android assets。

职责范围：
- 读取 `user_assets/pets/<pet-id>/workflow.json` 与 `runtime/manifest.json`；
- 拒绝未批准角色、未批准走路、未批准扩展和未经确认的互动台词；
- 拒绝未经确认或语义哈希变化的养成玩法配置；
- 只复制运行清单实际引用的透明 PNG，验证路径、图像、哈希和素材数量；
- 将混合桌宠降级为 Android 精灵路线，并排除 Android 不支持的 Live2D 子树；
- 删除 `selfie_photo` 映射，禁止原图、自拍视频、候选、草稿、备份和绝对路径进入 APK；
- 在 Android 私有生成目录输出脱敏清单与逐文件 SHA-256 报告。

Agent 快速定位：
- 门禁校验位于 `_validate_workflow()`；
- 运行资源收集位于 `_collect_asset_references()`；
- 事务装配入口位于 `prepare_android_pet()`；
- 命令行入口位于 `main()`。

本工具不修改宠物工作区、不覆盖原始素材，也不执行 Android SDK 安装或 APK 签名。
生成目录 `android/app/src/main/assets/pet/` 被 Git 忽略，可由同一已批准运行包重建。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image

from onepic_desktop_pet.care import care_config_sha256, normalize_care_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANDROID_ASSET_ROOT = (
    PROJECT_ROOT / "android" / "app" / "src" / "main" / "assets" / "pet"
)
EXCLUDED_MANIFEST_KEYS = {
    "selfie_photo",
    # Android 客户端只实现精灵渲染；Live2D 模型与纹理由 Windows 混合后端使用，
    # 不能因为 model3.json 的间接引用而进入 APK。
    "hybrid_runtime",
    # 这是工作区中的可选生成源，不是 runtime 相对路径；Android 只使用已装配的
    # touch_feedback.transition_frames。
    "optional_transition_sources",
}
ANDROID_EXCLUDED_RUNTIME_ROOTS = {
    "live2d",
}
FORBIDDEN_SOURCE_PARTS = {
    "authoring",
    "backup",
    "backups",
    "candidate",
    "candidates",
    "draft",
    "drafts",
    "original",
    "review",
    "source",
}


class AndroidPackageError(ValueError):
    """表示审批、路径、素材或隐私契约不满足 Android 装配要求。"""


@dataclass(frozen=True)
class AssetRecord:
    """记录一个进入 Android 包的相对文件、大小和哈希。"""

    path: str
    size_bytes: int
    sha256: str
    pixel_size: tuple[int, int]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AndroidPackageError(f"缺少必要文件：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AndroidPackageError(f"无法读取 JSON {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise AndroidPackageError(f"JSON 顶层必须是对象：{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_workflow(
    workflow: dict[str, Any],
    manifest: dict[str, Any],
    pet_id: str,
) -> dict[str, Any]:
    character = workflow.get("character")
    walk = workflow.get("walk")
    if not isinstance(character, dict) or character.get("status") != "approved":
        raise AndroidPackageError("标准角色尚未通过审批，禁止装配 Android 包")
    if character.get("user_confirmed") is not True:
        raise AndroidPackageError("标准角色缺少用户直接确认")
    if not isinstance(walk, dict) or walk.get("status") != "approved":
        raise AndroidPackageError("走路尚未通过审批，禁止装配 Android 包")
    if walk.get("user_confirmed") is not True:
        raise AndroidPackageError("走路缺少用户直接确认")
    if manifest.get("pet_id") != pet_id:
        raise AndroidPackageError(
            f"运行清单 pet_id 不匹配：{manifest.get('pet_id')!r} != {pet_id!r}"
        )

    directional_count = int(manifest.get("directional_frame_count", 0))
    if directional_count != 0:
        raise AndroidPackageError(
            "当前请求未启用八向视线，但运行清单包含 directional 帧"
        )

    configured_extensions = manifest.get("extension_animations", {})
    if configured_extensions and not isinstance(configured_extensions, dict):
        raise AndroidPackageError("extension_animations 必须是对象")
    approved_extensions: set[str] = set()
    extension_approvals = workflow.get("extension_approvals", {})
    if isinstance(extension_approvals, dict):
        for approval in extension_approvals.values():
            if not isinstance(approval, dict):
                continue
            if (
                approval.get("status") != "approved"
                or approval.get("user_confirmed") is not True
                or approval.get("activation_allowed") is not True
            ):
                continue
            items = approval.get("items", {})
            if isinstance(items, dict):
                approved_extensions.update(str(name) for name in items)
    missing_extensions = sorted(set(configured_extensions) - approved_extensions)
    if missing_extensions:
        raise AndroidPackageError(
            "运行清单包含未批准扩展：" + ", ".join(missing_extensions)
        )

    dialogue = manifest.get("dialogue_system", {})
    if isinstance(dialogue, dict) and dialogue.get("enabled") is True:
        feature = workflow.get("feature_status", {}).get("local_dialogue_v1", {})
        if (
            not isinstance(feature, dict)
            or feature.get("status") != "activated"
            or feature.get("user_confirmed") is not True
        ):
            raise AndroidPackageError("运行清单启用了未确认的本地互动台词")

    care = normalize_care_config(manifest.get("care_simulation", {}))
    if care.get("enabled") is True:
        feature = workflow.get("feature_status", {}).get("vpet_care_v1", {})
        actual_hash = care_config_sha256(care)
        if (
            not isinstance(feature, dict)
            or feature.get("status") != "activated"
            or feature.get("user_confirmed") is not True
            or feature.get("activation_allowed") is not True
            or feature.get("approved_config_sha256") != actual_hash
            or feature.get("config_sha256") != actual_hash
        ):
            raise AndroidPackageError("运行清单启用了未确认的 VPet 养成配置")

    return {
        "character_status": character["status"],
        "walk_status": walk["status"],
        "approved_extensions": sorted(configured_extensions),
        "dialogue_enabled": bool(
            isinstance(dialogue, dict) and dialogue.get("enabled") is True
        ),
        "care_enabled": bool(care.get("enabled") is True),
        "directional_frame_count": directional_count,
    }


def _iter_png_strings(
    value: Any,
    *,
    key: str | None = None,
) -> Iterable[str]:
    if key in EXCLUDED_MANIFEST_KEYS:
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _iter_png_strings(child_value, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _iter_png_strings(child, key=key)
    elif isinstance(value, str) and value.lower().endswith(".png"):
        yield value


def _safe_runtime_asset(runtime_root: Path, raw_path: str) -> tuple[str, Path]:
    normalized = raw_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or ":" in normalized
        or ".." in pure.parts
        or not pure.parts
    ):
        raise AndroidPackageError(f"运行清单包含非法资源路径：{raw_path}")
    if any(part.lower() in FORBIDDEN_SOURCE_PARTS for part in pure.parts):
        raise AndroidPackageError(f"运行清单引用了禁止打包的制作素材：{raw_path}")
    target = (runtime_root / Path(*pure.parts)).resolve()
    runtime_resolved = runtime_root.resolve()
    if not target.is_relative_to(runtime_resolved):
        raise AndroidPackageError(f"资源路径逃逸运行目录：{raw_path}")
    if not target.is_file() or target.is_symlink():
        raise AndroidPackageError(f"运行资源不存在或为符号链接：{raw_path}")
    return pure.as_posix(), target


def _validate_image(relative_path: str, path: Path) -> AssetRecord:
    try:
        with Image.open(path) as image:
            image.load()
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            if not has_alpha:
                raise AndroidPackageError(
                    f"Android 桌宠资源必须支持透明通道：{relative_path}"
                )
            width, height = image.size
    except AndroidPackageError:
        raise
    except (OSError, ValueError) as exc:
        raise AndroidPackageError(f"无法解码 PNG {relative_path}：{exc}") from exc
    if width <= 0 or height <= 0:
        raise AndroidPackageError(f"PNG 尺寸非法：{relative_path}")
    return AssetRecord(
        path=relative_path,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        pixel_size=(width, height),
    )


def _collect_asset_references(
    runtime_root: Path,
    manifest: dict[str, Any],
) -> tuple[list[AssetRecord], dict[str, Path]]:
    paths: dict[str, Path] = {}
    for raw_path in _iter_png_strings(manifest):
        relative, source = _safe_runtime_asset(runtime_root, raw_path)
        paths[relative] = source
    if not paths:
        raise AndroidPackageError("运行清单没有引用任何 PNG")

    runtime_pngs = {
        path.relative_to(runtime_root).as_posix()
        for path in runtime_root.rglob("*.png")
        if (
            path.is_file()
            and not path.is_symlink()
            and path.relative_to(runtime_root).parts[0].lower()
            not in ANDROID_EXCLUDED_RUNTIME_ROOTS
        )
    }
    unreferenced = sorted(runtime_pngs - set(paths))
    if unreferenced:
        raise AndroidPackageError(
            "运行目录含有清单未引用的 PNG，拒绝模糊打包："
            + ", ".join(unreferenced[:8])
        )
    records = [_validate_image(relative, paths[relative]) for relative in sorted(paths)]
    return records, paths


def _sanitized_manifest(
    manifest: dict[str, Any],
    *,
    source_manifest_sha256: str,
    gates: dict[str, Any],
) -> dict[str, Any]:
    clean = copy.deepcopy(manifest)
    source_rendering_mode = str(clean.get("rendering_mode", "sprite"))
    clean.pop("selfie_photo", None)
    clean.pop("hybrid_runtime", None)
    # 休息提醒当前只由 Windows 的托盘、Win32 idle/fullscreen 探针和 Qt
    # 操作卡实现；Android 包必须移除该段，避免把未实现能力带进运行清单。
    clean.pop("break_reminders", None)
    # Android 当前没有 Live2D 后端；保留完整精灵动作并明确声明降级，避免清单留下
    # 指向未打包 model3/moc/texture 文件的悬空引用。
    clean["rendering_mode"] = "sprite"
    touch_feedback = clean.get("touch_feedback")
    if isinstance(touch_feedback, dict):
        touch_feedback.pop("optional_transition_sources", None)
    clean["android_package"] = {
        "schema_version": 1,
        "privacy_mode": "share-no-selfie",
        "source_manifest_sha256": source_manifest_sha256,
        "character_gate": gates["character_status"],
        "walk_gate": gates["walk_status"],
        "directional_mouse_follow_enabled": False,
        "source_rendering_mode": source_rendering_mode,
        "live2d_runtime_excluded": True,
        "break_reminders_supported": False,
    }
    serialized = json.dumps(clean, ensure_ascii=False)
    if "\\" in serialized and ":\\" in serialized:
        raise AndroidPackageError("脱敏运行清单仍包含 Windows 绝对路径")
    return clean


def _replace_generated_directory(staging: Path, target: Path) -> None:
    parent = target.parent.resolve()
    if target.resolve().parent != parent or staging.resolve().parent != parent:
        raise AndroidPackageError("Android 生成目录不在预期父目录内")
    previous = target.with_name("pet.previous")
    if previous.exists():
        shutil.rmtree(previous)
    if target.exists():
        target.replace(previous)
    try:
        staging.replace(target)
    except OSError:
        if previous.exists() and not target.exists():
            previous.replace(target)
        raise


def prepare_android_pet(
    pet_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    target_root: Path | None = None,
) -> dict[str, Any]:
    """验证并装配一只宠物的 Android 私有 assets，返回脱敏报告。"""

    if not pet_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in pet_id):
        raise AndroidPackageError("pet-id 只能包含小写字母、数字、短横线和下划线")
    root = project_root.resolve()
    workspace = root / "user_assets" / "pets" / pet_id
    runtime_root = workspace / "runtime"
    workflow_path = workspace / "workflow.json"
    manifest_path = runtime_root / "manifest.json"
    workflow = _load_json(workflow_path)
    manifest = _load_json(manifest_path)
    gates = _validate_workflow(workflow, manifest, pet_id)
    records, sources = _collect_asset_references(runtime_root, manifest)
    source_manifest_sha256 = _sha256(manifest_path)
    clean_manifest = _sanitized_manifest(
        manifest,
        source_manifest_sha256=source_manifest_sha256,
        gates=gates,
    )

    target = (
        target_root.resolve()
        if target_root is not None
        else root / "android" / "app" / "src" / "main" / "assets" / "pet"
    )
    expected_parent = (root / "android" / "app" / "src" / "main" / "assets").resolve()
    if target.parent.resolve() != expected_parent:
        raise AndroidPackageError(
            f"Android assets 目标必须位于 {expected_parent}：{target}"
        )
    staging = target.with_name("pet.staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        for record in records:
            destination = staging / Path(*PurePosixPath(record.path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sources[record.path], destination)
            if _sha256(destination) != record.sha256:
                raise AndroidPackageError(f"复制后哈希不一致：{record.path}")

        manifest_output = staging / "manifest.json"
        manifest_output.write_text(
            json.dumps(clean_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report = {
            "schema_version": 1,
            "pet_id": pet_id,
            "display_name": manifest.get("display_name", pet_id),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "privacy_mode": "share-no-selfie",
            "source_runtime_manifest_sha256": source_manifest_sha256,
            "bundled_manifest_sha256": _sha256(manifest_output),
            "gates": gates,
            "asset_count": len(records),
            "total_asset_bytes": sum(record.size_bytes for record in records),
            "assets": [
                {
                    "path": record.path,
                    "size_bytes": record.size_bytes,
                    "sha256": record.sha256,
                    "pixel_size": list(record.pixel_size),
                }
                for record in records
            ],
            "privacy_checks": {
                "source_selfie_excluded": True,
                "workflow_excluded": True,
                "candidates_excluded": True,
                "backups_excluded": True,
                "absolute_paths_excluded": True,
                "live2d_runtime_excluded": True,
            },
        }
        report_path = staging / "package-report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _replace_generated_directory(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把已批准 OnePic 宠物运行包脱敏装配为 Android assets"
    )
    parser.add_argument("--pet-id", required=True, help="宠物工作区 ID")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = prepare_android_pet(args.pet_id)
    except AndroidPackageError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "passed",
                "pet_id": report["pet_id"],
                "asset_count": report["asset_count"],
                "total_asset_bytes": report["total_asset_bytes"],
                "privacy_checks": report["privacy_checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
