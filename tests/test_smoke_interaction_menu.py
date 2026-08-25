"""验证互动菜单发布烟测能独立推导目标、按序列去重并过滤空动作。"""

from tools.smoke_interaction_menu import expected_interaction_actions


def test_expected_menu_prefers_signature_and_extension_over_manual_aliases() -> None:
    """同一扩展序列只保留最高优先级入口，文字不同也不能重复展示。"""

    manifest = {
        "extension_animations": {
            "hug": {"menu_label": "抱抱小熊"},
            "write": {"menu_label": "认真写字"},
            "hidden": {},
        },
        "companion_behaviors": {
            "signature_action": {
                "enabled": True,
                "label": "抱抱小熊",
                "sequence": "hug",
                "state": "happy",
            },
            "manual_actions": [
                {"label": "重复抱熊", "sequence": "hug", "state": "happy"},
                {"label": "写字别名", "sequence": "write", "state": "idle"},
                {"label": "隐藏动作", "sequence": "hidden", "state": "idle"},
                {"label": "好奇看看", "state": "curious"},
                {"label": "无效动作", "sequence": "missing", "state": "idle"},
            ],
        },
    }

    assert expected_interaction_actions(manifest) == [
        {
            "label": "打个招呼",
            "target": "builtin:interaction",
            "source": "builtin",
        },
        {
            "label": "自拍一下",
            "target": "builtin:selfie",
            "source": "builtin",
        },
        {
            "label": "抱抱小熊",
            "target": "extension:hug",
            "source": "signature",
        },
        {
            "label": "认真写字",
            "target": "extension:write",
            "source": "extension",
        },
        {
            "label": "隐藏动作",
            "target": "extension:hidden",
            "source": "manual",
        },
        {
            "label": "好奇看看",
            "target": "state:curious",
            "source": "manual",
        },
    ]


def test_expected_menu_also_deduplicates_identical_visible_labels() -> None:
    """两个不同目标若误配成相同文案，菜单仍只显示一个可理解入口。"""

    manifest = {
        "extension_animations": {
            "first": {"menu_label": "看看表情"},
            "second": {"menu_label": "看看表情"},
        }
    }

    actions = expected_interaction_actions(manifest)

    assert [item["label"] for item in actions] == [
        "打个招呼",
        "自拍一下",
        "看看表情",
    ]
    assert actions[-1]["target"] == "extension:first"
