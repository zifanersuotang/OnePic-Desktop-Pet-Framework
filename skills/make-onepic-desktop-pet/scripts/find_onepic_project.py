"""Locate a local OnePic-Desktop-Pet checkout without modifying the filesystem."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


MARKERS = (
    "OnePicDesktopPet.spec",
    "tools/onepic_workflow.py",
    "agent-guide/AGENT_GUIDE.md",
)


def is_project(path: Path) -> bool:
    """Return whether a directory contains the expected OnePic markers."""

    return path.is_dir() and all((path / marker).is_file() for marker in MARKERS)


def candidate_roots(start: Path) -> list[Path]:
    """Return bounded roots that commonly contain the checkout."""

    roots = [start.resolve(), *start.resolve().parents]
    documents = Path.home() / "Documents"
    if documents not in roots:
        roots.append(documents)
    return roots


def find_project(start: Path) -> Path | None:
    """Find the closest marked project, then search two levels under Documents."""

    for root in candidate_roots(start):
        if is_project(root):
            return root
    documents = Path.home() / "Documents"
    if documents.is_dir():
        try:
            first_level = list(documents.iterdir())
        except OSError:
            first_level = []
        for first in first_level:
            if is_project(first):
                return first
            if first.is_dir():
                try:
                    second_level = list(first.iterdir())
                except OSError:
                    continue
                for second in second_level:
                    if is_project(second):
                        return second
    return None


def main() -> int:
    """Print the resolved checkout path or return a nonzero status."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=Path, default=Path.cwd())
    args = parser.parse_args()
    project = find_project(args.start)
    if project is None:
        print("未找到 OnePic-Desktop-Pet 项目。")
        return 2
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
