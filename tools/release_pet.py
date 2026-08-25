"""
本模块准备最小化打包素材，并验证完整目录 ZIP 的隐私、运行默认值、CRC 与 SHA-256。

它不生成图片、不上传网络；private-local、share-with-approved-selfie 和
share-no-selfie 三种模式都只复制运行时、最小审批状态及按策略允许的自拍。
原始参考、视频、候选、草稿、备份和客户工作区永不进入发布暂存区。
智能陪伴的 SQLite 数据库与 DPAPI 密文也属于用户本地数据，扫描时
会按扩展名与文件头特征硬性拒绝，并检查任意盘符或 UNC 绝对路径。
活动区域属于用户本机偏好；分享构建必须默认关闭且不能预置制作者屏幕坐标。
发布报告同时记录源码、成品、混合窗口和全路线双击聊天烟测证据；最小 workflow
保留互动预览与标准角色的双哈希绑定，避免分享包把已批准静态角色误判为未批准。
prepare v2 以随机批次号绑定 QA、指针、workflow 与自拍哈希；finalize 还会重查当前
授权状态，并要求 schema 化烟测报告同时匹配最终 EXE 和完整 one-dir 成品树指纹。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shutil
import zlib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onepic_desktop_pet.workflow import (
    WorkflowError,
    character_is_approved,
    interaction_is_approved,
    load_workflow,
    require_custom_pet_approved,
    walk_is_approved,
)

try:
    from .pet_workspace import (
        application_build_evidence,
        file_sha256,
        load_config,
        require_current_runtime_review,
        runtime_asset_bundle_sha256,
        validate_workflow_bindings,
        workspace_path,
    )
except ImportError:
    from pet_workspace import (
        application_build_evidence,
        file_sha256,
        load_config,
        require_current_runtime_review,
        runtime_asset_bundle_sha256,
        validate_workflow_bindings,
        workspace_path,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVACY_MODES = {
    "private-local",
    "share-with-approved-selfie",
    "share-no-selfie",
}
FORBIDDEN_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".psd",
    ".kra",
    ".sqlite",
    ".sqlite3",
    ".dpapi",
}
FORBIDDEN_PARTS = {
    "source",
    "candidate",
    "drafts",
    "backups",
    "failed-builds",
    "staging",
}
TEXT_SUFFIXES = {".json", ".txt", ".md", ".ini", ".yaml", ".yml", ".cfg"}
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]{1,2}(?![\\/])"
    r"|\\\\[^\\\s]+\\[^\\\s]+"
    r")",
    re.IGNORECASE,
)
PRIVATE_FILE_SIGNATURES = {
    b"SQLite format 3\x00": "SQLite 数据库文件头",
    b"ONEPIC-DPAPI": "OnePic DPAPI 密文文件头",
}
VENDOR_PRIVATE_NAME_EXEMPT_ROOTS = {"pyside6"}
PREPARATION_SCHEMA_VERSION = 2
EVIDENCE_SCHEMA_VERSION = 1


def validate_release_settings(path: Path | None = None) -> dict[str, Any]:
    """验证发布默认设置未启用活动区域或携带本机屏幕坐标。"""

    settings_path = path or PROJECT_ROOT / "config" / "settings.json"
    if not settings_path.is_file():
        raise FileNotFoundError(f"发布默认设置不存在：{settings_path}")
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"发布默认设置无法读取：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("发布默认设置必须是 JSON 对象")
    if data.get("movement_region_enabled") is not False:
        raise RuntimeError("分享包的活动区域默认开关必须明确为 false")
    if data.get("movement_region") is not None:
        raise RuntimeError("分享包不得预置制作者机器的活动区域坐标")
    return {
        "status": "passed",
        "movement_region_enabled": False,
        "movement_region": None,
    }


def _minimal_workflow(state: dict[str, Any]) -> dict[str, Any]:
    """返回运行门禁所需的最小、无来源路径审批状态。"""

    if not character_is_approved(state):
        raise RuntimeError("角色确认门禁未通过")
    character = state["character"]
    packaged: dict[str, Any] = {
        "version": 2,
        "pet_id": state.get("pet_id"),
        "character": {
            "status": "approved",
            "user_confirmed": True,
            "candidate_sha256": character["candidate_sha256"],
            "approved_candidate_sha256": character[
                "approved_candidate_sha256"
            ],
        },
    }
    if walk_is_approved(state):
        walk = state["walk"]
        asset_hash = walk.get("asset_sha256") or walk["preview_sha256"]
        packaged["walk"] = {
            "status": "approved",
            "user_confirmed": True,
            "asset_sha256": asset_hash,
            "approved_asset_sha256": asset_hash,
        }
    elif interaction_is_approved(state):
        interaction = state["interaction"]
        packaged["interaction"] = {
            "status": "approved",
            "user_confirmed": True,
            "mode": interaction["mode"],
            "approved_mode": interaction["approved_mode"],
            "preview_sha256": interaction["preview_sha256"],
            "approved_asset_sha256": interaction["approved_asset_sha256"],
            "character_sha256": interaction["character_sha256"],
            "approved_character_sha256": interaction[
                "approved_character_sha256"
            ],
        }
    else:
        raise RuntimeError("走路或替代互动门禁未通过")
    return packaged


def privacy_scan(root: Path) -> dict[str, Any]:
    """扫描私密目录、危险扩展名/文件头和任意本机绝对路径。"""

    failures: list[str] = []
    files: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        files.append(relative)
        relative_parts = path.relative_to(root).parts
        lowered_parts = {part.lower() for part in relative_parts}
        is_bundled_vendor_runtime = (
            len(relative_parts) >= 2
            and relative_parts[0].lower() == "_internal"
            and relative_parts[1].lower()
            in VENDOR_PRIVATE_NAME_EXEMPT_ROOTS
        )
        if not is_bundled_vendor_runtime and lowered_parts & FORBIDDEN_PARTS:
            failures.append(f"{relative}: 包含禁止的私密目录名")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"{relative}: 禁止的私密/工程文件扩展名")
        try:
            with path.open("rb") as stream:
                header = stream.read(32)
        except OSError as exc:
            failures.append(f"{relative}: 无法读取文件头（{exc}）")
        else:
            for signature, label in PRIVATE_FILE_SIGNATURES.items():
                if header.startswith(signature):
                    failures.append(f"{relative}: 检测到{label}")
        if path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                failures.append(f"{relative}: 文本文件不是稳定 UTF-8")
            else:
                if ABSOLUTE_PATH_PATTERN.search(text):
                    failures.append(f"{relative}: 包含本机绝对路径")
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "file_count": len(files),
        "files": files,
    }


def distribution_tree_sha256(root: Path) -> str:
    """按相对路径、大小与内容计算完整 one-dir 成品树指纹。"""

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def prepare_package(
    pet_id: str,
    mode: str,
    output: Path,
) -> dict[str, Any]:
    """建立供 PyInstaller 使用的最小 user_assets 暂存树。"""

    if mode not in PRIVACY_MODES:
        raise ValueError(f"未知隐私模式：{mode}")
    release_settings = validate_release_settings()
    if output.exists():
        raise FileExistsError(f"发布暂存目录已存在，拒绝覆盖：{output}")
    workspace = workspace_path(pet_id)
    config = load_config(workspace)
    runtime_attestation = require_current_runtime_review(workspace)
    workflow_path = workspace / "workflow.json"
    state = require_custom_pet_approved(workflow_path)
    binding_failures = validate_workflow_bindings(workspace)
    if binding_failures:
        raise RuntimeError(
            "当前授权来源或审批绑定已失效，禁止准备发布：\n"
            + "\n".join(f"- {item}" for item in binding_failures)
        )
    runtime = workspace / str(config["runtime_directory"])
    qa_path = workspace / str(config["review_directory"]) / "assets-qa.json"
    if not runtime.is_dir() or not qa_path.is_file():
        raise FileNotFoundError("缺少已装配 runtime 或 QA 报告")
    qa = json.loads(qa_path.read_text(encoding="utf-8-sig"))
    if qa.get("status") != "passed":
        raise RuntimeError("素材 QA 未通过，禁止发布")

    preparation_id = secrets.token_hex(16)
    prepared_at = datetime.now(UTC).isoformat(timespec="seconds")
    user_root = output / "user_assets"
    packaged_workflow = user_root / "pets" / pet_id / "workflow.json"
    packaged_workflow.parent.mkdir(parents=True)
    packaged_workflow.write_text(
        json.dumps(_minimal_workflow(state), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (user_root / "active-pet.json").write_text(
        json.dumps(
            {
                "version": 2,
                "pet_id": pet_id,
                "display_name": config["display_name"],
                "workflow": f"pets/{pet_id}/workflow.json",
                "package_mode": mode,
                "preparation_id": preparation_id,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copytree(runtime, user_root / "pet")

    selfie = workspace / str(config["selfie_file"])
    include_selfie = mode == "private-local" and selfie.is_file()
    if mode == "share-with-approved-selfie":
        approved_hash = (
            config.get("sharing", {}).get("selfie_approved_sha256")
            if isinstance(config.get("sharing"), dict)
            else None
        )
        actual_hash = file_sha256(selfie) if selfie.is_file() else None
        if not approved_hash or approved_hash != actual_hash:
            raise RuntimeError(
                "share-with-approved-selfie 需要 pet-config.json 中"
                " sharing.selfie_approved_sha256 明确匹配当前自拍"
            )
        include_selfie = True
    packaged_selfie_sha256: str | None = None
    if include_selfie:
        if not selfie.is_file():
            raise FileNotFoundError(f"缺少允许打包的自拍：{selfie}")
        shutil.copy2(selfie, user_root / "selfie.png")
        packaged_selfie_sha256 = file_sha256(user_root / "selfie.png")

    scan = privacy_scan(output)
    if scan["status"] != "passed":
        raise RuntimeError("发布暂存隐私扫描失败：\n" + "\n".join(scan["failures"]))
    report = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "preparation_id": preparation_id,
        "prepared_at": prepared_at,
        "pet_id": pet_id,
        "mode": mode,
        "selfie_included": include_selfie,
        "selfie_sha256": packaged_selfie_sha256,
        "qa_sha256": file_sha256(qa_path),
        "packaged_workflow_sha256": file_sha256(packaged_workflow),
        "active_pointer_sha256": file_sha256(user_root / "active-pet.json"),
        "base_frame_count": qa.get("base_frame_count", 38),
        "optional_touch_frame_count": qa.get("optional_touch_frame_count", 0),
        "extension_frame_count": qa.get("extension_frame_count", 0),
        "directional_frame_count": qa.get("directional_frame_count", 0),
        "total_frame_count": qa.get("total_frame_count", qa.get("frame_count")),
        "total_asset_frame_count": qa.get(
            "total_asset_frame_count",
            qa.get("frame_count", qa.get("total_frame_count")),
        ),
        "runtime_review": {
            "status": "passed",
            "build_id": runtime_attestation["build_id"],
            "build_input_fingerprint": runtime_attestation[
                "build_input_fingerprint"
            ],
            "application_fingerprint": runtime_attestation[
                "application_fingerprint"
            ],
            "runtime_asset_fingerprint": runtime_attestation[
                "runtime_asset_fingerprint"
            ],
            "review_visual_fingerprint": runtime_attestation[
                "review_visual_fingerprint"
            ],
            "runtime_manifest_sha256": runtime_attestation[
                "runtime_manifest_sha256"
            ],
        },
        "release_settings": release_settings,
        "privacy_scan": scan,
    }
    (output / "package-preparation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def verify_packaged_runtime_attestation(
    distribution: Path,
    pet_id: str,
    mode: str,
    preparation: dict[str, Any],
) -> dict[str, Any]:
    """证明成品内客户 runtime 与准备期已验收素材、清单和应用源码一致。"""

    expected = preparation.get("runtime_review", {})
    if not isinstance(expected, dict):
        raise RuntimeError("发布准备报告缺少 runtime_review 取证")
    runtime_root = distribution / "_internal" / "user_assets" / "pet"
    manifest_path = runtime_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("成品缺少包内 user_assets/pet/manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"成品 runtime manifest 无法解析：{exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("成品 runtime manifest 不是 JSON 对象")
    user_root = runtime_root.parent
    pointer_path = user_root / "active-pet.json"
    workflow_path = user_root / "pets" / pet_id / "workflow.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
        packaged_workflow = json.loads(
            workflow_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"成品活动指针或 workflow 无法解析：{exc}") from exc
    if not isinstance(pointer, dict) or not isinstance(packaged_workflow, dict):
        raise RuntimeError("成品活动指针或 workflow 不是 JSON 对象")
    if (
        pointer.get("pet_id") != pet_id
        or pointer.get("workflow") != f"pets/{pet_id}/workflow.json"
        or pointer.get("package_mode") != mode
        or pointer.get("preparation_id") != preparation.get("preparation_id")
        or packaged_workflow.get("pet_id") != pet_id
    ):
        raise RuntimeError("成品活动指针、workflow 与待发布 pet-id/隐私模式不一致")
    packaged_file_comparisons = {
        "active_pointer_sha256": (
            preparation.get("active_pointer_sha256"),
            file_sha256(pointer_path),
        ),
        "packaged_workflow_sha256": (
            preparation.get("packaged_workflow_sha256"),
            file_sha256(workflow_path),
        ),
    }
    changed_packaged_files = [
        name
        for name, (expected_hash, actual_hash) in packaged_file_comparisons.items()
        if not expected_hash or expected_hash != actual_hash
    ]
    if changed_packaged_files:
        raise RuntimeError(
            "成品活动指针或 workflow 与 prepare 证据不一致："
            + ", ".join(changed_packaged_files)
        )
    try:
        require_custom_pet_approved(workflow_path)
    except WorkflowError as exc:
        raise RuntimeError("成品最小 workflow 未通过角色与动作审批门禁") from exc
    expected_selfie = preparation.get("selfie_included") is True
    selfie_path = user_root / "selfie.png"
    actual_selfie = selfie_path.is_file()
    if mode == "share-no-selfie" and expected_selfie:
        raise RuntimeError("share-no-selfie 的准备报告不得允许自拍")
    if mode == "share-with-approved-selfie" and not expected_selfie:
        raise RuntimeError("share-with-approved-selfie 的准备报告必须包含自拍")
    if actual_selfie != expected_selfie:
        raise RuntimeError("成品自拍存在状态与发布准备报告不一致")
    expected_selfie_sha256 = preparation.get("selfie_sha256")
    if expected_selfie:
        actual_selfie_sha256 = file_sha256(selfie_path)
        if (
            not isinstance(expected_selfie_sha256, str)
            or actual_selfie_sha256 != expected_selfie_sha256
        ):
            raise RuntimeError("成品自拍 SHA-256 与发布准备报告不一致")
    elif expected_selfie_sha256 is not None:
        raise RuntimeError("未包含自拍的发布准备报告不得声明自拍 SHA-256")
    actual_runtime_fingerprint = runtime_asset_bundle_sha256(runtime_root)
    actual_manifest_sha256 = file_sha256(manifest_path)
    actual_application_fingerprint = application_build_evidence()["fingerprint"]
    comparisons = {
        "pet_id": (pet_id, manifest.get("pet_id")),
        "build_id": (expected.get("build_id"), manifest.get("build_id")),
        "build_input_fingerprint": (
            expected.get("build_input_fingerprint"),
            manifest.get("build_input_fingerprint"),
        ),
        "application_fingerprint": (
            expected.get("application_fingerprint"),
            manifest.get("application_fingerprint"),
        ),
        "runtime_asset_fingerprint": (
            expected.get("runtime_asset_fingerprint"),
            actual_runtime_fingerprint,
        ),
        "manifest_runtime_asset_fingerprint": (
            manifest.get("runtime_asset_fingerprint"),
            actual_runtime_fingerprint,
        ),
        "runtime_manifest_sha256": (
            expected.get("runtime_manifest_sha256"),
            actual_manifest_sha256,
        ),
        "current_application_fingerprint": (
            expected.get("application_fingerprint"),
            actual_application_fingerprint,
        ),
    }
    failures = [
        f"{name} 不一致"
        for name, (expected_value, actual_value) in comparisons.items()
        if not expected_value or expected_value != actual_value
    ]
    if failures:
        raise RuntimeError("成品 runtime 取证失败：" + "；".join(failures))
    return {
        "status": "passed",
        "pet_id": pet_id,
        "build_id": manifest["build_id"],
        "build_input_fingerprint": manifest["build_input_fingerprint"],
        "application_fingerprint": actual_application_fingerprint,
        "runtime_asset_fingerprint": actual_runtime_fingerprint,
        "runtime_manifest_sha256": actual_manifest_sha256,
        "rendering_mode": manifest.get("rendering_mode", "sprite"),
        "selfie_included": actual_selfie,
        "selfie_sha256": expected_selfie_sha256,
    }


def verify_current_preparation_state(
    pet_id: str,
    mode: str,
    preparation: dict[str, Any],
) -> dict[str, Any]:
    """在封包前重查当前 QA、runtime 和自拍授权，拒绝复用陈旧准备报告。"""

    workspace = workspace_path(pet_id)
    config = load_config(workspace)
    workflow_path = workspace / "workflow.json"
    require_custom_pet_approved(workflow_path)
    binding_failures = validate_workflow_bindings(workspace)
    if binding_failures:
        raise RuntimeError(
            "发布准备后当前审批绑定已失效："
            + "；".join(binding_failures)
        )
    qa_path = workspace / str(config["review_directory"]) / "assets-qa.json"
    expected_qa_sha256 = preparation.get("qa_sha256")
    if (
        not qa_path.is_file()
        or not isinstance(expected_qa_sha256, str)
        or file_sha256(qa_path) != expected_qa_sha256
    ):
        raise RuntimeError("发布准备后的 QA 报告已缺失或变化，请重新 prepare")
    current_runtime = require_current_runtime_review(workspace)
    expected_runtime = preparation.get("runtime_review")
    if not isinstance(expected_runtime, dict):
        raise RuntimeError("发布准备报告缺少 runtime_review")
    runtime_keys = (
        "build_id",
        "build_input_fingerprint",
        "application_fingerprint",
        "runtime_asset_fingerprint",
        "review_visual_fingerprint",
        "runtime_manifest_sha256",
    )
    changed = [
        key
        for key in runtime_keys
        if not expected_runtime.get(key)
        or expected_runtime.get(key) != current_runtime.get(key)
    ]
    if changed:
        raise RuntimeError(
            "发布准备后的 runtime/视觉验收已变化，请重新 prepare："
            + ", ".join(changed)
        )
    expected_selfie_sha256 = preparation.get("selfie_sha256")
    selfie = workspace / str(config["selfie_file"])
    if mode == "share-with-approved-selfie":
        approved_sha256 = (
            config.get("sharing", {}).get("selfie_approved_sha256")
            if isinstance(config.get("sharing"), dict)
            else None
        )
        actual_sha256 = file_sha256(selfie) if selfie.is_file() else None
        if (
            not isinstance(expected_selfie_sha256, str)
            or approved_sha256 != expected_selfie_sha256
            or actual_sha256 != expected_selfie_sha256
        ):
            raise RuntimeError("自拍授权或源自拍在 prepare 后已变化，请重新 prepare")
    return {
        "status": "passed",
        "qa_sha256": expected_qa_sha256,
        "runtime_review": {
            key: current_runtime[key]
            for key in runtime_keys
        },
        "selfie_sha256": expected_selfie_sha256,
    }


def _load_passed_evidence(
    path: Path | None,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取一份成功 JSON 证据，并返回适合写入发布报告的哈希摘要。"""

    if path is None or not path.is_file():
        raise FileNotFoundError(f"finalize 缺少{label} JSON 证据")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} JSON 证据无法解析：{exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or data.get("status") != "passed"
    ):
        raise RuntimeError(f"{label} JSON 证据未通过")
    return data, {
        "status": "passed",
        "sha256": file_sha256(path),
        "file": path.name,
    }


def verify_release_evidence(
    distribution: Path,
    pet_id: str,
    preparation: dict[str, Any],
    *,
    tests_report: Path | None,
    source_smoke_report: Path | None,
    packaged_smoke_report: Path | None,
    interaction_menu_smoke_report: Path | None,
    double_click_chat_smoke_report: Path | None,
    packaged_double_click_chat_smoke_report: Path | None,
    hybrid_pet_window_smoke_report: Path | None = None,
    require_hybrid: bool = False,
) -> dict[str, Any]:
    """把各项烟测证据绑定到当前 pet、准备批次和最终 EXE 内容。"""

    executable = distribution / "OnePicDesktopPet.exe"
    executable_sha256 = file_sha256(executable)
    distribution_sha256 = distribution_tree_sha256(distribution)
    loaded: dict[str, dict[str, Any]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    evidence_paths = {
        "tests": (tests_report, "单元测试"),
        "source_smoke": (source_smoke_report, "源码启动烟测"),
        "packaged_smoke": (packaged_smoke_report, "成品启动烟测"),
        "interaction_menu": (
            interaction_menu_smoke_report,
            "互动菜单烟测",
        ),
        "source_double_click_chat": (
            double_click_chat_smoke_report,
            "源码双击聊天烟测",
        ),
        "packaged_double_click_chat": (
            packaged_double_click_chat_smoke_report,
            "成品双击聊天烟测",
        ),
    }
    for key, (path, label) in evidence_paths.items():
        loaded[key], summaries[key] = _load_passed_evidence(path, label)

    tests = loaded["tests"]
    if (
        tests.get("unit_only") is not True
        or tests.get("pytest_exit_code") != 0
    ):
        raise RuntimeError("单元测试证据未证明 UnitOnly pytest 成功")

    for key in ("source_smoke", "packaged_smoke"):
        report = loaded[key]
        if (
            report.get("exit_code") != 0
            or report.get("expected_pet_id") != pet_id
            or report.get("actual_pet_id") != pet_id
        ):
            raise RuntimeError(f"{key} 未绑定当前 pet-id 或成功退出码")
    if loaded["source_smoke"].get("frozen") is not False:
        raise RuntimeError("源码启动烟测证据不是源码进程")

    expected_runtime = preparation.get("runtime_review", {})
    expected_build_id = expected_runtime.get("build_id")
    expected_manifest_sha256 = expected_runtime.get("runtime_manifest_sha256")
    for key in (
        "source_smoke",
        "packaged_smoke",
        "interaction_menu",
        "source_double_click_chat",
        "packaged_double_click_chat",
    ):
        report = loaded[key]
        if (
            not expected_build_id
            or report.get("build_id") != expected_build_id
            or not expected_manifest_sha256
            or report.get("runtime_manifest_sha256")
            != expected_manifest_sha256
        ):
            raise RuntimeError(f"{key} 未绑定当前 runtime build/manifest")

    expected_preparation_id = preparation.get("preparation_id")
    for key in ("packaged_smoke", "packaged_double_click_chat"):
        report = loaded[key]
        if (
            report.get("frozen") is not True
            or report.get("executable_sha256") != executable_sha256
            or report.get("distribution_sha256") != distribution_sha256
            or report.get("expected_pet_id") != pet_id
            or report.get("actual_pet_id") != pet_id
            or report.get("preparation_id") != expected_preparation_id
        ):
            raise RuntimeError(
                f"{key} 未绑定当前 EXE/成品树、pet-id 或 preparation_id"
            )
    if (
        loaded["packaged_double_click_chat"].get(
            "manifest_is_packaged_customer_runtime"
        )
        is not True
    ):
        raise RuntimeError("成品双击聊天烟测未加载包内客户 runtime")

    for key in ("interaction_menu", "source_double_click_chat"):
        if loaded[key].get("pet_id") != pet_id:
            raise RuntimeError(f"{key} 未绑定当前 pet-id")

    if require_hybrid:
        hybrid, hybrid_summary = _load_passed_evidence(
            hybrid_pet_window_smoke_report,
            "Hybrid PetWindow 烟测",
        )
        if (
            hybrid.get("pet_id") != pet_id
            or hybrid.get("build_id") != expected_build_id
            or hybrid.get("runtime_manifest_sha256")
            != expected_manifest_sha256
        ):
            raise RuntimeError("Hybrid PetWindow 烟测未绑定当前 pet/runtime")
        summaries["hybrid_pet_window"] = hybrid_summary
    return {
        "status": "passed",
        "executable_sha256": executable_sha256,
        "distribution_sha256": distribution_sha256,
        "preparation_id": expected_preparation_id,
        "reports": summaries,
    }


def finalize_release(
    pet_id: str,
    mode: str,
    distribution: Path,
    output_directory: Path,
    *,
    preparation_report: Path | None = None,
    tests_report: Path | None = None,
    source_smoke_report: Path | None = None,
    packaged_smoke_report: Path | None = None,
    interaction_menu_smoke_report: Path | None = None,
    double_click_chat_smoke_report: Path | None = None,
    packaged_double_click_chat_smoke_report: Path | None = None,
    hybrid_pet_window_smoke_report: Path | None = None,
    tests_passed: bool = False,
    source_smoke_passed: bool = False,
    packaged_smoke_passed: bool = False,
    hybrid_pet_window_smoke_passed: bool = False,
    double_click_chat_smoke_passed: bool = False,
    packaged_double_click_chat_smoke_passed: bool = False,
) -> dict[str, Any]:
    """压缩完整目录并验证逐文件 SHA/CRC、ZIP CRC 和归档 SHA。"""

    if mode not in PRIVACY_MODES:
        raise ValueError(f"未知隐私模式：{mode}")
    common_gates = {
        "tests": tests_passed,
        "source_smoke": source_smoke_passed,
        "packaged_exe_smoke": packaged_smoke_passed,
        "source_double_click_chat_smoke": double_click_chat_smoke_passed,
        "packaged_double_click_chat_smoke": (
            packaged_double_click_chat_smoke_passed
        ),
    }
    missing_gates = [name for name, passed in common_gates.items() if not passed]
    if missing_gates:
        raise RuntimeError(
            "发布前置测试未全部通过：" + ", ".join(missing_gates)
        )
    if preparation_report is None or not preparation_report.is_file():
        raise FileNotFoundError("finalize 必须提供当前 package-preparation.json")
    if not (distribution / "OnePicDesktopPet.exe").is_file():
        raise FileNotFoundError("dist/OnePicDesktopPet 缺少主程序")
    release_settings = validate_release_settings(
        distribution / "_internal" / "config" / "settings.json"
    )
    scan = privacy_scan(distribution)
    if scan["status"] != "passed":
        raise RuntimeError("打包目录隐私扫描失败：\n" + "\n".join(scan["failures"]))
    try:
        preparation = json.loads(
            preparation_report.read_text(encoding="utf-8-sig")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"发布准备报告无法解析：{exc}") from exc
    if not isinstance(preparation, dict):
        raise RuntimeError("发布准备报告必须是 JSON 对象")
    if (
        preparation.get("schema_version") != PREPARATION_SCHEMA_VERSION
        or not isinstance(preparation.get("preparation_id"), str)
        or not preparation.get("prepared_at")
        or preparation.get("pet_id") != pet_id
        or preparation.get("mode") != mode
    ):
        raise RuntimeError("发布准备报告版本、批次、pet-id 或隐私模式不一致")
    packaged_runtime_attestation = verify_packaged_runtime_attestation(
        distribution,
        pet_id,
        mode,
        preparation,
    )
    require_hybrid = (
        packaged_runtime_attestation["rendering_mode"] == "hybrid-live2d"
    )
    if require_hybrid and not hybrid_pet_window_smoke_passed:
        raise RuntimeError("hybrid-live2d 发布必须通过真实 PetWindow 烟测")
    current_preparation_state = verify_current_preparation_state(
        pet_id,
        mode,
        preparation,
    )
    release_evidence = verify_release_evidence(
        distribution,
        pet_id,
        preparation,
        tests_report=tests_report,
        source_smoke_report=source_smoke_report,
        packaged_smoke_report=packaged_smoke_report,
        interaction_menu_smoke_report=interaction_menu_smoke_report,
        double_click_chat_smoke_report=double_click_chat_smoke_report,
        packaged_double_click_chat_smoke_report=(
            packaged_double_click_chat_smoke_report
        ),
        hybrid_pet_window_smoke_report=hybrid_pet_window_smoke_report,
        require_hybrid=require_hybrid,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    archive = output_directory / f"OnePicDesktopPet-{pet_id}-{mode}.zip"
    if archive.exists():
        raise FileExistsError(f"发布 ZIP 已存在，拒绝覆盖：{archive}")

    records: list[dict[str, Any]] = []
    files = sorted(path for path in distribution.rglob("*") if path.is_file())
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as bundle:
        for path in files:
            relative = Path(distribution.name) / path.relative_to(distribution)
            data = path.read_bytes()
            bundle.writestr(relative.as_posix(), data)
            records.append(
                {
                    "path": relative.as_posix(),
                    "size": len(data),
                    "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    with zipfile.ZipFile(archive, "r") as bundle:
        failed_member = bundle.testzip()
        if failed_member:
            raise RuntimeError(f"ZIP CRC 校验失败：{failed_member}")
        zip_records = {item.filename: item for item in bundle.infolist()}
        for record in records:
            info = zip_records.get(record["path"])
            if info is None or f"{info.CRC:08X}" != record["crc32"]:
                raise RuntimeError(f"ZIP 条目 CRC 不一致：{record['path']}")

    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    report = {
        "schema_version": 2,
        "pet_id": pet_id,
        "privacy_mode": mode,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "delivery": archive.name,
        "directory": distribution.name,
        "file_count": len(records),
        "archive_size": archive.stat().st_size,
        "archive_sha256": archive_sha,
        "zip_crc": "passed",
        "verification": {
            "tests": "passed" if tests_passed else "not-recorded",
            "source_smoke": (
                "passed" if source_smoke_passed else "not-recorded"
            ),
            "packaged_exe_smoke": (
                "passed" if packaged_smoke_passed else "not-recorded"
            ),
            "hybrid_pet_window_smoke": (
                "passed"
                if hybrid_pet_window_smoke_passed
                else "not-applicable-or-not-recorded"
            ),
            "double_click_chat_smoke": (
                "passed"
                if (
                    double_click_chat_smoke_passed
                    and packaged_double_click_chat_smoke_passed
                )
                else "not-recorded"
            ),
            "source_double_click_chat_smoke": (
                "passed"
                if double_click_chat_smoke_passed
                else "not-recorded"
            ),
            "packaged_double_click_chat_smoke": (
                "passed"
                if packaged_double_click_chat_smoke_passed
                else "not-recorded"
            ),
            "settings_scope": "isolated ONEPIC_SETTINGS_DIR",
            "companion_data_scope": "isolated ONEPIC_COMPANION_DATA_DIR",
            "packaged_runtime_asset_fingerprint": "passed",
        },
        "package_preparation": preparation,
        "current_preparation_state": current_preparation_state,
        "release_evidence": release_evidence,
        "packaged_runtime_attestation": packaged_runtime_attestation,
        "release_settings": release_settings,
        "privacy_scan": scan,
        "files": records,
    }
    report_path = output_directory / f"release-report-{pet_id}-{mode}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = output_directory / f"release-report-{pet_id}-{mode}.md"
    markdown.write_text(
        "\n".join(
            (
                f"# OnePic release report: {pet_id}",
                "",
                f"- Privacy mode: `{mode}`",
                f"- Delivery: `{archive.name}`",
                f"- Files: {len(records)}",
                f"- ZIP CRC: passed",
                f"- ZIP SHA-256: `{archive_sha}`",
                f"- Privacy scan: `{scan['status']}`",
                "- Movement region defaults: `disabled / null`",
                f"- Tests: `{report['verification']['tests']}`",
                f"- Source smoke: `{report['verification']['source_smoke']}`",
                "- Packaged EXE smoke: "
                f"`{report['verification']['packaged_exe_smoke']}`",
                "- Hybrid PetWindow smoke: "
                f"`{report['verification']['hybrid_pet_window_smoke']}`",
                "- Double-click chat smoke: "
                f"`{report['verification']['double_click_chat_smoke']}`",
                "- Packaged double-click chat smoke: "
                f"`{report['verification']['packaged_double_click_chat_smoke']}`",
                "",
            )
        ),
        encoding="utf-8",
    )
    return {"archive": archive, "report": report_path, "details": report}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备并校验 OnePic 完整目录发布包")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--pet-id", required=True)
    prepare.add_argument("--mode", choices=sorted(PRIVACY_MODES), required=True)
    prepare.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--pet-id", required=True)
    finalize.add_argument("--mode", choices=sorted(PRIVACY_MODES), required=True)
    finalize.add_argument("--distribution", type=Path, required=True)
    finalize.add_argument("--output-directory", type=Path, required=True)
    finalize.add_argument("--preparation-report", type=Path)
    finalize.add_argument("--tests-report", type=Path)
    finalize.add_argument("--source-smoke-report", type=Path)
    finalize.add_argument("--packaged-smoke-report", type=Path)
    finalize.add_argument("--interaction-menu-smoke-report", type=Path)
    finalize.add_argument("--double-click-chat-smoke-report", type=Path)
    finalize.add_argument(
        "--packaged-double-click-chat-smoke-report",
        type=Path,
    )
    finalize.add_argument("--hybrid-pet-window-smoke-report", type=Path)
    finalize.add_argument("--tests-passed", action="store_true")
    finalize.add_argument("--source-smoke-passed", action="store_true")
    finalize.add_argument("--packaged-smoke-passed", action="store_true")
    finalize.add_argument(
        "--hybrid-pet-window-smoke-passed",
        action="store_true",
    )
    finalize.add_argument(
        "--double-click-chat-smoke-passed",
        action="store_true",
    )
    finalize.add_argument(
        "--packaged-double-click-chat-smoke-passed",
        action="store_true",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        result = prepare_package(
            args.pet_id,
            args.mode,
            args.output.resolve(),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = finalize_release(
            args.pet_id,
            args.mode,
            args.distribution.resolve(),
            args.output_directory.resolve(),
            preparation_report=(
                args.preparation_report.resolve()
                if args.preparation_report is not None
                else None
            ),
            tests_report=(
                args.tests_report.resolve()
                if args.tests_report is not None
                else None
            ),
            source_smoke_report=(
                args.source_smoke_report.resolve()
                if args.source_smoke_report is not None
                else None
            ),
            packaged_smoke_report=(
                args.packaged_smoke_report.resolve()
                if args.packaged_smoke_report is not None
                else None
            ),
            interaction_menu_smoke_report=(
                args.interaction_menu_smoke_report.resolve()
                if args.interaction_menu_smoke_report is not None
                else None
            ),
            double_click_chat_smoke_report=(
                args.double_click_chat_smoke_report.resolve()
                if args.double_click_chat_smoke_report is not None
                else None
            ),
            packaged_double_click_chat_smoke_report=(
                args.packaged_double_click_chat_smoke_report.resolve()
                if args.packaged_double_click_chat_smoke_report is not None
                else None
            ),
            hybrid_pet_window_smoke_report=(
                args.hybrid_pet_window_smoke_report.resolve()
                if args.hybrid_pet_window_smoke_report is not None
                else None
            ),
            tests_passed=args.tests_passed,
            source_smoke_passed=args.source_smoke_passed,
            packaged_smoke_passed=args.packaged_smoke_passed,
            hybrid_pet_window_smoke_passed=(
                args.hybrid_pet_window_smoke_passed
            ),
            double_click_chat_smoke_passed=(
                args.double_click_chat_smoke_passed
            ),
            packaged_double_click_chat_smoke_passed=(
                args.packaged_double_click_chat_smoke_passed
            ),
        )
        print(result["archive"])
        print(result["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
