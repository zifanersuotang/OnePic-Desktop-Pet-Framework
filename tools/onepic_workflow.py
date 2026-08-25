"""
本模块提供“一图桌宠”原图登记和人工确认门禁的命令行工具。

职责范围：
- 登记用户最初上传的原图，保留私有原始副本，并生成全分辨率自拍 PNG；
- 登记首张标准角色候选、生成风格和人物特色说明，等待用户在图形窗口中明确确认；
- 显式显示、置顶并激活人工确认窗口，避免确认进程在桌面后台无可见窗口；
- 提供仅绑定本机回环地址的浏览器确认页，作为桌面隔离时的图形审批兜底；
- 根据私有宠物清单生成走路 GIF 预览，等待用户确认动作自然度；
- 为静态或载具路线提交替代互动预览，并以证据哈希执行确认或否决 CAS；
- 迁移旧版审批时，同时绑定旧 GIF 证据和目标工作区的八帧素材束；
- 查询当前流程状态，并为动作生成或打包执行门禁检查。

Agent 快速定位：
- 原图登记位于 register_source()；
- 标准角色候选和确认位于 register_character_candidate()、review_character_candidate()；
- 走路预览和确认位于 create_walk_review()、approve_walk()；
- 替代互动闭环位于 submit_interaction_preview()、approve_interaction() 和
  reject_interaction()；
- 命令行入口位于 main()。

输入为用户本地图片和私有素材清单，输出只写入 Git 忽略的 `user_assets/`。
工具不访问网络、不修改公开 `assets/`，覆盖已有自拍或流程状态前会先写入私有备份目录。

使用示例：
    py tools/onepic_workflow.py init --source user_assets/image.png
    py tools/onepic_workflow.py character-candidate --image candidate.png --feature 长辫子
    py tools/onepic_workflow.py approve-character
    py tools/onepic_workflow.py walk-review
    py tools/pet_workspace.py agent-status <pet-id>
    py tools/onepic_workflow.py status
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw, ImageOps

from onepic_desktop_pet.workflow import (
    WorkflowError,
    character_is_approved,
    custom_pet_is_approved,
    load_workflow,
    now_utc,
    require_character_approved,
    require_custom_pet_approved,
    save_workflow,
    migrate_legacy_workflow,
    walk_is_approved,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_ROOT = Path(
    os.environ.get("ONEPIC_USER_ROOT", PROJECT_ROOT / "user_assets")
).expanduser().resolve()
STATE_PATH = USER_ROOT / "workflow.json"
INTERACTION_MODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def configure_pet(pet_id: str | None) -> Path:
    """把命令切换到指定或当前活动宠物的独立工作流。"""

    global STATE_PATH
    selected = (pet_id or "").strip()
    if not selected:
        pointer = USER_ROOT / "active-pet.json"
        if pointer.is_file():
            data = json.loads(pointer.read_text(encoding="utf-8-sig"))
            selected = str(data.get("pet_id") or data.get("active_pet_id") or "")
    if selected:
        STATE_PATH = USER_ROOT / "pets" / selected / "workflow.json"
    return STATE_PATH


def private_root() -> Path:
    """返回当前工作流所属工作区；兼容旧版全局测试布局。"""

    return STATE_PATH.parent


def is_legacy_layout() -> bool:
    """返回当前是否仍在兼容旧版全局 user_assets/workflow.json。"""

    return private_root().parent.name != "pets"


def file_sha256(path: Path) -> str:
    """计算文件哈希，便于确认自拍照片确实来自登记原图。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files_sha256(paths: list[Path]) -> str:
    """按排序后的相对名和内容哈希计算确定性的素材束哈希。"""

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _relative(path: Path) -> str:
    """返回相对于项目根目录的正斜杠路径。"""

    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def _backup_if_present(path: Path) -> None:
    """覆盖私有文件前按时间保存可恢复副本。"""

    if not path.is_file():
        return
    backup_root = private_root() / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = now_utc()[:19].replace("-", "").replace(":", "")
    stem = path.stem[:24]
    shutil.copy2(path, backup_root / f"{stem}-{stamp}{path.suffix}")


def _open_valid_image(path: Path) -> Image.Image:
    """读取、纠正 EXIF 方向并返回与原图同分辨率的像素内容。"""

    try:
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"无法读取输入图片：{path}：{exc}") from exc


def register_source(source_path: Path) -> dict[str, Any]:
    """登记原图，同时保存原始文件副本和全分辨率自拍 PNG。"""

    source = source_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"找不到最初上传的原图：{source}")
    image = _open_valid_image(source)
    source_root = private_root() / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".img"
    original_copy = source_root / f"original{suffix}"
    selfie_path = (
        private_root() / "selfie.png"
        if is_legacy_layout()
        else source_root / "selfie.png"
    )
    _backup_if_present(original_copy)
    _backup_if_present(selfie_path)
    _backup_if_present(STATE_PATH)
    if source != original_copy.resolve():
        shutil.copy2(source, original_copy)
    image.save(selfie_path, "PNG", optimize=True)
    state: dict[str, Any] = {
        "version": 2,
        "pet_id": (
            private_root().name
            if private_root().parent.name == "pets"
            else "legacy"
        ),
        "created_at": now_utc(),
        "source": {
            "original": _relative(original_copy),
            "original_sha256": file_sha256(original_copy),
            "selfie": _relative(selfie_path),
            "selfie_pixel_size": list(image.size),
        },
        "character": {
            "status": "not_submitted",
            "candidate": None,
            "identity_features": [],
        },
        "walk": {"status": "not_generated", "preview": None},
        "interaction": {"status": "not_generated", "preview": None},
    }
    save_workflow(state, STATE_PATH)
    return state


def register_character_candidate(
    image_path: Path,
    features: list[str],
    style: str = "preserve_original",
) -> dict[str, Any]:
    """保存首张标准角色候选，并将流程停在等待用户确认状态。"""

    state = load_workflow(STATE_PATH)
    if not state.get("source"):
        raise WorkflowError("请先使用 init 命令登记最初上传的原图。")
    image = _open_valid_image(image_path.resolve())
    review_root = private_root() / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    candidate = review_root / "standard-character-candidate.png"
    _backup_if_present(candidate)
    image.save(candidate, "PNG", optimize=True)
    state["character"] = {
        "status": "awaiting_user_confirmation",
        "candidate": _relative(candidate),
        "candidate_sha256": file_sha256(candidate),
        "style": style,
        "identity_features": [item.strip() for item in features if item.strip()],
        "submitted_at": now_utc(),
    }
    state["walk"] = {"status": "not_generated", "preview": None}
    state["interaction"] = {"status": "not_generated", "preview": None}
    save_workflow(state, STATE_PATH)
    return state


def approve_character(
    confirmed: bool,
    confirmation_method: str = "direct_user_review",
) -> dict[str, Any]:
    """在用户看过候选并确认后锁定标准角色及其文件哈希。"""

    if not confirmed:
        raise WorkflowError("必须由用户查看标准角色候选图并明确确认。")
    state = load_workflow(STATE_PATH)
    character = state.get("character", {})
    candidate = character.get("candidate")
    if character.get("status") != "awaiting_user_confirmation" or not candidate:
        raise WorkflowError("当前没有等待确认的标准角色候选图。")
    candidate_path = PROJECT_ROOT / candidate
    candidate_hash = character.get("candidate_sha256")
    if not candidate_path.is_file() or file_sha256(candidate_path) != candidate_hash:
        raise WorkflowError("候选图在提交后发生变化，请重新登记并确认。")
    approved = private_root() / "candidate" / "approved.png"
    approved.parent.mkdir(parents=True, exist_ok=True)
    _backup_if_present(approved)
    shutil.copy2(PROJECT_ROOT / candidate, approved)
    character.update(
        {
            "status": "approved",
            "approved_image": _relative(approved),
            "approved_candidate_sha256": candidate_hash,
            "user_confirmed": True,
            "confirmation_method": confirmation_method,
            "approved_at": now_utc(),
        }
    )
    state["character"] = character
    save_workflow(state, STATE_PATH)
    return state


def reject_character(reason: str = "用户要求重新生成") -> dict[str, Any]:
    """撤销当前角色确认并保留旧文件，避免错误素材继续生成动作。"""

    state = load_workflow(STATE_PATH)
    if not state.get("source"):
        raise WorkflowError("尚未登记原图，无法重置标准角色。")
    _backup_if_present(STATE_PATH)
    character = state.get("character", {})
    character.update(
        {
            "status": "rejected",
            "user_confirmed": False,
            "rejected_reason": reason.strip() or "用户要求重新生成",
            "rejected_at": now_utc(),
        }
    )
    character.pop("approved_candidate_sha256", None)
    character.pop("approved_at", None)
    state["character"] = character
    state["walk"] = {"status": "not_generated", "preview": None}
    state["interaction"] = {"status": "not_generated", "preview": None}
    save_workflow(state, STATE_PATH)
    return state


def review_character_candidate() -> int:
    """显示标准角色候选图；返回 1 表示确认、2 表示要求重做、0 表示关闭。"""

    state = load_workflow(STATE_PATH)
    character = state.get("character", {})
    candidate = character.get("candidate")
    if character.get("status") != "awaiting_user_confirmation" or not candidate:
        raise WorkflowError("当前没有等待确认的标准角色候选图。")
    candidate_path = PROJECT_ROOT / candidate
    if not candidate_path.is_file():
        raise WorkflowError(f"标准角色候选图不存在：{candidate_path}")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
    )

    app = QApplication.instance() or QApplication([])
    dialog = QDialog()
    dialog.setWindowTitle("确认标准角色 · OnePic Desktop Pet")
    dialog.setMinimumWidth(520)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(dialog)
    title = QLabel("请确认：这张图是否保留了角色的辨识度和原有气质？")
    title.setWordWrap(True)
    layout.addWidget(title)
    image_label = QLabel()
    image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image_label.setPixmap(
        QPixmap(str(candidate_path)).scaled(
            460,
            560,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    layout.addWidget(image_label)
    style_labels = {
        "preserve_original": "保留原画风",
        "light_chibi": "轻度 Q 版",
        "full_chibi": "完整 Q 版",
    }
    details = QLabel(
        "生成风格："
        + style_labels.get(character.get("style"), character.get("style", "未记录"))
        + "\n身份特征："
        + "、".join(character.get("identity_features", []))
    )
    details.setWordWrap(True)
    layout.addWidget(details)
    buttons = QHBoxLayout()
    redo_button = QPushButton("不符合，重新生成")
    confirm_button = QPushButton("符合，这就是我要的角色")
    confirm_button.setDefault(True)
    redo_button.clicked.connect(lambda: dialog.done(2))
    confirm_button.clicked.connect(dialog.accept)
    buttons.addWidget(redo_button)
    buttons.addWidget(confirm_button)
    layout.addLayout(buttons)
    dialog.adjustSize()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    app.processEvents()
    result = dialog.exec()
    app.processEvents()
    return int(result)


def review_character_candidate_browser() -> int:
    """在仅限本机访问的浏览器页面中显示候选图并等待用户直接点击。"""

    state = load_workflow(STATE_PATH)
    character = state.get("character", {})
    candidate = character.get("candidate")
    if character.get("status") != "awaiting_user_confirmation" or not candidate:
        raise WorkflowError("当前没有等待确认的标准角色候选图。")
    candidate_path = PROJECT_ROOT / candidate
    if not candidate_path.is_file():
        raise WorkflowError(f"标准角色候选图不存在：{candidate_path}")

    expected_hash = str(character.get("candidate_sha256") or "")
    token = secrets.token_urlsafe(24)
    result = {"value": 0}
    style_labels = {
        "preserve_original": "保留原画风",
        "light_chibi": "轻度 Q 版",
        "full_chibi": "完整 Q 版",
    }
    style = style_labels.get(
        character.get("style"),
        str(character.get("style") or "未记录"),
    )
    features = "、".join(character.get("identity_features", []))

    class ApprovalHandler(BaseHTTPRequestHandler):
        """处理单次本地候选图确认，不记录或传输浏览数据。"""

        def _write(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            return secrets.compare_digest(query.get("token", [""])[0], token)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if not self._authorized():
                self._write(403, "text/plain; charset=utf-8", "无权访问".encode())
                return
            if parsed.path == "/candidate.png":
                self._write(200, "image/png", candidate_path.read_bytes())
                return
            if parsed.path != "/":
                self._write(404, "text/plain; charset=utf-8", b"Not found")
                return
            page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>确认标准角色 · OnePic Desktop Pet</title>
<style>
body{{margin:0;background:#eef1f5;color:#18202a;font:16px/1.55 "Microsoft YaHei",sans-serif}}
main{{max-width:920px;margin:24px auto;padding:24px;background:white;border-radius:18px;
box-shadow:0 12px 40px #1c273522}}
h1{{margin:0 0 8px;font-size:25px}} .note{{color:#536170;margin:0 0 18px}}
.preview{{display:flex;justify-content:center;background:linear-gradient(135deg,#f5f7fa,#e8edf3);
border-radius:14px;padding:18px;min-height:480px}}
.preview img{{max-width:100%;height:560px;object-fit:contain}}
.details{{margin:16px 0;padding:14px 16px;background:#f6f8fb;border-radius:10px}}
.actions{{display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap}}
button{{border:0;border-radius:10px;padding:12px 20px;font-size:16px;cursor:pointer}}
.redo{{background:#e9edf2;color:#26313c}} .approve{{background:#1769e0;color:white;font-weight:700}}
code{{word-break:break-all}}
</style></head><body><main>
<h1>确认标准角色 V4</h1>
<p class="note">此页面仅在你的电脑本机运行；点击结果会绑定下方候选图的 SHA-256。</p>
<div class="preview"><img src="/candidate.png?token={token}" alt="标准角色 V4"></div>
<div class="details"><div><b>生成风格：</b>{html.escape(style)}</div>
<div><b>身份特征：</b>{html.escape(features)}</div>
<div><b>候选哈希：</b><code>{html.escape(expected_hash)}</code></div></div>
<div class="actions">
<form method="post" action="/reject?token={token}"><button class="redo">不符合，重新生成</button></form>
<form method="post" action="/approve?token={token}"><button class="approve">符合，这就是我要的角色</button></form>
</div></main></body></html>"""
            self._write(200, "text/html; charset=utf-8", page.encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if not self._authorized():
                self._write(403, "text/plain; charset=utf-8", "无权访问".encode())
                return
            current = load_workflow(STATE_PATH).get("character", {})
            current_path = PROJECT_ROOT / str(current.get("candidate") or "")
            hash_matches = (
                current.get("status") == "awaiting_user_confirmation"
                and current_path.is_file()
                and current.get("candidate_sha256") == expected_hash
                and file_sha256(current_path) == expected_hash
            )
            if not hash_matches:
                self._write(
                    409,
                    "text/plain; charset=utf-8",
                    "候选图或流程状态已变化，请重新打开确认页。".encode(),
                )
                return
            if parsed.path == "/approve":
                result["value"] = 1
                message = "已收到确认，V4 正在锁定。可以返回 Codex。"
            elif parsed.path == "/reject":
                result["value"] = 2
                message = "已收到重做请求。可以返回 Codex。"
            else:
                self._write(404, "text/plain; charset=utf-8", b"Not found")
                return
            self._write(
                200,
                "text/html; charset=utf-8",
                (
                    "<!doctype html><meta charset='utf-8'><title>确认完成</title>"
                    "<body style='font:20px Microsoft YaHei;padding:48px'>"
                    f"<h1>{html.escape(message)}</h1></body>"
                ).encode("utf-8"),
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), ApprovalHandler) as server:
        port = int(server.server_address[1])
        url = f"http://127.0.0.1:{port}/?token={token}"
        print(f"本机确认页：{url}", flush=True)
        webbrowser.open(url, new=2)
        server.serve_forever(poll_interval=0.1)
    return int(result["value"])


def _review_frame(frame: Image.Image) -> Image.Image:
    """把透明走路帧放到带地面的浅色背景，便于识别滑步。"""

    rgba = frame.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (247, 247, 244, 255))
    draw = ImageDraw.Draw(canvas)
    ground_y = rgba.height - 15
    draw.line((20, ground_y, rgba.width - 20, ground_y), fill=(190, 166, 150, 255), width=2)
    canvas.alpha_composite(rgba)
    return canvas.convert("RGB")


def create_walk_review(manifest_path: Path) -> dict[str, Any]:
    """读取八帧走路素材并生成必须由用户查看的 GIF 预览。"""

    state = require_character_approved(STATE_PATH)
    manifest_file = manifest_path.resolve()
    if not manifest_file.is_file():
        raise FileNotFoundError(f"找不到私有宠物清单：{manifest_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    walk_paths = manifest.get("animations", {}).get("walk", [])
    if len(walk_paths) != 8:
        raise WorkflowError(f"走路预览必须恰好 8 帧，当前为 {len(walk_paths)} 帧。")
    frames: list[Image.Image] = []
    for relative in walk_paths:
        path = manifest_file.parent / relative
        if not path.is_file():
            raise FileNotFoundError(f"走路清单引用了缺失文件：{path}")
        with Image.open(path) as source:
            frames.append(_review_frame(source))
    review_root = private_root() / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    preview = review_root / "walk-preview.gif"
    _backup_if_present(preview)
    frames[0].save(
        preview,
        save_all=True,
        append_images=frames[1:],
        duration=90,
        loop=0,
        optimize=False,
    )
    asset_hash = files_sha256(
        [(manifest_file.parent / relative).resolve() for relative in walk_paths]
    )
    state["walk"] = {
        "status": "awaiting_user_confirmation",
        "preview": _relative(preview),
        "preview_sha256": file_sha256(preview),
        "asset_sha256": asset_hash,
        "asset_binding_kind": "walk_frame_bundle",
        "asset_paths": [
            _relative((manifest_file.parent / relative).resolve())
            for relative in walk_paths
        ],
        "frame_count": len(frames),
        "submitted_at": now_utc(),
        "review_checks": [
            "左右脚确实交替",
            "落脚、下压、迈步、抬升四阶段完整",
            "身体和头发存在自然起伏或滞后",
            "连续循环没有平移、滑步和跳帧",
        ],
    }
    save_workflow(state, STATE_PATH)
    return state


def approve_walk(
    confirmed: bool,
    *,
    expected_preview_sha256: str,
    expected_asset_sha256: str,
) -> dict[str, Any]:
    """在用户看过 GIF 后显式确认走路自然度。"""

    if not confirmed:
        raise WorkflowError("必须传入 --yes，且只能在用户查看走路 GIF 后执行。")
    state = require_character_approved(STATE_PATH)
    walk = state.get("walk", {})
    if walk.get("status") != "awaiting_user_confirmation":
        raise WorkflowError("当前没有等待确认的走路动态预览。")
    preview = walk.get("preview")
    preview_path = PROJECT_ROOT / str(preview or "")
    if not preview or not preview_path.is_file():
        raise WorkflowError("走路动态预览不存在，不能确认。")
    expected_preview_hash = walk.get("preview_sha256")
    if expected_preview_hash != expected_preview_sha256:
        raise WorkflowError("等待确认的走路预览已切换，请重新展示最新 GIF。")
    if (
        not expected_preview_hash
        or file_sha256(preview_path) != expected_preview_hash
    ):
        raise WorkflowError("走路动态预览已在提交后变化，请重新生成并查看。")
    asset_hash = walk.get("asset_sha256") or walk.get("preview_sha256")
    if not asset_hash:
        raise WorkflowError("走路预览缺少素材哈希，请重新生成预览。")
    if asset_hash != expected_asset_sha256:
        raise WorkflowError("等待确认的走路帧束已切换，请重新展示最新 GIF。")
    asset_paths = walk.get("asset_paths")
    if not isinstance(asset_paths, list) or len(asset_paths) != 8:
        raise WorkflowError("走路预览缺少当前八帧路径绑定，请重新生成预览。")
    current_paths = [PROJECT_ROOT / str(value) for value in asset_paths]
    if not all(path.is_file() for path in current_paths):
        raise WorkflowError("走路预览绑定的当前八帧不完整，请重新生成预览。")
    if files_sha256(current_paths) != asset_hash:
        raise WorkflowError("走路八帧已在展示后变化，请重新生成并查看 GIF。")
    walk.update(
        {
            "status": "approved",
            "user_confirmed": True,
            "approved_asset_sha256": asset_hash,
            "approved_at": now_utc(),
        }
    )
    state["walk"] = walk
    save_workflow(state, STATE_PATH)
    return state


def reject_walk(
    confirmed: bool,
    *,
    reason: str,
    expected_preview_sha256: str,
    expected_asset_sha256: str,
) -> dict[str, Any]:
    """以 CAS 记录用户对当前走路证据的否决和修改意见。"""

    if not confirmed:
        raise WorkflowError("拒绝走路预览必须传入 --yes 并记录用户原始反馈。")
    state = require_character_approved(STATE_PATH)
    walk = state.get("walk", {})
    if not isinstance(walk, dict) or walk.get("status") != "awaiting_user_confirmation":
        raise WorkflowError("当前没有等待反馈的走路动态预览。")
    if (
        walk.get("preview_sha256") != expected_preview_sha256
        or walk.get("asset_sha256") != expected_asset_sha256
    ):
        raise WorkflowError("等待反馈的走路证据已切换，请重新展示最新 GIF。")
    feedback = reason.strip()
    if not feedback:
        raise WorkflowError("拒绝走路预览时必须记录具体原因。")
    walk.update(
        {
            "status": "rejected",
            "user_confirmed": False,
            "rejected_preview_sha256": expected_preview_sha256,
            "rejected_asset_sha256": expected_asset_sha256,
            "rejection_reason": feedback,
            "rejected_at": now_utc(),
        }
    )
    state["walk"] = walk
    save_workflow(state, STATE_PATH)
    return state


def submit_interaction_preview(
    *,
    mode: str,
    preview_path: Path,
) -> dict[str, Any]:
    """把替代互动证据复制到当前工作区，并停在等待用户确认状态。"""

    state = require_character_approved(STATE_PATH)
    interaction_mode = mode.strip()
    if INTERACTION_MODE_PATTERN.fullmatch(interaction_mode) is None:
        raise WorkflowError("替代互动 mode 只能包含小写字母、数字、下划线或连字符。")
    source = preview_path.resolve()
    if not source.is_file():
        raise WorkflowError(f"替代互动预览不存在：{source}")
    suffix = source.suffix.lower()
    if suffix not in {".gif", ".png", ".webp", ".mp4", ".mov", ".mkv"}:
        raise WorkflowError("替代互动预览必须是 GIF、PNG、WebP 或常见视频文件。")
    source_hash = file_sha256(source)
    character_hash = str(
        state.get("character", {}).get("candidate_sha256") or ""
    )
    if not character_hash:
        raise WorkflowError("标准角色审批缺少候选哈希，不能提交互动预览。")
    previous = state.get("interaction", {})
    if (
        isinstance(previous, dict)
        and previous.get("status") == "rejected"
        and previous.get("rejected_preview_sha256") == source_hash
        and previous.get("rejected_character_sha256") == character_hash
        and previous.get("rejected_mode", previous.get("mode"))
        == interaction_mode
    ):
        raise WorkflowError("被否决的互动证据没有变化；请先按反馈修改再重新提交。")
    feedback_history = []
    if isinstance(previous, dict):
        prior_history = previous.get("feedback_history", [])
        if isinstance(prior_history, list):
            feedback_history.extend(
                item for item in prior_history if isinstance(item, dict)
            )
        if previous.get("status") == "rejected":
            feedback_history.append(
                {
                    "mode": previous.get("rejected_mode", previous.get("mode")),
                    "preview_sha256": previous.get("rejected_preview_sha256"),
                    "character_sha256": previous.get("rejected_character_sha256"),
                    "reason": previous.get("rejection_reason"),
                    "rejected_at": previous.get("rejected_at"),
                }
            )
    review_root = private_root() / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    preview = review_root / f"interaction-preview{suffix}"
    _backup_if_present(preview)
    if source != preview.resolve():
        shutil.copy2(source, preview)
    preview_sha256 = file_sha256(preview)
    state["interaction"] = {
        "status": "awaiting_user_confirmation",
        "mode": interaction_mode,
        "preview": _relative(preview),
        "preview_sha256": preview_sha256,
        "character_sha256": character_hash,
        "feedback_history": feedback_history[-20:],
        "submitted_at": now_utc(),
        "review_checks": [
            "互动只表达当前声明的单一行为",
            "角色身份、肢体和道具在整个预览中一致",
            "循环、停留与反馈节奏自然且无跳帧",
        ],
    }
    save_workflow(state, STATE_PATH)
    return state


def _current_interaction_submission(
    state: dict[str, Any],
    expected_preview_sha256: str,
    expected_mode: str,
) -> tuple[dict[str, Any], Path, str]:
    """以 CAS 校验当前待确认互动、角色和磁盘预览仍是同一份证据。"""

    interaction = state.get("interaction", {})
    if (
        not isinstance(interaction, dict)
        or interaction.get("status") != "awaiting_user_confirmation"
    ):
        raise WorkflowError("当前没有等待反馈的替代互动预览。")
    recorded_hash = str(interaction.get("preview_sha256") or "")
    if recorded_hash != expected_preview_sha256:
        raise WorkflowError("等待反馈的替代互动证据已切换，请重新展示最新预览。")
    if interaction.get("mode") != expected_mode:
        raise WorkflowError("等待反馈的替代互动模式已切换，请重新展示最新预览。")
    preview_value = interaction.get("preview")
    preview = (PROJECT_ROOT / str(preview_value or "")).resolve()
    workspace_root = private_root().resolve()
    if (
        not preview_value
        or not preview.is_file()
        or (preview != workspace_root and workspace_root not in preview.parents)
    ):
        raise WorkflowError("替代互动预览不存在，请重新提交。")
    if file_sha256(preview) != recorded_hash:
        raise WorkflowError("替代互动预览已在提交后变化，请重新提交并展示。")
    character_hash = str(
        state.get("character", {}).get("candidate_sha256") or ""
    )
    if not character_hash or interaction.get("character_sha256") != character_hash:
        raise WorkflowError("标准角色已在互动预览提交后变化，请重新制作并提交预览。")
    return interaction, preview, character_hash


def approve_interaction(
    confirmed: bool,
    *,
    expected_preview_sha256: str,
    expected_mode: str,
) -> dict[str, Any]:
    """以 CAS 确认用户实际查看的当前替代互动证据。"""

    if not confirmed:
        raise WorkflowError(
            "必须传入 --yes，且只能在用户查看替代互动预览后执行。"
        )
    state = require_character_approved(STATE_PATH)
    interaction, _preview, character_hash = _current_interaction_submission(
        state,
        expected_preview_sha256,
        expected_mode,
    )
    interaction.update(
        {
            "status": "approved",
            "approved_asset_sha256": expected_preview_sha256,
            "approved_character_sha256": character_hash,
            "approved_mode": expected_mode,
            "user_confirmed": True,
            "confirmation_method": "direct_user_review",
            "approved_at": now_utc(),
        }
    )
    state["interaction"] = interaction
    save_workflow(state, STATE_PATH)
    return state


def reject_interaction(
    confirmed: bool,
    *,
    reason: str,
    expected_preview_sha256: str,
    expected_mode: str,
) -> dict[str, Any]:
    """以 CAS 记录用户对当前替代互动证据的否决和原始反馈。"""

    if not confirmed:
        raise WorkflowError("拒绝替代互动必须传入 --yes 并记录用户原始反馈。")
    feedback = reason.strip()
    if not feedback:
        raise WorkflowError("拒绝替代互动时必须记录具体原因。")
    state = require_character_approved(STATE_PATH)
    interaction, _preview, character_hash = _current_interaction_submission(
        state,
        expected_preview_sha256,
        expected_mode,
    )
    interaction.update(
        {
            "status": "rejected",
            "user_confirmed": False,
            "rejected_preview_sha256": expected_preview_sha256,
            "rejected_character_sha256": character_hash,
            "rejected_mode": expected_mode,
            "rejection_reason": feedback,
            "rejected_at": now_utc(),
        }
    )
    state["interaction"] = interaction
    save_workflow(state, STATE_PATH)
    return state


def print_status(state: dict[str, Any]) -> None:
    """打印适合用户和 Agent 阅读的简短状态。"""

    source = state.get("source")
    references = source.get("references", []) if isinstance(source, dict) else []
    if not references:
        source_status = "未登记"
    elif any(
        item.get("relationship") in {
            "same_subject",
            "same_subject_derived_view",
        }
        for item in references
        if isinstance(item, dict)
    ):
        source_status = "已登记"
    else:
        source_status = "仅历史身份锚点（无原始自拍）"
    print(f"原图：{source_status}")
    print(f"标准角色：{state.get('character', {}).get('status', '未开始')}")
    print(f"走路预览：{state.get('walk', {}).get('status', '未开始')}")
    print(f"替代互动：{state.get('interaction', {}).get('status', '未开始')}")
    print(f"允许批量生成动作：{'是' if character_is_approved(state) else '否'}")
    print(
        "允许加载和打包本地桌宠："
        f"{'是' if custom_pet_is_approved(STATE_PATH) else '否'}"
    )


def parse_args() -> argparse.Namespace:
    """解析子命令参数。"""

    parser = argparse.ArgumentParser(description="管理一图桌宠的人工确认门禁")
    parser.add_argument(
        "--pet-id",
        help="目标宠物工作区；省略时使用 active-pet.json",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="登记原图并自动设置自拍照片")
    initialize.add_argument("--source", type=Path, required=True)
    candidate = commands.add_parser("character-candidate", help="登记首张标准角色候选")
    candidate.add_argument("--image", type=Path, required=True)
    candidate.add_argument("--feature", action="append", default=[])
    candidate.add_argument(
        "--style",
        choices=("preserve_original", "light_chibi", "full_chibi"),
        default="preserve_original",
    )
    approve_character_command = commands.add_parser(
        "approve-character",
        help="打开窗口确认标准角色",
    )
    approve_character_command.add_argument(
        "--browser",
        action="store_true",
        help="使用仅限本机访问的浏览器确认页",
    )
    reset = commands.add_parser("reset-character", help="撤销错误角色并等待重新生成")
    reset.add_argument("--reason", default="用户要求重新生成")
    review = commands.add_parser("walk-review", help="生成八帧走路动态预览")
    review.add_argument(
        "--manifest",
        type=Path,
        default=USER_ROOT / "pet" / "manifest.json",
    )
    approve_motion = commands.add_parser("approve-walk", help="确认走路动态预览")
    approve_motion.add_argument("--yes", action="store_true")
    approve_motion.add_argument("--expected-preview-sha256", required=True)
    approve_motion.add_argument("--expected-asset-sha256", required=True)
    reject_motion = commands.add_parser("reject-walk", help="否决当前走路动态预览")
    reject_motion.add_argument("--yes", action="store_true")
    reject_motion.add_argument("--reason", required=True)
    reject_motion.add_argument("--expected-preview-sha256", required=True)
    reject_motion.add_argument("--expected-asset-sha256", required=True)
    interaction_review = commands.add_parser(
        "interaction-review",
        help="提交静止或载具路线的替代互动预览并等待确认",
    )
    interaction_review.add_argument("--mode", required=True)
    interaction_review.add_argument("--preview", type=Path, required=True)
    approve_interaction_command = commands.add_parser(
        "approve-interaction",
        help="以当前证据哈希确认替代互动预览",
    )
    approve_interaction_command.add_argument("--yes", action="store_true")
    approve_interaction_command.add_argument(
        "--expected-preview-sha256",
        required=True,
    )
    approve_interaction_command.add_argument("--expected-mode", required=True)
    reject_interaction_command = commands.add_parser(
        "reject-interaction",
        help="以当前证据哈希否决替代互动预览",
    )
    reject_interaction_command.add_argument("--yes", action="store_true")
    reject_interaction_command.add_argument("--reason", required=True)
    reject_interaction_command.add_argument(
        "--expected-preview-sha256",
        required=True,
    )
    reject_interaction_command.add_argument("--expected-mode", required=True)
    commands.add_parser("check-actions", help="检查是否允许批量生成动作")
    commands.add_parser("check-package", help="检查是否允许加载或打包私有桌宠")
    commands.add_parser("status", help="显示当前制作流程状态")
    migrate = commands.add_parser("migrate-legacy", help="迁移旧版全局 workflow")
    migrate.add_argument("legacy_pet_id")
    return parser.parse_args()


def main() -> int:
    """执行指定流程命令并返回进程退出码。"""

    args = parse_args()
    try:
        if args.command == "migrate-legacy":
            legacy = USER_ROOT / "workflow.json"
            legacy_state = load_workflow(legacy)
            preview_hash = None
            walk_asset_hash = None
            interaction_hash = None
            walk_preview = legacy_state.get("walk", {}).get("preview")
            if walk_preview and (PROJECT_ROOT / walk_preview).is_file():
                preview_hash = file_sha256(PROJECT_ROOT / walk_preview)
            try:
                from pet_workspace import (
                    file_bundle_sha256,
                    load_config,
                    workspace_path,
                )

                workspace = workspace_path(args.legacy_pet_id)
                config = load_config(workspace)
                walk_root = workspace / str(config["walk_directory"])
                walk_paths = [
                    walk_root / f"walk_{index:02d}.png"
                    for index in range(1, 9)
                ]
                if all(path.is_file() for path in walk_paths):
                    walk_asset_hash = file_bundle_sha256(walk_paths)
            except (FileNotFoundError, ValueError):
                walk_asset_hash = None
            interaction_preview = legacy_state.get("interaction", {}).get("preview")
            if interaction_preview and (PROJECT_ROOT / interaction_preview).is_file():
                interaction_hash = file_sha256(PROJECT_ROOT / interaction_preview)
            destination, backup = migrate_legacy_workflow(
                USER_ROOT,
                args.legacy_pet_id,
                preview_sha256=preview_hash,
                walk_asset_sha256=walk_asset_hash,
                interaction_sha256=interaction_hash,
            )
            print(f"已迁移：{destination}")
            print(f"旧状态备份：{backup}")
            return 0
        configure_pet(args.pet_id)
        if args.command == "init":
            state = register_source(args.source)
        elif args.command == "character-candidate":
            state = register_character_candidate(args.image, args.feature, args.style)
        elif args.command == "approve-character":
            review_result = (
                review_character_candidate_browser()
                if args.browser
                else review_character_candidate()
            )
            if review_result == 1:
                confirmation_method = (
                    "local_browser_user_review"
                    if args.browser
                    else "gui_user_review"
                )
                state = approve_character(True, confirmation_method)
            elif review_result == 2:
                state = reject_character("用户在确认窗口中要求重新生成")
            else:
                raise WorkflowError("确认窗口已关闭，标准角色仍保持待确认状态。")
        elif args.command == "reset-character":
            state = reject_character(args.reason)
        elif args.command == "walk-review":
            state = create_walk_review(args.manifest)
        elif args.command == "approve-walk":
            state = approve_walk(
                args.yes,
                expected_preview_sha256=args.expected_preview_sha256,
                expected_asset_sha256=args.expected_asset_sha256,
            )
        elif args.command == "reject-walk":
            state = reject_walk(
                args.yes,
                reason=args.reason,
                expected_preview_sha256=args.expected_preview_sha256,
                expected_asset_sha256=args.expected_asset_sha256,
            )
        elif args.command == "interaction-review":
            state = submit_interaction_preview(
                mode=args.mode,
                preview_path=args.preview,
            )
        elif args.command == "approve-interaction":
            state = approve_interaction(
                args.yes,
                expected_preview_sha256=args.expected_preview_sha256,
                expected_mode=args.expected_mode,
            )
        elif args.command == "reject-interaction":
            state = reject_interaction(
                args.yes,
                reason=args.reason,
                expected_preview_sha256=args.expected_preview_sha256,
                expected_mode=args.expected_mode,
            )
        elif args.command == "check-actions":
            state = require_character_approved(STATE_PATH)
        elif args.command == "check-package":
            state = require_custom_pet_approved(STATE_PATH)
        else:
            state = load_workflow(STATE_PATH)
        print_status(state)
        return 0
    except (FileNotFoundError, WorkflowError, json.JSONDecodeError) as exc:
        print(f"流程未通过：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
