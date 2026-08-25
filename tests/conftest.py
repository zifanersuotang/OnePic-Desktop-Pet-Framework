"""
本模块把 pytest 的文件写入限制在项目内可访问的测试沙箱。

Windows 沙箱中 pytest 自带 tmp_path 会以 0o700 创建目录并移除运行身份所需 ACL，
因此这里使用普通继承权限的新目录，不清理、不修改真实 user_assets 或用户设置。
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ROOT = Path(
    os.environ.get(
        "ONEPIC_TEST_SANDBOX",
        PROJECT_ROOT / ".test-sandbox" / "pytest-runs",
    )
).resolve() / f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
SESSION_ROOT.mkdir(parents=True, exist_ok=False)

os.environ.setdefault("ONEPIC_USER_ROOT", str(SESSION_ROOT / "user_assets"))
os.environ.setdefault("ONEPIC_SETTINGS_DIR", str(SESSION_ROOT / "settings"))
os.environ.setdefault("LOCALAPPDATA", str(SESSION_ROOT / "localappdata"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """返回继承项目 ACL 的唯一目录，避开 pytest 的 0o700 basetemp。"""

    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", request.node.name)[:32]
    path = SESSION_ROOT / f"{safe_name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path
