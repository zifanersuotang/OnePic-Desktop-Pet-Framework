"""Tests for optional switchable desktop-pet catalogs."""

import json

import pytest

from onepic_desktop_pet.profiles import load_pet_profile_catalog


def test_profile_catalog_loads_labels_and_default(tmp_path, monkeypatch) -> None:
    for folder in ("desk", "boots"):
        root = tmp_path / "pet_profiles" / folder
        root.mkdir(parents=True)
        (root / "manifest.json").write_text("{}", encoding="utf-8")
    catalog = tmp_path / "pet_catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "default_pet_id": "desk-follow",
                "profiles": [
                    {
                        "id": "desk-follow",
                        "label": "嘟嘴半身（鼠标跟随）",
                        "manifest": "pet_profiles/desk/manifest.json",
                    },
                    {
                        "id": "black-boots",
                        "label": "黑衣长靴女孩（全身）",
                        "manifest": "pet_profiles/boots/manifest.json",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONEPIC_PET_CATALOG", str(catalog))

    loaded = load_pet_profile_catalog()

    assert loaded is not None
    assert loaded.default_pet_id == "desk-follow"
    assert [profile.pet_id for profile in loaded.profiles] == [
        "desk-follow",
        "black-boots",
    ]
    assert loaded.by_id("black-boots").label == "黑衣长靴女孩（全身）"


def test_profile_catalog_rejects_manifest_escape(tmp_path, monkeypatch) -> None:
    outside = tmp_path.parent / "outside-manifest.json"
    outside.write_text("{}", encoding="utf-8")
    catalog = tmp_path / "pet_catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "unsafe",
                        "label": "unsafe",
                        "manifest": "../outside-manifest.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONEPIC_PET_CATALOG", str(catalog))

    with pytest.raises(ValueError, match="不能离开"):
        load_pet_profile_catalog()
