"""只读检查 OnePic 的框架身份、发布边界与常见隐私泄漏。

运行方式：
    python scripts/check_framework.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "project_type": "desktop_pet",
    "framework_id": (
        "io.github.zifanersuotang."
        "onepic-desktop-pet-framework.py312-pyside6.v1"
    ),
    "runtime": "python_3.12_pyside6",
    "renderer": "qt_widgets_sprite_with_source_only_live2d_adapter",
    "platform": "windows_10_11",
    "entry": "main.py",
}
PROHIBITED_FILES = {
    "assets/live2d_web/live2d.bundle.js",
    "assets/live2d_web/live2dcubismcore.min.js",
}
PROHIBITED_SUFFIXES = {".moc3", ".model3.json", ".psd"}
PRIVATE_MARKERS = (
    b"C:" + b"\\Users\\" + b"17615",
    b"1761525999" + b"@" + b"qq.com",
)
TEXT_SUFFIXES = {
    ".cs",
    ".css",
    ".gradle",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def tracked_files() -> list[Path]:
    """返回 Git 跟踪文件；源码归档中没有 Git 时使用受控枚举。"""

    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(
                part in {".git", ".venv", "build", "dist", "node_modules"}
                for part in path.relative_to(ROOT).parts
            )
        ]
    return [
        ROOT / value.decode("utf-8")
        for value in result.stdout.split(b"\0")
        if value
    ]


def main() -> int:
    """执行检查并以非零状态报告阻断项。"""

    errors: list[str] = []
    config_path = ROOT / "project.config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"project.config.json 无法读取：{exc}")
        config = {}
    for key, value in EXPECTED.items():
        if config.get(key) != value:
            errors.append(
                f"project.config.json 的 {key} 应为 {value!r}，"
                f"实际为 {config.get(key)!r}"
            )

    try:
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
    except (OSError, KeyError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"pyproject.toml 无法读取：{exc}")
        project = {}
    if project.get("requires-python") != ">=3.12,<3.13":
        errors.append("Python 运行时边界必须保持 >=3.12,<3.13")

    files = tracked_files()
    relative_files = {
        path.relative_to(ROOT).as_posix()
        for path in files
        if path.is_file() and path.is_relative_to(ROOT)
    }
    for name in sorted(PROHIBITED_FILES & relative_files):
        errors.append(f"公开树包含禁止分发的 Live2D 运行文件：{name}")
    for name in sorted(relative_files):
        path = Path(name)
        if (
            name.startswith(("build/", "dist/", ".venv/"))
            or "/node_modules/" in f"/{name}"
            or (
                name.startswith("user_assets/")
                and name != "user_assets/README.md"
            )
        ):
            errors.append(f"公开树包含私有或生成路径：{name}")
        if path.suffix.lower() in PROHIBITED_SUFFIXES:
            errors.append(f"公开树包含未登记的模型/源素材：{name}")

    for path in files:
        if (
            not path.is_file()
            or path.suffix.lower() not in TEXT_SUFFIXES
            or path.stat().st_size > 2_000_000
        ):
            continue
        data = path.read_bytes()
        for marker in PRIVATE_MARKERS:
            if marker in data:
                errors.append(
                    f"文本包含隐私或密钥标记："
                    f"{path.relative_to(ROOT).as_posix()}"
                )
                break

    spec = (ROOT / "OnePicDesktopPet.spec").read_text(encoding="utf-8")
    if '("assets", "assets")' in spec:
        errors.append("默认 spec 仍会打包完整 assets 目录")
    if "live2dcubismcore" in spec.lower():
        errors.append("默认 spec 仍包含 Live2D Core")
    for excluded_module in (
        "onepic_desktop_pet.live2d_view",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ):
        if f'"{excluded_module}"' not in spec:
            errors.append(f"默认 spec 未排除可选运行模块：{excluded_module}")

    print("[OnePic Framework Check]")
    print(f"根目录: {ROOT}")
    print(f"跟踪文件: {len(relative_files)}")
    if errors:
        for error in errors:
            print(f"- ERROR: {error}")
        print(f"结论: 失败（{len(errors)} 个阻断项）")
        return 1
    print("结论: 通过；框架身份、默认发布边界与隐私标记均正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
