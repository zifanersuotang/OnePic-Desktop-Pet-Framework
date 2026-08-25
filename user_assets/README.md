# 用户私有素材

这里保存未获公开授权的原图、标准角色候选、动作、审批状态、运行版本和备份。
每只宠物必须使用独立目录：

```text
user_assets/pets/<pet-id>/
```

先运行 `scripts/new_pet.ps1` 建立工作区，再按 `workflow.json` 完成标准角色、
基础动作、走路或替代互动及所选扩展包的哈希审批。当前激活版本位于
`user_assets/pet/`，`active-pet.json` 只保存当前宠物指针。

本目录除本说明外默认被 `.gitignore` 忽略。正式分享包只能通过
`scripts/release_pet.ps1` 创建；原图、视频、候选、草稿、备份、本机设置和未明确
许可的自拍不得进入 Git 或分享暂存区。
