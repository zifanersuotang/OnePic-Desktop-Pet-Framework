# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


# 默认 Windows Sprite 发布只携带运行所需演示帧、图标和配置。
# Live2D 适配源码可供研究，但仓库不分发 Cubism Core 或预构建 Web bundle；
# 只有完成独立授权审查后，维护者才可在私有发布流程中显式追加相关文件。
datas = [
    ("assets/pet", "assets/pet"),
    ("assets/icons", "assets/icons"),
    ("config", "config"),
]
private_assets = Path(
    os.environ.get("ONEPIC_PACKAGE_USER_ROOT", "user_assets")
)
include_private_assets = (
    os.environ.get("ONEPIC_INCLUDE_USER_ASSETS") == "1"
    and private_assets.exists()
)
icon_path = Path("assets/icons/pet.png")
if include_private_assets:
    # 分享版只携带运行所需文件，避免把原图、历史角色和备份误装入发布目录。
    active_pointer = private_assets / "active-pet.json"
    pointer_data = __import__("json").loads(active_pointer.read_text(encoding="utf-8"))
    package_pet_id = pointer_data["pet_id"]
    workflow_asset = private_assets / "pets" / package_pet_id / "workflow.json"
    datas.extend(
        [
            (str(active_pointer), "user_assets"),
            (str(workflow_asset), f"user_assets/pets/{package_pet_id}"),
            (str(private_assets / "pet"), "user_assets/pet"),
        ]
    )
    if (
        os.environ.get("ONEPIC_INCLUDE_SELFIE_ASSET") == "1"
        and (private_assets / "selfie.png").is_file()
    ):
        datas.append((str(private_assets / "selfie.png"), "user_assets"))
    private_icon = private_assets / "pet" / "icon.png"
    if private_icon.is_file():
        icon_path = private_icon

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "onepic_desktop_pet.live2d_view",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)
# 本程序不使用 Qt Quick Controls；PySide6 hook 默认收集的 FluentWinUI3
# 主题包含 140+ 字符的资源路径，会让 Windows 资源管理器在较深目录解压失败。
# 只排除该未使用主题，保留 Qt Widgets 和其余 Qt 运行文件。
a.datas = [
    item
    for item in a.datas
    if "PySide6/qml/QtQuick/Controls/FluentWinUI3/"
    not in item[0].replace("\\", "/")
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OnePicDesktopPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(icon_path)],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OnePicDesktopPet",
)
