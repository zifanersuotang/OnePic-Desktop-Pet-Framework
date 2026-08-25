# 固定流程与命令

## 初始化

```powershell
.\scripts\check_environment.ps1
# 仅当 .venv 不存在时执行：
.\scripts\setup_environment.ps1
.\scripts\new_pet.ps1 `
  -PetId "<id>" -DisplayName "<name>" `
  -Source "<absolute-path>" -Mode full-body `
  -RenderingMode sprite `
  -RecommendedDisplayHeight 220
```

`sprite` 是无条件默认路线：38 帧基础配方 + 新宠物两张摸头过渡帧。用户没有
明确提出 Live2D、Cubism 或混合版时，不得改模式或创建 Live2D 资源。只有
明确要求时才把初始化参数改为 `-RenderingMode hybrid-live2d`；仍必须先完成
同一套精灵角色、动作、摸头与走路门禁。

真人/实拍图片会登记为原始照片气泡；视频另传原视频代表帧。虚构角色或只有旧生成
素材、没有原始照片时，将 `pet-config.json` 的 `selfie_required` 设为 `false`，
不得创建占位自拍。`workflow.json` 必须位于当前宠物工作区。

活动区域由 Windows 引擎自动提供，不要写进工作区配置或审批。保持共享默认
`movement_region_enabled=false`、`movement_region=null`；发布前检查未携带
制作者坐标。用户运行后可从右键菜单自行设置，仅约束自主走动与追鼠标。

初始化或接手已有工作区后运行：

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <id>
```

每次只执行报告中的一个 `allowed_next_actions`，完成或失败后重新观察。
`must_pause=true` 时必须停在用户输入或审批门禁。展示当前证据后，只有用户明确
通过或否决时才执行对应字段。无占位符的 `command`、`approval_command`、
`rejection_command`、`recovery_command` 原样执行；`command_template`、
`rejection_command_template`、`recovery_command_template` 只能收集并安全转义
`required_inputs` 指定的值后渲染，模板本身不可执行。不得手写省略 CAS 哈希的命令；
执行后重新观察，详见 `agent-loop.md`。

## 角色门禁

```powershell
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py `
  --pet-id <id> character-candidate `
  --image "user_assets/pets/<id>/candidate/approved.png" `
  --style preserve_original --feature "<identity>"
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py `
  --pet-id <id> approve-character
```

第二条命令只能在用户已查看候选时执行。

## 走路或替代互动门禁

走路顺序：右接触、右下压、左迈步、左抬升、左接触、左下压、右迈步、右抬升。

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <id>
```

按报告的完整 `decision.command` 生成走路预览；展示当前 GIF 与八帧后，执行准确的
批准命令，或用 `required_inputs.user_feedback` 渲染否决模板。`static` 与
`vehicle-seated` 没有八相位走路时，报告会先给出含 `{preview_path}` 的
`interaction-review` `command_template`；只填声明路径，提交后重新观察并展示当前
预览，再执行同时绑定预览哈希与 mode 的批准或否决命令。否决理由会保留，修订后
必须提交新预览，不能直接改审批状态。互动批准有效时，构建器从已批准标准角色生成
静止占位序列并写入 `stationary=true`。`full-body`、`animal` 只允许八相位走路；
`half-body` 默认走路，仅在显式进入互动且没有批准走路时使用后备。若走路与互动
都已批准，验证与发布最小 workflow 以走路为准。

## 进阶模块门禁

只有用户明确选择进阶版、扩展动作或具体能力后才追加。常规多帧动作和六帧
微表情包均按“版本目录 → GIF/联系表/哈希报告 → 用户直接确认 → 配置启用”执行。
微表情固定为平静、狐疑、惊讶、害羞、沉思、开心，身体与镜头必须完全一致。

侧面走路采用两个连续门禁：

1. 生成一张向右侧身模型并单独确认；
2. 以该模型为身份锚点生成八相位侧走 GIF、联系表和帧束哈希，再单独确认。

侧走只能作为可切换变体，必须保留正面走路并将正面设为默认。配置中使用
`walk_variant=true`、`walk_variant_default=false` 和
`replaces_forward_walk=false`。

纯本地互动台词不新增图片，但启用前必须展示真实气泡预览，把规范化后的台词、
概率、冷却和动作映射计算为语义配置哈希并取得用户确认。自定义台词只按宠物保存在
本机。八向视线仍是独立点名选配，不能随上述模块自动启用。

Windows 双击快捷聊天是框架默认 UI，不需要动作素材审批，也不等于启用
本地台词或云聊。新工作区应保留 `companion_ui.double_click_chat=true`，
构建验证精简聊天窗、晶体回复气泡和打开时零网络请求。

## 装配、激活、发布

```powershell
.\.venv\Scripts\python.exe .\tools\pet_workspace.py touch-transitions <id>
.\scripts\build_pet.ps1 -PetId <id> -Activate
.\.venv\Scripts\python.exe .\tools\pet_workspace.py agent-status <id>
```

`touch-transitions` 在角色门禁通过后确定性复用已验收反应帧；新宠物仍应默认提供
两张专用过渡图，真正声明为零张的旧宠物可安全降级为运行时混合。迁移项目若声明
需要专用帧却缺少来源取证，命令会先备份旧文件、确定性重建并要求对新 runtime
重新视觉验收，不得静默采信旧帧。

构建按 staging → QA → 输入指纹取证 → 原子激活执行。失败时保留活动 runtime，
先检查 `failed-builds/`；只有诊断为瞬态失败后才执行完整
`decision.recovery_command`，或用 `required_inputs.diagnostic_reason` 安全渲染
`recovery_command_template`，不得盲目重试或回滚。

最终 runtime 只能在用户查看当前 GIF、联系表、QA 和真实窗口后，用
`agent-status` 返回的 `decision.approval_command` 批准；否决则执行完整
`decision.rejection_command`，或用 `required_inputs.user_feedback` 安全渲染
`rejection_command_template`，随后重新观察。仅 `sharing` 授权变化不会废止
runtime；应用源码、打包资源、配方、运行配置或已批准素材变化必须重新构建与验收。

发布准备写 schema-v2 `package-preparation.json`：随机 `preparation_id`、
`prepared_at`、QA/包内指针/最小 workflow/可选自拍 SHA-256。finalize 重查当前
workflow、QA、runtime 和 share-with 自拍授权，包内自拍必须匹配准备哈希。它还强制
读取 schema-v1 passed 的 UnitOnly 测试、源码/成品启动、互动菜单、源码/成品双击
聊天六份 JSON。除测试外五份报告都必须绑定当前 `build_id` 和
`runtime_manifest_sha256`；Hybrid 报告也绑定当前 pet/runtime。两份成品报告必须
绑定最终 EXE SHA-256、完整 one-dir
`distribution_sha256`、pet-id 和 preparation_id；烟测后改动任何 `_internal` 文件都
拒绝 finalize，passed 开关或退出码本身不是证据。

隐私扫描只豁免 `_internal/PySide6` vendor 源中的 `source` 目录名；
`_internal/user_assets` 下的私密目录继续拒绝。烟测隔离并恢复设置、用户目录、宠物
定位、陪伴、演示、测试及打包环境，包括 `ONEPIC_TESTS_PASSED`、
`ONEPIC_INCLUDE_USER_ASSETS`、`ONEPIC_PACKAGE_USER_ROOT`、
`ONEPIC_INCLUDE_SELFIE_ASSET`。全部通过后才执行隐私 → CRC/SHA → 完整目录 ZIP。
只有重新观察得到 `ready_to_release` 后才运行：

```powershell
.\scripts\release_pet.ps1 -PetId <id> -Mode share-no-selfie
```

仅在用户明确选择 `hybrid-live2d` 时，先在未激活 workspace 上运行
`tools/smoke_live2d_view.py` 并检查 alpha 截图，再进入同一事务发布；详见
`live2d-hybrid.md`。

## 迁移

```powershell
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py migrate-legacy <id>
.\.venv\Scripts\python.exe .\tools\pet_workspace.py migrate-existing <id>
```

第二条只接受有候选、八帧、passed QA、38 帧 runtime 和人工 walk GIF 的历史项目。
若其配置要求摸头过渡帧却没有当前来源记录，继续按 `agent-status` 的
`touch-transitions` 动作备份、重建并重新验收。
