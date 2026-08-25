"""
本模块使用 Windows DPAPI 保存智能陪伴服务的 API 密钥。

职责范围：
- 用当前 Windows 用户范围的 DPAPI 加密 API 密钥，再写入独立二进制文件；
- 默认写入 LocalAppData，测试可通过 ``ONEPIC_COMPANION_DATA_DIR`` 重定向；
- 兼容原有单文件存储，并可按“宠物 + HTTP(S) 服务源”隔离密钥；
- 绑定存储的文件名只使用摘要，DPAPI 专属熵也包含绑定，拒绝跨绑定解密；
- 拒绝损坏、错误版本或无法解密的密钥文件；
- 提供保存、读取、存在性检查和可恢复删除的最小接口。

本模块不访问网络。非 Windows、DPAPI 不可用或加解密失败时会抛出明确异常并停止，
绝不降级为明文文件。
"""

from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import os
import sys
import unicodedata
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_SECRET_FILE_NAME = "companion-api-key.dpapi"
_BOUND_SECRET_FILE_PREFIX = "companion-api-key-v1-"
_FILE_MAGIC = b"ONEPIC-DPAPI\x01"
_ENTROPY = b"OnePicDesktopPet/companion-api-key/v1"
_BOUND_ENTROPY_CONTEXT = b"OnePicDesktopPet/companion-api-key/binding/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretStorageError(RuntimeError):
    """表示密钥无法被安全保存或读取。"""


class SecretStorageUnavailable(SecretStorageError):
    """表示当前平台没有可用的 Windows DPAPI。"""


class _DataBlob(ctypes.Structure):
    """对应 Windows DATA_BLOB 结构。"""

    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def api_key_path() -> Path:
    """返回 DPAPI 密钥文件路径，测试可通过环境变量重定向目录。"""

    override = os.environ.get("ONEPIC_COMPANION_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve() / _SECRET_FILE_NAME
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".desktop_pet"
    return base / "OnePicDesktopPet" / _SECRET_FILE_NAME


def normalize_endpoint_origin(endpoint: str) -> str:
    """把 HTTP(S) 端点规范化为不含路径、查询和片段的服务源。

    主机名会转为小写 ASCII（IDNA），HTTP 的 80 端口及 HTTPS 的 443
    端口会省略，其他显式端口会保留。为避免凭据混入绑定标识，拒绝 URL
    用户名和密码。
    """

    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("endpoint 不能为空")
    try:
        parsed = urlsplit(endpoint.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint 端口无效") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("endpoint 仅支持 http 或 https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint 不能包含用户名或密码")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("endpoint 缺少主机名")
    hostname = hostname.rstrip(".")
    if not hostname or "%" in hostname:
        raise ValueError("endpoint 主机名无效")

    is_loopback = False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            canonical_host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("endpoint 主机名无效") from exc
        is_loopback = canonical_host == "localhost"
    else:
        canonical_host = address.compressed.lower()
        is_loopback = address.is_loopback
        if address.version == 6:
            canonical_host = f"[{canonical_host}]"
    if scheme == "http" and not is_loopback:
        raise ValueError("远程 endpoint 必须使用 https；http 仅允许本机回环地址")

    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{canonical_host}{port_suffix}"


def _normalize_pet_id(pet_id: str) -> str:
    """返回稳定的宠物标识；文件路径不会直接使用该文本。"""

    if not isinstance(pet_id, str):
        raise ValueError("pet_id 必须是字符串")
    normalized = unicodedata.normalize("NFC", pet_id.strip())
    if not normalized:
        raise ValueError("pet_id 不能为空")
    if len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ValueError("pet_id 格式无效")
    return normalized


def _binding_material(pet_id: str, origin: str) -> bytes:
    """构造无歧义且版本化的绑定材料。"""

    pet_bytes = pet_id.encode("utf-8")
    origin_bytes = origin.encode("ascii")
    return (
        b"onepic-companion-api-binding-v1\x00"
        + len(pet_bytes).to_bytes(4, "big")
        + pet_bytes
        + len(origin_bytes).to_bytes(4, "big")
        + origin_bytes
    )


def _input_blob(data: bytes) -> tuple[_DataBlob, Any]:
    """创建在 Win32 调用期间保持有效的输入 DATA_BLOB。"""

    buffer = ctypes.create_string_buffer(data, max(1, len(data)))
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _dpapi_libraries() -> tuple[Any, Any]:
    """加载 DPAPI 与 LocalFree；不可用时失败关闭。"""

    if sys.platform != "win32":
        raise SecretStorageUnavailable("API 密钥存储仅支持 Windows DPAPI")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
    except (AttributeError, OSError) as exc:
        raise SecretStorageUnavailable("Windows DPAPI 不可用") from exc
    return crypt32, kernel32


def _last_windows_error(action: str) -> SecretStorageError:
    """把最近一次 Win32 错误转换为不泄露密钥的异常。"""

    error_code = ctypes.get_last_error()
    message = ctypes.FormatError(error_code).strip() if error_code else "未知错误"
    return SecretStorageError(f"{action}失败：{message}（{error_code}）")


def _protect_bytes(plaintext: bytes, *, entropy_bytes: bytes = _ENTROPY) -> bytes:
    """使用当前 Windows 用户范围 DPAPI 加密字节。"""

    crypt32, kernel32 = _dpapi_libraries()
    source, source_buffer = _input_blob(plaintext)
    entropy, entropy_buffer = _input_blob(entropy_bytes)
    output = _DataBlob()
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "OnePic Desktop Pet Companion API Key",
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise _last_windows_error("DPAPI 加密")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))


def _unprotect_bytes(ciphertext: bytes, *, entropy_bytes: bytes = _ENTROPY) -> bytes:
    """使用当前 Windows 用户范围 DPAPI 解密字节。"""

    crypt32, kernel32 = _dpapi_libraries()
    source, source_buffer = _input_blob(ciphertext)
    entropy, entropy_buffer = _input_blob(entropy_bytes)
    output = _DataBlob()
    description = wintypes.LPWSTR()
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise _last_windows_error("DPAPI 解密")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
        kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))


class ApiKeyStore:
    """管理一个只包含 DPAPI 密文的 API 密钥文件。"""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve() if path is not None else api_key_path()
        )
        self._entropy = _ENTROPY
        self.pet_id: str | None = None
        self.endpoint_origin: str | None = None

    @classmethod
    def for_binding(
        cls,
        pet_id: str,
        endpoint: str,
        directory: str | os.PathLike[str] | None = None,
    ) -> "ApiKeyStore":
        """创建按宠物和端点服务源隔离的 DPAPI 密钥存储。

        ``endpoint`` 可以是完整 API URL；只有规范化后的 HTTP(S) origin 参与
        绑定。``directory`` 是可选的密钥目录，省略时沿用默认数据目录。
        文件名完全由 SHA-256 十六进制摘要组成，不拼入用户提供的路径文本。
        """

        normalized_pet_id = _normalize_pet_id(pet_id)
        origin = normalize_endpoint_origin(endpoint)
        material = _binding_material(normalized_pet_id, origin)
        binding_digest = hashlib.sha256(material).hexdigest()
        base_directory = (
            Path(directory).expanduser().resolve()
            if directory is not None
            else api_key_path().parent
        )
        path = base_directory / (
            f"{_BOUND_SECRET_FILE_PREFIX}{binding_digest}.dpapi"
        )
        store = cls(path)
        store._entropy = hashlib.sha256(
            _BOUND_ENTROPY_CONTEXT + b"\x00" + material
        ).digest()
        store.pet_id = normalized_pet_id
        store.endpoint_origin = origin
        return store

    def save(self, api_key: str) -> None:
        """DPAPI 加密并原子保存非空 API 密钥。"""

        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key 不能为空")
        plaintext = api_key.encode("utf-8")
        ciphertext = _protect_bytes(plaintext, entropy_bytes=self._entropy)
        if not ciphertext or ciphertext == plaintext:
            raise SecretStorageError("DPAPI 未返回可接受的密文")

        payload = _FILE_MAGIC + ciphertext
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 与目标放在同目录以保留 os.replace 原子性，但不重复较长的
        # 绑定文件名，避免深层 LocalAppData 路径触发 Windows MAX_PATH。
        temporary = self.path.with_name(
            f".key-{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecretStorageError(f"无法保存 API 密钥文件：{exc}") from exc

    def load(self) -> str | None:
        """解密 API 密钥；文件不存在时返回 ``None``。"""

        _dpapi_libraries()
        if not self.path.exists():
            return None
        try:
            payload = self.path.read_bytes()
        except OSError as exc:
            raise SecretStorageError(f"无法读取 API 密钥文件：{exc}") from exc
        if not payload.startswith(_FILE_MAGIC) or len(payload) == len(_FILE_MAGIC):
            raise SecretStorageError("API 密钥文件格式无效")
        plaintext = _unprotect_bytes(
            payload[len(_FILE_MAGIC) :],
            entropy_bytes=self._entropy,
        )
        try:
            api_key = plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStorageError("DPAPI 解密结果不是有效 UTF-8") from exc
        if not api_key.strip():
            raise SecretStorageError("DPAPI 解密结果为空")
        return api_key

    def exists(self) -> bool:
        """返回密钥文件是否存在，不尝试解密。"""

        return self.path.is_file()

    def delete(self) -> bool:
        """删除密钥文件；不存在时返回 ``False``。"""

        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise SecretStorageError(f"无法删除 API 密钥文件：{exc}") from exc
        return True


def save_api_key(api_key: str) -> None:
    """保存默认位置的 API 密钥。"""

    ApiKeyStore().save(api_key)


def load_api_key() -> str | None:
    """读取默认位置的 API 密钥。"""

    return ApiKeyStore().load()


def delete_api_key() -> bool:
    """删除默认位置的 API 密钥。"""

    return ApiKeyStore().delete()
