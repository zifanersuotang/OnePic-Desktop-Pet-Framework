"""
通过 See-through 官方 Hugging Face Space 对公开演示图执行单图语义拆层。

本工具只服务于无人值守的公开素材实验，安全边界如下：
- 输入必须位于项目内，且不得位于 ``user_assets``；
- 输出只能写入 ``.tmp/assisted-rigging-runs``；
- 不修改宠物运行目录、``workflow.json``、配置或当前激活素材；
- 只从官方 Space 同源地址下载 PSD 和预览图；
- 审计报告只记录项目相对路径、哈希和公开服务地址，不记录本机绝对路径。

官方免费演示使用 ZeroGPU，可能需要排队或受每日额度限制。此工具不会绕过额度、
登录、确认门槛或服务端错误；失败时会保留清晰的错误信息，供稍后安全重试。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPACE_URL = "https://24yearsold-see-through-demo.hf.space/"
OUTPUT_ROOT = Path(".tmp/assisted-rigging-runs")
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class SeeThroughSpaceError(RuntimeError):
    """表示路径、网络、队列或输出不满足 See-through 安全实验契约。"""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def validate_paths(
    source: Path,
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, Path, Path]:
    """解析并验证公开输入与隔离输出目录。"""

    root = project_root.resolve()
    source_path = source.resolve()
    target = output_dir.resolve(strict=False)
    allowed_output_root = (root / OUTPUT_ROOT).resolve(strict=False)

    if not source_path.is_file():
        raise SeeThroughSpaceError(f"输入图片不存在：{source}")
    if source_path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise SeeThroughSpaceError(
            "官方演示输入只接受 PNG、JPEG 或 WebP 图片。"
        )
    if source_path.is_symlink():
        raise SeeThroughSpaceError("无人值守实验不接受符号链接输入。")
    if not _is_relative_to(source_path, root):
        raise SeeThroughSpaceError("输入图片必须位于项目目录内。")
    if _is_relative_to(source_path, root / "user_assets"):
        raise SeeThroughSpaceError(
            "禁止把 user_assets 上传到外部服务；请使用公开演示素材。"
        )
    if not _is_relative_to(target, allowed_output_root):
        raise SeeThroughSpaceError(
            "See-through 输出只能写入 .tmp/assisted-rigging-runs。"
        )
    return source_path, target, root


def _parse_sse(lines: Iterable[str]) -> list[Any]:
    """读取 Gradio 队列 SSE，返回 ``complete`` 事件的数据。"""

    event_name = ""
    data_lines: list[str] = []

    def finish_event() -> list[Any] | None:
        nonlocal event_name, data_lines
        if not event_name:
            data_lines = []
            return None
        payload_text = "\n".join(data_lines)
        current_event = event_name
        event_name = ""
        data_lines = []
        if current_event == "error":
            raise SeeThroughSpaceError(
                f"See-through 服务端任务失败：{payload_text or '未知错误'}"
            )
        if current_event != "complete":
            return None
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise SeeThroughSpaceError(
                "See-through 返回了无法解析的完成事件。"
            ) from exc
        if not isinstance(payload, list):
            raise SeeThroughSpaceError("See-through 完成事件不是预期的结果数组。")
        return payload

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            result = finish_event()
            if result is not None:
                return result
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())

    result = finish_event()
    if result is not None:
        return result
    raise SeeThroughSpaceError("See-through 队列连接结束，但没有收到完成事件。")


def _same_origin(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return (
        parsed.scheme == "https"
        and parsed.scheme == base.scheme
        and parsed.netloc == base.netloc
    )


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip(".-")
    return cleaned or fallback


def _download(
    session: requests.Session,
    url: str,
    destination: Path,
    *,
    base_url: str,
    timeout_seconds: int,
) -> None:
    if not _same_origin(url, base_url):
        raise SeeThroughSpaceError(
            f"拒绝下载非官方 Space 同源输出：{urlparse(url).netloc}"
        )
    with session.get(
        url,
        stream=True,
        timeout=(30, timeout_seconds),
    ) as response:
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    stream.write(chunk)


def _file_url(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    return url if isinstance(url, str) and url else None


def run_space_inference(
    source: Path,
    output_dir: Path,
    *,
    resolution: int = 768,
    seed: int = 42,
    tblr_split: bool = False,
    base_url: str = DEFAULT_SPACE_URL,
    project_root: Path = PROJECT_ROOT,
    timeout_seconds: int = 1800,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """上传公开图片、等待官方推理，并保存 PSD、图层预览和审计报告。"""

    source_path, target, root = validate_paths(
        source,
        output_dir,
        project_root=project_root,
    )
    if resolution < 768 or resolution > 1280:
        raise SeeThroughSpaceError("官方演示分辨率必须在 768 到 1280 之间。")
    if not 0 <= seed <= 9999:
        raise SeeThroughSpaceError("随机种子必须在 0 到 9999 之间。")

    normalized_base = base_url.rstrip("/") + "/"
    client = session or requests.Session()
    target.mkdir(parents=True, exist_ok=True)

    mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    with source_path.open("rb") as stream:
        upload_response = client.post(
            urljoin(normalized_base, "gradio_api/upload"),
            files={"files": (source_path.name, stream, mime_type)},
            timeout=(30, timeout_seconds),
        )
    upload_response.raise_for_status()
    uploaded = upload_response.json()
    if not isinstance(uploaded, list) or not uploaded or not isinstance(uploaded[0], str):
        raise SeeThroughSpaceError("官方 Space 未返回有效的上传文件标识。")

    call_response = client.post(
        urljoin(normalized_base, "gradio_api/call/inference"),
        json={
            "data": [
                {
                    "path": uploaded[0],
                    "orig_name": source_path.name,
                    "mime_type": mime_type,
                    "meta": {"_type": "gradio.FileData"},
                },
                resolution,
                seed,
                tblr_split,
            ]
        },
        timeout=(30, 120),
    )
    call_response.raise_for_status()
    event_id = call_response.json().get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise SeeThroughSpaceError("官方 Space 未返回有效的队列事件编号。")

    with client.get(
        urljoin(normalized_base, f"gradio_api/call/inference/{event_id}"),
        stream=True,
        timeout=(30, timeout_seconds),
    ) as queue_response:
        queue_response.raise_for_status()
        result = _parse_sse(
            line.decode("utf-8", errors="replace")
            if isinstance(line, bytes)
            else line
            for line in queue_response.iter_lines()
        )

    if not result:
        raise SeeThroughSpaceError("See-through 返回了空结果。")
    psd_url = _file_url(result[0])
    if psd_url is None:
        raise SeeThroughSpaceError("See-through 结果中缺少 PSD 下载地址。")

    psd_path = target / "see-through-output.psd"
    _download(
        client,
        psd_url,
        psd_path,
        base_url=normalized_base,
        timeout_seconds=timeout_seconds,
    )

    layer_records: list[dict[str, Any]] = []
    gallery = result[1] if len(result) > 1 and isinstance(result[1], list) else []
    for index, gallery_item in enumerate(gallery, start=1):
        if not isinstance(gallery_item, dict):
            continue
        image_item = gallery_item.get("image")
        image_url = _file_url(image_item)
        if image_url is None:
            continue
        original_name = (
            image_item.get("orig_name")
            if isinstance(image_item, dict)
            else None
        )
        filename = _safe_filename(
            str(original_name or f"layer-{index:02d}.png"),
            f"layer-{index:02d}.png",
        )
        layer_path = target / "layers" / filename
        _download(
            client,
            image_url,
            layer_path,
            base_url=normalized_base,
            timeout_seconds=timeout_seconds,
        )
        caption = gallery_item.get("caption")
        layer_records.append(
            {
                "caption": caption if isinstance(caption, str) else None,
                "path": _relative(layer_path, root),
                "sha256": _sha256(layer_path),
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_kind": "see-through-official-space-inference",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": _relative(source_path, root),
            "sha256": _sha256(source_path),
        },
        "inference": {
            "service": normalized_base,
            "resolution": resolution,
            "seed": seed,
            "tblr_split": tblr_split,
        },
        "output": {
            "psd": _relative(psd_path, root),
            "psd_sha256": _sha256(psd_path),
            "layer_preview_count": len(layer_records),
        },
        "layers": layer_records,
        "privacy": {
            "user_assets_read": False,
            "public_asset_uploaded": True,
            "absolute_paths_recorded": False,
            "runtime_modified": False,
        },
    }
    report_path = target / "see-through-space-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用 See-through 官方 Space 拆分项目内公开角色图。"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=768)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tblr-split", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        report = run_space_inference(
            args.source,
            args.output_dir,
            resolution=args.resolution,
            seed=args.seed,
            tblr_split=args.tblr_split,
            timeout_seconds=args.timeout_seconds,
        )
    except (SeeThroughSpaceError, requests.RequestException, ValueError) as exc:
        print(f"[失败] {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
