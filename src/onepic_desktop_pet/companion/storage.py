"""
本模块提供按宠物隔离的智能陪伴本地数据层。

职责范围：
- 使用 SQLite 保存角色档案、用户档案、聊天历史、可编辑记忆、预设指令和工具设置；
- 所有业务数据均以 ``pet_id`` 分区，避免不同桌宠共享人设或记忆；
- 提供历史清空、单项删除、整只宠物删除和不含密钥的 JSON 可序列化导出；
- 默认把数据库写入当前用户的 LocalAppData，测试可通过环境变量重定向；
- 每个公开操作只持有一个短连接，避免跨线程复用 SQLite 连接。

本模块只依赖 Python 标准库，不访问网络，也不保存 API 密钥。API 密钥由同目录的
``secrets.py`` 使用 Windows DPAPI 单独保护。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_DATABASE_NAME = "companion.sqlite3"
_SCHEMA_VERSION = 1
_SCHEMA_LOCK = threading.Lock()
_SECRET_FIELD_NAMES = {
    "apikey",
    "apitoken",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "password",
    "secret",
}


def companion_database_path() -> Path:
    """返回智能陪伴数据库路径，测试可通过环境变量重定向目录。"""

    override = os.environ.get("ONEPIC_COMPANION_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve() / _DATABASE_NAME
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".desktop_pet"
    return base / "OnePicDesktopPet" / _DATABASE_NAME


def _utc_now() -> str:
    """返回适合排序和 JSON 导出的 UTC 时间。"""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _required_text(value: object, field_name: str) -> str:
    """规范化不可为空且不含 NUL 的文本标识。"""

    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} 不能包含 NUL 字符")
    return normalized


def _json_object(value: Mapping[str, Any], field_name: str) -> str:
    """把映射编码为稳定 JSON，并给不可序列化输入提供清晰错误。"""

    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} 必须是映射")
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须可以序列化为 JSON") from exc


def _json_strings(values: Sequence[str] | None, field_name: str) -> str:
    """把字符串序列编码为稳定 JSON。"""

    if values is None:
        clean: list[str] = []
    elif isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} 必须是字符串序列")
    else:
        clean = [str(value).strip() for value in values]
        clean = [value for value in clean if value]
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))


def _decoded_object(raw: str) -> dict[str, Any]:
    """解码由本模块写入的 JSON 对象。"""

    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("数据库中的 JSON 对象格式无效")
    return value


def _decoded_strings(raw: str) -> list[str]:
    """解码由本模块写入的字符串列表。"""

    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("数据库中的字符串列表格式无效")
    return value


def _reject_secret_fields(value: object) -> None:
    """阻止 API 密钥等凭据误入普通 SQLite 工具设置。"""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = "".join(
                character for character in str(key).lower() if character.isalnum()
            )
            if normalized_key in _SECRET_FIELD_NAMES:
                raise ValueError("工具设置不能包含密钥；请使用 companion.secrets")
            _reject_secret_fields(child)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for child in value:
            _reject_secret_fields(child)


class CompanionStorage:
    """管理一个智能陪伴 SQLite 数据库，公开方法可从不同线程调用。"""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else companion_database_path()
        )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """创建一个仅供当前操作使用的短连接。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        """幂等创建数据库结构。"""

        with _SCHEMA_LOCK:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        name TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS character_profiles (
                        pet_id TEXT PRIMARY KEY,
                        profile_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_profiles (
                        pet_id TEXT PRIMARY KEY,
                        profile_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pet_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_messages_pet_id_id
                    ON messages (pet_id, id);

                    CREATE TABLE IF NOT EXISTS memories (
                        pet_id TEXT NOT NULL,
                        memory_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tags_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (pet_id, memory_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_memories_pet_updated
                    ON memories (pet_id, updated_at);

                    CREATE TABLE IF NOT EXISTS preset_commands (
                        pet_id TEXT NOT NULL,
                        command_id TEXT NOT NULL,
                        label TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        enabled INTEGER NOT NULL,
                        sort_order INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (pet_id, command_id)
                    );

                    CREATE TABLE IF NOT EXISTS tool_settings (
                        pet_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        settings_json TEXT NOT NULL,
                        enabled INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (pet_id, tool_name)
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO schema_meta (name, value) VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET value = excluded.value
                    """,
                    ("schema_version", str(_SCHEMA_VERSION)),
                )

    def _set_profile(
        self,
        table: str,
        pet_id: str,
        profile: Mapping[str, Any],
    ) -> None:
        """写入经过白名单选择的档案表。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        payload = _json_object(profile, "profile")
        if table not in {"character_profiles", "user_profiles"}:
            raise ValueError("不支持的档案类型")
        sql = (
            f"INSERT INTO {table} (pet_id, profile_json, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(pet_id) DO UPDATE SET "
            "profile_json = excluded.profile_json, updated_at = excluded.updated_at"
        )
        with self._connect() as connection:
            connection.execute(sql, (normalized_pet_id, payload, _utc_now()))

    def _get_profile(self, table: str, pet_id: str) -> dict[str, Any] | None:
        """读取经过白名单选择的档案表。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        if table not in {"character_profiles", "user_profiles"}:
            raise ValueError("不支持的档案类型")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT profile_json FROM {table} WHERE pet_id = ?",
                (normalized_pet_id,),
            ).fetchone()
        return None if row is None else _decoded_object(row["profile_json"])

    def set_character_profile(
        self,
        pet_id: str,
        profile: Mapping[str, Any],
    ) -> None:
        """新增或替换一只宠物的角色档案。"""

        self._set_profile("character_profiles", pet_id, profile)

    def get_character_profile(self, pet_id: str) -> dict[str, Any] | None:
        """返回角色档案，不存在时返回 ``None``。"""

        return self._get_profile("character_profiles", pet_id)

    def set_user_profile(
        self,
        pet_id: str,
        profile: Mapping[str, Any],
    ) -> None:
        """新增或替换一只宠物所使用的委托人档案。"""

        self._set_profile("user_profiles", pet_id, profile)

    def get_user_profile(self, pet_id: str) -> dict[str, Any] | None:
        """返回委托人档案，不存在时返回 ``None``。"""

        return self._get_profile("user_profiles", pet_id)

    def add_message(
        self,
        pet_id: str,
        role: str,
        content: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        """追加一条聊天消息并返回自增消息编号。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_role = _required_text(role, "role")
        normalized_content = str(content)
        if not normalized_content.strip():
            raise ValueError("content 不能为空")
        metadata_json = _json_object(
            {} if metadata is None else metadata,
            "metadata",
        )
        timestamp = (
            _required_text(created_at, "created_at")
            if created_at is not None
            else _utc_now()
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    pet_id, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized_pet_id,
                    normalized_role,
                    normalized_content,
                    metadata_json,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def list_messages(
        self,
        pet_id: str,
        *,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """按时间正序返回最近一页消息。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_limit = min(10_000, max(1, int(limit)))
        parameters: list[Any] = [normalized_pet_id]
        predicate = "pet_id = ?"
        if before_id is not None:
            predicate += " AND id < ?"
            parameters.append(int(before_id))
        parameters.append(normalized_limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, role, content, metadata_json, created_at
                FROM messages
                WHERE {predicate}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        """把消息行转换为 JSON 可序列化映射。"""

        return {
            "id": int(row["id"]),
            "role": row["role"],
            "content": row["content"],
            "metadata": _decoded_object(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    def clear_messages(self, pet_id: str) -> int:
        """清空一只宠物的聊天历史并返回删除条数。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM messages WHERE pet_id = ?",
                (normalized_pet_id,),
            )
            return max(0, int(cursor.rowcount))

    clear_history = clear_messages

    def save_memory(
        self,
        pet_id: str,
        content: str,
        *,
        memory_id: str | None = None,
        title: str = "",
        tags: Sequence[str] | None = None,
    ) -> str:
        """新增或编辑一条记忆并返回稳定的记忆编号。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_memory_id = (
            _required_text(memory_id, "memory_id")
            if memory_id is not None
            else uuid.uuid4().hex
        )
        normalized_content = str(content)
        if not normalized_content.strip():
            raise ValueError("content 不能为空")
        tags_json = _json_strings(tags, "tags")
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    pet_id, memory_id, title, content, tags_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pet_id, memory_id) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    tags_json = excluded.tags_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_pet_id,
                    normalized_memory_id,
                    str(title).strip(),
                    normalized_content,
                    tags_json,
                    now,
                    now,
                ),
            )
        return normalized_memory_id

    def get_memory(self, pet_id: str, memory_id: str) -> dict[str, Any] | None:
        """按编号读取一条记忆。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_memory_id = _required_text(memory_id, "memory_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT memory_id, title, content, tags_json, created_at, updated_at
                FROM memories WHERE pet_id = ? AND memory_id = ?
                """,
                (normalized_pet_id, normalized_memory_id),
            ).fetchone()
        return None if row is None else self._memory_from_row(row)

    def list_memories(self, pet_id: str) -> list[dict[str, Any]]:
        """按最近编辑优先列出一只宠物的全部记忆。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT memory_id, title, content, tags_json, created_at, updated_at
                FROM memories WHERE pet_id = ?
                ORDER BY updated_at DESC, memory_id ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
        """把记忆行转换为 JSON 可序列化映射。"""

        return {
            "memory_id": row["memory_id"],
            "title": row["title"],
            "content": row["content"],
            "tags": _decoded_strings(row["tags_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_memory(self, pet_id: str, memory_id: str) -> bool:
        """删除一条可编辑记忆。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_memory_id = _required_text(memory_id, "memory_id")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE pet_id = ? AND memory_id = ?",
                (normalized_pet_id, normalized_memory_id),
            )
            return cursor.rowcount > 0

    def save_preset_command(
        self,
        pet_id: str,
        command_id: str,
        label: str,
        prompt: str,
        *,
        enabled: bool = True,
        sort_order: int = 0,
    ) -> None:
        """新增或编辑一条预设指令。"""

        values = (
            _required_text(pet_id, "pet_id"),
            _required_text(command_id, "command_id"),
            _required_text(label, "label"),
            _required_text(prompt, "prompt"),
            int(bool(enabled)),
            int(sort_order),
            _utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO preset_commands (
                    pet_id, command_id, label, prompt, enabled, sort_order, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pet_id, command_id) DO UPDATE SET
                    label = excluded.label,
                    prompt = excluded.prompt,
                    enabled = excluded.enabled,
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def list_preset_commands(self, pet_id: str) -> list[dict[str, Any]]:
        """按显示顺序返回预设指令。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT command_id, label, prompt, enabled, sort_order, updated_at
                FROM preset_commands WHERE pet_id = ?
                ORDER BY sort_order ASC, command_id ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
        return [
            {
                "command_id": row["command_id"],
                "label": row["label"],
                "prompt": row["prompt"],
                "enabled": bool(row["enabled"]),
                "sort_order": int(row["sort_order"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def delete_preset_command(self, pet_id: str, command_id: str) -> bool:
        """删除一条预设指令。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_command_id = _required_text(command_id, "command_id")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM preset_commands
                WHERE pet_id = ? AND command_id = ?
                """,
                (normalized_pet_id, normalized_command_id),
            )
            return cursor.rowcount > 0

    def set_tool_settings(
        self,
        pet_id: str,
        tool_name: str,
        settings: Mapping[str, Any],
        *,
        enabled: bool = True,
    ) -> None:
        """新增或替换一个白名单工具的本地设置。"""

        _reject_secret_fields(settings)
        values = (
            _required_text(pet_id, "pet_id"),
            _required_text(tool_name, "tool_name"),
            _json_object(settings, "settings"),
            int(bool(enabled)),
            _utc_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_settings (
                    pet_id, tool_name, settings_json, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(pet_id, tool_name) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def get_tool_settings(self, pet_id: str, tool_name: str) -> dict[str, Any] | None:
        """读取一个工具的设置与启用状态。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_tool_name = _required_text(tool_name, "tool_name")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tool_name, settings_json, enabled, updated_at
                FROM tool_settings WHERE pet_id = ? AND tool_name = ?
                """,
                (normalized_pet_id, normalized_tool_name),
            ).fetchone()
        return None if row is None else self._tool_from_row(row)

    def list_tool_settings(self, pet_id: str) -> list[dict[str, Any]]:
        """列出一只宠物的全部工具设置。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT tool_name, settings_json, enabled, updated_at
                FROM tool_settings WHERE pet_id = ? ORDER BY tool_name ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
        return [self._tool_from_row(row) for row in rows]

    @staticmethod
    def _tool_from_row(row: sqlite3.Row) -> dict[str, Any]:
        """把工具设置行转换为 JSON 可序列化映射。"""

        return {
            "tool_name": row["tool_name"],
            "settings": _decoded_object(row["settings_json"]),
            "enabled": bool(row["enabled"]),
            "updated_at": row["updated_at"],
        }

    def delete_tool_settings(self, pet_id: str, tool_name: str) -> bool:
        """删除一个工具的设置。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        normalized_tool_name = _required_text(tool_name, "tool_name")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM tool_settings WHERE pet_id = ? AND tool_name = ?
                """,
                (normalized_pet_id, normalized_tool_name),
            )
            return cursor.rowcount > 0

    def export_pet_data(self, pet_id: str) -> dict[str, Any]:
        """导出一只宠物的陪伴数据；结果不含 API 密钥。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        with self._connect() as connection:
            character_row = connection.execute(
                "SELECT profile_json FROM character_profiles WHERE pet_id = ?",
                (normalized_pet_id,),
            ).fetchone()
            user_row = connection.execute(
                "SELECT profile_json FROM user_profiles WHERE pet_id = ?",
                (normalized_pet_id,),
            ).fetchone()
            message_rows = connection.execute(
                """
                SELECT id, role, content, metadata_json, created_at
                FROM messages WHERE pet_id = ? ORDER BY id ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
            memory_rows = connection.execute(
                """
                SELECT memory_id, title, content, tags_json, created_at, updated_at
                FROM memories WHERE pet_id = ?
                ORDER BY updated_at DESC, memory_id ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
            command_rows = connection.execute(
                """
                SELECT command_id, label, prompt, enabled, sort_order, updated_at
                FROM preset_commands WHERE pet_id = ?
                ORDER BY sort_order ASC, command_id ASC
                """,
                (normalized_pet_id,),
            ).fetchall()
            tool_rows = connection.execute(
                """
                SELECT tool_name, settings_json, enabled, updated_at
                FROM tool_settings WHERE pet_id = ? ORDER BY tool_name ASC
                """,
                (normalized_pet_id,),
            ).fetchall()

        return {
            "schema_version": _SCHEMA_VERSION,
            "pet_id": normalized_pet_id,
            "character_profile": (
                None
                if character_row is None
                else _decoded_object(character_row["profile_json"])
            ),
            "user_profile": (
                None if user_row is None else _decoded_object(user_row["profile_json"])
            ),
            "messages": [self._message_from_row(row) for row in message_rows],
            "memories": [self._memory_from_row(row) for row in memory_rows],
            "preset_commands": [
                {
                    "command_id": row["command_id"],
                    "label": row["label"],
                    "prompt": row["prompt"],
                    "enabled": bool(row["enabled"]),
                    "sort_order": int(row["sort_order"]),
                    "updated_at": row["updated_at"],
                }
                for row in command_rows
            ],
            "tool_settings": [self._tool_from_row(row) for row in tool_rows],
        }

    export_pet = export_pet_data

    def delete_pet_data(self, pet_id: str) -> dict[str, int]:
        """事务删除一只宠物的全部陪伴数据并返回逐表计数。"""

        normalized_pet_id = _required_text(pet_id, "pet_id")
        tables = (
            "character_profiles",
            "user_profiles",
            "messages",
            "memories",
            "preset_commands",
            "tool_settings",
        )
        deleted: dict[str, int] = {}
        with self._connect() as connection:
            for table in tables:
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE pet_id = ?",
                    (normalized_pet_id,),
                )
                deleted[table] = max(0, int(cursor.rowcount))
        return deleted

    delete_pet = delete_pet_data
