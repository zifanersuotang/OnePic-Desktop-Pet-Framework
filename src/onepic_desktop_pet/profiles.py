"""Load an optional catalog of switchable desktop-pet profiles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .resources import resource_path


@dataclass(frozen=True)
class PetProfile:
    """A user-facing pet choice and its runtime animation manifest."""

    pet_id: str
    label: str
    manifest_path: Path


@dataclass(frozen=True)
class PetProfileCatalog:
    """Validated switchable-pet catalog."""

    default_pet_id: str
    profiles: tuple[PetProfile, ...]

    def by_id(self, pet_id: str) -> PetProfile | None:
        return next(
            (profile for profile in self.profiles if profile.pet_id == pet_id),
            None,
        )


def _catalog_path() -> Path | None:
    override = os.environ.get("ONEPIC_PET_CATALOG")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"ONEPIC_PET_CATALOG 指向的形象目录不存在：{path}"
            )
        return path
    try:
        return resource_path("user_assets/pet_catalog.json")
    except FileNotFoundError:
        return None


def load_pet_profile_catalog() -> PetProfileCatalog | None:
    """Return the optional profile catalog, validating ids and manifest paths."""

    path = _catalog_path()
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("pet_catalog.json 必须包含 JSON 对象")
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("pet_catalog.json 至少需要一个 profiles 条目")

    profiles: list[PetProfile] = []
    seen_ids: set[str] = set()
    catalog_root = path.parent.resolve()
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise ValueError("pet_catalog.json 的 profiles 条目必须是对象")
        pet_id = str(raw.get("id", "")).strip()
        label = str(raw.get("label", "")).strip()
        relative_manifest = str(raw.get("manifest", "")).strip()
        if not pet_id or not label or not relative_manifest:
            raise ValueError("每个形象必须包含非空的 id、label 和 manifest")
        if pet_id in seen_ids:
            raise ValueError(f"形象 id 重复：{pet_id}")
        manifest_path = (catalog_root / relative_manifest).resolve()
        if catalog_root not in manifest_path.parents:
            raise ValueError(f"形象清单不能离开 user_assets 目录：{relative_manifest}")
        if not manifest_path.is_file():
            raise FileNotFoundError(f"缺少形象清单：{manifest_path}")
        seen_ids.add(pet_id)
        profiles.append(PetProfile(pet_id, label, manifest_path))

    default_pet_id = str(data.get("default_pet_id", profiles[0].pet_id)).strip()
    if default_pet_id not in seen_ids:
        raise ValueError(f"默认形象不存在：{default_pet_id}")
    return PetProfileCatalog(default_pet_id, tuple(profiles))
