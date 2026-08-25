"""检查仓库 Markdown 中指向本地文件的相对链接。

外部 URL、邮件、锚点和应用专用链接不联网验证。
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("#", "http://", "https://", "mailto:", "chatgpt-")


def main() -> int:
    """报告缺失的本地链接目标。"""

    missing: list[str] = []
    checked = 0
    for markdown in sorted(ROOT.rglob("*.md")):
        if any(
            part in {".git", ".venv", "build", "dist", "node_modules", "user_assets"}
            for part in markdown.relative_to(ROOT).parts
        ):
            continue
        text = markdown.read_text(encoding="utf-8-sig")
        for match in LINK_PATTERN.finditer(text):
            raw = match.group(1).strip()
            if raw.startswith("<") and raw.endswith(">"):
                raw = raw[1:-1]
            if not raw or raw.lower().startswith(SKIP_PREFIXES):
                continue
            target_text = urllib.parse.unquote(raw.split("#", 1)[0])
            if not target_text:
                continue
            checked += 1
            target = (markdown.parent / target_text).resolve()
            if not target.exists():
                missing.append(
                    f"{markdown.relative_to(ROOT).as_posix()} -> {target_text}"
                )
    print(f"[OnePic Markdown Links] checked={checked}")
    if missing:
        for value in missing:
            print(f"- MISSING: {value}")
        return 1
    print("result=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
