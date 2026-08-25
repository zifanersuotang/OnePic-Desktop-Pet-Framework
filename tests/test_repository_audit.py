"""
本模块用小型本地夹具验证全库审计器的只读边界、重复判定与报告契约。

测试不读取真实 ``user_assets``，不执行清理脚本，也不运行 Git、网络、构建、
审批或发布。硬链接、符号链接策略、同内容异名、同名异内容、忽略型依赖目录、
权限错误、长路径和中文路径都在测试沙箱内验证。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from tools import repository_audit


def _scan(root: Path, output: Path | None = None) -> dict[str, object]:
    return repository_audit.run_audit(
        root,
        output or (root.parent / f"{root.name}-report"),
        collect_git=False,
        near_limit=100,
        progress_every=10_000,
        write_output=output is not None,
        progress=None,
    )


def test_exact_duplicates_distinguish_hardlinks_from_physical_copies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "仓库"
    (root / "src").mkdir(parents=True)
    (root / "cache" / "__pycache__").mkdir(parents=True)
    original = root / "src" / "原件.bin"
    copy = root / "cache" / "__pycache__" / "different-name.bin"
    linked = root / "src" / "hardlink.bin"
    payload = b"same-content-across-three-paths"
    original.write_bytes(payload)
    copy.write_bytes(payload)
    os.link(original, linked)

    report = _scan(root)

    groups = report["duplicates"]["exact_groups"]
    group = next(item for item in groups if item["size_each"] == len(payload))
    assert group["path_count"] == 3
    assert group["physical_file_count"] == 2
    assert group["hardlink_path_count"] == 1
    assert group["estimated_duplicate_bytes"] == len(payload)
    assert report["summary"]["logical_bytes"] > report["summary"]["physical_bytes"]
    assert report["duplicates"]["hardlink_groups"]


def test_same_name_different_content_is_not_an_exact_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "assets" / "first").mkdir(parents=True)
    (root / "assets" / "second").mkdir(parents=True)
    left = root / "assets" / "first" / "角色-v1.txt"
    right = root / "assets" / "second" / "角色-v1.txt"
    left.write_text("first payload", encoding="utf-8")
    right.write_text("second payload", encoding="utf-8")

    report = _scan(root)

    exact_paths = {
        path
        for group in report["duplicates"]["exact_groups"]
        for path in group["paths"]
    }
    assert left.relative_to(root).as_posix() not in exact_paths
    assert right.relative_to(root).as_posix() not in exact_paths


def test_dependency_and_ignored_style_directories_are_counted_not_skipped(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    dependency = root / "app" / "node_modules" / "pkg" / "index.js"
    build = root / "android" / "app" / "build" / "artifact.bin"
    git_object = root / ".git" / "objects" / "aa" / "object"
    dependency.parent.mkdir(parents=True)
    build.parent.mkdir(parents=True)
    git_object.parent.mkdir(parents=True)
    dependency.write_text("dependency", encoding="utf-8")
    build.write_bytes(b"build")
    git_object.write_bytes(b"git database")

    report = _scan(root)

    assert report["summary"]["file_count"] == 2
    categories = {item["name"]: item for item in report["aggregates"]["categories"]}
    assert categories["dependency"]["file_count"] == 1
    assert categories["generated"]["file_count"] == 1
    assert all(".git/objects" not in record["path"] for record in report["scan_errors"])


def test_dependency_dist_is_not_misclassified_as_release(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    dependency = root / "app" / "node_modules" / "pkg" / "dist" / "index.js"
    release = root / "dist" / "OnePicDesktopPet" / "manifest.json"
    dependency.parent.mkdir(parents=True)
    release.parent.mkdir(parents=True)
    dependency.write_text(r"const fixture = 'C:\\example\\path';", encoding="utf-8")
    release.write_text(r'{"path":"C:\\private\\file"}', encoding="utf-8")

    report = _scan(root)

    categories = {item["name"]: item for item in report["aggregates"]["categories"]}
    assert categories["dependency"]["file_count"] == 1
    assert categories["release"]["file_count"] == 1
    risks = report["risk_findings"]
    assert not any(item["path"] == dependency.relative_to(root).as_posix() for item in risks)
    assert any(
        item["path"] == release.relative_to(root).as_posix()
        and item["severity"] == "P0"
        for item in risks
    )


def test_user_authoring_dist_is_asset_and_test_secret_pattern_is_p2(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    authoring = (
        root
        / "bongocat-live2d-factory"
        / "user_assets"
        / "pets"
        / "fixture"
        / "authoring"
        / "standalone-source"
        / "dist"
        / "index.js"
    )
    test_file = root / "app" / "src" / "crypto.test.ts"
    authoring.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    authoring.write_text(r"const fixture = 'C:\\authoring\\path';", encoding="utf-8")
    test_file.write_text("const fixture = 'sk-" + "A" * 32 + "';", encoding="utf-8")

    report = _scan(root)

    categories = {item["name"]: item for item in report["aggregates"]["categories"]}
    assert categories["asset"]["file_count"] == 1
    authoring_path = authoring.relative_to(root).as_posix()
    authoring_risks = [
        item for item in report["risk_findings"] if item["path"] == authoring_path
    ]
    assert authoring_risks
    assert all(item["severity"] == "P1" for item in authoring_risks)
    test_risks = [
        item
        for item in report["risk_findings"]
        if item["path"] == test_file.relative_to(root).as_posix()
    ]
    assert test_risks
    assert all(item["severity"] == "P2" for item in test_risks)


def test_symlink_or_reparse_point_is_recorded_and_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        link.write_text("synthetic link", encoding="utf-8")
        original = repository_audit._is_reparse_point

        def synthetic_reparse(info: os.stat_result) -> bool:
            return info.st_size == len("synthetic link") or original(info)

        monkeypatch.setattr(repository_audit, "_is_reparse_point", synthetic_reparse)

    report = _scan(root)

    assert any(item["path"] == "link.txt" and not item["followed"] for item in report["links"])
    assert sum(item["path"] == "link.txt" for item in report["links"]) == 1


def test_permission_error_is_evidence_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "private.bin").write_bytes(b"private")
    original_scandir = repository_audit.os.scandir

    def guarded_scandir(path: os.PathLike[str] | str):
        if Path(path) == blocked:
            raise PermissionError("fixture")
        return original_scandir(path)

    monkeypatch.setattr(repository_audit.os, "scandir", guarded_scandir)

    state = repository_audit.scan_tree(root, progress=None)

    assert state.files == []
    assert state.errors == [
        {"path": "blocked", "operation": "scandir", "error": "PermissionError"}
    ]


def test_long_and_chinese_paths_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "中文仓库"
    filename = "角色素材.txt"
    filler_length = max(1, 242 - len(str(root)) - len(filename) - 2)
    if filler_length > 180:
        pytest.skip("测试沙箱前缀过短，单个安全路径段不足以命中长路径阈值")
    current = root / ("长" * filler_length)
    current.mkdir(parents=True)
    file = current / filename
    file.write_text("中文内容", encoding="utf-8")

    report = _scan(root)

    assert report["summary"]["file_count"] == 1
    assert report["long_paths"]
    assert "角色素材.txt" in report["long_paths"][0]["path"]


def test_near_duplicate_images_are_candidates_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    first = root / "assets" / "动作-v1.png"
    second = root / "assets" / "动作-v2.png"
    first.parent.mkdir(parents=True)
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, 50, 50), fill=(240, 120, 80, 255))
    image.save(first)
    draw.point((30, 30), fill=(241, 121, 81, 255))
    image.save(second)

    report = _scan(root)

    candidates = report["near_duplicates"]["candidates"]
    assert candidates
    assert all(item["disposition"] == "manual_review" for item in candidates)
    assert report["near_duplicates"]["candidate_only"] is True


def test_privacy_findings_redact_secret_values(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    secret = root / "telegram-accounting-bot" / ".env"
    secret.parent.mkdir(parents=True)
    secret_value = "sk-" + "A" * 32
    secret.write_text(
        f"API_KEY={secret_value}\nLOCAL=C:\\Private\\customer\\photo.png\n",
        encoding="utf-8",
    )

    report = _scan(root)
    serialized = json.dumps(report["risk_findings"], ensure_ascii=False)

    assert secret_value not in serialized
    assert any(item["rule"] == "openai_style_key" for item in report["risk_findings"])
    assert any(item["rule"] == "windows_absolute_path" for item in report["risk_findings"])
    assert all(item["value_redacted"] for item in report["risk_findings"])


def test_user_assets_hash_match_in_release_is_p1_candidate_not_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    private_copy = root / "user_assets" / "pets" / "fixture" / "runtime" / "icon.png"
    release_copy = root / "dist" / "OnePicDesktopPet" / "icon.png"
    private_copy.parent.mkdir(parents=True)
    release_copy.parent.mkdir(parents=True)
    private_copy.write_bytes(b"approved-runtime-copy")
    release_copy.write_bytes(b"approved-runtime-copy")

    report = _scan(root)

    matches = [
        item
        for item in report["risk_findings"]
        if item["rule"] == "user_assets_content_hash_in_release"
    ]
    assert len(matches) == 1
    assert matches[0]["severity"] == "P1"
    assert "不单独证明" in matches[0]["evidence"]


def test_73_frame_manifest_is_legal_must_keep_runtime_copy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    manifest = root / "user_assets" / "pet" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    base_frames = [f"base/{index:02d}.png" for index in range(38)]
    extensions = {
        "advanced_v1": {"frames": [f"advanced/{index:02d}.png" for index in range(25)]},
        "side_walk_v1": {
            "frames": [f"side/{index:02d}.png" for index in range(8)],
            "walk_variant": True,
            "walk_variant_default": False,
            "replaces_forward_walk": False,
        },
    }
    manifest.write_text(
        json.dumps(
            {
                "pet_id": "fixture-pet",
                "base_frame_count": 38,
                "optional_touch_frame_count": 2,
                "extension_frame_count": 33,
                "directional_frame_count": 0,
                "total_frame_count": 40,
                "total_asset_frame_count": 73,
                "animations": {"all": base_frames},
                "extension_animations": extensions,
            }
        ),
        encoding="utf-8",
    )

    report = _scan(root)

    item = report["frame_manifests"][0]
    assert item["tier"] == "OnePic 73-frame advanced with side walk"
    assert item["status"] == "consistent"
    assert item["disposition"] == "must_keep"


def test_reports_and_cleanup_script_are_reproducible_and_dry_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    cache = root / ".pytest_cache" / "v" / "cache.bin"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    output = tmp_path / "audit-output"

    report = _scan(root, output)

    expected = {
        "repository-audit.json",
        "audit-report.md",
        "directory-inventory.csv",
        "exact-duplicates.csv",
        "near-duplicates.csv",
        "cleanup-candidates.csv",
        "risk-findings.csv",
        "git-status.csv",
        "cleanup-plan.ps1",
    }
    assert expected == {path.name for path in output.iterdir()}
    cleanup = (output / "cleanup-plan.ps1").read_text(encoding="utf-8-sig")
    assert "-WhatIf:(-not $Execute)" in cleanup
    assert "Resolve-Path -LiteralPath" in cleanup
    assert report["snapshot"]["output_excluded"] == str(output.resolve())


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError):
        repository_audit.run_audit(
            root,
            output,
            collect_git=False,
            write_output=True,
            progress=None,
        )
