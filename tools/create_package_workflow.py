"""为分享版桌宠生成运行门禁所需的最小审批状态。

分享包不需要来源路径、来源哈希、评审文件或客户原图。本工具只保留运行门禁读取的
审批字段；替代互动同时保留预览和标准角色哈希，确保包内互动仍绑定获批角色。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from onepic_desktop_pet.workflow import (
    character_is_approved,
    interaction_is_approved,
    load_workflow,
    walk_is_approved,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    state = load_workflow(args.source.resolve())
    if not character_is_approved(state):
        raise RuntimeError("Character approval gate has not passed")
    if not (walk_is_approved(state) or interaction_is_approved(state)):
        raise RuntimeError("Motion or interaction approval gate has not passed")

    character = state["character"]
    packaged = {
        "version": 2,
        "pet_id": state.get("pet_id"),
        "character": {
            "status": "approved",
            "user_confirmed": True,
            "candidate_sha256": character["candidate_sha256"],
            "approved_candidate_sha256": character[
                "approved_candidate_sha256"
            ],
        },
    }
    if walk_is_approved(state):
        walk = state["walk"]
        asset_hash = walk.get("asset_sha256") or walk["preview_sha256"]
        packaged["walk"] = {
            "status": "approved",
            "user_confirmed": True,
            "asset_sha256": asset_hash,
            "approved_asset_sha256": asset_hash,
        }
    elif interaction_is_approved(state):
        interaction = state["interaction"]
        packaged["interaction"] = {
            "status": "approved",
            "user_confirmed": True,
            "mode": interaction["mode"],
            "approved_mode": interaction["approved_mode"],
            "preview_sha256": interaction["preview_sha256"],
            "approved_asset_sha256": interaction["approved_asset_sha256"],
            "character_sha256": interaction["character_sha256"],
            "approved_character_sha256": interaction[
                "approved_character_sha256"
            ],
        }

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(packaged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
