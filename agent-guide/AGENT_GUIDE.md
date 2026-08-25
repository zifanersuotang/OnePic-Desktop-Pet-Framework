# Agent 执行入口

本项目的目标不是让 Agent 只返回一段提示词，而是让 Agent 按固定架构完成环境检查、素材处理、代码配置、测试、视觉验收和打包。

执行时必须遵守以下顺序：

1. 阅读根目录 `AGENTS.md`、`文件夹说明.md` 和本文件；
2. 先运行 `scripts/check_environment.ps1`；
   接手已有 `<pet-id>` 或恢复中断任务时，随后运行
   `tools/pet_workspace.py agent-status <pet-id>`；每次变更、审批、失败或构建后
   重新运行，每轮只执行一个允许动作，`must_pause=true` 时必须暂停；
3. 缺少系统软件时停止并请求用户确认，不自动修改系统；
4. 运行 `scripts/setup_environment.ps1` 创建项目虚拟环境；
5. 接收用户拥有使用权的图片或视频，用 `scripts/new_pet.ps1` 创建独立工作区；
   未提 Live2D 时保持默认 `RenderingMode sprite`，制作 38 帧基础 + 2 张摸头
   过渡帧；只有用户明确要求 Live2D/Cubism/混合版时才传
   `-RenderingMode hybrid-live2d`；图片自动成为自拍成片，视频必须从原视频提取代表帧；
6. 让用户选择保留原画风、轻度 Q 版或完整 Q 版，分析脸型、眼型、眉形、发型、服装、配饰和气质，只生成首张标准角色候选；
7. 运行 `approve-character` 打开图形确认窗口；用户未亲自点击确认时必须暂停，禁止生成动作；
8. 标准角色确认后，按“一个动作一个批次”生成基础动作：每批只保留 2–3 个候选，且只能以已批准标准角色为身份锚点，禁止在残次帧上继续改图。逐张检查肢体完整、单一动作、身份一致、无道具粘连和无重影轮廓；任一项失败即停止该批并从标准角色重生成。合格候选必须通过 `tools/pet_workspace.py action-review` 记录哈希和五项质检后才能写入 `actions/transparent/`。之后制作新宠物默认两张摸头过渡帧，并为八相位走路生成 GIF 预览；惊讶动作应克制自然，由独立“！”符号增强，不使用双手高举和大张嘴的尖叫姿势；只有用户明确提出进阶版、扩展动作或点名能力时才追加多帧动作包，每组必须独立生成 GIF、联系表和哈希报告并取得直接确认；六帧微表情包必须保持身体与镜头不变并作为整包确认；侧面走路必须先确认单张侧身模型，再确认八相位侧走，且只能作为可切换项保留正面走路默认；纯本地互动台词包不新增图片，也必须由用户点名后才启用，展示真实气泡预览并将台词、概率、冷却和动作映射的语义配置哈希绑定确认，同时检查自定义持久化和隐私；八向视线是独立实验选项，不属于进阶版默认内容，未明确点名时必须保持 `directional_mouse_follow.enabled=false`；
9. 八相位走路或替代互动预览提交后重新运行 `agent-status`，展示报告绑定的 GIF、
   帧束或互动预览；用户未确认时必须暂停。用户明确通过或否决后，只能原样执行
   不含占位符的 `decision.approval_command` / `rejection_command`，或仅用
   `required_inputs.user_feedback` 安全渲染 `rejection_command_template`，并再次观察；
   不得直接执行模板或手写省略证据哈希的命令。`static`、`vehicle-seated` 没有
   走路时必须先按报告运行
   `interaction-review` 提交预览；批准/否决命令同时绑定预览哈希与 mode，否决反馈
   保留到重新修订和提交的下一轮。`full-body`、`animal` 只走八相位路线；若走路
   与互动均已批准，验证与发布最小 workflow 以走路为准；
10. 两个门禁通过后先运行 `tools/pet_workspace.py validate`，再用 `scripts/build_pet.ps1 -Activate` 生成 38 帧基础配方与 0–2 张摸头过渡帧；显式选择的进阶动作逐组通过哈希审批后才装配，侧面走路必须恰好八帧并匹配侧身模型与帧束两级审批，正面走路保持默认，未点名的互动台词、八向视线和 Live2D 不得进入运行清单；只有显式混合路线才在此后追加并验证 Live2D 待机层；所有输出先进入 staging，QA 和测试通过后才激活；
11. Windows 基础版和进阶版都保留右键“活动区域”：默认关闭且不写入宠物
    manifest；开启时只限制自主走动与追鼠标，手动拖动忽略自定义矩形但保留屏幕
    防丢失边界。检查开关、坐标、本机持久化和多屏安全回退，不把制作者坐标带入分享包；
12. 运行单元测试、源码启动冒烟测试和适用于所有 sprite/hybrid 路线的双击精简
    聊天零联网烟测；构建后的 runtime、QA 和 latest-build 必须匹配同一 build ID、
    当前输入指纹与应用源码指纹。构建失败时保留活动版本并检查 `failed-builds/`；
    只有诊断为瞬态失败后才用 `required_inputs.diagnostic_reason` 安全渲染
    `decision.recovery_command_template`（或执行无占位符的 `recovery_command`），
    不得盲目重试；
13. 用户查看当前 build 的 GIF、联系表、QA 与真实窗口并明确验收后，原样执行
    `agent-status` 的 `decision.approval_command`；否决则按 `required_inputs` 安全渲染
    `decision.rejection_command_template`，随后重新观察。仅 `sharing` 授权变化不废止 runtime，
    应用源码、打包资源或其他构建输入变化必须重新构建与验收。进入
    `ready_to_release` 后运行 `scripts/release_pet.ps1`。package-preparation 必须为
    schema v2，含随机 `preparation_id`、时间及 QA/指针/workflow/自拍哈希；finalize
    重查当前 QA、runtime 和 share-with 自拍授权，并读取 schema v1 passed 的测试、
    源码/成品启动、互动菜单、源码/成品双击六份 JSON。除测试外五份证据都必须绑定
    当前 build ID 与 runtime manifest SHA-256，Hybrid 证据也绑定 pet/runtime；两份
    成品证据必须绑定最终
    EXE SHA-256、完整 one-dir `distribution_sha256`、pet-id 和 preparation_id；烟测后
    改动 `_internal` 任一文件或其他任何不一致都不生成 ZIP；
14. 公开发布前再次执行隐私与授权检查。只豁免 `_internal/PySide6` 随附 vendor 源
    中的 `source` 目录名，`_internal/user_assets` 下任何私密目录仍须拒绝。烟测结束
    必须恢复 `ONEPIC_TESTS_PASSED`、`ONEPIC_INCLUDE_USER_ASSETS`、
    `ONEPIC_PACKAGE_USER_ROOT`、`ONEPIC_INCLUDE_SELFIE_ASSET` 及既有隔离环境变量。

完整的多桌宠工作区结构和统一命令见 `docs/可复用桌宠制作流程.md`；
进阶动作、微表情、侧面走路、互动台词的版本审批和八向视线选配边界见
`docs/进阶版桌宠定制框架.md`。
