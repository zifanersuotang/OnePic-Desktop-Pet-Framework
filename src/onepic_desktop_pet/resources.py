"""
本模块统一定位桌面宠物在源码环境和 PyInstaller 打包环境中的资源文件。

职责范围：
- 判断当前是否运行于 PyInstaller 临时解包目录；
- 将相对资源路径解析为绝对路径；
- 对缺失资源给出明确异常，不负责加载或修改图片内容。

Agent 快速定位：
- 项目或打包资源根目录由 resource_root() 返回；
- 业务模块统一通过 resource_path() 获取素材和默认配置路径。

输入为项目相对路径，输出为已验证存在的 pathlib.Path。
本模块仅访问文件元数据，不修改文件、不访问网络。
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """返回源码项目根目录或 PyInstaller 解包资源根目录。"""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parents[2]


def resource_path(relative_path: str | Path) -> Path:
    """解析并验证资源路径，缺失时抛出 FileNotFoundError。"""

    path = resource_root() / Path(relative_path)
    if not path.exists():
        raise FileNotFoundError(f"缺少应用资源：{path}")
    return path
