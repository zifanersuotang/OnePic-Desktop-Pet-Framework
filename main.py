"""
本模块是 Windows 桌面宠物应用的命令行启动入口。

职责范围：
- 解析可选的 `--smoke-test-ms` 自动退出参数；
- 为源码与成品启动烟测写入 pet-id、准备批次和可执行文件哈希证据；
- 为发布包提供可在成品 EXE 内运行的真实双击快捷聊天烟测入口；
- 成品烟测强制核对包内客户 manifest 路径与预期 pet-id，禁止回退 demo；
- 调用 onepic_desktop_pet.app.run() 启动 Qt 应用；
- 不包含窗口、行为或配置业务逻辑。

Agent 快速定位：
- 命令行参数在 parse_args() 中定义；
- 主入口位于 main()；
- 具体应用生命周期请阅读 src/onepic_desktop_pet/app.py。

输入为命令行参数，输出为进程退出码。正常启动会显示桌面窗口和托盘图标，退出时应用
可能在当前用户本地应用数据目录保存窗口位置。

使用示例：
    py main.py
    py main.py --smoke-test-ms 1500
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from onepic_desktop_pet.app import run
from onepic_desktop_pet.packaged_smoke import (
    distribution_tree_sha256,
    run_double_click_chat_smoke,
)
from onepic_desktop_pet.resources import resource_path


def _file_sha256(path: Path) -> str:
    """流式计算启动进程映像的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_basic_smoke_report(
    output: Path,
    *,
    exit_code: int,
    expected_pet_id: str | None,
    smoke_test_ms: int,
) -> bool:
    """把一次真实启动结果绑定到当前资源、pet-id 与进程映像。"""

    frozen = bool(getattr(sys, "frozen", False))
    executable = Path(sys.executable).resolve()
    report: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "exit_code": exit_code,
        "smoke_test_ms": smoke_test_ms,
        "frozen": frozen,
        "executable_sha256": _file_sha256(executable),
        "distribution_sha256": (
            distribution_tree_sha256(executable.parent) if frozen else None
        ),
        "expected_pet_id": expected_pet_id,
    }
    try:
        manifest_path = resource_path("user_assets/pet/manifest.json").resolve()
        pointer_path = resource_path("user_assets/active-pet.json").resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or not isinstance(pointer, dict):
            raise TypeError("manifest 与 active-pet.json 必须是 JSON 对象")
        report.update(
            {
                "manifest": str(manifest_path),
                "actual_pet_id": manifest.get("pet_id"),
                "build_id": manifest.get("build_id"),
                "runtime_manifest_sha256": _file_sha256(manifest_path),
                "preparation_id": pointer.get("preparation_id"),
            }
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        report["evidence_error"] = f"{type(exc).__name__}: {exc}"
    passed = bool(
        exit_code == 0
        and expected_pet_id
        and report.get("actual_pet_id") == expected_pet_id
        and "evidence_error" not in report
    )
    report["status"] = "passed" if passed else "failed"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return passed


def parse_args() -> argparse.Namespace:
    """解析启动参数。"""

    parser = argparse.ArgumentParser(description="启动 Windows 桌面宠物")
    parser.add_argument(
        "--smoke-test-ms",
        type=int,
        default=None,
        help="在指定毫秒后自动退出，仅用于启动验证",
    )
    parser.add_argument(
        "--smoke-report",
        type=Path,
        help="把启动烟测的可执行文件哈希与 pet-id 证据写入 JSON",
    )
    parser.add_argument(
        "--smoke-double-click-chat-output",
        type=Path,
        help="成品双击聊天烟测 JSON 输出路径",
    )
    parser.add_argument(
        "--smoke-double-click-chat-capture",
        type=Path,
        help="成品双击聊天烟测截图路径",
    )
    parser.add_argument(
        "--smoke-double-click-chat-timeout-ms",
        type=int,
        default=35_000,
        help="成品双击聊天烟测超时毫秒数",
    )
    parser.add_argument(
        "--smoke-expected-pet-id",
        help="启动或双击聊天烟测必须加载的客户 pet-id",
    )
    return parser.parse_args()


def main() -> int:
    """启动应用并返回 Qt 事件循环退出码。"""

    args = parse_args()
    smoke_output = args.smoke_double_click_chat_output
    smoke_capture = args.smoke_double_click_chat_capture
    if (smoke_output is None) != (smoke_capture is None):
        raise ValueError("双击聊天烟测必须同时提供 JSON 输出与截图路径")
    if smoke_output is not None and smoke_capture is not None:
        if not args.smoke_expected_pet_id:
            raise ValueError("成品双击聊天烟测必须提供 --smoke-expected-pet-id")
        return run_double_click_chat_smoke(
            smoke_output,
            smoke_capture,
            expected_pet_id=args.smoke_expected_pet_id,
            timeout_ms=max(1, args.smoke_double_click_chat_timeout_ms),
        )
    if args.smoke_report is not None and args.smoke_test_ms is None:
        raise ValueError("--smoke-report 必须与 --smoke-test-ms 一起使用")
    exit_code = run(smoke_test_ms=args.smoke_test_ms)
    if args.smoke_report is not None:
        passed = _write_basic_smoke_report(
            args.smoke_report,
            exit_code=exit_code,
            expected_pet_id=args.smoke_expected_pet_id,
            smoke_test_ms=max(1, args.smoke_test_ms or 1),
        )
        return 0 if passed else 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
