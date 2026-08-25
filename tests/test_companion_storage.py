"""
本模块测试智能陪伴 SQLite 数据层的宠物隔离、增删改查、导出与并发短连接。

所有文件均写入测试沙箱，不读取真实用户数据，也不访问网络。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from onepic_desktop_pet.companion.storage import (
    CompanionStorage,
    companion_database_path,
)


def test_default_path_can_be_redirected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ONEPIC_COMPANION_DATA_DIR", str(tmp_path))

    assert companion_database_path() == tmp_path.resolve() / "companion.sqlite3"


def test_profiles_and_messages_are_isolated_by_pet(tmp_path: Path) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")
    suspicious_pet_id = "cat'; DROP TABLE messages; --"

    storage.set_character_profile("cat", {"name": "小猫", "temper": "安静"})
    storage.set_user_profile("cat", {"nickname": "委托人"})
    storage.set_character_profile(suspicious_pet_id, {"name": "安全参数"})
    first = storage.add_message("cat", "user", "你好")
    second = storage.add_message(
        "cat",
        "assistant",
        "晚上好",
        metadata={"mood": "calm"},
    )
    storage.add_message("dog", "user", "另一只宠物")

    assert storage.get_character_profile("cat")["name"] == "小猫"
    assert storage.get_user_profile("cat") == {"nickname": "委托人"}
    assert storage.get_character_profile(suspicious_pet_id) == {"name": "安全参数"}
    assert [message["id"] for message in storage.list_messages("cat")] == [
        first,
        second,
    ]
    assert storage.list_messages("cat")[1]["metadata"] == {"mood": "calm"}
    assert len(storage.list_messages("dog")) == 1


def test_recent_message_page_is_chronological_and_history_can_be_cleared(
    tmp_path: Path,
) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")
    ids = [storage.add_message("cat", "user", f"消息 {index}") for index in range(5)]

    page = storage.list_messages("cat", limit=2)
    older = storage.list_messages("cat", limit=2, before_id=page[0]["id"])

    assert [item["id"] for item in page] == ids[-2:]
    assert [item["id"] for item in older] == ids[1:3]
    assert storage.clear_history("cat") == 5
    assert storage.list_messages("cat") == []


def test_memories_commands_tools_export_and_pet_delete(tmp_path: Path) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")
    storage.set_character_profile("cat", {"name": "白猫"})
    memory_id = storage.save_memory(
        "cat",
        "用户喜欢桂花茶",
        title="偏好",
        tags=["饮食", "长期"],
    )
    storage.save_memory(
        "cat",
        "用户现在更喜欢乌龙茶",
        memory_id=memory_id,
        title="偏好",
        tags=["饮食"],
    )
    storage.save_preset_command(
        "cat",
        "comfort",
        "求安慰",
        "请结合人设安慰用户",
        sort_order=2,
    )
    storage.set_tool_settings(
        "cat",
        "random_number",
        {"minimum": 1, "maximum": 100},
        enabled=False,
    )

    memory = storage.get_memory("cat", memory_id)
    tool = storage.get_tool_settings("cat", "random_number")
    exported = storage.export_pet("cat")

    assert memory is not None
    assert memory["content"] == "用户现在更喜欢乌龙茶"
    assert memory["tags"] == ["饮食"]
    assert tool is not None and tool["enabled"] is False
    assert exported["character_profile"] == {"name": "白猫"}
    assert exported["memories"][0]["memory_id"] == memory_id
    assert exported["preset_commands"][0]["command_id"] == "comfort"
    assert "api_key" not in json.dumps(exported, ensure_ascii=False).lower()

    deleted = storage.delete_pet("cat")

    assert sum(deleted.values()) == 4
    assert storage.export_pet_data("cat")["character_profile"] is None
    assert storage.list_memories("cat") == []
    assert storage.list_preset_commands("cat") == []
    assert storage.list_tool_settings("cat") == []


def test_individual_delete_apis_do_not_cross_pet_boundaries(tmp_path: Path) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")
    memory_id = storage.save_memory("cat", "记忆")
    storage.save_preset_command("cat", "one", "一", "内容")
    storage.set_tool_settings("cat", "clock", {"hourly": True})

    assert storage.delete_memory("dog", memory_id) is False
    assert storage.delete_memory("cat", memory_id) is True
    assert storage.delete_preset_command("cat", "one") is True
    assert storage.delete_tool_settings("cat", "clock") is True
    assert storage.get_memory("cat", memory_id) is None


def test_tool_settings_reject_api_keys_and_other_secret_fields(
    tmp_path: Path,
) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")

    with pytest.raises(ValueError, match="不能包含密钥"):
        storage.set_tool_settings(
            "cat",
            "chat_provider",
            {"model": "example", "credentials": {"api_key": "secret"}},
        )

    assert storage.list_tool_settings("cat") == []


def test_short_connections_support_concurrent_message_writes(tmp_path: Path) -> None:
    storage = CompanionStorage(tmp_path / "companion.sqlite3")

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(
            executor.map(
                lambda index: storage.add_message("cat", "user", f"并发 {index}"),
                range(32),
            )
        )

    messages = storage.list_messages("cat", limit=100)
    assert len(messages) == 32
    assert len(set(ids)) == 32
    assert [item["id"] for item in messages] == sorted(ids)
