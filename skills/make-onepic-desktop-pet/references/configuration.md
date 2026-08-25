# 配方、视觉质量与摸头配置

`config/animation-recipe.v1.json` 由 schema 校验并固定 38 帧基础：
idle 6、walk 8、sit 5、sleep 5、drag 3、selfie 4，其余七个表情各 1。
新宠物默认两张摸头过渡，总数为 40；迁移项目允许 0–2 张，因此兼容 38、39
或 40 帧。

alpha-mass 默认取已确认走路帧中位数与静态动作安全上限的共同最小值。共同目标
低于走路中位数 60% 时失败；静态动作偏差上限 12%，坐睡过渡 22%。

摸头默认时序：

- surprised 320 ms；
- shy/happy 760 ms；
- 每张过渡 180 ms；
- idle blend 160 ms；
- 节流 1400 ms，播放期间再次点击忽略。

分支策略可设 `fixed_shy`、`fixed_happy`、`alternate`、`weighted` 或
`weighted_no_repeat`。默认最后一种，权重 shy 0.55 / happy 0.45。

头部点击区来自当前缩放和 alpha 轮廓顶部窄带。拖拽与双击可抢占摸头序列，
右键与自动状态机不堆叠序列。

## 活动区域运行偏好

活动区域由所有 Windows 基础版和进阶版共享，不放入宠物 manifest：

```json
{
  "movement_region_enabled": false,
  "movement_region": null
}
```

开启后 `movement_region` 是用户本机的 `[x, y, width, height]` 全局坐标；首次
开启可围绕当前位置生成不超过 800×500 的默认矩形。只让自主走动与追鼠标使用
该区域。拖动、缩放和“回到主屏幕”忽略自定义矩形，但继续以透明角色的可见轮廓
限制在屏幕可用区域内。切换或松手不能立即搬动角色；自主行为恢复后才应用边界。
分享包保持默认关闭，不携带制作者坐标。该偏好不增加帧数，也不需要素材审批。

## 渲染路线

新工作区默认 `rendering_mode: "sprite"`，代表升级基础款：38 帧基础配方 +
两张默认摸头过渡帧。不得根据“效果更好”“本机装有 Cubism”或历史宠物曾使用
Live2D 自动切换路线。

只有用户在当前宠物请求中明确说使用 Live2D、Cubism 或混合版，才设置
`rendering_mode: "hybrid-live2d"`。混合版仍须保留完整精灵路线与回退；
`sprite` 配置若同时启用 `hybrid_runtime`，构建应直接失败。

## 进阶扩展与八向视线

基础模板保持 `extension_animations: {}`。只有用户明确要求进阶版、扩展动作或
点名某项能力后，才按 `advanced-customization.md` 增加版本化多帧包，并为每批
候选单独生成预览、确认和帧束哈希。

六帧微表情包应作为一个原子扩展项配置，语义固定为 `calm`、`skeptical`、
`surprised`、`shy`、`pensive`、`happy`，不得用身体动作或镜头变化伪装成
表情差异。

侧面走路必须在侧身模型和八相位走路分别确认后才可配置。示例：

```json
{
  "extension_animations": {
    "side_walk_v1": {
      "base_state": "walk",
      "walk_variant": true,
      "walk_variant_label": "侧面走路",
      "walk_variant_default": false,
      "replaces_forward_walk": false,
      "mirror_for_left": true,
      "frames": [
        "actions/extensions/side_walk_v1/01.png",
        "actions/extensions/side_walk_v1/02.png",
        "actions/extensions/side_walk_v1/03.png",
        "actions/extensions/side_walk_v1/04.png",
        "actions/extensions/side_walk_v1/05.png",
        "actions/extensions/side_walk_v1/06.png",
        "actions/extensions/side_walk_v1/07.png",
        "actions/extensions/side_walk_v1/08.png"
      ]
    }
  }
}
```

`frames` 必须恰好八张。正面走路继续保留并作为新安装默认项；切换偏好按
`pet_id` 保存在本机，不得写回分享包或改变其他宠物。

八向视线不属于进阶版默认包。模板必须显式保持：

```json
{
  "directional_mouse_follow": {
    "enabled": false
  }
}
```

只有用户在当前宠物请求中明确提出视线跟随后，才能配置九帧方向素材；历史宠物、
参考图或其他功能不能作为启用依据。用户要求移除时只关闭配置并重新构建，保留
旧素材和审批记录用于回滚。

## 纯本地互动台词

基础模板保持 `dialogue_system.enabled=false`。用户明确点名聊天气泡或随机台词后，
可配置按动作分组的 `lines`，以及 `bubble_duration_ms`、`cooldown_ms`、
`no_repeat_window` 和 `speak_chance`。构建器负责规范化并写入运行清单；用户在
右键菜单中的修改按 `pet_id` 保存在本机设置，不进入角色默认配置。启用前必须
展示真实气泡预览，使用规范化配置计算语义 SHA-256，并把用户确认绑定到该哈希；
台词、概率、冷却或动作映射变化后旧确认自动失效。

## 双击快捷聊天 UI

Windows 新工作区默认保留：

```json
{
  "companion_ui": {
    "double_click_chat": true,
    "compact_chat": true,
    "reply_bubble_style": "crystal"
  }
}
```

这是框架默认 UI，不是 `dialogue_system` 台词包，也不会自动启用云聊。
构建器写入 `network_on_open: false`；双击只打开聊天页，右键菜单仍打开
完整陪伴中心。只有用户明确要求禁用时才将 `double_click_chat` 设为
`false`；陪伴存储不可用时必须回退到本地表情反馈，不阻断基础桌宠。

## 混合 Live2D

显式选择 `hybrid-live2d` 后，`pet-config.json` 可增加 `hybrid_runtime`：
`enabled`、`idle_backend:
live2d`、`source_directory`、`model_file`、可选 `blink_overlay_file`、
`layer_manifest_file`、`idle_animation`、`sprite_states` 和
`fallback_backend: sprite`。`idle_animation` 可控制强度、眨眼间隔、
眨眼闭合/停留/睁开时长，以及头部、身体、眼球、头发和根节点运动；没有
实际绑定或独立图层的能力必须关闭，
不得只凭参数名称宣称已经支持。运行 manifest 只保留相对路径。
构建必须解析并复制 `model3.json` 的实际引用，缺少 `Groups`/`HitAreas` 时只在
staging 中补空数组。详细导出和烟测规则见 `live2d-hybrid.md`。
