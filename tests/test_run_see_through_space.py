"""
验证 See-through 官方 Space 调用器的路径、SSE 和同源下载安全边界。

测试不会连接网络、不会读取 ``user_assets``，也不会修改宠物运行目录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.run_see_through_space import (
    SeeThroughSpaceError,
    _parse_sse,
    _same_origin,
    validate_paths,
)


def test_validate_paths_accepts_public_asset_and_isolated_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "assets" / "demo" / "public.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"public-demo")
    output = root / ".tmp" / "assisted-rigging-runs" / "space"

    resolved_source, resolved_output, resolved_root = validate_paths(
        source,
        output,
        project_root=root,
    )

    assert resolved_source == source.resolve()
    assert resolved_output == output.resolve()
    assert resolved_root == root.resolve()


def test_validate_paths_rejects_private_asset(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "user_assets" / "pets" / "private" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"private")

    with pytest.raises(SeeThroughSpaceError, match="user_assets"):
        validate_paths(
            source,
            root / ".tmp" / "assisted-rigging-runs" / "space",
            project_root=root,
        )


def test_validate_paths_rejects_runtime_output(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "assets" / "demo" / "public.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"public-demo")

    with pytest.raises(SeeThroughSpaceError, match="\\.tmp/assisted-rigging-runs"):
        validate_paths(
            source,
            root / "assets" / "pet" / "generated",
            project_root=root,
        )


def test_parse_sse_returns_complete_payload() -> None:
    expected = [{"url": "https://example.test/output.psd"}, []]
    payload = json.dumps(expected)

    assert _parse_sse(
        [
            "event: heartbeat",
            "data: null",
            "",
            "event: complete",
            f"data: {payload}",
            "",
        ]
    ) == expected


def test_parse_sse_raises_for_error_event() -> None:
    with pytest.raises(SeeThroughSpaceError, match="服务端任务失败"):
        _parse_sse(["event: error", 'data: "quota exceeded"', ""])


def test_download_origin_requires_official_https_host() -> None:
    base = "https://24yearsold-see-through-demo.hf.space/"

    assert _same_origin(
        "https://24yearsold-see-through-demo.hf.space/gradio_api/file=x",
        base,
    )
    assert not _same_origin("http://24yearsold-see-through-demo.hf.space/x", base)
    assert not _same_origin("https://example.test/x", base)
