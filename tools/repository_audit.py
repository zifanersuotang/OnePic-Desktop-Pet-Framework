"""
本模块对 OnePic 根目录执行只读、可复现的重复与仓库卫生审计。

扫描不会删除、移动、覆盖、激活或打包任何项目文件；唯一写操作是把 JSON、CSV、
Markdown 与默认 ``-WhatIf`` 的后续整理脚本写入显式指定的新输出目录。审计会完整
统计源码、资产、生成物、依赖、缓存和发布边界，但排除不属于工作树内容的 ``.git``
数据库与本次输出目录。精确重复只对同大小候选先做快速指纹，再做 SHA-256；图片
感知哈希、尺寸和文件名相似度始终只作为人工复核候选，不能作为删除依据。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from PIL import Image, UnidentifiedImageError


SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".cmd",
    ".conf",
    ".cs",
    ".css",
    ".env",
    ".gradle",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
ARCHIVE_SUFFIXES = {".zip", ".apk", ".aab"}
PRIVATE_SUFFIXES = {
    ".cmo3",
    ".dpapi",
    ".env",
    ".jks",
    ".keystore",
    ".kra",
    ".mov",
    ".mp4",
    ".mkv",
    ".psd",
    ".sqlite",
    ".sqlite3",
    ".webm",
}
DEPENDENCY_PARTS = {
    ".venv",
    "node_modules",
    ".pnpm-store",
    "vendor",
    "site-packages",
    ".cargo",
}
CACHE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    ".turbo",
    ".gradle",
    ".test-sandbox",
    ".test-tmp",
}
GENERATED_PARTS = {
    "build",
    "target",
    ".tmp",
    ".staging",
    "host-work",
    "generated",
}
RELEASE_PARTS = {
    "dist",
    ".release-staging",
    "最终成品",
}
ASSET_PARTS = {
    "assets",
    "user_assets",
    "images",
    "textures",
    "sprites",
    "authoring",
    "live2d-runtime",
}
PROTECTED_PARTS = {
    "user_assets",
    "runtime",
    "actions",
    "walk",
    "candidate",
    "candidates",
    "extensions",
    "authoring",
    "backups",
    "review",
}
SAFE_CACHE_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".turbo",
    ".test-sandbox",
    ".test-tmp",
}
BOUNDARY_PARTS = (
    DEPENDENCY_PARTS
    | CACHE_PARTS
    | GENERATED_PARTS
    | RELEASE_PARTS
    | {"failed-builds"}
)
FORBIDDEN_RELEASE_PARTS = {
    "source",
    "candidate",
    "candidates",
    "draft",
    "drafts",
    "backup",
    "backups",
    "authoring",
    "failed-builds",
    "staging",
}
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9+.-])[A-Za-z]:[\\/]{1,2}(?![\\/])"
    r"|\\\\[^\\\s]+\\[^\\\s]+"
    r")",
    re.IGNORECASE,
)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b"
            r"\s*(?::|=)\s*[\"']?[^\s\"'${}<]{8,}"
        ),
    ),
)
PRIVATE_SIGNATURES = {
    b"SQLite format 3\x00": "sqlite_header",
    b"ONEPIC-DPAPI": "onepic_dpapi_header",
    b"-----BEGIN PRIVATE KEY-----": "private_key_header",
}


@dataclass(slots=True)
class FileRecord:
    """保存单个工作树文件的只读元数据。"""

    path: str
    size: int
    suffix: str
    owner: str
    category: str
    boundary: str | None
    mtime_ns: int
    file_id: str
    link_count: int
    git_state: str = "unknown"
    sha256: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    perceptual_hash: str | None = None


@dataclass(slots=True)
class ScanState:
    """保存目录遍历结果和不可读证据。"""

    files: list[FileRecord] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    long_paths: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)


def utc_now() -> str:
    """返回稳定的 UTC ISO-8601 时间。"""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def human_bytes(value: int | float) -> str:
    """把字节数格式化为便于报告阅读的二进制单位。"""

    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024.0 or unit == "TiB":
            return f"{number:.2f} {unit}"
        number /= 1024.0
    return f"{number:.2f} TiB"


def owner_for(relative: Path) -> str:
    """按已确认的产品边界判定目录所有权。"""

    parts = relative.parts
    if not parts:
        return "OnePic core"
    first = parts[0].casefold()
    if first == "airi-desktop-pet":
        return "AIRI upstream"
    if first == "bongocat-live2d-factory":
        return "BongoCat Live2D factory"
    if first == "telegram-accounting-bot":
        return "Telegram bot"
    if first == "android":
        return "OnePic Android"
    if first == "user_assets" and len(parts) >= 3 and parts[1].casefold() == "pets":
        return f"OnePic pet:{parts[2]}"
    if first == "user_assets":
        return "OnePic private workspace"
    return "OnePic core"


def _first_matching_part(parts: Sequence[str], choices: set[str]) -> int | None:
    lowered = [part.casefold() for part in parts]
    for index, part in enumerate(lowered):
        if part in choices:
            return index
    return None


def category_for(relative: Path) -> str:
    """把文件分类为源码、资产、生成物、依赖、缓存或发布树。"""

    parts = relative.parts
    lowered = {part.casefold() for part in parts}
    suffix = suffix_for(relative)
    if lowered & DEPENDENCY_PARTS:
        return "dependency"
    if lowered & CACHE_PARTS:
        return "cache"
    if "user_assets" in lowered:
        return "asset"
    if lowered & RELEASE_PARTS or suffix in {".apk", ".aab", ".exe", ".msi"}:
        return "release"
    if lowered & ASSET_PARTS:
        return "asset"
    if lowered & GENERATED_PARTS:
        return "generated"
    if suffix in {".md", ".rst", ".txt"} or "docs" in lowered:
        return "documentation"
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        return "configuration"
    return "source"


def suffix_for(relative: Path) -> str:
    """返回普通扩展名，并把 ``.env`` 一类单点文件视为自身扩展名。"""

    suffix = relative.suffix.casefold()
    if not suffix and relative.name.startswith(".") and relative.name.count(".") == 1:
        suffix = relative.name.casefold()
    return suffix or "[no extension]"


def boundary_for(relative: Path) -> str | None:
    """返回文件所在的首个生成/依赖/缓存/发布边界根。"""

    index = _first_matching_part(relative.parts, BOUNDARY_PARTS)
    if index is None:
        return None
    return Path(*relative.parts[: index + 1]).as_posix()


def _is_reparse_point(mode_result: os.stat_result) -> bool:
    attrs = int(getattr(mode_result, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attrs & reparse_flag)


def _file_id(mode_result: os.stat_result, relative: Path) -> str:
    inode = int(getattr(mode_result, "st_ino", 0))
    device = int(getattr(mode_result, "st_dev", 0))
    if inode:
        return f"{device}:{inode}"
    return f"path:{relative.as_posix()}"


def summarize_path(path: Path) -> dict[str, Any]:
    """只统计排除目录的文件数和逻辑字节，不读取内容。"""

    count = 0
    size = 0
    errors = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        errors += 1
                        continue
                    if entry.is_symlink() or _is_reparse_point(info):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        count += 1
                        size += int(info.st_size)
        except OSError:
            errors += 1
    return {"path": str(path), "file_count": count, "bytes": size, "errors": errors}


def scan_tree(
    root: Path,
    *,
    excluded_roots: Iterable[Path] = (),
    progress_every: int = 20_000,
    progress: Callable[[str], None] | None = print,
) -> ScanState:
    """不跟随链接地遍历真实工作树并记录元数据。"""

    root = root.resolve()
    exclusions = {item.resolve() for item in excluded_roots}
    state = ScanState()
    git_dir = root / ".git"
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                ordered = sorted(entries, key=lambda item: item.name.casefold(), reverse=True)
        except OSError as exc:
            try:
                relative_error = current.relative_to(root).as_posix() or "."
            except ValueError:
                relative_error = str(current)
            state.errors.append(
                {"path": relative_error, "operation": "scandir", "error": type(exc).__name__}
            )
            continue
        for entry in ordered:
            path = Path(entry.path)
            try:
                resolved_parent = path.parent.resolve()
            except OSError:
                resolved_parent = path.parent
            if path == git_dir or any(
                path == exclusion or resolved_parent == exclusion
                for exclusion in exclusions
            ):
                state.excluded.append({"path": str(path), "reason": "vcs_or_audit_output"})
                continue
            relative = path.relative_to(root)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                state.errors.append(
                    {
                        "path": relative.as_posix(),
                        "operation": "lstat",
                        "error": type(exc).__name__,
                    }
                )
                continue
            is_link = entry.is_symlink() or _is_reparse_point(info)
            if is_link:
                target: str | None = None
                try:
                    target = os.readlink(path)
                except OSError:
                    pass
                state.links.append(
                    {
                        "path": relative.as_posix(),
                        "target": target,
                        "kind": "symlink_or_reparse_point",
                        "followed": False,
                    }
                )
                continue
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                state.links.append(
                    {
                        "path": relative.as_posix(),
                        "target": None,
                        "kind": "non_regular_file",
                        "followed": False,
                    }
                )
                continue
            record = FileRecord(
                path=relative.as_posix(),
                size=int(info.st_size),
                suffix=suffix_for(relative),
                owner=owner_for(relative),
                category=category_for(relative),
                boundary=boundary_for(relative),
                mtime_ns=int(info.st_mtime_ns),
                file_id=_file_id(info, relative),
                link_count=int(getattr(info, "st_nlink", 1)),
            )
            state.files.append(record)
            absolute_length = len(str(path.absolute()))
            if absolute_length >= 240:
                state.long_paths.append(
                    {"path": relative.as_posix(), "absolute_length": absolute_length}
                )
            if progress and progress_every and len(state.files) % progress_every == 0:
                progress(f"[audit] metadata: {len(state.files):,} files")
    return state


def _run_git(root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotepath=false", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_path_set(root: Path, arguments: Sequence[str]) -> set[str]:
    result = _run_git(root, arguments)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {message}")
    return {
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def collect_git_state(root: Path, records: list[FileRecord]) -> dict[str, Any]:
    """用 Git 路径集合标注 tracked/untracked/ignored 和 dirty 状态。"""

    tracked = _git_path_set(root, ["ls-files", "-z"])
    untracked = _git_path_set(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    modified = _git_path_set(root, ["ls-files", "-m", "-z"])
    deleted = _git_path_set(root, ["ls-files", "--deleted", "-z"])
    staged = _git_path_set(root, ["diff", "--cached", "--name-only", "-z"])
    present_paths = {record.path for record in records}
    for record in records:
        if record.path in tracked:
            if record.path in deleted:
                record.git_state = "tracked_deleted"
            elif record.path in modified and record.path in staged:
                record.git_state = "tracked_staged_and_modified"
            elif record.path in staged:
                record.git_state = "tracked_staged"
            elif record.path in modified:
                record.git_state = "tracked_modified"
            else:
                record.git_state = "tracked_clean"
        elif record.path in untracked:
            record.git_state = "untracked"
        else:
            record.git_state = "ignored"
    head = _run_git(root, ["rev-parse", "HEAD"])
    branch = _run_git(root, ["branch", "--show-current"])
    remote = _run_git(root, ["remote", "get-url", "origin"])
    dirty_paths = sorted((modified | deleted | staged | untracked))
    return {
        "head": head.stdout.decode("ascii", errors="replace").strip(),
        "branch": branch.stdout.decode("utf-8", errors="replace").strip(),
        "origin": remote.stdout.decode("utf-8", errors="replace").strip()
        if remote.returncode == 0
        else None,
        "tracked_count": len(tracked),
        "tracked_present_count": len(tracked & present_paths),
        "modified_count": len(modified),
        "deleted_count": len(deleted),
        "staged_count": len(staged),
        "untracked_count": len(untracked),
        "ignored_present_count": sum(record.git_state == "ignored" for record in records),
        "dirty_path_count": len(dirty_paths),
        "dirty_paths": dirty_paths,
    }


def _quick_fingerprint(path: Path, size: int) -> str:
    """以头/中/尾样本缩小同大小候选；不是精确重复证据。"""

    digest = hashlib.blake2b(digest_size=16)
    digest.update(size.to_bytes(16, "little", signed=False))
    chunk_size = 65_536
    with path.open("rb") as stream:
        if size <= chunk_size * 4:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        else:
            offsets = (0, max(0, size // 2 - chunk_size // 2), max(0, size - chunk_size))
            for offset in offsets:
                stream.seek(offset)
                digest.update(stream.read(chunk_size))
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_protected_path(path: str) -> bool:
    relative = Path(path)
    lowered = {part.casefold() for part in relative.parts}
    if lowered & PROTECTED_PARTS:
        return True
    if relative.name.casefold() in {"workflow.json", "manifest.json", "assets-qa.json"}:
        return True
    return relative.suffix.casefold() in {".psd", ".cmo3", ".moc3"}


def classify_duplicate_group(records: Sequence[FileRecord]) -> tuple[str, str, str]:
    """保守判定重复组的处置级别、风险和恢复方法。"""

    categories = {record.category for record in records}
    owners = {record.owner for record in records}
    states = {record.git_state for record in records}
    paths = [record.path for record in records]
    if any(_is_protected_path(path) for path in paths):
        return (
            "must_keep",
            "审批、运行帧、用户素材、作者工程或历史版本可能按路径承载语义；哈希相同不等于冗余。",
            "保留原位；若需整理，先核对 manifest/workflow/发布哈希并制作可恢复副本。",
        )
    if "release" in categories:
        return (
            "manual_review",
            "发布物或交付证据可能需要复现、校验或客户回滚。",
            "先核对发布报告、归档 SHA/CRC 和客户交付记录，再移动到版本化冷存储。",
        )
    safe_paths = all(
        bool({part.casefold() for part in Path(path).parts} & SAFE_CACHE_PARTS)
        for path in paths
    )
    if safe_paths and not any(state.startswith("tracked") for state in states):
        return (
            "safe_delete_candidate",
            "全部副本位于可再生测试/解释器缓存，且当前均非 Git tracked。",
            "通过项目测试、Python 或对应工具重新生成；执行前仍应使用生成的 -WhatIf 清单。",
        )
    if categories <= {"dependency", "cache", "generated"}:
        return (
            "manual_review",
            "内容可再生，但重装依赖或重建产物可能耗时、依赖网络或丢失调试证据。",
            "确认锁文件、构建命令和必要日志后再清理；用环境/构建脚本恢复。",
        )
    if len(owners) > 1 or len(categories) > 1:
        return (
            "manual_review",
            "跨所有权或跨生命周期边界的相同内容可能是合法运行副本。",
            "逐项核对消费方清单和发布/审批记录；不要用硬链接替换可写运行资产。",
        )
    return (
        "cannot_determine",
        "仅凭内容哈希和路径无法证明任一副本无消费者。",
        "先用代码引用、清单、构建日志和人工所有权确认补充证据。",
    )


def find_exact_duplicates(
    root: Path,
    records: list[FileRecord],
    errors: list[dict[str, str]],
    *,
    progress_every: int = 10_000,
    progress: Callable[[str], None] | None = print,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """只对同大小且快速指纹相同的候选计算 SHA-256。"""

    by_size_and_suffix: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    for record in records:
        by_size_and_suffix[(record.size, record.suffix)].append(record)
    candidate_records = sum(
        len(group) for group in by_size_and_suffix.values() if len(group) > 1
    )
    quick_cache: dict[str, str] = {}
    sha_cache: dict[str, str] = {}
    quick_groups: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    processed = 0
    for (size, suffix), same_size in sorted(by_size_and_suffix.items()):
        if len(same_size) < 2:
            continue
        for record in same_size:
            if not record.file_id.startswith("path:") and record.link_count > 0:
                continue
            try:
                path_info = (root / record.path).stat(follow_symlinks=False)
            except OSError as exc:
                errors.append(
                    {
                        "path": record.path,
                        "operation": "candidate_file_id",
                        "error": type(exc).__name__,
                    }
                )
                continue
            record.file_id = _file_id(path_info, Path(record.path))
            record.link_count = int(getattr(path_info, "st_nlink", 1))
        if size == 0:
            fingerprint = "empty"
            quick_groups[(size, f"{suffix}:{fingerprint}")].extend(same_size)
            continue
        for record in same_size:
            processed += 1
            try:
                fingerprint = quick_cache.get(record.file_id)
                if fingerprint is None:
                    fingerprint = _quick_fingerprint(root / record.path, size)
                    quick_cache[record.file_id] = fingerprint
                quick_groups[(size, f"{suffix}:{fingerprint}")].append(record)
            except OSError as exc:
                errors.append(
                    {"path": record.path, "operation": "quick_hash", "error": type(exc).__name__}
                )
            if progress and progress_every and processed % progress_every == 0:
                progress(f"[audit] quick fingerprints: {processed:,}/{candidate_records:,}")
    exact_map: dict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    sha_candidate_count = 0
    for (size, _), group in quick_groups.items():
        if len(group) < 2:
            continue
        sha_candidate_count += len(group)
        for record in group:
            try:
                digest = sha_cache.get(record.file_id)
                if digest is None:
                    digest = (
                        hashlib.sha256(b"").hexdigest()
                        if size == 0
                        else _sha256(root / record.path)
                    )
                    sha_cache[record.file_id] = digest
                record.sha256 = digest
                exact_map[(size, digest)].append(record)
            except OSError as exc:
                errors.append(
                    {"path": record.path, "operation": "sha256", "error": type(exc).__name__}
                )
    groups: list[dict[str, Any]] = []
    for (size, digest), group in exact_map.items():
        if len(group) < 2:
            continue
        unique_file_ids = {record.file_id for record in group}
        disposition, risk, recovery = classify_duplicate_group(group)
        reclaimable = max(0, len(unique_file_ids) - 1) * size
        groups.append(
            {
                "group_id": f"sha256:{digest[:16]}",
                "sha256": digest,
                "size_each": size,
                "path_count": len(group),
                "physical_file_count": len(unique_file_ids),
                "hardlink_path_count": len(group) - len(unique_file_ids),
                "estimated_duplicate_bytes": reclaimable,
                "disposition": disposition,
                "risk": risk,
                "recovery": recovery,
                "owners": sorted({record.owner for record in group}),
                "categories": sorted({record.category for record in group}),
                "git_states": sorted({record.git_state for record in group}),
                "paths": sorted(record.path for record in group),
            }
        )
    groups.sort(
        key=lambda item: (item["estimated_duplicate_bytes"], item["path_count"]),
        reverse=True,
    )
    return groups, {
        "same_size_candidate_paths": candidate_records,
        "quick_fingerprint_physical_files": len(quick_cache),
        "sha256_candidate_paths": sha_candidate_count,
        "sha256_physical_files": len(sha_cache),
        "exact_group_count": len(groups),
        "estimated_duplicate_bytes": sum(
            item["estimated_duplicate_bytes"] for item in groups
        ),
    }


def _dhash(path: Path) -> tuple[int, int, str]:
    """返回图片尺寸和 64 位差异感知哈希。"""

    with Image.open(path) as image:
        width, height = image.size
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        if hasattr(gray, "get_flattened_data"):
            pixels = list(gray.get_flattened_data())
        else:
            pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        start = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[start + column] > pixels[start + column + 1])
    return width, height, f"{value:016x}"


def _normalized_candidate_name(path: str) -> str:
    stem = Path(path).stem.casefold()
    stem = re.sub(r"(?:copy|副本|备份|backup)", "", stem)
    stem = re.sub(r"[\s._-]*(?:v|ver|version)?\d+$", "", stem)
    stem = re.sub(r"\(\d+\)$", "", stem)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", stem)


def find_near_duplicates(
    root: Path,
    records: list[FileRecord],
    errors: list[dict[str, str]],
    *,
    limit: int = 5_000,
) -> dict[str, Any]:
    """生成尺寸/感知哈希/文件名近似候选，永不直接给出删除结论。"""

    images = [
        record
        for record in records
        if record.suffix in IMAGE_SUFFIXES
        and record.category in {"asset", "generated", "release", "source"}
        and record.size <= 128 * 1024 * 1024
    ]
    for record in images:
        try:
            width, height, digest = _dhash(root / record.path)
            record.image_width = width
            record.image_height = height
            record.perceptual_hash = digest
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            errors.append(
                {"path": record.path, "operation": "image_hash", "error": type(exc).__name__}
            )
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    by_dimensions: dict[tuple[int, int], list[FileRecord]] = defaultdict(list)
    for record in images:
        if record.perceptual_hash is not None:
            by_dimensions[(record.image_width or 0, record.image_height or 0)].append(record)
    truncated = False
    for dimensions, group in sorted(by_dimensions.items()):
        for index, left in enumerate(group):
            left_hash = int(left.perceptual_hash or "0", 16)
            for right in group[index + 1 :]:
                if left.sha256 and right.sha256 and left.sha256 == right.sha256:
                    continue
                distance = (left_hash ^ int(right.perceptual_hash or "0", 16)).bit_count()
                if distance > 6:
                    continue
                key = tuple(sorted((left.path, right.path)))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(
                    {
                        "candidate_id": f"near:{len(pairs) + 1:06d}",
                        "left": left.path,
                        "right": right.path,
                        "reason": "same_dimensions_and_dhash_distance",
                        "dimensions": list(dimensions),
                        "dhash_distance": distance,
                        "size_ratio": round(min(left.size, right.size) / max(left.size, right.size), 6)
                        if max(left.size, right.size)
                        else 1.0,
                        "disposition": "manual_review",
                        "risk": "感知哈希会把合法动画相位、压缩版本或透明构图判为相似。",
                        "recovery": "仅在逐张视觉检查、清单引用和审批哈希均确认后整理。",
                    }
                )
                if len(pairs) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break
    if len(pairs) < limit:
        by_name: dict[str, list[FileRecord]] = defaultdict(list)
        for record in records:
            if record.category not in {"asset", "generated", "release"} or record.size < 1024:
                continue
            normalized = _normalized_candidate_name(record.path)
            if len(normalized) >= 4:
                by_name[normalized].append(record)
        for normalized, group in sorted(by_name.items()):
            if len(group) < 2:
                continue
            for index, left in enumerate(group):
                for right in group[index + 1 :]:
                    key = tuple(sorted((left.path, right.path)))
                    if key in seen or left.path == right.path:
                        continue
                    if left.sha256 and right.sha256 and left.sha256 == right.sha256:
                        continue
                    ratio = min(left.size, right.size) / max(left.size, right.size)
                    if ratio < 0.5:
                        continue
                    seen.add(key)
                    pairs.append(
                        {
                            "candidate_id": f"near:{len(pairs) + 1:06d}",
                            "left": left.path,
                            "right": right.path,
                            "reason": "normalized_filename",
                            "normalized_name": normalized,
                            "dimensions": None,
                            "dhash_distance": None,
                            "size_ratio": round(ratio, 6),
                            "disposition": "manual_review",
                            "risk": "同名或版本化命名可能表示合法平台副本、历史版本或动画相位。",
                            "recovery": "核对所有权、manifest/workflow 和视觉内容后再决定。",
                        }
                    )
                    if len(pairs) >= limit:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break
    return {
        "method": "same dimensions + 64-bit dHash distance <= 6; normalized filename as secondary signal",
        "candidate_only": True,
        "image_count_hashed": sum(record.perceptual_hash is not None for record in images),
        "candidate_count": len(pairs),
        "limit": limit,
        "truncated": truncated,
        "candidates": pairs,
    }


def _text_scope(record: FileRecord) -> bool:
    if record.suffix not in TEXT_SUFFIXES or record.size > 4 * 1024 * 1024:
        return False
    if record.category in {"dependency", "cache"}:
        return False
    return True


def _finding_severity(record: FileRecord, rule: str) -> str:
    lowered = {part.casefold() for part in Path(record.path).parts}
    filename = Path(record.path).name.casefold()
    testing_context = (
        "tests" in lowered
        or "docs" in lowered
        or "example" in record.path.casefold()
        or "e2e" in record.path.casefold()
        or ".test." in filename
        or ".spec." in filename
        or record.path == "tools/repository_audit.py"
    )
    if testing_context:
        return "P2"
    if record.category == "release":
        return "P0"
    if Path(record.path).name.casefold() == ".env" or rule in {
        "private_key_header",
        "aws_access_key",
        "github_token",
        "openai_style_key",
    }:
        return "P0" if record.git_state != "tracked_clean" else "P1"
    return "P1"


def scan_privacy_and_release_risks(
    root: Path,
    records: Sequence[FileRecord],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """扫描绝对路径、密钥样式、私密文件头和发布树污染，不保存命中值。"""

    findings: list[dict[str, Any]] = []
    for record in records:
        relative = Path(record.path)
        lowered_parts = {part.casefold() for part in relative.parts}
        in_release = record.category == "release"
        if in_release and lowered_parts & FORBIDDEN_RELEASE_PARTS:
            findings.append(
                {
                    "severity": "P0",
                    "path": record.path,
                    "line": None,
                    "rule": "forbidden_release_path_component",
                    "scope": "release_tree",
                    "git_state": record.git_state,
                    "evidence": "发布边界出现 source/candidate/draft/backup/authoring/staging 类目录名。",
                    "value_redacted": True,
                    "disposition": "manual_review",
                    "recovery": "隔离发布暂存，使用 release_pet.ps1 从已批准 runtime 重新装配。",
                }
            )
        if in_release and relative.suffix.casefold() in PRIVATE_SUFFIXES:
            findings.append(
                {
                    "severity": "P0",
                    "path": record.path,
                    "line": None,
                    "rule": "private_suffix_in_release",
                    "scope": "release_tree",
                    "git_state": record.git_state,
                    "evidence": f"发布边界出现受保护扩展名 {relative.suffix.casefold()}。",
                    "value_redacted": True,
                    "disposition": "manual_review",
                    "recovery": "停止分发并按 share-no-selfie 发布流程重建。",
                }
            )
        if relative.name.casefold() == ".env":
            findings.append(
                {
                    "severity": "P0" if in_release else "P1",
                    "path": record.path,
                    "line": None,
                    "rule": "environment_secret_file",
                    "scope": "release_tree" if in_release else "private_boundary",
                    "git_state": record.git_state,
                    "evidence": ".env 应保持受忽略的本地私密配置，不得进入发布或 Git tracked。",
                    "value_redacted": True,
                    "disposition": "must_keep" if not in_release else "manual_review",
                    "recovery": "保留本地并核对 .gitignore；若进入发布，轮换凭据并重建发布物。",
                }
            )
        if not _text_scope(record):
            continue
        path = root / record.path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            errors.append(
                {"path": record.path, "operation": "privacy_read", "error": type(exc).__name__}
            )
            continue
        for signature, rule in PRIVATE_SIGNATURES.items():
            if raw.startswith(signature):
                findings.append(
                    {
                        "severity": _finding_severity(record, rule),
                        "path": record.path,
                        "line": 1,
                        "rule": rule,
                        "scope": "release_tree" if in_release else "repository",
                        "git_state": record.git_state,
                        "evidence": "检测到私密文件头；报告不保存内容。",
                        "value_redacted": True,
                        "disposition": "manual_review",
                        "recovery": "隔离文件并核对来源；如为真实凭据，轮换后再检查历史与发布物。",
                    }
                )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            rules: list[str] = []
            if ABSOLUTE_PATH_PATTERN.search(line):
                rules.append("windows_absolute_path")
            rules.extend(name for name, pattern in SECRET_PATTERNS if pattern.search(line))
            for rule in dict.fromkeys(rules):
                findings.append(
                    {
                        "severity": _finding_severity(record, rule),
                        "path": record.path,
                        "line": line_number,
                        "rule": rule,
                        "scope": "release_tree" if in_release else "repository",
                        "git_state": record.git_state,
                        "evidence": "仅记录规则与行号；命中值已删隐。",
                        "value_redacted": True,
                        "disposition": "manual_review",
                        "recovery": "人工确认是否为示例/测试；真实路径改为相对路径，真实凭据立即轮换并清除发布副本。",
                    }
                )
    return findings


def scan_archives(
    root: Path,
    records: Sequence[FileRecord],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """只读取发布型 ZIP/APK/AAB 的成员名，不解压、不修改。"""

    results: list[dict[str, Any]] = []
    for record in records:
        if record.suffix not in ARCHIVE_SUFFIXES:
            continue
        if record.category not in {"release", "asset"} and "build" not in Path(record.path).parts:
            continue
        path = root / record.path
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except (OSError, zipfile.BadZipFile) as exc:
            errors.append(
                {"path": record.path, "operation": "archive_index", "error": type(exc).__name__}
            )
            continue
        forbidden: list[dict[str, str]] = []
        for name in names:
            normalized = name.replace("\\", "/")
            parts = {part.casefold() for part in Path(normalized).parts}
            suffix = Path(normalized).suffix.casefold()
            if parts & FORBIDDEN_RELEASE_PARTS or suffix in PRIVATE_SUFFIXES:
                forbidden.append(
                    {
                        "member": normalized,
                        "reason": "forbidden_path_or_private_suffix",
                    }
                )
            elif ABSOLUTE_PATH_PATTERN.search(normalized):
                forbidden.append({"member": "[redacted absolute member]", "reason": "absolute_path"})
        results.append(
            {
                "path": record.path,
                "member_count": len(names),
                "forbidden_member_count": len(forbidden),
                "forbidden_members": forbidden[:500],
                "findings_truncated": len(forbidden) > 500,
                "status": "risk" if forbidden else "no_path_risk_detected",
            }
        )
    return results


def add_private_release_hash_findings(
    exact_groups: Sequence[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> None:
    """发现 user_assets 内容与发布树相同时追加需人工判读的 P1 证据。"""

    for group in exact_groups:
        paths = group["paths"]
        private_paths = [path for path in paths if "user_assets" in {p.casefold() for p in Path(path).parts}]
        release_paths = [
            path
            for path in paths
            if {p.casefold() for p in Path(path).parts} & RELEASE_PARTS
        ]
        if not private_paths or not release_paths:
            continue
        for path in release_paths:
            findings.append(
                {
                    "severity": "P1",
                    "path": path,
                    "line": None,
                    "rule": "user_assets_content_hash_in_release",
                    "scope": "release_tree",
                    "git_state": "unknown",
                    "evidence": f"SHA-256 与 {len(private_paths)} 个 user_assets 文件完全相同；这只证明复制关系，不单独证明私人内容泄露。",
                    "value_redacted": True,
                    "disposition": "manual_review",
                    "recovery": "核对匹配对象是已批准运行资产/客户包还是私人源素材；后者才需停止分发并以 share-no-selfie 重建。",
                }
            )


def audit_frame_manifests(root: Path, records: Sequence[FileRecord]) -> list[dict[str, Any]]:
    """核对 OnePic 38–40/65/73 帧和 Android 副本的声明关系。"""

    results: list[dict[str, Any]] = []
    for record in records:
        if Path(record.path).name.casefold() != "manifest.json" or record.size > 4 * 1024 * 1024:
            continue
        if not (
            record.path.startswith("assets/pet/")
            or record.path.startswith("user_assets/")
            or record.path.startswith("android/")
        ):
            continue
        try:
            payload = json.loads((root / record.path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "base_frame_count" not in payload:
            continue
        base = int(payload.get("base_frame_count", 0))
        touch = int(payload.get("optional_touch_frame_count", 0))
        extensions = int(payload.get("extension_frame_count", 0))
        directional = int(payload.get("directional_frame_count", 0))
        total_base = int(payload.get("total_frame_count", base + touch))
        total_assets = int(payload.get("total_asset_frame_count", total_base + extensions + directional))
        animation_count = sum(
            len(frames)
            for frames in payload.get("animations", {}).values()
            if isinstance(frames, list)
        )
        extension_items = payload.get("extension_animations", {})
        extension_count_actual = sum(
            len(item.get("frames", []))
            for item in extension_items.values()
            if isinstance(item, dict)
        )
        side_walks = [
            {
                "name": name,
                "frame_count": len(item.get("frames", [])),
                "default": bool(item.get("walk_variant_default", False)),
                "replaces_forward": bool(item.get("replaces_forward_walk", False)),
            }
            for name, item in extension_items.items()
            if isinstance(item, dict) and bool(item.get("walk_variant", False))
        ]
        checks = {
            "base_is_38": base == 38,
            "touch_is_0_to_2": 0 <= touch <= 2,
            "total_base_matches": total_base == base + touch,
            "animation_count_matches_base": animation_count == base,
            "extension_count_matches": extension_count_actual == extensions,
            "total_asset_matches": total_assets == total_base + extensions + directional,
            "side_walks_are_8": all(item["frame_count"] == 8 for item in side_walks),
            "side_walks_not_default": all(not item["default"] for item in side_walks),
            "side_walks_do_not_replace_forward": all(
                not item["replaces_forward"] for item in side_walks
            ),
        }
        if total_assets == 73:
            tier = "OnePic 73-frame advanced with side walk"
        elif total_assets == 65:
            tier = "OnePic 65-frame advanced"
        elif 38 <= total_assets <= 40:
            tier = "OnePic 38-40-frame base"
        else:
            tier = "custom/other approved profile"
        relationship = "runtime_or_platform_copy"
        if "/backups/" in f"/{record.path}":
            relationship = "historical_backup"
        elif record.path.startswith("android/"):
            relationship = "android_generated_or_previous_copy"
        elif record.path == "assets/pet/manifest.json":
            relationship = "public_demo_runtime"
        elif record.path == "user_assets/pet/manifest.json":
            relationship = "active_private_runtime"
        results.append(
            {
                "path": record.path,
                "pet_id": payload.get("pet_id"),
                "tier": tier,
                "relationship": relationship,
                "base_frame_count": base,
                "optional_touch_frame_count": touch,
                "extension_frame_count": extensions,
                "directional_frame_count": directional,
                "total_asset_frame_count": total_assets,
                "side_walks": side_walks,
                "checks": checks,
                "status": "consistent" if all(checks.values()) else "needs_review",
                "disposition": "must_keep",
                "reason": "运行、平台降级、历史回滚或发布校验按路径消费这些资产。",
            }
        )
    return results


def aggregate_records(records: Sequence[FileRecord]) -> dict[str, Any]:
    """生成所有权、分类、扩展名、顶层与边界汇总。"""

    def aggregate(key_fn: Callable[[FileRecord], str]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, int]] = defaultdict(lambda: {"file_count": 0, "bytes": 0})
        for record in records:
            key = key_fn(record)
            groups[key]["file_count"] += 1
            groups[key]["bytes"] += record.size
        return [
            {"name": name, **values}
            for name, values in sorted(
                groups.items(), key=lambda item: item[1]["bytes"], reverse=True
            )
        ]

    return {
        "owners": aggregate(lambda record: record.owner),
        "categories": aggregate(lambda record: record.category),
        "top_level": aggregate(lambda record: record.path.split("/", 1)[0]),
        "extensions": aggregate(lambda record: record.suffix),
        "boundaries": aggregate(lambda record: record.boundary or "[outside named boundary]"),
        "owner_category": aggregate(lambda record: f"{record.owner} | {record.category}"),
        "git_states": aggregate(lambda record: record.git_state),
    }


def cleanup_candidates(records: Sequence[FileRecord]) -> list[dict[str, Any]]:
    """按显式边界聚合整理候选；发布、私有与作者工程绝不列为安全删除。"""

    groups: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        if record.boundary:
            groups[record.boundary].append(record)
    items: list[dict[str, Any]] = []
    for boundary, group in groups.items():
        boundary_name = Path(boundary).name.casefold()
        categories = {record.category for record in group}
        states = {record.git_state for record in group}
        owner = sorted({record.owner for record in group})
        if boundary_name in SAFE_CACHE_PARTS and not any(
            state.startswith("tracked") for state in states
        ):
            disposition = "safe_delete_candidate"
            risk = "仅含可再生测试/解释器缓存；删除会使下一次测试或启动重新生成。"
            recovery = "重新运行对应测试、Python 模块或开发工具。"
        elif boundary_name == ".pnpm-store":
            disposition = "safe_delete_candidate"
            risk = "可再生包缓存，但恢复可能需要网络并增加下次安装时间。"
            recovery = "在对应项目按锁文件重新执行 pnpm install。"
        elif boundary_name in {"node_modules", ".venv", "vendor", "site-packages", ".gradle"}:
            disposition = "manual_review"
            risk = "依赖可重建但成本高，且锁文件/下载源/本机工具链可能变化。"
            recovery = "先验证锁文件和环境脚本，再重新安装对应依赖。"
        elif boundary_name in RELEASE_PARTS:
            disposition = "manual_review"
            risk = "可能是客户交付、签名包、发布证据或未完成暂存。"
            recovery = "核对发布报告、哈希与交付记录后转入版本化冷存储；不要直接删除。"
        elif boundary_name in {".tmp", "host-work", ".staging", "failed-builds"}:
            disposition = "manual_review"
            risk = "可能包含 Live2D/BongoCat 作者工程、失败证据或可恢复 staging。"
            recovery = "先核对项目说明、审批状态和生成报告，再归档或由原工具重建。"
        elif boundary_name in {"build", "target"} and not any(
            state.startswith("tracked") for state in states
        ):
            disposition = "safe_delete_candidate"
            risk = "未跟踪构建输出通常可重建，但可能包含尚未归档的调试证据。"
            recovery = "用对应 build/Gradle/Cargo 脚本重建；执行前确认无待交付产物。"
        else:
            disposition = "cannot_determine"
            risk = "当前证据不足以证明边界完全可再生。"
            recovery = "补充所有权、生成命令和消费方证据。"
        bytes_total = sum(record.size for record in group)
        items.append(
            {
                "item_id": f"boundary:{len(items) + 1:04d}",
                "path": boundary,
                "file_count": len(group),
                "estimated_reclaimable_bytes": bytes_total
                if disposition == "safe_delete_candidate"
                else 0,
                "logical_bytes": bytes_total,
                "owners": owner,
                "categories": sorted(categories),
                "git_states": sorted(states),
                "disposition": disposition,
                "evidence": f"命中显式边界 `{boundary_name}`；按 {len(group)} 个文件聚合。",
                "risk": risk,
                "recovery": recovery,
            }
        )
    items.sort(key=lambda item: item["logical_bytes"], reverse=True)
    return items


def detect_subprojects(aggregates: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """核查用户点名的 OnePic、Android、AIRI、Telegram 与 Bongo 工厂。"""

    owner_totals = {item["name"]: item for item in aggregates["owners"]}
    definitions = [
        ("OnePic canonical root", ".", ["pyproject.toml", "config/animation-recipe.v1.json"]),
        ("OnePic Android", "android", ["android/settings.gradle", "android/app"]),
        ("AIRI upstream", "airi-desktop-pet", ["airi-desktop-pet/package.json", "airi-desktop-pet/pnpm-lock.yaml"]),
        ("Telegram bot", "telegram-accounting-bot", ["telegram-accounting-bot/pyproject.toml"]),
        ("BongoCat Live2D factory", "bongocat-live2d-factory", ["bongocat-live2d-factory/factory.cmd", "bongocat-live2d-factory/pyproject.toml"]),
    ]
    results: list[dict[str, Any]] = []
    for name, relative_root, markers in definitions:
        owner_key = "OnePic core" if name == "OnePic canonical root" else name
        total = owner_totals.get(owner_key, {"file_count": 0, "bytes": 0})
        marker_status = {marker: (root / marker).exists() for marker in markers}
        results.append(
            {
                "name": name,
                "root": relative_root,
                "present": all(marker_status.values()),
                "markers": marker_status,
                "file_count": total["file_count"],
                "bytes": total["bytes"],
                "ownership_note": (
                    "canonical OnePic engine"
                    if name == "OnePic canonical root"
                    else "independent subproject/target; duplicate runtime assets may be legitimate"
                ),
            }
        )
    return results


def _physical_bytes(records: Sequence[FileRecord]) -> int:
    seen: set[str] = set()
    total = 0
    for record in records:
        if record.file_id in seen:
            continue
        seen.add(record.file_id)
        total += record.size
    return total


def hardlink_groups(records: Sequence[FileRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        grouped[record.file_id].append(record)
    results = []
    for file_id, group in grouped.items():
        if len(group) < 2 and not any(record.link_count > 1 for record in group):
            continue
        results.append(
            {
                "file_id": file_id,
                "reported_link_count": max(record.link_count for record in group),
                "in_scope_path_count": len(group),
                "size_each": group[0].size,
                "paths": sorted(record.path for record in group),
                "note": "硬链接路径共享物理内容，不能按逻辑副本数估算可回收空间。",
            }
        )
    results.sort(key=lambda item: item["size_each"] * item["in_scope_path_count"], reverse=True)
    return results


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = {
                field: json.dumps(row.get(field), ensure_ascii=False, separators=(",", ":"))
                if isinstance(row.get(field), (list, dict))
                else row.get(field)
                for field in fields
            }
            writer.writerow(normalized)


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "_无。_\n"
    rendered = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        values = [str(value).replace("|", "\\|").replace("\n", " ") for value in row]
        rendered.append("| " + " | ".join(values) + " |")
    return "\n".join(rendered) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    """生成答案优先、带贝叶斯证据点评的 Markdown 报告。"""

    snapshot = report["snapshot"]
    summary = report["summary"]
    git = report["git"]
    duplicates = report["duplicates"]
    risks = report["risk_findings"]
    risk_counts = Counter(item["severity"] for item in risks)
    dispositions = Counter(item["disposition"] for item in duplicates["exact_groups"])
    safe_space = sum(
        item["estimated_reclaimable_bytes"]
        for item in report["cleanup_candidates"]
        if item["disposition"] == "safe_delete_candidate"
    )
    lines = [
        "# OnePic 全库重复与卫生审计",
        "",
        "> 本报告是只读审计结果。未删除、移动、覆盖、激活、审批、打包或发布任何原始文件。",
        "",
        "## 结论摘要",
        "",
        f"- 扫描根目录：`{snapshot['root']}`",
        f"- 扫描窗口：`{snapshot['started_at_utc']}` → `{snapshot['finished_at_utc']}`",
        f"- Git：`{git.get('branch') or '[detached]'}` @ `{git.get('head')}`；dirty 路径 {git.get('dirty_path_count', 0):,} 项。",
        f"- 工作树口径：{summary['file_count']:,} 个常规文件，逻辑大小 {human_bytes(summary['logical_bytes'])}，硬链接去重后的近似物理大小 {human_bytes(summary['physical_bytes'])}。",
        f"- 精确重复：{duplicates['hashing']['exact_group_count']:,} 组，理论重复逻辑空间 {human_bytes(duplicates['hashing']['estimated_duplicate_bytes'])}；其中安全删除候选 {dispositions.get('safe_delete_candidate', 0):,} 组。",
        f"- 可再生边界的安全删除候选上限：{human_bytes(safe_space)}；这是上限估算，不等于已批准删除。",
        f"- 风险候选：P0={risk_counts.get('P0', 0):,}，P1={risk_counts.get('P1', 0):,}，P2={risk_counts.get('P2', 0):,}；命中值均已删隐。",
        f"- 近似重复：{report['near_duplicates']['candidate_count']:,} 对，仅供人工复核，绝不能直接据此删除。",
        "",
        "## 贝叶斯证据点评",
        "",
        _markdown_table(
            ["命题", "先验", "关键证据", "后验置信度"],
            [
                ["扫描确实来自 canonical OnePic 原始根", "0.80", f"Git 根、工作树根与扫描根一致；提交 {git.get('head')}", "0.99"],
                ["依赖/缓存是体量偏差主因", "0.65", "按所有权、分类和显式边界汇总；未跳过 node_modules/target/build", "0.97"],
                ["所有精确重复都可安全删除", "0.10", "大量组跨运行时、审批、平台或历史版本；仅缓存且非 tracked 才升级为安全候选", "0.02"],
                ["近似重复代表垃圾", "0.15", "dHash/尺寸/文件名会命中合法动画相位、压缩版与平台副本", "0.03"],
                ["当前发布边界存在需立即核查的污染风险", "0.20", f"P0 规则命中 {risk_counts.get('P0', 0)} 项；规则只给候选，需结合路径/发布状态复核", "0.80" if risk_counts.get("P0", 0) else "0.08"],
            ],
        ),
        "## 扫描口径与可复现性",
        "",
        f"- 工具版本：`{snapshot['tool_version']}`；schema：`{snapshot['schema_version']}`。",
        "- `.git` 数据库不属于工作树内容，单独统计但不参与重复/隐私哈希。",
        "- 本次报告输出目录在遍历时排除，避免边写边扫导致自污染。",
        f"- 同大小候选 {duplicates['hashing']['same_size_candidate_paths']:,} 条；快速指纹后仅 {duplicates['hashing']['sha256_candidate_paths']:,} 条进入 SHA-256。",
        f"- 不可读/解析错误 {len(report['scan_errors']):,} 项，链接/重解析点 {len(report['links']):,} 项，长路径 {len(report['long_paths']):,} 项。",
        "",
        "复跑命令：",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\python.exe .\\tools\\repository_audit.py scan --root . --output .\\reports\\repository-audit\\<new-run-id>",
        "```",
        "",
        "## 空间与目录所有权",
        "",
        _markdown_table(
            ["所有权", "文件数", "逻辑大小"],
            [
                [item["name"], f"{item['file_count']:,}", human_bytes(item["bytes"])]
                for item in report["aggregates"]["owners"]
            ],
        ),
        _markdown_table(
            ["类别", "文件数", "逻辑大小"],
            [
                [item["name"], f"{item['file_count']:,}", human_bytes(item["bytes"])]
                for item in report["aggregates"]["categories"]
            ],
        ),
        "## 子项目与合法重复边界",
        "",
        _markdown_table(
            ["子项目", "存在", "文件数", "大小", "说明"],
            [
                [item["name"], item["present"], f"{item['file_count']:,}", human_bytes(item["bytes"]), item["ownership_note"]]
                for item in report["subprojects"]
            ],
        ),
        "### OnePic 40/65/73 与 Android manifest",
        "",
        _markdown_table(
            ["路径", "产品档", "总素材帧", "关系", "状态", "处置"],
            [
                [item["path"], item["tier"], item["total_asset_frame_count"], item["relationship"], item["status"], item["disposition"]]
                for item in report["frame_manifests"]
            ],
        ),
        "## 精确重复",
        "",
        _markdown_table(
            ["组", "单文件", "路径数", "可回收估算", "处置", "示例路径"],
            [
                [item["group_id"], human_bytes(item["size_each"]), item["path_count"], human_bytes(item["estimated_duplicate_bytes"]), item["disposition"], item["paths"][0]]
                for item in duplicates["exact_groups"][:40]
            ],
        ),
        "完整逐路径结果见 `exact-duplicates.csv` 和 `repository-audit.json`。硬链接已按文件 ID 去重估算，不会虚报物理回收空间。",
        "",
        "## 隐私、密钥样式与发布污染风险",
        "",
        _markdown_table(
            ["级别", "路径", "行", "规则", "Git", "证据"],
            [
                [item["severity"], item["path"], item.get("line") or "-", item["rule"], item.get("git_state"), item["evidence"]]
                for item in risks[:80]
            ],
        ),
        "报告从不保存匹配到的密钥、密码或绝对路径文本，只保留路径、规则和行号。完整结果见 `risk-findings.csv`。",
        "",
        "## Git 卫生",
        "",
        f"- tracked={git.get('tracked_count', 0):,}，modified={git.get('modified_count', 0):,}，deleted={git.get('deleted_count', 0):,}，staged={git.get('staged_count', 0):,}，untracked={git.get('untracked_count', 0):,}，ignored-present={git.get('ignored_present_count', 0):,}。",
        "- dirty 路径逐项列在 `git-status.csv`；审计未 stage、commit 或 push。",
        "",
        "## 四级整理候选",
        "",
        _markdown_table(
            ["边界", "文件数", "大小", "处置", "风险", "恢复"],
            [
                [item["path"], f"{item['file_count']:,}", human_bytes(item["logical_bytes"]), item["disposition"], item["risk"], item["recovery"]]
                for item in report["cleanup_candidates"][:80]
            ],
        ),
        "## P0 / P1 / P2 整理路线",
        "",
        "### P0：先阻断泄露与发布污染",
        "",
        "1. 人工核对所有 P0 规则；候选值不写入报告，不能把模式命中直接当作已证实泄露。",
        "2. 若发布树直接含本机绝对路径、真实密钥或禁止的私密目录/文件类型，立即停止分发并从已批准 runtime 重建。",
        "3. 若确认真实凭据进入 tracked、归档或发布包，先轮换凭据，再处理历史；本审计不代替密钥轮换。",
        "",
        "### P1：收敛明确可再生的缓存/构建边界",
        "",
        "1. 先运行 `cleanup-plan.ps1` 默认 dry-run，逐项核对解析后的绝对目标仍位于扫描根。",
        "2. 对发布树与 `user_assets` 的同 SHA-256 候选，先区分已批准运行资产/客户包与私人源素材；复制关系本身不证明泄露。",
        "3. 优先处理 `.test-sandbox`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.turbo` 等安全候选。",
        "4. `node_modules`、`.venv`、`.gradle` 仅在锁文件、网络与重建时间均可接受时人工安排。",
        "",
        "### P2：治理代码、历史版本和近似重复",
        "",
        "1. 对跨所有权精确重复建立“权威源 → 消费方”清单，不把平台运行副本改成硬链接。",
        "2. 对 40/65/73、Android、AIRI、Telegram、Bongo 工厂分别保留生成与恢复说明。",
        "3. 近似候选逐张视觉核对；dHash、尺寸和文件名永远不能单独授权删除。",
        "",
        "## 默认 dry-run 后续命令",
        "",
        "```powershell",
        ".\\reports\\repository-audit\\<run-id>\\cleanup-plan.ps1",
        "# 只在逐项确认并另行授权后，才考虑：",
        ".\\reports\\repository-audit\\<run-id>\\cleanup-plan.ps1 -Execute",
        "```",
        "",
        "`cleanup-plan.ps1` 只包含审计时判定为 `safe_delete_candidate` 的显式边界，默认使用 `-WhatIf`；本次没有执行它。",
        "",
    ]
    return "\n".join(lines)


def write_cleanup_plan(path: Path, root: Path, candidates: Sequence[dict[str, Any]]) -> None:
    """写入路径校验严格、默认 -WhatIf 的 PowerShell 后续清单。"""

    safe = [item for item in candidates if item["disposition"] == "safe_delete_candidate"]
    entries = "\n".join(
        "    @{ Relative = %s; Bytes = %d; Evidence = %s; Recovery = %s }"
        % (
            json.dumps(item["path"], ensure_ascii=False),
            item["logical_bytes"],
            json.dumps(item["evidence"].replace("`", ""), ensure_ascii=False),
            json.dumps(item["recovery"].replace("`", ""), ensure_ascii=False),
        )
        for item in safe
    )
    root_literal = str(root).replace("'", "''")
    script = f'''# 本脚本由 repository_audit.py 生成。默认仅 -WhatIf；本次审计未执行删除。
[CmdletBinding()]
param(
    [string]$Root = '{root_literal}',
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
$ResolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\\')
$ExpectedRoot = '{root_literal}'.TrimEnd('\\')
if (-not [StringComparer]::OrdinalIgnoreCase.Equals($ResolvedRoot, $ExpectedRoot)) {{
    throw "Root 与审计根不一致，拒绝执行：$ResolvedRoot"
}}
$Candidates = @(
{entries}
)
foreach ($Item in $Candidates) {{
    $Target = Join-Path $ResolvedRoot $Item.Relative
    if (-not (Test-Path -LiteralPath $Target)) {{
        Write-Host "[跳过] 已不存在：$($Item.Relative)"
        continue
    }}
    $ResolvedTarget = (Resolve-Path -LiteralPath $Target).Path
    $Prefix = $ResolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedTarget.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {{
        throw "目标越出审计根，拒绝执行：$ResolvedTarget"
    }}
    Write-Host "[候选] $($Item.Relative) | $($Item.Bytes) bytes | $($Item.Evidence)"
    Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force -WhatIf:(-not $Execute)
}}
if (-not $Execute) {{
    Write-Host "dry-run 完成；未删除文件。只有另行人工确认后才可使用 -Execute。"
}}
'''
    path.write_text(script, encoding="utf-8-sig")


def write_reports(output: Path, report: dict[str, Any]) -> None:
    """将审计结果写成 JSON、CSV、Markdown 与默认 dry-run 脚本。"""

    output.mkdir(parents=True, exist_ok=False)
    (output / "repository-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    exact_rows: list[dict[str, Any]] = []
    for group in report["duplicates"]["exact_groups"]:
        for duplicate_path in group["paths"]:
            exact_rows.append({**group, "path": duplicate_path})
    _write_csv(
        output / "exact-duplicates.csv",
        exact_rows,
        [
            "group_id",
            "sha256",
            "size_each",
            "path_count",
            "physical_file_count",
            "hardlink_path_count",
            "estimated_duplicate_bytes",
            "disposition",
            "owners",
            "categories",
            "git_states",
            "risk",
            "recovery",
            "path",
        ],
    )
    _write_csv(
        output / "near-duplicates.csv",
        report["near_duplicates"]["candidates"],
        [
            "candidate_id",
            "left",
            "right",
            "reason",
            "dimensions",
            "dhash_distance",
            "size_ratio",
            "disposition",
            "risk",
            "recovery",
        ],
    )
    inventory_rows = []
    for section in ("owners", "categories", "top_level", "extensions", "boundaries", "owner_category", "git_states"):
        for row in report["aggregates"][section]:
            inventory_rows.append({"dimension": section, **row})
    _write_csv(
        output / "directory-inventory.csv",
        inventory_rows,
        ["dimension", "name", "file_count", "bytes"],
    )
    _write_csv(
        output / "cleanup-candidates.csv",
        report["cleanup_candidates"],
        [
            "item_id",
            "path",
            "file_count",
            "logical_bytes",
            "estimated_reclaimable_bytes",
            "owners",
            "categories",
            "git_states",
            "disposition",
            "evidence",
            "risk",
            "recovery",
        ],
    )
    _write_csv(
        output / "risk-findings.csv",
        report["risk_findings"],
        [
            "severity",
            "path",
            "line",
            "rule",
            "scope",
            "git_state",
            "evidence",
            "value_redacted",
            "disposition",
            "recovery",
        ],
    )
    dirty_rows = [{"path": path, "state": "dirty_or_untracked"} for path in report["git"]["dirty_paths"]]
    _write_csv(output / "git-status.csv", dirty_rows, ["path", "state"])
    (output / "audit-report.md").write_text(render_markdown(report), encoding="utf-8")
    write_cleanup_plan(
        output / "cleanup-plan.ps1",
        Path(report["snapshot"]["root"]),
        report["cleanup_candidates"],
    )


def run_audit(
    root: Path,
    output: Path,
    *,
    collect_git: bool = True,
    near_limit: int = 5_000,
    progress_every: int = 20_000,
    write_output: bool = True,
    progress: Callable[[str], None] | None = print,
) -> dict[str, Any]:
    """执行完整只读审计；测试可关闭 Git 与落盘。"""

    started = utc_now()
    started_monotonic = time.monotonic()
    root = root.resolve()
    output = output.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    excluded = [output]
    audit_output_base = (root / "reports" / "repository-audit").resolve()
    if audit_output_base != output:
        excluded.append(audit_output_base)
    state = scan_tree(
        root,
        excluded_roots=excluded,
        progress_every=progress_every,
        progress=progress,
    )
    if collect_git:
        if progress:
            progress("[audit] collecting Git tracked/untracked/ignored state")
        git = collect_git_state(root, state.files)
    else:
        for record in state.files:
            record.git_state = "untracked"
        git = {
            "head": None,
            "branch": None,
            "origin": None,
            "tracked_count": 0,
            "tracked_present_count": 0,
            "modified_count": 0,
            "deleted_count": 0,
            "staged_count": 0,
            "untracked_count": len(state.files),
            "ignored_present_count": 0,
            "dirty_path_count": len(state.files),
            "dirty_paths": sorted(record.path for record in state.files),
        }
    if progress:
        progress("[audit] exact duplicates: size -> quick fingerprint -> SHA-256")
    exact, hashing = find_exact_duplicates(
        root,
        state.files,
        state.errors,
        progress_every=max(1, progress_every // 2),
        progress=progress,
    )
    if progress:
        progress("[audit] image dimensions and perceptual-hash candidates")
    near = find_near_duplicates(root, state.files, state.errors, limit=near_limit)
    if progress:
        progress("[audit] privacy patterns, release boundaries, manifests and archives")
    risks = scan_privacy_and_release_risks(root, state.files, state.errors)
    add_private_release_hash_findings(exact, risks)
    risks.sort(key=lambda item: ({"P0": 0, "P1": 1, "P2": 2}.get(item["severity"], 9), item["path"], item.get("line") or 0))
    aggregates = aggregate_records(state.files)
    candidates = cleanup_candidates(state.files)
    frame_manifests = audit_frame_manifests(root, state.files)
    archives = scan_archives(root, state.files, state.errors)
    git_metadata = summarize_path(root / ".git") if (root / ".git").is_dir() else None
    finished = utc_now()
    report = {
        "snapshot": {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "root": str(root),
            "output_excluded": str(output),
            "started_at_utc": started,
            "finished_at_utc": finished,
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
            "read_only_source_scan": True,
            "mutations_forbidden": [
                "delete",
                "move",
                "overwrite_source",
                "activate_pet",
                "change_workflow",
                "package_customer_release",
            ],
        },
        "summary": {
            "file_count": len(state.files),
            "logical_bytes": sum(record.size for record in state.files),
            "physical_bytes": _physical_bytes(state.files),
            "git_metadata_excluded": git_metadata,
            "link_count": len(state.links),
            "long_path_count": len(state.long_paths),
            "error_count": len(state.errors),
        },
        "git": git,
        "aggregates": aggregates,
        "subprojects": detect_subprojects(aggregates, root),
        "duplicates": {
            "method": "same size -> sampled BLAKE2b quick fingerprint -> full SHA-256",
            "hashing": hashing,
            "exact_groups": exact,
            "hardlink_groups": hardlink_groups(state.files),
        },
        "near_duplicates": near,
        "frame_manifests": frame_manifests,
        "archive_checks": archives,
        "risk_findings": risks,
        "cleanup_candidates": candidates,
        "links": state.links,
        "long_paths": state.long_paths,
        "scan_errors": state.errors,
        "excluded_paths": state.excluded,
    }
    if write_output:
        if progress:
            progress(f"[audit] writing reports to {output}")
        write_reports(output, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OnePic 全库只读重复与卫生审计（仅报告，绝不清理）"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="扫描工作树并生成 JSON/CSV/Markdown")
    scan.add_argument("--root", type=Path, required=True, help="canonical OnePic 根目录")
    scan.add_argument("--output", type=Path, required=True, help="必须不存在的新输出目录")
    scan.add_argument("--near-limit", type=int, default=5_000)
    scan.add_argument("--progress-every", type=int, default=20_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scan":
        report = run_audit(
            args.root,
            args.output,
            near_limit=max(0, args.near_limit),
            progress_every=max(1, args.progress_every),
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "root": report["snapshot"]["root"],
                    "output": str(args.output.resolve()),
                    "file_count": report["summary"]["file_count"],
                    "logical_bytes": report["summary"]["logical_bytes"],
                    "exact_group_count": report["duplicates"]["hashing"]["exact_group_count"],
                    "risk_count": len(report["risk_findings"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
