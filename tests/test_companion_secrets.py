"""
本模块测试 DPAPI 密钥文件的路径隔离、绑定熵、密文写入、失败关闭和删除行为。

测试使用受控替身，不调用网络、不读取真实凭据，也不写入真实 LocalAppData。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

from onepic_desktop_pet.companion import secrets


def test_secret_path_can_be_redirected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ONEPIC_COMPANION_DATA_DIR", str(tmp_path))

    assert secrets.api_key_path() == tmp_path.resolve() / "companion-api-key.dpapi"


def test_store_writes_only_protected_payload(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "key.dpapi"
    store = secrets.ApiKeyStore(path)
    plaintext = "sk-test-private-value"
    ciphertext = b"controlled-dpapi-ciphertext"

    monkeypatch.setattr(
        secrets,
        "_protect_bytes",
        lambda value, *, entropy_bytes: ciphertext,
    )
    monkeypatch.setattr(secrets, "_dpapi_libraries", lambda: (object(), object()))
    monkeypatch.setattr(
        secrets,
        "_unprotect_bytes",
        lambda value, *, entropy_bytes: (
            plaintext.encode("utf-8") if value == ciphertext else b""
        ),
    )

    store.save(plaintext)

    payload = path.read_bytes()
    assert plaintext.encode("utf-8") not in payload
    assert ciphertext in payload
    assert store.load() == plaintext
    assert store.exists() is True


def test_encryption_failure_never_creates_plaintext_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = secrets.ApiKeyStore(tmp_path / "key.dpapi")

    def fail_closed(_value: bytes, *, entropy_bytes: bytes) -> bytes:
        del entropy_bytes
        raise secrets.SecretStorageUnavailable("DPAPI unavailable")

    monkeypatch.setattr(secrets, "_protect_bytes", fail_closed)

    with pytest.raises(secrets.SecretStorageUnavailable):
        store.save("sk-must-not-be-written")

    assert store.exists() is False
    assert list(tmp_path.iterdir()) == []


def test_non_windows_dpapi_is_explicitly_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(secrets.sys, "platform", "linux")

    with pytest.raises(secrets.SecretStorageUnavailable):
        secrets._dpapi_libraries()


def test_corrupt_file_is_rejected_and_delete_is_idempotent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "key.dpapi"
    path.write_bytes(b"not-a-dpapi-file")
    store = secrets.ApiKeyStore(path)
    monkeypatch.setattr(secrets, "_dpapi_libraries", lambda: (object(), object()))

    with pytest.raises(secrets.SecretStorageError, match="格式无效"):
        store.load()

    assert store.delete() is True
    assert store.delete() is False


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (" HTTPS://API.Example.COM:443/v1/chat?x=1#part ", "https://api.example.com"),
        ("http://localhost:80/api", "http://localhost"),
        ("https://Example.com:8443/api", "https://example.com:8443"),
        ("https://例子.测试/v1", "https://xn--fsqu00a.xn--0zwm56d"),
        ("http://127.0.0.1:80/v1", "http://127.0.0.1"),
        ("http://[::1]:80/v1", "http://[::1]"),
    ],
)
def test_endpoint_origin_is_canonical(endpoint: str, expected: str) -> None:
    assert secrets.normalize_endpoint_origin(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "api.example.com/v1",
        "ftp://api.example.com/v1",
        "https:///missing-host",
        "https://user:password@example.com/v1",
        "https://example.com:99999/v1",
        "https://[fe80::1%25Ethernet]/v1",
        "http://example.com/v1",
        "http://192.0.2.10/v1",
        "http://[2001:db8::1]/v1",
    ],
)
def test_endpoint_origin_rejects_ambiguous_or_unsafe_values(endpoint: str) -> None:
    with pytest.raises(ValueError):
        secrets.normalize_endpoint_origin(endpoint)


def test_bound_store_path_is_stable_isolated_and_not_injectable(
    tmp_path: Path,
) -> None:
    first = secrets.ApiKeyStore.for_binding(
        " ../../危险宠物/../ ",
        "https://API.EXAMPLE.com:443/v1/chat/completions",
        tmp_path,
    )
    same = secrets.ApiKeyStore.for_binding(
        "../../危险宠物/../",
        "https://api.example.com/another/path?ignored=yes",
        tmp_path,
    )
    another_pet = secrets.ApiKeyStore.for_binding(
        "another-pet",
        "https://api.example.com/v1",
        tmp_path,
    )
    another_origin = secrets.ApiKeyStore.for_binding(
        "../../危险宠物/../",
        "https://api.example.com:8443/v1",
        tmp_path,
    )

    assert first.path == same.path
    assert first._entropy == same._entropy
    assert first.path.parent == tmp_path.resolve()
    assert re.fullmatch(
        r"companion-api-key-v1-[0-9a-f]{64}\.dpapi",
        first.path.name,
    )
    assert "危险" not in first.path.name
    assert another_pet.path != first.path
    assert another_pet._entropy != first._entropy
    assert another_origin.path != first.path
    assert another_origin._entropy != first._entropy
    assert first.pet_id == "../../危险宠物/../"
    assert first.endpoint_origin == "https://api.example.com"


def test_bound_store_passes_binding_entropy_to_dpapi(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first = secrets.ApiKeyStore.for_binding(
        "pet-a", "https://api.example.com/v1", tmp_path
    )
    second = secrets.ApiKeyStore.for_binding(
        "pet-b", "https://api.example.com/v1", tmp_path
    )
    captured: dict[bytes, tuple[bytes, bytes]] = {}

    def protect(value: bytes, *, entropy_bytes: bytes) -> bytes:
        ciphertext = b"ciphertext-" + bytes([len(captured) + 1])
        captured[ciphertext] = (value, entropy_bytes)
        return ciphertext

    def unprotect(value: bytes, *, entropy_bytes: bytes) -> bytes:
        plaintext, original_entropy = captured[value]
        if entropy_bytes != original_entropy:
            raise secrets.SecretStorageError("绑定不匹配")
        return plaintext

    monkeypatch.setattr(secrets, "_protect_bytes", protect)
    monkeypatch.setattr(secrets, "_unprotect_bytes", unprotect)
    monkeypatch.setattr(secrets, "_dpapi_libraries", lambda: (object(), object()))

    first.save("sk-bound-first")
    second.save("sk-bound-second")

    assert first.load() == "sk-bound-first"
    assert second.load() == "sk-bound-second"
    shutil.copyfile(first.path, second.path)
    with pytest.raises(secrets.SecretStorageError, match="绑定不匹配"):
        second.load()


def test_legacy_explicit_path_keeps_original_entropy(tmp_path: Path) -> None:
    path = tmp_path / "legacy.dpapi"
    store = secrets.ApiKeyStore(path)

    assert store.path == path.resolve()
    assert store._entropy == secrets._ENTROPY
    assert store.pet_id is None
    assert store.endpoint_origin is None


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows DPAPI")
def test_real_windows_dpapi_roundtrip_and_cross_binding_rejection(
    tmp_path: Path,
) -> None:
    first = secrets.ApiKeyStore.for_binding(
        "real-pet-a", "https://api.example.com/v1", tmp_path
    )
    second = secrets.ApiKeyStore.for_binding(
        "real-pet-b", "https://api.example.com/v1", tmp_path
    )
    api_key = "sk-real-dpapi-roundtrip-only"

    first.save(api_key)

    assert api_key.encode("utf-8") not in first.path.read_bytes()
    assert first.load() == api_key
    shutil.copyfile(first.path, second.path)
    with pytest.raises(secrets.SecretStorageError):
        second.load()
