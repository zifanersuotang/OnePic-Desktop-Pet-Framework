# 混合 Live2D 待机

只在用户对当前宠物明确要求 Live2D、Cubism 或混合版，且已具备合法分层素材
时启用。不得因为本机安装了 Cubism、上一只宠物使用过 Live2D，或 Agent 认为
效果更好而推断启用。新建独立宠物 workspace，并明确设置
`rendering_mode: "hybrid-live2d"`；不得覆盖已发布宠物，也不得用 Live2D
取代基础款角色、动作、两张摸头过渡帧或走路确认门禁。

## 运行边界

- Live2D 负责 idle 的轻微呼吸、摇摆和可选细表情。
- walk、sit、sleep、drag、selfie、表情与摸头继续使用透明帧。
- `fallback_backend` 固定为 `sprite`；Live2D ready 前不得隐藏 PNG。
- idle 加载期必须让透明 WebEngine 保持可见并在 PNG 后方完成合成；不能在
  `ready` 前隐藏 WebEngine，否则 Chromium 可能暂停执行并形成 ready 死锁。
- PySide6 保留窗口、拖拽、双击、右键菜单、头部命中和自动状态机。
- 宿主页只从本地资源加载；HTTP 仅绑定 `127.0.0.1`，WebEngine 使用无痕
  profile，不写 `%LOCALAPPDATA%`。
- CDI3 出现参数名不等于 ArtMesh/Deformer 已绑定。必须运行参数端点探针；
  最小值与最大值没有像素差异时，不得把该能力标为已实现。

## 素材与导出

把 PSD、CMO3 和审计截图放在
`user_assets/pets/<id>/authoring/live2d/`，SDK 运行文件放在
`live2d-runtime/`。Cubism 5.3 模型若本地 Web Core 只支持 MOC 5，应按 SDK
5.0 兼容目标导出；不要复制 Editor 内部 DLL 代替官方 Core。

在 `pet-config.json` 配置：

```json
{
  "hybrid_runtime": {
    "enabled": true,
    "idle_backend": "live2d",
    "source_directory": "live2d-runtime",
    "model_file": "Pet.model3.json",
    "layer_manifest_file": "layers/layered-idle.json",
    "blink_overlay_file": "authoring/live2d/layers/head-closed.png",
    "idle_animation": {
      "intensity": 1.0,
      "blink": true,
      "head_motion": true,
      "body_motion": true,
      "eye_motion": false,
      "hair_motion": false,
      "root_motion": true,
      "blink_interval_ms": [2600, 4600],
      "blink_duration_ms": [105, 60, 160]
    },
    "sprite_states": ["walk", "sit", "sleep", "drag", "selfie"],
    "fallback_backend": "sprite"
  }
}
```

`blink_duration_ms` 依次是闭合、闭眼停留和睁开时长。自然默认值较短；角色
验收时若肉眼难以识别，可配置夸张慢眨。自然眨眼建议总计不超过 800 ms；
慢眨允许闭合 60–1500 ms、停留 20–3000 ms、睁开 60–2000 ms，三段总计
不得超过 5000 ms。`blink_interval_ms` 支持 1.2–60 秒，第一次眨眼也必须
服从该区间，不得写死启动后时间。

主窗口的单次 idle 驻留必须至少覆盖“最大眨眼间隔 + 三段眨眼总时长 +
500 ms 合成余量”。不能只在独立 WebGL 视图中验证眨眼；还要验证桌宠自主
状态机不会在自动眨眼开始前切换到 walk、sit 或 selfie。WebGL `ready` 后
应重新保证完整驻留窗口，模型加载耗时不能占用这段时间。

`layer_manifest_file` 只用于已有合法分层素材、但 Cubism 参数尚未绑定的安全
过渡方案。分层清单至少包含 `body`、`arm_left`、`arm_right`、`head_open`
和 `head_closed`，每层必须是同画布 RGBA PNG 并带旋转轴点。它不是完整
Cubism 关键形状绑定的替代品；报告应明确渲染后端为 `layered-webgl`。

分层预览还必须通过“完整角色”检查：

- 手臂根部置于身体层后方，由躯干自然遮住接缝；不得露出直切肩线或让整条
  手臂像悬浮贴纸。左右待机姿势应成对且重心一致，挥手臂只用于动作状态；
- 领口、项圈和胸饰按真实遮挡关系放在头部后、躯干前；跨越两个深度的饰品
  应拆层，不能用一个大图层同时压住脸、头发和身体；
- 写实度、材质和线条密度必须与主体一致。过大工业挂件、未经收口的分件和
  角色轮廓断裂均视为视觉 QA 失败；
- 对称零件可在声明式图层中使用 `mirror_x: true` 确定性复用，但必须保留
  原动作源图，不能用覆盖方式破坏挥手等已有状态；
- 每次改动层序、轴点、缩放或镜像后，都要生成透明合成预览并运行真实
  `PetWindow` 烟测；只看 PSD 分层或 WebGL `ready` 不足以通过。
- 分层慢眨不得交替替换两张完整头图，也不得用整头透明交叉淡化。声明
  `blink_regions` 后，构建器应把闭眼内容羽化限制在左右眼区，运行时让完整
  睁眼头始终存在，并用眼形遮罩连续覆盖；未完成的 Cubism 探针底模必须同时
  `visible=false`、`renderable=false`，加载与眨眼全过程都不能闪现。

构建只打包所选 `model3.json` 及其 MOC、纹理、DisplayInfo、Physics、Pose、
UserData、Expressions、Motions 和声音引用。PSD、CMO3、历史导出与原始分层
永不进入分享包。简单 Editor 导出缺少 `Groups`/`HitAreas` 时，由 staging
确定性补空数组，不修改审计源文件。

## 渲染与烟测

项目宿主固定使用 `easy-live2d 0.4.4`、PixiJS 8.18.1 和本地官方 Cubism Web
Core。重新构建宿主 bundle 后运行：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\tools\smoke_live2d_view.py `
  --runtime-root .\user_assets\pets\<id> `
  --model live2d-runtime\<model>.model3.json `
  --screenshot .\user_assets\pets\<id>\review\live2d-runtime-smoke.png
```

必须同时满足：`ready: true`、无模型加载错误、截图存在、
`nontransparent_pixels > 0`。不要仅以网络请求或 JS ready 判断视觉通过。
受限自动化沙箱外运行烟测时可由执行环境批准启动 Qt WebEngine；正式应用不应
关闭 Chromium 沙箱。

发布仍只用 `scripts/release_pet.ps1`。确认 source smoke、packaged EXE
smoke、隐私、ZIP CRC 和 SHA-256 均进入 release report。

首次导出或修改 MOC 后运行参数端点探针：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe .\tools\probe_live2d_parameters.py `
  --runtime-root .\user_assets\pets\<id> `
  --model live2d-runtime\<model>.model3.json `
  --output .\user_assets\pets\<id>\review\live2d-parameter-probe.json `
  --capture-dir .\user_assets\pets\<id>\review\live2d-parameter-probe
```

分层后端还必须运行 `tools/smoke_layered_live2d.py`，强制切换睁眼/闭眼并比较
运动前后截图；眨眼与运动差异任一低于阈值都不得发布。模型名和分层清单名
必须读取当前宠物 `pet-config.json` 的 `model_file` 与
`layer_manifest_file`，不得凭通用文件名手写猜测。真实窗口烟测还应按用户
实际 `display_height` 复现，并断言完整睁眼头仍可见、闭眼覆盖率达到终点、
眼区数量正确，以及 Cubism 探针底模不可见且不可渲染。
