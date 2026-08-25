# OnePic Desktop Pet Agent 规则

1. 开始工作前先阅读 `文件夹说明.md`、`技术更新.md` 和 `agent-guide/AGENT_GUIDE.md`。
2. 环境检查是制作流程的第一步；不得在没有用户确认的情况下安装系统软件、修改环境变量或申请管理员权限。
3. Python 依赖只能安装到项目 `.venv`，优先调用 `scripts/` 中已有脚本。
4. 未明确授权公开的图片只能放入 `user_assets/`；虚拟角色或其他已获授权素材可以进入公开演示目录，但必须同步写明授权范围。
5. 修改 Python 文件时同步维护文件顶部的中文模块文档字符串。
6. 原图登记后先让用户选择保留原画风、轻度 Q 版或完整 Q 版，再只生成一张标准角色候选；必须通过确认窗口获得用户确认，未确认时不得批量生成动作。
7. 标准角色确认后才能生成动作；走路必须包含左右落脚、下压、迈步和抬升八相位，并生成 GIF 交给用户确认。
7.1 基础动作必须每次只生成一个动作，每个动作一批只保留 2–3 个候选；不得在残次帧上继续改图，重做时必须回到已批准标准角色。
7.2 任何动作候选入库前，必须逐张检查肢体完整、只表达一个动作、身份一致、道具不粘连、轮廓无重影；四手、少手、断肢、融合道具或重复肢体任一项出现即停止该批次。
8. 每只宠物只使用 `user_assets/pets/<pet-id>/workflow.json`；角色与走路/替代互动确认必须绑定当前素材哈希，未通过时不得加载、激活或打包。
9. 自拍成片必须使用最初登记的原图，不得用动画帧、标准角色图或其他演示图片代替。
10. 新增角色动作后必须运行素材、行为、配置、流程和窗口测试；自动测试不能替代视觉验收。
11. 不删除历史素材，不覆盖用户原图；需要替换动作时保留可恢复副本。
12. 打包公开版本前检查 `user_assets/`、`dist/` 和打包资源，确认不存在私人图片和本机绝对路径。
13. 制作新角色时先用 `scripts/new_pet.ps1` 创建 `user_assets/pets/<pet-id>/`，不得把不同桌宠的候选图、动作和走路帧混在同一目录。
14. 优先用 `tools/pet_workspace.py` 验证素材，并用 `scripts/build_pet.ps1` 构建和激活；角色专用脚本只保留为历史案例，不继续复制。
15. 默认动画为“38 帧基础配方 + 0–2 张摸头过渡帧”；新宠物默认必须准备两张（40 帧），旧宠物缺少时必须安全降级。
16. 分享版只允许通过 `scripts/release_pet.ps1` 生成完整目录 ZIP；不得单发 EXE，不得把原图、视频、草稿或备份复制进发布暂存区。
17. 用户未明确提出 Live2D、Cubism 或混合版时，必须走 `rendering_mode=sprite`
    的升级基础款：38 帧基础配方 + 新宠物默认 2 张摸头过渡帧。不得主动启用、
    生成或打包 Live2D；只有明确要求后才使用 `hybrid-live2d` 追加待机层。
17.1 公开仓库与默认 PyInstaller 包不得包含 Live2D Cubism Core、预构建 Web
    bundle、模型、纹理或客户资产。即使用户明确要求 Live2D，也必须先完成独立许可
    审查，并在私有隔离目录中提供其自行取得的运行库；不得把专有文件回写公开主线。
18. 只有用户明确提出“进阶版”“扩展动作”或点名某项能力时，才追加多帧扩展包。
    每批扩展必须版本化保存，生成 GIF、联系表和哈希报告，单独获得用户确认；
    不得覆盖已批准帧，也不得用自动测试代替扩展包视觉验收。
19. 八向视线/鼠标视线跟随是独立选配项，不属于基础款，也不属于进阶版默认包。
    用户未在当前宠物请求中明确提出时，`directional_mouse_follow.enabled` 必须为
    `false`；不得因为参考图、历史宠物或“效果更丰富”而推断启用。
20. 活动区域是所有 Windows 基础版和进阶版共享的运行设置，不属于动作扩展，
    不增加帧数或审批门禁。分享包默认 `movement_region_enabled=false`，不得把
    制作者机器坐标写入宠物 manifest；用户开启后只约束自主走动与追鼠标，
    手动拖动忽略自定义矩形并继续保留屏幕防丢失边界。
21. 接手已有宠物、恢复中断任务，以及每次文件变更、审批、失败或构建之后，必须运行
    `tools/pet_workspace.py agent-status <pet-id>` 重新观察。每轮只执行报告中的一个
    `allowed_next_actions`；`must_pause=true` 时停止等待对应用户输入或视觉确认，
    不得把自动检查当成人工批准。`decision.command`、`approval_command`、
    `rejection_command`、`recovery_command` 只有在非空且不含占位符时才可原样执行；
    `command_template`、`rejection_command_template`、`recovery_command_template`
    不可直接执行，只能收集 `required_inputs` 声明的值、安全转义为单个参数后渲染，
    不得新增或猜测其他参数。每次执行后立即重新观察。
22. 构建失败会隔离 staging 并保留当前活动版本，此时只检查 `failed-builds/`，不得
    盲目重试或运行全局回滚。只有诊断为可重试的瞬态失败时，才按当前报告的
    `decision.recovery_command`，或用 `required_inputs.diagnostic_reason` 安全渲染
    `recovery_command_template`；只有某版本成功激活后又被用户否决时，先核对
    `user_assets/active-pet.json` 的宠物 ID，再使用 `scripts/rollback_pet.ps1`。
23. runtime、`assets-qa.json` 与 `latest-build.json` 必须绑定同一 build ID 和当前
    构建输入指纹。用户查看该 build 的 GIF、联系表、QA 与真实窗口并明确验收后，
    才能原样执行 `agent-status` 返回的 `decision.approval_command`；否决则执行完整
    `rejection_command`，或用 `required_inputs.user_feedback` 安全渲染
    `rejection_command_template` 并保留反馈。仅修改 `sharing` 不废止 runtime 验收，
    但应用源码、打包资源或其他构建输入变化必须重新构建与验收。发布必须复用该已
    验收 runtime，并以 fail-closed 方式核对包内固定清单路径、宠物身份、发布模式、
    runtime/应用指纹和自拍包含状态；源码与包内 EXE 的真实双击聊天烟测必须在隔离并
    可恢复的环境变量下全部通过。
24. `full-body` 与 `animal` 只允许八相位走路门禁；`static` 与
    `vehicle-seated` 路线没有八相位走路时，必须先用
    `interaction-review` 提交替代互动预览，再由 `agent-status` 进入等待审批；通过或
    否决都只能执行报告中同时绑定当前预览哈希与 mode 的对应命令，否决后从已批准
    标准角色修订并重新提交，不得直接写入批准状态。若走路与互动都已批准，验证与
    发布最小 workflow 始终以走路为准；互动只在没有已批准走路时作为允许模式的后备。
25. `package-preparation.json` 必须为 schema v2，含随机 `preparation_id`、
    `prepared_at`、QA、活动指针、最小 workflow 与可选自拍哈希。finalize 必须重查当前
    workflow、QA、runtime 验收和 `share-with-approved-selfie` 授权，并按哈希核对包内
    自拍；还必须读取 schema v1 且 passed 的测试、源码/成品启动、互动菜单、源码/成品
    双击聊天六份 JSON 证据。除测试外五份报告都必须匹配当前 `build_id` 与
    `runtime_manifest_sha256`，Hybrid 报告也必须绑定当前 pet/runtime。两份成品证据
    还必须同时绑定最终 EXE SHA-256、完整
    one-dir `distribution_sha256`、pet-id 与本次 `preparation_id`；烟测后替换
    `_internal` 任一文件都必须失败，不能只依赖命令退出码或 passed 布尔值。
26. 隐私扫描只允许 `_internal/PySide6` 随附 vendor 源中的 `source` 目录名例外；
    `_internal/user_assets` 下出现 source、candidate、drafts、backups、failed-builds 或
    staging 必须拒绝。发布脚本结束时还必须恢复 `ONEPIC_TESTS_PASSED`、
    `ONEPIC_INCLUDE_USER_ASSETS`、`ONEPIC_PACKAGE_USER_ROOT`、
    `ONEPIC_INCLUDE_SELFIE_ASSET` 及既有隔离环境变量。
