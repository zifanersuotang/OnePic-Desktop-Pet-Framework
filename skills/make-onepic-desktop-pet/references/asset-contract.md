# 素材契约

## 必需输入

```text
candidate/approved.png
actions/candidates/<action>/<batch-id>/candidate_01.png ... candidate_03.png
actions/transparent/
  wave.png happy.png selfie.png surprised.png
  annoyed.png sit.png sleep.png drag.png
walk/frames/walk_01.png ... walk_08.png
```

新工作区必须有 `review/action-source-gate.json`。每个基础动作必须从
2–3 个候选中选出，并绑定五项全通过的视觉质检、已批准角色 SHA-256
和当前正式动作 SHA-256。失败候选不得作为下一轮生成底图。

新宠物默认方案必须制作：

```text
actions/transparent/touch_soften.png
actions/transparent/touch_return.png
```

新建工作区默认要求两张并纳入 QA。迁移的旧宠物可提供 0–2 张；缺少时使用
已验收表情帧的内存混合过渡。

所有运行输入使用 RGBA PNG，透明四角，主体与突出配饰不得裁切。走路共用缩放，
必须有交替落脚、腿/手臂形变、重心与身体起伏；整体平移不算走路。

`actions/transparent/selfie.png` 是桌宠的动画动作图，应由已确认角色形象生成；
它不等于原始照片。可选的原始照片气泡固定使用 `source/selfie.png`，只能来自
最初图片或原视频代表帧。虚构角色或只有旧生成素材、没有原始照片时，在
`pet-config.json` 设置 `"selfie_required": false`，不得伪造来源图；激活时会
清除上一只宠物残留的全局自拍，回滚仍可恢复。参考集在 workflow 中以哈希登记为
同一主体；裁切、去相框、去背景和不同角度不得误判为新身份。

模式：

- `full-body`：完整站立、走路和鞋底接触；
- `half-body`：可使用确认后的鼠标跟随或替代互动；
- `animal`：保持物种步态与四肢关系；配方里的左右接触表示对应前爪与对角后爪
  的领先相位，不应机械套用双足腿名；
- `vehicle-seated`：载具/坐姿互动代替不自然的走路；
- `static`：必须确认静止互动预览。
